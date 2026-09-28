"""Queries must authorize resolved inputs, not just filter output names."""
import sqlite3
import pytest
import sqlcipher3

SQL_ERRORS = (sqlite3.Error, sqlcipher3.Error)
from saas_bench.public_query import public_query_boundary, PUBLIC_TABLE_DOCS

@pytest.fixture(params=['sqlite', 'sqlcipher'])
def db(request, tmp_path):
    if request.param == 'sqlcipher':
        from saas_bench.db_protection import create_encrypted
        conn = create_encrypted(tmp_path / 'query-test.nmdb', key='public-query-regression-key')
    else:
        conn = sqlite3.connect(':memory:')
    conn.executescript('''
      CREATE TABLE customers(customer_id INTEGER, group_id TEXT, seat_count REAL, satisfaction REAL);
      INSERT INTO customers VALUES(1, 'S1', 41.5, -0.8);
      CREATE TABLE subscriptions(customer_id INTEGER, seat_count INTEGER, effective_price REAL);
      INSERT INTO subscriptions VALUES(1,41,100);
      CREATE TABLE agent_social_media_posts(agent_post_id INTEGER, content TEXT, views INTEGER,
          effect_by_group TEXT, reasoning_by_group TEXT, views_by_group TEXT);
      INSERT INTO agent_social_media_posts VALUES(1,'hello',5,'secret','secret','secret');
      CREATE TABLE enterprise_turns(message_id INTEGER, thread_id INTEGER, customer_id INTEGER, next_reply_day INTEGER);
      INSERT INTO enterprise_turns VALUES(1,10,1,7);
      CREATE TABLE _request_journal(secret TEXT);
      INSERT INTO _request_journal VALUES('private action payload');
      CREATE TABLE _runtime_checkpoint(secret BLOB);
      INSERT INTO _runtime_checkpoint VALUES('private simulator state');
      CREATE TABLE customer_state(customer_id INTEGER,satisfaction REAL);
      INSERT INTO customer_state VALUES(1,-0.8);
    ''')
    yield conn
    conn.close()

@pytest.mark.parametrize('sql', [
 'SELECT satisfaction AS cash FROM customers',
 'SELECT 1 FROM customers WHERE satisfaction < 0',
 'SELECT group_id FROM customers ORDER BY satisfaction',
 'SELECT count(*) FROM customers GROUP BY satisfaction',
 'SELECT count(*) FROM customers HAVING sum(satisfaction)<0',
 'SELECT sum(customer_id) FILTER(WHERE satisfaction<0) FROM customers',
 'SELECT row_number() OVER(ORDER BY satisfaction) FROM customers',
 'WITH a AS (SELECT satisfaction AS x FROM customers) SELECT x FROM a',
 'SELECT c.customer_id FROM customers c WHERE EXISTS(SELECT 1 FROM main.customers x WHERE x.satisfaction=c.customer_id)',
 'SELECT a.customer_id FROM customers a JOIN main.customers b ON b.satisfaction=a.customer_id',
 'SELECT effect_by_group AS content FROM agent_social_media_posts',
 'SELECT reasoning_by_group FROM main.agent_social_media_posts',
 'SELECT views_by_group FROM temp.agent_social_media_posts',
 'SELECT seat_count FROM customers',
 'SELECT * FROM main.customers',
 'SELECT * FROM customer_state',
 'SELECT * FROM _request_journal',
 'SELECT * FROM _runtime_checkpoint',
 'SELECT * FROM main.sqlite_master',
 'SELECT * FROM sqlite_temp_master',
 'PRAGMA table_info(customers)',
 "SELECT * FROM pragma_table_info('customers')",
 "SELECT load_extension('anything')",
 "SELECT readfile('anything')",
 'SELECT app_secret()',
 "SELECT sqlcipher_export('main')",
 'DELETE FROM main.customers',
 '-- comment\nUPDATE main.customers SET satisfaction=0',
 'WITH x AS (SELECT 1) DELETE FROM main.customers',
 "ATTACH ':memory:' AS extra",
 'CREATE TEMP TABLE stolen AS SELECT * FROM main.customers',
 'DROP TABLE main.customers',
 'BEGIN',
])
def test_denies_resolved_hidden_inputs_and_nonqueries(db, sql):
    db.create_function('app_secret', 0, lambda: 'secret')
    with public_query_boundary(db):
        with pytest.raises(SQL_ERRORS):
            db.execute(sql).fetchall()
    # Boundary cleanup must leave simulator reads/writes working.
    assert db.execute('SELECT satisfaction FROM customers').fetchone() == (-0.8,)
    assert db.execute('SELECT name FROM sqlite_temp_master').fetchall() == []

