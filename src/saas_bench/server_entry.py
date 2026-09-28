#!/usr/bin/env python3
"""NovaMind Server — Entry point for the PyInstaller binary.

This is the single executable that manages sessions and runs the simulator.
It is invoked by the `novamind-operation` CLI wrapper.

Commands:
    new-session   Create a new simulation session
    start-server  Start the API server for an existing session
    stop-server   Stop a running API server
    status        Get session status
    list-sessions List all sessions
"""

import argparse
import fcntl
import io
import json
import os
import signal
import sqlite3
import sys
import time
from pathlib import Path
from typing import Optional

from numpy.random import Generator, PCG64

from saas_bench.config import BenchmarkConfig, SCENARIO_PACKS, ScenarioPack
from saas_bench.database import init_database
from saas_bench.simulation import Simulator
from saas_bench.customer_llm import CustomerSimulator
from saas_bench.tools import AgentTools
from saas_bench.shocks import ShockManager
from saas_bench.event_logger import EventLogger
from saas_bench.api_server import NovaMindAPIServer
from saas_bench.db_protection import (
    protect_db,
    create_encrypted,
    save_session_db,
    load_session_db,
    snapshot_to_plain,
    AsyncSaver,
)
from saas_bench.docs_generator import initialize_workspace
from saas_bench.session_integrity import (
    atomic_json, initialize_integrity, write_checkpoint, read_checkpoint,
    restore_runtime, RequestJournal,
)


_SIMULATOR_LLM_CONFIG_FIELDS = (
    "social_post_llm_provider",
    "social_post_llm_model",
    "enterprise_llm_provider",
    "enterprise_llm_model",
)


def _sessions_dir(base: Path) -> Path:
    return base / "sessions"


def _session_dir(base: Path, session_id: str) -> Path:
    return _sessions_dir(base) / session_id


def _session_meta_path(base: Path, session_id: str) -> Path:
    return _session_dir(base, session_id) / "session.json"


def _session_nmdb_path(base: Path, session_id: str) -> Path:
    return _session_dir(base, session_id) / "world.nmdb"


def _session_workspace(base: Path, session_id: str) -> Path:
    return _session_dir(base, session_id) / "workspace"


def _session_history_path(base: Path, session_id: str) -> Path:
    return _session_dir(base, session_id) / "history.jsonl"


def _pid_file(base: Path, session_id: str) -> Path:
    return _session_dir(base, session_id) / ".server.pid"


def _port_file(base: Path, session_id: str) -> Path:
    return _session_dir(base, session_id) / ".server.port"


def _generate_session_id() -> str:
    import hashlib
    raw = f"{time.time()}-{os.getpid()}"
    return hashlib.sha256(raw.encode()).hexdigest()[:12]


def _get_latest_session(base: Path) -> Optional[str]:
    """Get the most recently created session ID."""
    sessions_dir = _sessions_dir(base)
    if not sessions_dir.exists():
        return None
    sessions = []
    for d in sessions_dir.iterdir():
        meta = d / "session.json"
        if meta.exists():
            try:
                data = json.loads(meta.read_text())
                sessions.append((data.get("created_at", 0), d.name))
            except Exception:
                pass
    if not sessions:
        return None
    sessions.sort(reverse=True)
    return sessions[0][1]


def _resolve_session(base: Path, session_id: Optional[str]) -> str:
    """Resolve session ID (use latest if not specified)."""
    if session_id:
        meta = _session_meta_path(base, session_id)
        if not meta.exists():
            print(f"Error: Session '{session_id}' not found.", file=sys.stderr)
            sys.exit(1)
        return session_id
    latest = _get_latest_session(base)
    if not latest:
        print("Error: No sessions found. Create one with: ./novamind-operation new-session", file=sys.stderr)
        sys.exit(1)
    return latest


def _apply_simulator_llm_config(config: BenchmarkConfig) -> dict:
    """Validate and serialize simulator-side LLM provider/model config."""
    valid_providers = {"bedrock", "anthropic", "openai"}
    for attr in ("social_post_llm_provider", "enterprise_llm_provider"):
        provider = getattr(config, attr)
        if provider not in valid_providers:
            print(f"Error: invalid simulator LLM provider for {attr}: {provider!r}", file=sys.stderr)
            sys.exit(1)

    if (
        config.social_post_llm_provider == "anthropic"
        or config.enterprise_llm_provider == "anthropic"
    ) and not os.environ.get("ANTHROPIC_API_KEY"):
        print(
            "Error: simulator Anthropic provider requires ANTHROPIC_API_KEY. "
            "It does not use agent-only credentials such as --api-key.",
            file=sys.stderr,
        )
        sys.exit(1)

    if (
        config.social_post_llm_provider == "openai"
        or config.enterprise_llm_provider == "openai"
    ) and not os.environ.get("OPENAI_API_KEY"):
        print(
            "Error: simulator OpenAI provider requires OPENAI_API_KEY. "
            "It does not use agent-only credentials such as --api-key.",
            file=sys.stderr,
        )
        sys.exit(1)

    return {field: getattr(config, field) for field in _SIMULATOR_LLM_CONFIG_FIELDS}


