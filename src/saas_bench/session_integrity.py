"""Durable, fail-closed session boundary for the public benchmark.

Only complete requests are resumable. An interrupted request is deliberately
NOT rolled back or replayed: its game must be reported as unrecoverable.
Runtime state and request results live inside the protected database, never in
agent-readable diagnostics. Pickles are internal versioned checkpoint data.
"""
import hashlib
import json
import os
import pickle
import time
from pathlib import Path

VERSION = 1


def atomic_json(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + '.tmp')
    with temporary.open('w') as f:
        json.dump(value, f, indent=2, default=str)
        f.flush()
        os.fsync(f.fileno())
    os.replace(temporary, path)
    fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def initialize_integrity(conn):
    conn.execute('CREATE TABLE IF NOT EXISTS _runtime_checkpoint (id INTEGER PRIMARY KEY CHECK(id=1), version INTEGER NOT NULL, day INTEGER NOT NULL, state BLOB NOT NULL)')
    conn.execute('CREATE TABLE IF NOT EXISTS _request_journal (request_id TEXT PRIMARY KEY, digest TEXT NOT NULL, operation TEXT NOT NULL, status TEXT NOT NULL, result TEXT, created_at REAL NOT NULL, payload TEXT NOT NULL)')
    conn.commit()


def capture_runtime(simulator, tools=None, shock_manager=None):
    excluded = {'conn', 'config', 'customer_simulator', 'event_logger', '_cached_all_subscribers', '_personas_cache'}
    state = {'simulator': {k: v for k, v in vars(simulator).items() if k not in excluded},
             'config': simulator.config,
             'tool_day': tools.current_day if tools else simulator.current_day,
             'shock_rng': shock_manager.rng if shock_manager else None}
    # One pickle preserves the shared identity of RNG objects.
    return pickle.dumps(state, protocol=5)


def write_checkpoint(conn, simulator, tools=None, shock_manager=None):
    blob = capture_runtime(simulator, tools, shock_manager)
    conn.execute('INSERT OR REPLACE INTO _runtime_checkpoint VALUES (1,?,?,?)',
                 (VERSION, simulator.current_day, blob))


def read_checkpoint(conn):
    row = conn.execute('SELECT version, day, state FROM _runtime_checkpoint WHERE id=1').fetchone()
    if not row or row[0] != VERSION:
        raise RuntimeError('unrecoverable_session: missing or incompatible complete checkpoint')
    pending = conn.execute("SELECT request_id FROM _request_journal WHERE status != 'completed' LIMIT 1").fetchone()
    if pending:
        raise RuntimeError('unrecoverable_session: interrupted request; do not replay or create a replacement game')
    state = pickle.loads(row[2])
    if state['simulator']['current_day'] != row[1] or state['tool_day'] != row[1]:
        raise RuntimeError('unrecoverable_session: checkpoint day mismatch')
    return state


def restore_runtime(state, simulator, tools=None, shock_manager=None):
    simulator.__dict__.update(state['simulator'])
    simulator.config = state['config']
    if simulator.customer_simulator:
        simulator.customer_simulator.config = simulator.config
        simulator.customer_simulator.current_day = simulator.current_day
    simulator._restore_leads_overrides_to_ad_channels()
    if tools:
        tools.config = simulator.config
        tools.rng = simulator.rng
        tools.set_current_day(state['tool_day'])
    if shock_manager and state['shock_rng'] is not None:
        shock_manager.rng = state['shock_rng']


class RequestJournal:
    def __init__(self, conn, checkpoint):
        self.conn = conn
        self.checkpoint = checkpoint
        initialize_integrity(conn)

    def lookup(self, request_id):
        row = self.conn.execute('SELECT status,result FROM _request_journal WHERE request_id=?', (request_id,)).fetchone()
        if not row:
            return {'request_id': request_id, 'status': 'unknown'}
        return {'request_id': request_id, 'status': row[0], 'result': json.loads(row[1]) if row[1] else None}

    def execute(self, request_id, operation, payload, callback):
        if not isinstance(request_id, str) or not request_id or len(request_id) > 128:
            return {'success': False, 'error': 'request_id_required'}
        digest = hashlib.sha256(json.dumps([operation, payload], sort_keys=True, allow_nan=False).encode()).hexdigest()
        row = self.conn.execute('SELECT digest,status,result FROM _request_journal WHERE request_id=?', (request_id,)).fetchone()
        if row:
            if row[0] != digest:
                return {'success': False, 'error': 'request_id_payload_mismatch'}
            if row[1] == 'completed':
                return json.loads(row[2])
            return {'success': False, 'error': 'unrecoverable_session', 'request_id': request_id}
        if self.conn.execute("SELECT 1 FROM _request_journal WHERE status!='completed' LIMIT 1").fetchone():
            return {'success': False, 'error': 'unrecoverable_session'}
        self.conn.execute('INSERT INTO _request_journal VALUES (?,?,?,?,?,?,?)', (request_id, digest, operation, 'pending', None, time.time(), json.dumps(payload, sort_keys=True, allow_nan=False)))
        self.conn.commit()  # Durable BEFORE any action, including LLM calls.
        try:
            result = callback()
            if hasattr(result, 'to_json'):
                result = result.to_json()
            self.checkpoint()
            self.conn.execute("UPDATE _request_journal SET status='completed', result=? WHERE request_id=?", (json.dumps(result, default=str), request_id))
            self.conn.commit()  # Full runtime state + result acknowledged together.
            return result
        except BaseException:
            self.conn.rollback()
            # Already committed internal simulator writes must never be replayed.
            raise


def wait_for_exit(pid, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
            # Reaped by its parent later, but a zombie has already exited.
            stat = Path(f'/proc/{pid}/stat')
            if stat.exists() and stat.read_text().split(') ', 1)[1].startswith('Z'):
                return True
        except ProcessLookupError:
            return True
        time.sleep(0.1)
    return False
