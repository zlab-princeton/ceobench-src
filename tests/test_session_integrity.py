"""Regression tests for public-session integrity, not gameplay policy."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
import urllib.request

import pytest
from numpy.random import Generator, PCG64

from saas_bench.config import BenchmarkConfig
from saas_bench.database import init_database, get_cash
from saas_bench.simulation import Simulator
from saas_bench.tools import AgentTools
from saas_bench.shocks import ShockManager
from saas_bench.config import ScenarioPack
from saas_bench.session_integrity import (
    initialize_integrity, write_checkpoint, read_checkpoint,
    restore_runtime, RequestJournal, capture_runtime,
)
from saas_bench.api_server import NovaMindAPIServer

# Operator-side tests may inspect their own generated artifact checkpoint.
# This does not expose protected data through the agent interface.
if os.environ.get('INTEGRITY_ZIPAPP'):
    import saas_bench
    saas_bench.__path__.append(os.environ['INTEGRITY_ZIPAPP'] + '/saas_bench')


def world(tmp_path):
    import sqlite3
    initial = init_database(':memory:')
    conn = sqlite3.connect(':memory:', check_same_thread=False)
    conn.row_factory = sqlite3.Row
    initial.backup(conn)
    initial.close()
    config = BenchmarkConfig(seed=42)
    sim = Simulator(conn, config, Generator(PCG64(42)))
    sim.initialize()
    tools = AgentTools(conn, 0, tmp_path, rng=sim.rng, config=config, seed=42)
    shock = ShockManager(conn, sim.rng, ScenarioPack(name='test', description='test'))
    initialize_integrity(conn)
    write_checkpoint(conn, sim, tools, shock)
    conn.commit()
    return conn, sim, tools, shock


def test_full_configuration_and_all_rngs_restore(tmp_path):
    conn, sim, tools, shock = world(tmp_path)
    tools.set_promotion(global_promotion=3, by_group={'S1': 7}, by_customer={'1': 2}, by_group_plan={'S2': {'B': 5}})
    tools.set_targeted_ad_spend({'search_ads': {'S1': 400}})
    tools.set_targeted_dev_spend({'S1': 200})
    tools.set_targeted_ops_spend({'S2': 300})
    sim._customer_quality_noise[123] = .913
    sim._involuntary_churn_mu_cache[('S1', 2)] = .17
    sim.step_week()
    tools.set_current_day(sim.current_day)
    write_checkpoint(conn, sim, tools, shock)
    conn.commit()
    saved = read_checkpoint(conn)
    expected_config = dict(vars(sim.config))
    expected_rngs = {k: v.random() for k, v in vars(sim).items() if isinstance(v, Generator)}
    expected_shock = shock.rng.random()
    restored = Simulator(conn, BenchmarkConfig(seed=42), Generator(PCG64(42)))
    restored.initialize(resume=True)
    restore_runtime(saved, restored, tools, shock)
    assert vars(restored.config) == expected_config
    assert restored._customer_quality_noise[123] == .913
    assert restored._involuntary_churn_mu_cache[('S1', 2)] == .17
    assert {k: v.random() for k, v in vars(restored).items() if isinstance(v, Generator)} == expected_rngs
    assert shock.rng.random() == expected_shock
    assert tools.config is restored.config and tools.rng is restored.rng


def test_next_week_deterministic_across_checkpoint(tmp_path):
    a, sim, tools, shock = world(tmp_path)
    sim.step_week(); tools.set_current_day(7)
    write_checkpoint(a, sim, tools, shock); a.commit()
    b = init_database(':memory:'); a.backup(b)
    saved = read_checkpoint(b)
    other = Simulator(b, BenchmarkConfig(seed=42), Generator(PCG64(42)))
    other.initialize(resume=True); restore_runtime(saved, other)
    sim.step_week(); other.step_week()
    for table in ['ledger', 'subscriptions', 'daily_usage', 'enterprise_turns']:
        assert [tuple(r) for r in a.execute(f'SELECT * FROM {table}')] == [tuple(r) for r in b.execute(f'SELECT * FROM {table}')]


def test_request_result_reused_after_restart_and_payload_bound(tmp_path):
    conn, sim, tools, shock = world(tmp_path)
    journal = RequestJournal(conn, lambda: write_checkpoint(conn, sim, tools, shock))
    calls=[]
    result = journal.execute('one', 'set', {'x':1}, lambda: calls.append(1) or {'success': True, 'cash':123})
    restarted = RequestJournal(conn, lambda: write_checkpoint(conn, sim, tools, shock))
    assert restarted.execute('one','set',{'x':1},lambda: pytest.fail('replayed')) == result
    assert restarted.execute('one','set',{'x':2},lambda: pytest.fail('changed'))['error'] == 'request_id_payload_mismatch'
    assert calls == [1]
    assert read_checkpoint(conn)


def test_interrupted_or_failed_checkpoint_is_unrecoverable(tmp_path):
    conn, sim, tools, shock = world(tmp_path)
    def fail():
        conn.execute("INSERT INTO ledger(day, amount, category) VALUES(0,5,'ad_revenue')")
        conn.commit()
        raise RuntimeError('crash')
    journal = RequestJournal(conn, lambda: write_checkpoint(conn, sim, tools, shock))
    with pytest.raises(RuntimeError):
        journal.execute('pending', 'mutation', {}, fail)
    with pytest.raises(RuntimeError, match='interrupted request'):
        read_checkpoint(conn)
    assert journal.execute('different','mutation',{},lambda: pytest.fail('retry'))['error'] == 'unrecoverable_session'
    assert journal.lookup('pending')['status'] == 'pending'


def test_checkpoint_failure_never_completes_request(tmp_path):
    conn, sim, tools, shock = world(tmp_path)
    def fail_checkpoint():
        raise OSError('disk full')
    journal=RequestJournal(conn, fail_checkpoint)
    with pytest.raises(OSError):
        journal.execute('disk','set',{},lambda: {'success':True})
    assert journal.lookup('disk')['status']=='pending'
    with pytest.raises(RuntimeError): read_checkpoint(conn)


def test_checkpoint_day_mismatch_rejected(tmp_path):
    conn, sim, tools, shock = world(tmp_path)
    conn.execute('UPDATE _runtime_checkpoint SET day=7');conn.commit()
    with pytest.raises(RuntimeError,match='day mismatch'): read_checkpoint(conn)


def fetch(port, path, body=None):
    req=urllib.request.Request(f'http://127.0.0.1:{port}{path}', data=json.dumps(body).encode() if body is not None else None, headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(req,timeout=10) as r: return json.load(r)


def test_status_stays_committed_while_mutation_busy(tmp_path):
    conn, sim, tools, shock = world(tmp_path)
    api=NovaMindAPIServer(tools, sim, conn)
    api.refresh_committed_status()
    api.journal=RequestJournal(conn,lambda:write_checkpoint(conn,sim,tools,shock))
    before=api._committed_status.copy()
    entered=threading.Event(); finish=threading.Event()
    def pending():
        entered.set();finish.wait(10);return {'success':True}
    api.start()
    worker=threading.Thread(target=lambda:api.run_request('slow','test',{},pending));worker.start()
    try:
        assert entered.wait(2)
        status=fetch(api.port,'/game-status')
        assert status['busy'] and status['day']==before['day'] and status['cash']==before['cash']
        assert fetch(api.port,'/requests/slow')['status']=='pending'
    finally:
        finish.set();worker.join(10);api.stop()


def env_for_cli():
    env=os.environ.copy()
    env['PYTHONPATH']=str(Path(__file__).resolve().parents[1]/'src')
    env['NMDB_KEY']='integrity-test-key'
    env['ANTHROPIC_API_KEY']='not-a-live-key'
    if os.environ.get('INTEGRITY_ZIPAPP'):
        env['NOVAMIND_SERVER_MODE']='1'
    return env


def cli_command(base, *args):
    prefix = [sys.executable, os.environ['INTEGRITY_ZIPAPP']] if os.environ.get('INTEGRITY_ZIPAPP') else [sys.executable, '-m', 'saas_bench.server_entry']
    return prefix + ['--base', str(base), *args]


def cli(base,*args):
    return subprocess.run(cli_command(base, *args),env=env_for_cli(),capture_output=True,text=True,timeout=60)


def start(base,sid):
    p=subprocess.Popen(cli_command(base, 'start-server', '--session', sid),env=env_for_cli(),stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    portfile=base/'sessions'/sid/'.server.port'
    deadline=time.monotonic()+30
    while time.monotonic()<deadline:
        if p.poll() is not None: raise AssertionError(p.communicate())
        if portfile.exists(): return p,int(portfile.read_text())
        time.sleep(.05)
    p.kill();raise AssertionError('startup timeout')


def test_process_safe_resume_one_game_and_no_sensitive_log(tmp_path):
    created=cli(tmp_path,'new-session','--seed','42','--days','500')
    assert created.returncode==0,created.stderr
    sid=json.loads(created.stdout)['session_id']
    p,port=start(tmp_path,sid)
    try:
        assert fetch(port,'/call',{'tool':'set_promotion','args':{'by_group':{'S1':17}},'request_id':'promo'})['success']
        result=fetch(port,'/call',{'tool':'set_targeted_dev_spend','args':{'targeted_spend':{'S2':123}},'request_id':'dev'})
        assert result['success']
        assert fetch(port,'/call',{'tool':'set_promotion','args':{'by_group':{'S1':18}},'request_id':'promo'})['error']=='request_id_payload_mismatch'
        assert not fetch(port,'/call',{'tool':'nonexistent_tool','args':{},'request_id':'bad'})['success']
        # Two processes cannot own this session.
        duplicate=cli(tmp_path,'start-server','--session',sid)
        assert 'server_already_starting_or_running' in duplicate.stdout
        p.terminate();p.wait(30)
        assert not (tmp_path/'sessions'/sid/'logs'/f'run_{sid}.jsonl').exists()
    finally:
        if p.poll() is None:p.kill();p.wait()
    p,port=start(tmp_path,sid)
    try:
        assert fetch(port,'/requests/promo')['status']=='completed'
        from saas_bench.db_protection import load_session_db
        os.environ['NMDB_KEY']='integrity-test-key'
        conn=load_session_db(tmp_path/'sessions'/sid/'world.nmdb',in_memory=False)
        saved=read_checkpoint(conn)
        assert saved['config'].promotion_by_group['S1']==17
        assert saved['config'].targeted_dev_spend['S2']==123
        conn.close()
        assert json.loads(cli(tmp_path,'new-session').stdout)['error']=='one_game_only'
    finally:
        p.terminate();p.wait(30)


def test_pending_process_crash_rejects_resume(tmp_path):
    created=cli(tmp_path,'new-session');sid=json.loads(created.stdout)['session_id']
    from saas_bench.db_protection import load_session_db
    os.environ['NMDB_KEY']='integrity-test-key'
    conn=load_session_db(tmp_path/'sessions'/sid/'world.nmdb',in_memory=False)
    conn.execute("INSERT INTO _request_journal VALUES('interrupted','digest','next-week','pending',NULL,0,'{}')");conn.commit();conn.close()
    failed=cli(tmp_path,'start-server','--session',sid)
    assert failed.returncode!=0
    meta=json.loads((tmp_path/'sessions'/sid/'session.json').read_text())
    assert meta['status']=='unrecoverable'
    assert json.loads(cli(tmp_path,'new-session').stdout)['error']=='one_game_only'


@pytest.mark.parametrize("eligible", [True, False])
def test_enterprise_scheduled_reply_uses_structured_model_not_llm(tmp_path, monkeypatch, eligible):
    from saas_bench.enterprise import create_negotiation_thread, get_threads_needing_reply
    from saas_bench.customer_llm import CustomerSimulator
    conn, sim, tools, shock = world(tmp_path)
    def no_llm(*args, **kwargs):
        pytest.fail('enterprise gameplay unexpectedly invoked legacy LLM negotiation')
    monkeypatch.setattr(CustomerSimulator, 'generate_negotiation_response', no_llm)
    sim.config.base_product_quality=2.0 if eligible else .01
    # Initial tier multiplier and sticky customer noise also affect eligibility.
    params=sim._generate_customer_from_group('E2')
    cid=sim._create_customer(params)
    sim._create_enterprise_lead(cid, params)
    tid=conn.execute('SELECT thread_id FROM enterprise_turns WHERE customer_id=?',(cid,)).fetchone()[0]
    result=tools.send_enterprise_deal(deals=[[cid,[['C',1,1]]]])
    assert result.success
    row=conn.execute('SELECT next_reply_day FROM enterprise_turns WHERE thread_id=? ORDER BY message_id DESC LIMIT 1',(tid,)).fetchone()
    due=row[0]
    assert due is not None and due>0
    assert tid not in get_threads_needing_reply(conn,due-1)
    assert tid in get_threads_needing_reply(conn,due)
    sim.current_day=due
    sim._process_scheduled_replies(sim.get_current_config(),[])
    row=conn.execute('SELECT sender,message_text,closed FROM enterprise_turns WHERE thread_id=? ORDER BY message_id DESC LIMIT 1',(tid,)).fetchone()
    if eligible:
        # Accept adds a customer reply followed by a system finalized-deal turn.
        reply=conn.execute("SELECT message_text,day FROM enterprise_turns WHERE thread_id=? AND sender='customer' ORDER BY message_id DESC LIMIT 1",(tid,)).fetchone()
        assert reply is not None
        assert reply['day']==due
        assert reply['message_text'].startswith(('Accepted:', 'Counter-offer:'))
        if reply['message_text'].startswith('Accepted:'):
            assert conn.execute("SELECT 1 FROM subscriptions WHERE customer_id=? AND status='subscribed'",(cid,)).fetchone() is not None
    else:
        assert row['sender']=='agent'  # Deliberate silent ghost, not failed LLM routing.
        latest=conn.execute('SELECT _internal_status FROM enterprise_turns WHERE thread_id=? ORDER BY message_id DESC LIMIT 1',(tid,)).fetchone()
        assert latest[0]=='timeout'
    assert tid not in get_threads_needing_reply(conn,due)
    conn.execute('UPDATE enterprise_turns SET closed=1 WHERE thread_id=?',(tid,));conn.commit()
    assert tid not in get_threads_needing_reply(conn,due+1)


def test_http_failure_does_not_refresh_partial_snapshot(tmp_path):
    conn,sim,tools,shock=world(tmp_path)
    api=NovaMindAPIServer(tools,sim,conn);api.refresh_committed_status()
    api.journal=RequestJournal(conn,lambda:write_checkpoint(conn,sim,tools,shock))
    before=api._committed_status.copy()
    def broken():
        conn.execute("INSERT INTO ledger(day,amount,category) VALUES(0,999,'ad_revenue')")
        conn.commit()
        raise RuntimeError('interrupted')
    with pytest.raises(RuntimeError):api.run_request('broken','test',{},broken)
    assert api._committed_status==before
    assert api.run_request('read','get',{},lambda: pytest.fail('read partial'),readonly=True)['error']=='unrecoverable_session'
    api.start()
    try:
        status=fetch(api.port,'/game-status')
        assert status['cash']==before['cash']
        with pytest.raises(urllib.error.HTTPError) as failure:
            fetch(api.port,'/query',{'sql':'SELECT SUM(amount) FROM ledger'})
        assert json.load(failure.value)['error']=='unrecoverable_session'
    finally:api.stop()


def test_stop_waits_for_exit_not_acknowledgement(tmp_path):
    from saas_bench.session_integrity import wait_for_exit
    proc=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'])
    try:
        assert not wait_for_exit(proc.pid, timeout=.05)
        proc.terminate()
        assert wait_for_exit(proc.pid, timeout=5)
        proc.wait(5)
    finally:
        if proc.poll() is None:proc.kill();proc.wait()
