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

# Version 2 requires immutable customer baselines and consistent effective
# renewal prices. Version-1 state may contain already-shifted latent baselines;
# there is no lossless migration, so never resume it under the new semantics.
VERSION = 2


class IncompatibleCheckpointError(RuntimeError):
    """A committed session belongs to an incompatible simulator generation."""


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
    conn.execute('CREATE TABLE IF NOT EXISTS _public_action_history (request_id TEXT PRIMARY KEY, entry TEXT NOT NULL)')
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
    if not row:
        raise RuntimeError('unrecoverable_session: missing complete checkpoint')
    if row[0] != VERSION:
        raise IncompatibleCheckpointError('unrecoverable_session: incompatible checkpoint engine version; cannot resume with this engine; do not replay or create a replacement game')
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
        self._published_rowid = 0
        self._history_needs_rebuild = True

    def lookup(self, request_id):
        row = self.conn.execute('SELECT status,result FROM _request_journal WHERE request_id=?', (request_id,)).fetchone()
        if not row:
            return {'request_id': request_id, 'status': 'unknown'}
        return {'request_id': request_id, 'status': row[0], 'result': json.loads(row[1]) if row[1] else None}

    def execute(self, request_id, operation, payload, callback):
        if not isinstance(request_id, str) or not request_id or len(request_id) > 128:
            return {'success': False, 'error': 'request_id_required'}
        try:
            payload_json = json.dumps(payload, sort_keys=True, allow_nan=False)
            digest = hashlib.sha256(json.dumps([operation, payload], sort_keys=True, allow_nan=False).encode()).hexdigest()
        except (TypeError, ValueError):
            return {'success': False, 'error': 'invalid_request_payload'}
        row = self.conn.execute('SELECT digest,status,result FROM _request_journal WHERE request_id=?', (request_id,)).fetchone()
        if row:
            if row[0] != digest:
                return {'success': False, 'error': 'request_id_payload_mismatch'}
            if row[1] == 'completed':
                return json.loads(row[2])
            return {'success': False, 'error': 'unrecoverable_session', 'request_id': request_id}
        if self.conn.execute("SELECT 1 FROM _request_journal WHERE status!='completed' LIMIT 1").fetchone():
            return {'success': False, 'error': 'unrecoverable_session'}
        before_day = self.conn.execute('SELECT day FROM _runtime_checkpoint WHERE id=1').fetchone()[0]
        started_at = time.time()
        self.conn.execute('INSERT INTO _request_journal VALUES (?,?,?,?,?,?,?)', (request_id, digest, operation, 'pending', None, started_at, payload_json))
        self.conn.commit()  # Durable BEFORE any action, including LLM calls.
        try:
            result = callback()
            if hasattr(result, 'to_json'):
                result = result.to_json()
            self.checkpoint()
            self.conn.execute("UPDATE _request_journal SET status='completed', result=? WHERE request_id=?", (json.dumps(result, default=str), request_id))
            after_day = self.conn.execute('SELECT day FROM _runtime_checkpoint WHERE id=1').fetchone()[0]
            entry = {
                'type': 'mutation', 'request_id': request_id, 'operation': operation,
                'input': json.loads(payload_json), 'day_before': before_day, 'day': after_day,
                'started_at': started_at, 'timestamp': time.time(),
                'outcome': 'completed',
                'success': bool(result.get('success', False)) if isinstance(result, dict) else False,
            }
            # Never publish tool result payloads: those can contain private
            # diagnostics in future tools. Only submitted inputs and outcome.
            self.conn.execute('INSERT INTO _public_action_history VALUES (?,?)',
                              (request_id, json.dumps(entry, default=str)))
            self.conn.commit()  # Runtime, result, and public audit commit together.
            return result
        except BaseException:
            self.conn.rollback()
            # Already committed internal simulator writes must never be replayed.
            raise

    def publish_history(self, path, *, rebuild=False):
        """Project committed public records; authority remains inside the DB.

        A crash may leave this convenience JSONL stale or its last line torn.
        Rebuild on safe startup; offline `history` reads the DB directly.
        """
        path = Path(path)
        rebuild = rebuild or self._history_needs_rebuild
        if rebuild:
            temporary = path.with_name(path.name + '.tmp')
            with temporary.open('w') as stream:
                last = 0
                for row in self.conn.execute('SELECT rowid,entry FROM _public_action_history ORDER BY rowid'):
                    stream.write(row[1] + '\n')
                    last = row[0]
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            self._published_rowid = last
            self._history_needs_rebuild = False
        else:
            rows = self.conn.execute('SELECT rowid,entry FROM _public_action_history WHERE rowid>? ORDER BY rowid', (self._published_rowid,)).fetchall()
            if rows:
                self._history_needs_rebuild = True
                with path.open('a') as stream:
                    for row in rows:
                        stream.write(row[1] + '\n')
                    stream.flush()
                    os.fsync(stream.fileno())
                self._published_rowid = rows[-1][0]
                self._history_needs_rebuild = False
        return self._published_rowid


def public_history(conn, tail=50):
    """Read only public audit inputs/outcomes, including truthful pending IDs."""
    exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='_public_action_history'").fetchone()
    if not exists:
        return {'history': [], 'count': 0, 'authoritative': False,
                'error': 'history_unavailable_for_legacy_session'}
    limit = int(tail) if tail and int(tail) > 0 else -1
    rows = conn.execute('SELECT entry FROM _public_action_history ORDER BY rowid DESC LIMIT ?', (limit,)).fetchall()
    entries = [json.loads(row[0]) for row in reversed(rows)]
    pending = [{'request_id': row[0], 'operation': row[1], 'outcome': row[2]}
               for row in conn.execute("SELECT request_id,operation,status FROM _request_journal WHERE status!='completed' ORDER BY created_at")]
    missing = conn.execute("SELECT COUNT(*) FROM _request_journal r LEFT JOIN _public_action_history h USING(request_id) WHERE r.status='completed' AND h.request_id IS NULL").fetchone()[0]
    return {'history': entries, 'count': len(entries), 'authoritative': True,
            'pending_requests': pending, 'unaudited_legacy_requests': missing}


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