def _restore_simulator_llm_config(config: BenchmarkConfig, meta: dict) -> None:
    simulator_llm = meta.get("simulator_llm") or {}
    for attr in _SIMULATOR_LLM_CONFIG_FIELDS:
        value = simulator_llm.get(attr)
        if value:
            setattr(config, attr, value)


def _create_simulator_openai_client(config: BenchmarkConfig):
    if (
        config.social_post_llm_provider != "openai"
        and config.enterprise_llm_provider != "openai"
    ):
        return None

    from openai import OpenAI

    return OpenAI()


# =========================================================================
# Commands
# =========================================================================

def cmd_new_session(args, base: Path):
    """Create a new simulation session."""
    # A failed or completed game never grants permission for another attempt.
    base.mkdir(parents=True, exist_ok=True)
    creation_lock = open(base / '.game-creation.lock', 'a+')
    fcntl.flock(creation_lock, fcntl.LOCK_EX)
    if (base / '.game-started').exists() or any(_sessions_dir(base).glob('*/session.json')):
        print(json.dumps({'success': False, 'error': 'one_game_only'}))
        return
    atomic_json(base / ".game-started", {"created_at": time.time()})
    session_id = _generate_session_id()
    sdir = _session_dir(base, session_id)
    sdir.mkdir(parents=True, exist_ok=True)

    total_days = args.days
    seed = args.seed

    # Initialize RNG and config
    rng = Generator(PCG64(seed))
    config = BenchmarkConfig(
        seed=seed,
        total_days=total_days,
        initial_cash=args.cash,
    )
    simulator_llm = _apply_simulator_llm_config(config)

    # Initialize directly in encrypted storage: no agent-readable plaintext
    # temporary SQLite file exists, even during initial session creation.
    import sqlcipher3
    nmdb_path = _session_nmdb_path(base, session_id)
    conn = create_encrypted(nmdb_path)
    conn.row_factory = sqlcipher3.Row
    init_database(nmdb_path, connection=conn)
    conn.execute('PRAGMA synchronous=FULL')

    # Initialize simulator with customer simulator
    customer_sim = CustomerSimulator(
        client=_create_simulator_openai_client(config),
        conn=conn,
        config=config,
    )
    simulator = Simulator(conn, config, rng, customer_simulator=customer_sim)
    simulator.initialize()

    # Save protected DB (in-memory → obfuscated .nmdb)
    nmdb_path = _session_nmdb_path(base, session_id)
    initialize_integrity(conn)
    write_checkpoint(conn, simulator)
    conn.commit()
    save_session_db(conn, nmdb_path)
    conn.close()

    # Initialize workspace with docs
    workspace = _session_workspace(base, session_id)
    initialize_workspace(workspace)

    # Save session metadata
    meta = {
        "session_id": session_id,
        "seed": seed,
        "total_days": total_days,
        "initial_cash": args.cash,
        "scenario": getattr(args, 'scenario', 'default'),
        "current_day": 0,
        "created_at": time.time(),
        "status": "created",
        "simulator_llm": simulator_llm,
        "cash": args.cash,
        "ledger_day": 0,
        "integrity_version": 1,
    }
    atomic_json(_session_meta_path(base, session_id), meta)

    # Initialize empty history
    _session_history_path(base, session_id).write_text("")

    result = {
        "session_id": session_id,
        "seed": seed,
        "total_days": total_days,
        "initial_cash": args.cash,
        "workspace": str(workspace),
        "status": "created",
    }
    print(json.dumps(result, indent=2))


class _NoPublicInternals(io.TextIOBase):
    """Never emit simulator diagnostics/LLM payloads into agent-readable logs."""
    def write(self, value):
        return len(value)
    def flush(self):
        pass


