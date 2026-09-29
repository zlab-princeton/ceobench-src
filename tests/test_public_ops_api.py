"""Public scoped operations API matches engine behavior and rejects atomically."""
import copy
import json
import sqlite3
import urllib.request
from types import SimpleNamespace

import pytest
from numpy.random import default_rng

from saas_bench.api_server import NovaMindAPIServer, _dispatch_targeted_ops_spend
from saas_bench.config import BenchmarkConfig
from saas_bench.database import init_database
from saas_bench.novamind_api import analytics
from saas_bench.session_integrity import RequestJournal, initialize_integrity, write_checkpoint
from saas_bench.simulation import Simulator
from saas_bench.tools import AgentTools

FIELDS = ('targeted_ops_spend', 'targeted_ops_spend_by_plan',
          'targeted_ops_spend_by_group_plan', 'targeted_ops_spend_by_customer')

@pytest.fixture
def tools(tmp_path):
    conn = init_database(':memory:')
    config = BenchmarkConfig(seed=42)
    sim = Simulator(conn, config, default_rng(42))
    sim.initialize()
    tools = AgentTools(conn, 0, tmp_path, config=config, rng=sim.rng)
    yield tools
    conn.close()


def snapshot(tools):
    return (copy.deepcopy([getattr(tools.config, f) for f in FIELDS]),
            [tuple(r) for r in tools.conn.execute('SELECT * FROM config_overrides')],
            [tuple(r) for r in tools.conn.execute('SELECT * FROM ledger')])


def test_sdk_omission_and_clear_payloads(monkeypatch):
    calls = []
    monkeypatch.setattr(analytics._client, 'call', lambda tool, args: calls.append((tool,args)))
    analytics.set_targeted_ops_spend({'S1': 1})
    analytics.set_targeted_ops_spend(by_group={'S2':2}, by_plan={}, by_group_plan={'E1':{'C':3}}, by_customer={'42':4})
    analytics.set_targeted_ops_spend()
    assert calls == [
        ('set_targeted_ops_spend', {'targeted_spend': {'S1':1}}),
        ('set_targeted_ops_spend', {'by_group':{'S2':2},'by_plan':{},'by_group_plan':{'E1':{'C':3}},'by_customer':{'42':4}}),
        ('set_targeted_ops_spend', {}),
    ]


def test_scoped_and_legacy_dispatch_preserve_and_clear(tools):
    result = _dispatch_targeted_ops_spend(tools, {'by_group':{'S1':10},'by_plan':{'A':20},
        'by_group_plan':{'S2':{'B':30}},'by_customer':{'42':40}})
    assert result.success and result.data['total_extra_per_day'] == 100
    assert _dispatch_targeted_ops_spend(tools, {'S1':5}).success  # original raw HTTP format
    assert _dispatch_targeted_ops_spend(tools, {'targeted_spend': {'S2':7}}).success
    assert tools.config.targeted_ops_spend == {'S2':7}
    assert tools.config.targeted_ops_spend_by_plan == {'A':20}
    assert _dispatch_targeted_ops_spend(tools, {'by_group':{}}).success
    assert tools.config.targeted_ops_spend == {}
    assert _dispatch_targeted_ops_spend(tools, {}).data['total_extra_per_day'] == 90
    assert _dispatch_targeted_ops_spend(tools, {'by_plan':{},'by_group_plan':{},'by_customer':{}}).data['total_extra_per_day'] == 0
    assert _dispatch_targeted_ops_spend(tools, {'by_group':{'S1':0}}).success


@pytest.mark.parametrize('args', [
 {'by_group':{'S1':100},'by_plan':{'D':10}},
 {'by_group':{'S1':100},'by_group_plan':{'S2':{'D':10}}},
 {'by_group':{'S1':100},'by_group_plan':{'S2':3}},
 {'by_group':{'S1':100},'by_customer':{'x':10}},
 {'by_group':{'S1':100},'by_customer':{'0':10}},
 {'by_group':{'S1':100},'by_customer':{-1:10}},
 {'by_group':{'S1':100},'by_customer':{1.5:10}},
 {'by_group':{'S1':100},'by_customer':{True:10}},
 {'by_group':{'S1':100},'by_customer':{'42':-1}},
 {'by_group':{'NOT_A_GROUP':10}},
 {'by_group':[1]}, {'by_plan':[]}, {'by_group_plan':[]}, {'by_customer':[]},
 {'targeted_spend':{},'by_group':{}},
 {'by_plan':{'A':1},'unknown':2},
 {'by_group':{'S1':True}}, {'by_group':{'S1':'10'}},
 {'by_group':{'S1':float('nan')}}, {'by_plan':{'A':float('inf')}},
 {'by_group_plan':{'S1':{'A':float('-inf')}}}, {'by_customer':{'42':float('nan')}},
 {'by_group':{'S1':10**400}},
 {'by_group':{'S1':1e308},'by_plan':{'A':1e308}},
 [],
])
def test_invalid_requests_never_partially_mutate(tools, args):
    assert tools.set_targeted_ops_spend(by_group={'S3':8}, by_plan={'C':9}).success
    before = snapshot(tools)
    result = _dispatch_targeted_ops_spend(tools, args)
    assert not result.success
    assert snapshot(tools) == before


def test_sdk_real_http_scopes_and_validation(tmp_path, monkeypatch):
    # A real cross-thread API and durable request journal, not mocked dispatch.
    original = init_database(':memory:')
    conn = sqlite3.connect(':memory:', check_same_thread=False)
    conn.row_factory = sqlite3.Row
    original.backup(conn); original.close()
    config = BenchmarkConfig(seed=42)
    sim = Simulator(conn, config, default_rng(42)); sim.initialize()
    tools = AgentTools(conn,0,tmp_path,config=config,rng=sim.rng)
    initialize_integrity(conn); write_checkpoint(conn,sim,tools);conn.commit()
    api = NovaMindAPIServer(tools, sim, conn)
    api.journal = RequestJournal(conn,lambda:write_checkpoint(conn,sim,tools))
    api.start()
    monkeypatch.setenv('NOVAMIND_API_PORT',str(api.port))
    try:
        result = analytics.set_targeted_ops_spend(by_group={'S1':10},by_plan={'B':20},
            by_group_plan={'E1':{'C':30}},by_customer={'42':40})
        assert result['total_extra_per_day']==100
        before=snapshot(tools)
        with pytest.raises(analytics._client.NovaMindAPIError):
            analytics.set_targeted_ops_spend(by_group={'S1':999},by_plan={'invalid':1})
        assert snapshot(tools)==before
        assert not api._unrecoverable
        assert analytics.set_targeted_ops_spend({'S2':5})['total_extra_per_day']==95
        assert analytics.set_targeted_ops_spend(by_plan={})['total_extra_per_day']==75
    finally:
        api.stop();conn.close()
