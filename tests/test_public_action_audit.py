"""Committed CLI/SDK audit and long-running public wrapper regressions."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

import pytest

from test_session_integrity import world
from saas_bench.session_integrity import RequestJournal, write_checkpoint, public_history


def test_history_commit_retry_and_projection_crash_window(tmp_path):
    conn,sim,tools,shock=world(tmp_path)
    journal=RequestJournal(conn,lambda:write_checkpoint(conn,sim,tools,shock))
    calls=[]
    result=journal.execute('stable-id','call:test',{'public_input':3},lambda:calls.append(1) or {'success':True,'data':{'PRIVATE_SENTINEL':'hidden'}})
    # Commit happened, publication did not. Authority already contains the row.
    audit=public_history(conn)
    assert len(audit['history'])==1
    entry=audit['history'][0]
    assert entry['request_id']=='stable-id' and entry['input']=={'public_input':3}
    assert entry['day_before']==entry['day']==0 and entry['outcome']=='completed'
    assert 'PRIVATE_SENTINEL' not in json.dumps(audit)
    restarted=RequestJournal(conn,lambda:write_checkpoint(conn,sim,tools,shock))
    assert restarted.execute('stable-id','call:test',{'public_input':3},lambda:pytest.fail('replayed'))==result
    assert len(public_history(conn)['history'])==1 and calls==[1]
    projection=tmp_path/'history.jsonl'
    restarted.publish_history(projection,rebuild=True)
    restarted.publish_history(projection)
    assert len(projection.read_text().splitlines())==1
    # Torn projection is repaired from committed authority on startup.
    with projection.open('a') as f:f.write('{torn')
    restarted.publish_history(projection,rebuild=True)
    assert [json.loads(line) for line in projection.read_text().splitlines()]==audit['history']


def test_failed_and_pending_requests_truthful_and_no_nan_poison(tmp_path):
    conn,sim,tools,shock=world(tmp_path)
    journal=RequestJournal(conn,lambda:write_checkpoint(conn,sim,tools,shock))
    assert journal.execute('nan','set',{'value':float('nan')},lambda:pytest.fail('invalid mutation'))['error']=='invalid_request_payload'
    assert not public_history(conn)['pending_requests']
    journal.execute('rejected','set',{},lambda:{'success':False,'error':'PRIVATE_ERROR_DIAGNOSTIC'})
    def crash():raise RuntimeError('in flight')
    with pytest.raises(RuntimeError):journal.execute('pending','next-week',{},crash)
    audit=public_history(conn)
    assert len(audit['history'])==1 and not audit['history'][0]['success']
    assert audit['pending_requests']==[{'request_id':'pending','operation':'next-week','outcome':'pending'}]
    assert 'PRIVATE_ERROR_DIAGNOSTIC' not in json.dumps(audit)


def test_sdk_timeout_exposes_submitted_id(monkeypatch,capsys):
    from saas_bench.novamind_api import _client
    monkeypatch.setenv('NOVAMIND_API_PORT','12345')
    def timeout(*args,**kwargs):raise TimeoutError('lost response')
    monkeypatch.setattr(_client.urllib.request,'urlopen',timeout)
    with pytest.raises(_client.NovaMindAPIError) as exc:
        _client.call('set_prices',{'A':10},request_id='known-request')
    assert exc.value.request_id=='known-request' and exc.value.outcome=='unknown'
    assert json.loads(capsys.readouterr().err)['request_id']=='known-request'


def public_command(base,*args):
    artifact=os.environ.get('AUDIT_ZIPAPP')
    if artifact:
        return [sys.executable,str(base/'novamind-operation'),*args]
    # Execute the real public CLI in a subprocess. The controlled no-LLM engine
    # fixture uses the same persisted server lifecycle and command boundary.
    server="from saas_bench import server_entry as s; s.CustomerSimulator=lambda **kwargs:None; s.main()"
    wrapper=("from pathlib import Path;import sys;from saas_bench import _public_cli as c;"
             f"c._base_dir=lambda:Path({str(base)!r});"
             f"c._server_cmd_prefix=lambda:[sys.executable,'-c',{server!r}];c.main()")
    return [sys.executable,'-c',wrapper,*args]


def public_env():
    env=os.environ.copy()
    env['PYTHONPATH']=str(Path(__file__).resolve().parents[1]/'src')
    env['NMDB_KEY']='public-audit-test-key'
    env['ANTHROPIC_API_KEY']='not-a-live-key'
    env.pop('NOVAMIND_SERVER_MODE',None);env.pop('NOVAMIND_API_PORT',None)
    return env


def run_public(base,*args,timeout=60):
    return subprocess.run(public_command(base,*args),env=public_env(),capture_output=True,text=True,timeout=timeout)


@pytest.fixture
def public_game(tmp_path):
    source=Path(__file__).resolve().parents[1]
    shutil.copytree(source/'src/saas_bench/novamind_api',tmp_path/'docs/novamind_api')
    if os.environ.get('AUDIT_ZIPAPP'):
        shutil.copy2(os.environ['AUDIT_ZIPAPP'],tmp_path/'novamind-operation')
    created=run_public(tmp_path,'new-session')
    assert created.returncode==0,created.stderr
    sid=json.loads(created.stdout)['session_id']
    try:yield tmp_path,sid
    finally:
        run_public(tmp_path,'stop')


def test_real_public_sdk_script_audit_offline_and_crash_projection(public_game):
    base,sid=public_game
    code="from novamind_api._client import call;print(call('set_prices',{'A':19},request_id='sdk-audit'))"
    inline=run_public(base,'python-c',code)
    assert inline.returncode==0,inline.stderr
    assert 'sdk-audit' in inline.stderr
    script=base/'strategy.py';script.write_text(code)
    repeated=run_public(base,'python',str(script))
    assert repeated.returncode==0,repeated.stderr
    history=run_public(base,'history');assert history.returncode==0,history.stderr
    audit=json.loads(history.stdout)
    assert audit['authoritative'] and len(audit['history'])==1
    assert audit['history'][0]['operation']=='call:set_prices'
    assert audit['history'][0]['input']=={'A':19}
    sdir=base/'sessions'/sid
    projection=sdir/'history.jsonl'
    assert len(projection.read_text().splitlines())==1
    assert (sdir/'client-history.jsonl').exists()
    # Model crash after durable commit but before JSONL publication: remove
    # projection, terminate the server, and read authority without auto-start.
    projection.write_text('')
    import signal
    pid=int((sdir/'.server.pid').read_text());os.kill(pid,signal.SIGKILL)
    time.sleep(.15)
    offline=run_public(base,'history');assert offline.returncode==0,offline.stderr
    assert json.loads(offline.stdout)['history']==audit['history']
    assert int((sdir/'.server.pid').read_text())==pid
    assert projection.read_text()==''  # Read-only history did not restart/rebuild.


@pytest.mark.skipif(not os.environ.get('RUN_SLOW_PUBLIC_CLI'),reason='explicit 301-second public subprocess gate')
def test_public_script_survives_previous_300_second_limit(public_game):
    base,sid=public_game
    code="import time;from novamind_api._client import call;print('BEFORE_WAIT',flush=True);call('set_prices',{'A':17},request_id='before-wait');time.sleep(301);call('set_prices',{'A':18},request_id='after-wait');print('AFTER_WAIT',flush=True)"
    started=time.monotonic()
    proc=subprocess.Popen(public_command(base,'python-c',code),env=public_env(),stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    try:
        # Streaming is independently verified before the long wait completes.
        first=proc.stdout.readline().strip()
        assert first=='BEFORE_WAIT' and time.monotonic()-started<30
        stdout,stderr=proc.communicate(timeout=340)
        assert proc.returncode==0,stderr
        assert 'AFTER_WAIT' in stdout and time.monotonic()-started>=301
        audit=json.loads(run_public(base,'history').stdout)
        assert [e['request_id'] for e in audit['history']]==['before-wait','after-wait']
    finally:
        if proc.poll() is None:proc.kill();proc.wait()


@pytest.mark.skipif(bool(os.environ.get('AUDIT_ZIPAPP')),reason='deterministic no-LLM source fixture; compiled live-week gate is separate')
def test_cli_and_sdk_week_share_committed_audit(public_game):
    base,sid=public_game
    cli_week=run_public(base,'next-week','CLI weekly rationale',*(['100000','0','1000000']*4),'--request-id','cli-week')
    assert cli_week.returncode==0,cli_week.stderr
    code="from novamind_api._client import next_week; p={k:{'point':100000,'lower':0,'upper':1000000} for k in ['cash_1wk','cash_4wk','cash_12wk','cash_26wk']};next_week(p,'SDK weekly rationale',request_id='sdk-week')"
    sdk_week=run_public(base,'python-c',code)
    assert sdk_week.returncode==0,sdk_week.stderr
    entries=json.loads(run_public(base,'history').stdout)['history']
    assert [(e['request_id'],e['operation'],e['day_before'],e['day']) for e in entries]==[
        ('cli-week','next-week',0,7),('sdk-week','next-week',7,14)]
    assert entries[0]['input']['rationale']=='CLI weekly rationale'
    assert entries[1]['input']['rationale']=='SDK weekly rationale'
    assert all(e['success'] for e in entries)


def test_offline_history_shows_pending_without_resuming(public_game):
    base,sid=public_game
    result=run_public(base,'python-c',"from novamind_api._client import call;call('set_prices',{'A':18},request_id='committed-before-crash')")
    assert result.returncode==0,result.stderr
    assert json.loads(run_public(base,'stop').stdout)['success']
    if os.environ.get('AUDIT_ZIPAPP'):
        import saas_bench
        saas_bench.__path__.append(str(base/'novamind-operation')+'/saas_bench')
    from saas_bench.db_protection import load_session_db
    os.environ['NMDB_KEY']='public-audit-test-key'
    conn=load_session_db(base/'sessions'/sid/'world.nmdb',in_memory=False)
    conn.execute("INSERT INTO _request_journal VALUES('interrupted-week','digest','next-week','pending',NULL,0,'{}')")
    conn.commit()
    # Leave a writer transaction open. WAL read-only history must not block
    # behind this unfinished mutation or reveal its uncommitted data.
    conn.execute("UPDATE _request_journal SET operation='UNCOMMITTED_PRIVATE' WHERE request_id='interrupted-week'")
    started=time.monotonic()
    audit=run_public(base,'history',timeout=10)
    conn.rollback();conn.close()
    assert audit.returncode==0,audit.stderr
    assert time.monotonic()-started<10
    parsed=json.loads(audit.stdout)
    assert [e['request_id'] for e in parsed['history']]==['committed-before-crash']
    assert parsed['pending_requests']==[{'request_id':'interrupted-week','operation':'next-week','outcome':'pending'}]
    assert not (base/'sessions'/sid/'.server.pid').exists()
    resume=run_public(base,'resume')
    assert resume.returncode!=0
    assert json.loads(run_public(base,'history').stdout)==parsed


@pytest.mark.parametrize('response', [[], None, {}, {'success': 'true'}])
def test_sdk_malformed_success_is_unknown(monkeypatch, response):
    from saas_bench.novamind_api import _client
    import io
    monkeypatch.setenv('NOVAMIND_API_PORT','12345')
    monkeypatch.setattr(_client.urllib.request,'urlopen',lambda *a,**k:io.BytesIO(json.dumps(response).encode()))
    with pytest.raises(_client.NovaMindAPIError) as failure:
        _client.call('set_prices',{'A':17},request_id='shape-test')
    assert failure.value.request_id=='shape-test' and failure.value.outcome=='unknown'


def test_public_request_status_escapes_custom_id(public_game):
    base,sid=public_game
    request_id='a/b space?x#y%z'
    code=f"from novamind_api._client import call;call('set_prices',{{'A':17}},request_id={request_id!r})"
    result=run_public(base,'python-c',code)
    assert result.returncode==0,result.stderr
    lookup=run_public(base,'request-status',request_id)
    assert lookup.returncode==0,lookup.stderr
    assert json.loads(lookup.stdout)['request_id']==request_id
    assert json.loads(lookup.stdout)['status']=='completed'


def test_cli_timeout_reports_unknown_request(monkeypatch,capsys):
    from saas_bench import _public_cli
    def timeout(*a,**k):raise TimeoutError('response lost')
    monkeypatch.setattr(_public_cli.urllib.request,'urlopen',timeout)
    with pytest.raises(SystemExit):
        _public_cli._api_call(12345,'POST','/next-week',{'request_id':'cli-timeout'})
    message=capsys.readouterr().err
    assert 'outcome unknown' in message and 'cli-timeout' in message and 'do not replay' in message


def test_old_engine_checkpoint_rejected_but_history_readable(public_game):
    base,sid=public_game
    result=run_public(base,'python-c',"from novamind_api._client import call;call('set_prices',{'A':18},request_id='old-engine-action')")
    assert result.returncode==0,result.stderr
    assert json.loads(run_public(base,'stop').stdout)['success']
    if os.environ.get('AUDIT_ZIPAPP'):
        import saas_bench
        saas_bench.__path__.append(str(base/'novamind-operation')+'/saas_bench')
    from saas_bench.db_protection import load_session_db
    os.environ['NMDB_KEY']='public-audit-test-key'
    conn=load_session_db(base/'sessions'/sid/'world.nmdb',in_memory=False)
    conn.execute('UPDATE _runtime_checkpoint SET version=1')
    conn.commit();conn.close()
    before=run_public(base,'history')
    assert before.returncode==0,before.stderr
    assert json.loads(before.stdout)['history'][0]['request_id']=='old-engine-action'
    resume=run_public(base,'resume')
    assert resume.returncode!=0
    assert 'incompatible checkpoint engine version' in (resume.stdout+resume.stderr).lower()
    assert json.loads(run_public(base,'history').stdout)==json.loads(before.stdout)
    replacement=json.loads(run_public(base,'new-session').stdout)
    assert replacement=={'success':False,'error':'one_game_only'}
    assert len(list((base/'sessions').glob('*/session.json')))==1