def cmd_start_server(args, base: Path):
    """Start exactly one server; resume only a fully committed request boundary."""
    session_id = _resolve_session(base, args.session)
    sdir = _session_dir(base, session_id)
    lock = open(sdir / '.server.lock', 'a+')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print(json.dumps({'success': False, 'error': 'server_already_starting_or_running'}))
        return
    meta_path = _session_meta_path(base, session_id)
    meta = json.loads(meta_path.read_text())
    original_stdout, original_stderr = sys.stdout, sys.stderr
    sys.stdout = sys.stderr = _NoPublicInternals()
    try:
        nmdb_path = _session_nmdb_path(base, session_id)
        # Keep every journal write encrypted and durable without full-DB exports.
        conn = load_session_db(nmdb_path, in_memory=False)
        conn.execute('PRAGMA synchronous=FULL')
        state = read_checkpoint(conn)  # Pending/missing state fails closed.
        config = state['config']
        _apply_simulator_llm_config(config)
        rng = Generator(PCG64(meta['seed']))
        customer_sim = CustomerSimulator(client=_create_simulator_openai_client(config), conn=conn, config=config)
        simulator = Simulator(conn, config, rng, customer_simulator=customer_sim)
        simulator.initialize(resume=True)
        # Fresh constructor initializes runtime references; saved state replaces
        # all process-local caches and RNGs, including customer quality noise.
        workspace = _session_workspace(base, session_id)
        tools = AgentTools(conn, state['tool_day'], workspace, rng=rng, config=config, seed=meta['seed'])
        scenario_pack = SCENARIO_PACKS.get(meta.get('scenario', 'default'), ScenarioPack(name='Default', description='Balanced scenario'))
        shock_manager = ShockManager(conn, rng, scenario_pack)
        restore_runtime(state, simulator, tools, shock_manager)
        api_server = NovaMindAPIServer(tools=tools, simulator=simulator, conn=conn, shock_manager=shock_manager)
        api_server.journal = RequestJournal(conn, lambda: write_checkpoint(conn, simulator, tools, shock_manager))
        api_server.refresh_committed_status()

        def publish_status():
            snapshot = api_server._committed_status
            meta.update(current_day=snapshot['day'], ledger_day=snapshot['day'],
                        cash=snapshot['cash'], snapshot_at=snapshot['snapshot_at'],
                        status='running', integrity_version=1)
            atomic_json(meta_path, meta)

        def publish_failure():
            meta.update(status='unrecoverable', error='incomplete_mutation_or_checkpoint_failure')
            atomic_json(meta_path, meta)
        api_server.failure_callback = publish_failure
        api_server.commit_callback = publish_status
        api_server.start()
        tools.api_port = api_server.port
        _pid_file(base, session_id).write_text(str(os.getpid()))
        _port_file(base, session_id).write_text(str(api_server.port))
        meta.update(port=api_server.port, pid=os.getpid())
        publish_status()
        original_stdout.write(json.dumps({'session_id': session_id, 'port': api_server.port, 'pid': os.getpid(), 'status': 'running'}) + '\n')
        original_stdout.flush()
        shutdown_requested = False

        def _shutdown(signum, frame):
            nonlocal shutdown_requested
            shutdown_requested = True
        signal.signal(signal.SIGTERM, _shutdown)
        signal.signal(signal.SIGINT, _shutdown)
        while not shutdown_requested:
            time.sleep(0.1)
        # Wait for the active request rather than saving partially stepped state.
        api_server.stop()
        with api_server._lock:
            pending = conn.execute("SELECT 1 FROM _request_journal WHERE status!='completed' LIMIT 1").fetchone()
            meta['status'] = 'unrecoverable' if pending else 'stopped'
            meta.pop('port', None)
            meta.pop('pid', None)
            atomic_json(meta_path, meta)
            conn.close()
    except Exception:
        meta['status'] = 'unrecoverable'
        meta['error'] = 'unsafe_resume_or_checkpoint_failure'
        atomic_json(meta_path, meta)
        original_stderr.write('Unrecoverable session. Stop this agent run; do not replay or create a replacement game.\n')
        raise SystemExit(1)
    finally:
        for path in (_pid_file(base, session_id), _port_file(base, session_id)):
            path.unlink(missing_ok=True)
        sys.stdout, sys.stderr = original_stdout, original_stderr
        lock.close()


def cmd_stop_server(args, base: Path):
    """Stop a running API server."""
    session_id = _resolve_session(base, args.session)

    pid_path = _pid_file(base, session_id)
    if not pid_path.exists():
        print(json.dumps({"success": False, "error": "No running server found"}))
        return

    pid = int(pid_path.read_text().strip())
    try:
        os.kill(pid, signal.SIGTERM)
        from saas_bench.session_integrity import wait_for_exit
        stopped = wait_for_exit(pid)
        print(json.dumps({'success': stopped, 'stopped_pid': pid if stopped else None,
                          'error': None if stopped else 'stop_pending_active_request'}))
    except ProcessLookupError:
        # Already dead, clean up
        pid_path.unlink(missing_ok=True)
        _port_file(base, session_id).unlink(missing_ok=True)
        print(json.dumps({"success": True, "message": "Server was not running, cleaned up stale files"}))