@pytest.mark.parametrize('sql, expected', [
 ('SELECT * FROM customers', [(1,'S1')]),
 ('SELECT c.* FROM customers AS c', [(1,'S1')]),
 ('SELECT COUNT(*) FROM customers', [(1,)]),
 ('SELECT COUNT(*) FROM main.customers', [(1,)]),
 ('SELECT customer_id AS satisfaction FROM customers', [(1,)]),
 ('WITH a AS (SELECT * FROM customers) SELECT a.customer_id FROM a', [(1,)]),
 ('SELECT c.customer_id,s.seat_count FROM customers c JOIN subscriptions s USING(customer_id)', [(1,41)]),
 ('SELECT * FROM agent_social_media_posts', [(1,'hello',5)]),
 ('SELECT COUNT(*) AS n, SUM(effective_price) AS revenue FROM subscriptions', [(1,100)]),
 ('SELECT thread_id,MAX(message_id) FROM enterprise_turns GROUP BY thread_id', [(10,1)]),
 ('WITH customer_state AS (SELECT customer_id FROM customers) SELECT * FROM customer_state', [(1,)]),
 ('SELECT customer_id AS n, customer_id AS n FROM customers', [(1,1)]),
 ('SELECT 40+2', [(42,)]),
 ("SELECT json_extract('{\"a\":3}', '$.a')", [(3,)]),
 ('WITH RECURSIVE n(x) AS (VALUES(1) UNION ALL SELECT x+1 FROM n WHERE x<3) SELECT SUM(x) FROM n', [(6,)]),
])
def test_public_analysis_queries(db, sql, expected):
    for _ in range(2):  # exercise prepared-statement cache and boundary reinstall
        with public_query_boundary(db):
            assert db.execute(sql).fetchall() == expected


def test_previously_prepared_hidden_query_cannot_bypass(db):
    query = 'SELECT satisfaction FROM main.customers'
    assert db.execute(query).fetchall() == [(-0.8,)]
    with public_query_boundary(db):
        with pytest.raises(SQL_ERRORS):
            db.execute(query).fetchall()


def test_new_undocumented_columns_fail_closed(db):
    db.execute('ALTER TABLE customers ADD COLUMN future_secret TEXT')
    with public_query_boundary(db):
        assert [c[0] for c in db.execute('SELECT * FROM customers').description] == ['customer_id','group_id']
        with pytest.raises(SQL_ERRORS):
            db.execute('SELECT future_secret FROM main.customers')


def test_public_docs_exclude_internal_schema():
    assert 'customer_state' not in PUBLIC_TABLE_DOCS
    assert 'satisfaction' not in PUBLIC_TABLE_DOCS['customers']['columns']
    assert 'effect_by_group' not in PUBLIC_TABLE_DOCS['agent_social_media_posts']['columns']


@pytest.mark.parametrize('driver', ['sqlite', 'sqlcipher'])
def test_http_boundary_ignores_oracle_env_and_preserves_simulator_access(monkeypatch, tmp_path, driver):
    import json
    import urllib.request
    import urllib.error
    from types import SimpleNamespace
    from saas_bench.api_server import NovaMindAPIServer
    monkeypatch.setenv('ORACLE_MODE', '1')
    # Also cover an already-imported legacy module flag, if present.
    monkeypatch.setattr('saas_bench.api_server._ORACLE_MODE', True, raising=False)
    if driver == 'sqlcipher':
        from saas_bench.db_protection import create_encrypted
        conn = create_encrypted(tmp_path / 'http-test.nmdb', key='http-query-test-key')
        conn.row_factory = sqlcipher3.Row
    else:
        conn = sqlite3.connect(':memory:', check_same_thread=False)
        conn.row_factory = sqlite3.Row
    conn.executescript("CREATE TABLE customers(customer_id INTEGER,satisfaction REAL); INSERT INTO customers VALUES(1,-.8);")
    server = NovaMindAPIServer(SimpleNamespace(current_day=0), conn=conn)
    server.start()
    def query(sql):
        request = urllib.request.Request(f'http://127.0.0.1:{server.port}/query',
            data=json.dumps({'sql': sql}).encode(), headers={'Content-Type': 'application/json'})
        try:
            response = urllib.request.urlopen(request, timeout=10)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            return json.load(response)
    try:
        assert query('SELECT * FROM customers')['rows'] == [{'customer_id': 1}]
        denied = query('SELECT satisfaction AS cash FROM main.customers')
        assert not denied['success']
        assert 'satisfaction' not in denied['error']
        assert not query('/* harmless */ DELETE FROM main.customers')['success']
        assert query('WITH a AS (SELECT * FROM customers) SELECT count(*) AS n FROM a')['rows'] == [{'n': 1}]
        assert conn.execute('SELECT satisfaction FROM customers').fetchone()[0] == -.8
    finally:
        server.stop()
        conn.close()


def test_social_tool_uses_same_public_allowlist(monkeypatch, db):
    from types import SimpleNamespace
    from saas_bench.tools import AgentTools
    monkeypatch.setattr('saas_bench.tools.get_recent_social_posts', lambda *a, **k: [
        {'post_id': 2, 'day': 1, 'content': 'public reply', 'reply_to_agent_post_id': None,
         'source_group_id': 'S1', 'sentiment': .9, 'future_secret': 123}])
    result = AgentTools.get_social_posts(SimpleNamespace(conn=db))
    assert result.data['posts'] == [{'post_id': 2, 'day': 1, 'content': 'public reply',
                                     'reply_to_agent_post_id': None}]


def test_social_reply_retains_public_enrichment(monkeypatch, db):
    from types import SimpleNamespace
    from saas_bench.tools import AgentTools
    db.row_factory = sqlcipher3.Row if isinstance(db, sqlcipher3.Connection) else sqlite3.Row
    monkeypatch.setattr('saas_bench.tools.get_recent_social_posts', lambda *a, **k: [
        {'post_id': 2, 'day': 1, 'content': 'public reply', 'reply_to_agent_post_id': 1,
         'source_group_id': 'S1', 'customer_id': 98, 'sentiment': .9}])
    result = AgentTools.get_social_posts(SimpleNamespace(conn=db))
    assert result.data['posts'] == [{'post_id': 2, 'day': 1, 'content': 'public reply',
        'reply_to_agent_post_id': 1, 'replying_to_your_post': 'hello'}]