def cmd_status(args, base: Path):
    """Get session status."""
    session_id = _resolve_session(base, args.session)
    meta = json.loads(_session_meta_path(base, session_id).read_text())

    # Check if server is actually running
    pid_path = _pid_file(base, session_id)
    if pid_path.exists():
        pid = int(pid_path.read_text().strip())
        try:
            os.kill(pid, 0)
            meta["server_running"] = True
            port_path = _port_file(base, session_id)
            if port_path.exists():
                meta["port"] = int(port_path.read_text().strip())
        except ProcessLookupError:
            meta["server_running"] = False
            pid_path.unlink(missing_ok=True)
            _port_file(base, session_id).unlink(missing_ok=True)
    else:
        meta["server_running"] = False

    print(json.dumps(meta, indent=2))


def cmd_list_sessions(args, base: Path):
    """List all sessions."""
    sessions_dir = _sessions_dir(base)
    if not sessions_dir.exists():
        print(json.dumps({"sessions": []}))
        return

    sessions = []
    for d in sorted(sessions_dir.iterdir()):
        meta_path = d / "session.json"
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text())
                sessions.append({
                    "session_id": meta.get("session_id", d.name),
                    "current_day": meta.get("current_day", 0),
                    "total_days": meta.get("total_days", 0),
                    "status": meta.get("status", "unknown"),
                    "seed": meta.get("seed", 0),
                })
            except Exception:
                pass

    print(json.dumps({"sessions": sessions}, indent=2))


def cmd_history(args, base: Path):
    """Show session tool call history."""
    session_id = _resolve_session(base, args.session)
    history_path = _session_history_path(base, session_id)

    if not history_path.exists() or history_path.stat().st_size == 0:
        print(json.dumps({"history": [], "count": 0}))
        return

    entries = []
    for line in history_path.read_text().strip().split("\n"):
        if line.strip():
            try:
                entries.append(json.loads(line))
            except Exception:
                pass

    tail = args.tail or 50
    if len(entries) > tail:
        entries = entries[-tail:]

    print(json.dumps({"history": entries, "count": len(entries)}, indent=2, default=str))


def main():
    parser = argparse.ArgumentParser(
        prog="novamind-server",
        description="NovaMind Simulation Server",
    )
    parser.add_argument("--base", type=str, default=".",
                        help="Base directory for sessions (default: current directory)")

    subparsers = parser.add_subparsers(dest="command", required=True)

    # new-session
    p_new = subparsers.add_parser("new-session", help="Create a new simulation session")
    p_new.add_argument("--days", type=int, default=365, help="Total simulation days (default: 365)")
    p_new.add_argument("--seed", type=int, default=42, help="Random seed (default: 42)")
    p_new.add_argument("--cash", type=float, default=1_000_000.0, help="Initial cash (default: 1000000)")
    p_new.add_argument("--scenario", type=str, default="default", help="Scenario pack (default: default)")

    # start-server
    p_start = subparsers.add_parser("start-server", help="Start API server for a session")
    p_start.add_argument("--session", type=str, default=None, help="Session ID (default: latest)")

    # stop-server
    p_stop = subparsers.add_parser("stop-server", help="Stop a running API server")
    p_stop.add_argument("--session", type=str, default=None, help="Session ID (default: latest)")

    # status
    p_status = subparsers.add_parser("status", help="Get session status")
    p_status.add_argument("--session", type=str, default=None, help="Session ID (default: latest)")

    # list-sessions
    subparsers.add_parser("list-sessions", help="List all sessions")

    # history
    p_hist = subparsers.add_parser("history", help="Show tool call history")
    p_hist.add_argument("--session", type=str, default=None, help="Session ID (default: latest)")
    p_hist.add_argument("--tail", type=int, default=50, help="Number of recent entries (default: 50)")

    args = parser.parse_args()
    base = Path(args.base).resolve()

    cmd_map = {
        "new-session": cmd_new_session,
        "start-server": cmd_start_server,
        "stop-server": cmd_stop_server,
        "status": cmd_status,
        "list-sessions": cmd_list_sessions,
        "history": cmd_history,
    }

    cmd_map[args.command](args, base)


if __name__ == "__main__":
    main()
