"""NovaMind client-side CLI — the user-facing subcommand body.

This is the code that runs when `novamind-operation` is invoked with a
user-facing subcommand (new-session, next-week, python, query, ...).

It is packaged inside the `novamind-operation` zipapp alongside the compiled
simulation engine. When a subcommand needs engine work, it spawns the same
zipapp again with ``NOVAMIND_SERVER_MODE=1`` in the environment, which flips
the zipapp's ``__main__`` into server mode (``saas_bench.server_entry.main()``).

Agents should never touch this file directly — it is loaded from the zipapp by
Python's zipimport. The agent only interacts through the ``./novamind-operation``
CLI and the ``novamind_api`` SDK source at ``docs/novamind_api/``.
"""

import json
import os
import shutil
import signal
import subprocess
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path


def _zipapp_path() -> Path:
    """Absolute path of the running zipapp executable.

    ``sys.argv[0]`` is whatever the user typed to invoke us (usually
    ``./novamind-operation``). Inside the zipapp, ``__file__`` refers to a
    path *inside* the archive (``.../novamind-operation/_public_cli.py``),
    which is not a real filesystem location — so we always resolve off
    ``sys.argv[0]``.
    """
    return Path(sys.argv[0]).resolve()


def _base_dir() -> Path:
    """Directory containing the ``novamind-operation`` executable.

    This is the published repo root: sessions live under ``<base>/sessions/``,
    docs under ``<base>/docs/``, and the SDK source under
    ``<base>/docs/novamind_api/``.
    """
    return _zipapp_path().parent


def _sessions_dir() -> Path:
    return _base_dir() / "sessions"


def _server_cmd_prefix() -> list:
    """Command that re-enters the zipapp in server mode.

    The spawned subprocess sees ``NOVAMIND_SERVER_MODE=1`` in its environment
    (see ``_run_server_cmd``), which causes the zipapp's ``__main__`` to
    dispatch to ``saas_bench.server_entry.main()``.
    """
    return [sys.executable, str(_zipapp_path())]


def _server_env() -> dict:
    env = os.environ.copy()
    env["NOVAMIND_SERVER_MODE"] = "1"
    return env


def _run_server_cmd(args: list, capture: bool = True):
    cmd = _server_cmd_prefix() + ["--base", str(_base_dir())] + args
    if capture:
        return subprocess.run(cmd, capture_output=True, text=True, env=_server_env())
    return subprocess.run(cmd, env=_server_env())


def _get_latest_session() -> str:
    sessions_dir = _sessions_dir()
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


def _resolve_session(session_id: str = None) -> str:
    if os.environ.get("NOVAMIND_API_PORT"):
        return "__env__"
    if session_id:
        return session_id
    latest = _get_latest_session()
    if not latest:
        print("Error: No sessions found. Create one with: ./novamind-operation new-session", file=sys.stderr)
        sys.exit(1)
    return latest


def _session_meta(session_id: str) -> dict:
    meta_path = _sessions_dir() / session_id / "session.json"
    if not meta_path.exists():
        print(f"Error: Session '{session_id}' not found.", file=sys.stderr)
        sys.exit(1)
    return json.loads(meta_path.read_text())


def _ensure_server_running(session_id: str) -> int:
    env_port = os.environ.get("NOVAMIND_API_PORT")
    if env_port:
        return int(env_port)

    sdir = _sessions_dir() / session_id
    pid_file = sdir / ".server.pid"
    port_file = sdir / ".server.port"

    if pid_file.exists() and port_file.exists():
        pid = int(pid_file.read_text().strip())
        try:
            os.kill(pid, 0)
            return int(port_file.read_text().strip())
        except ProcessLookupError:
            pid_file.unlink(missing_ok=True)
            port_file.unlink(missing_ok=True)

    # Redirect server stdout/stderr to a log file so the orphaned server
    # can keep writing after this CLI process exits. Previously we used
    # subprocess.PIPE; once the parent CLI exited, the pipe's read end was
    # closed and the next server stderr write raised BrokenPipeError, which
    # surfaced to the agent as "internal_error" on the second next-week call.
    logs_dir = sdir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_path = logs_dir / "server.log"
    log_fd = open(log_path, "ab", buffering=0)
    try:
        cmd = _server_cmd_prefix() + ["--base", str(_base_dir()),
                                       "start-server", "--session", session_id]
        subprocess.Popen(
            cmd,
            stdout=log_fd,
            stderr=log_fd,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            env=_server_env(),
        )
    finally:
        log_fd.close()

    for _ in range(100):  # 10 s
        time.sleep(0.1)
        if port_file.exists():
            try:
                return int(port_file.read_text().strip())
            except ValueError:
                continue

    print(
        f"Error: Failed to start server. See {log_path} for details.",
        file=sys.stderr,
    )
    sys.exit(1)


def _api_call(port: int, method: str, path: str, body: dict = None) -> dict:
    url = f"http://127.0.0.1:{port}{path}"
    data = json.dumps(body or {}).encode() if method == "POST" else None
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"} if data else {},
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=1800) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body_bytes = e.read()
        try:
            return json.loads(body_bytes)
        except Exception:
            print(f"Error: HTTP {e.code}: {body_bytes.decode('utf-8', errors='replace')[:500]}", file=sys.stderr)
            sys.exit(1)
    except urllib.error.URLError as e:
        print(f"Error: Failed to connect to server: {e}", file=sys.stderr)
        sys.exit(1)


def _arena_company_env() -> dict:
    company_id = os.environ.get("CEOBENCH_ARENA_COMPANY_ID")
    coordinator_port = os.environ.get("CEOBENCH_ARENA_COORDINATOR_PORT")
    if not company_id or not coordinator_port:
        return {}
    return {
        "company_id": company_id,
        "display_name": os.environ.get("CEOBENCH_ARENA_DISPLAY_NAME") or company_id,
        "coordinator_port": int(coordinator_port),
    }


def _register_arena_company_if_needed(port: int) -> None:
    arena_env = _arena_company_env()
    if not arena_env:
        return
    result = _api_call(
        arena_env["coordinator_port"],
        "POST",
        "/arena-register-company",
        {
            "company_id": arena_env["company_id"],
            "display_name": arena_env["display_name"],
            "api_port": int(port),
        },
    )
    if not result.get("success"):
        print(
            f"Warning: failed to register Arena company: {result.get('error', 'unknown_error')}",
            file=sys.stderr,
        )


def _log_history(session_id: str, entry: dict):
    if session_id == "__env__":
        return
    history_path = _sessions_dir() / session_id / "history.jsonl"
    try:
        with open(history_path, "a") as f:
            f.write(json.dumps(entry, default=str) + "\n")
    except FileNotFoundError:
        pass


# =========================================================================
# Commands
# =========================================================================

def cmd_new_session(args):
    result = _run_server_cmd([
        "new-session",
        "--days", str(args.days),
        "--seed", str(args.seed),
        "--cash", str(args.cash),
    ])
    if result.returncode != 0:
        print(f"Error: {result.stderr}", file=sys.stderr)
        sys.exit(1)
    print(result.stdout.strip())


def cmd_next_week(args):
    session_id = _resolve_session(args.session)
    port = _ensure_server_running(session_id)
    _register_arena_company_if_needed(port)
    rationale = (args.rationale or "").strip()
    if not rationale:
        print("Error: rationale is required and must be a non-empty string.", file=sys.stderr)
        sys.exit(1)
    body = {
        "rationale": rationale,
        "predictions": {
            "cash_1wk":  {"point": float(args.cash_1wk_point),  "lower": float(args.cash_1wk_lower),  "upper": float(args.cash_1wk_upper)},
            "cash_4wk":  {"point": float(args.cash_4wk_point),  "lower": float(args.cash_4wk_lower),  "upper": float(args.cash_4wk_upper)},
            "cash_12wk": {"point": float(args.cash_12wk_point), "lower": float(args.cash_12wk_lower), "upper": float(args.cash_12wk_upper)},
            "cash_26wk": {"point": float(args.cash_26wk_point), "lower": float(args.cash_26wk_lower), "upper": float(args.cash_26wk_upper)},
        }
    }

    arena_env = _arena_company_env()
    if arena_env:
        status = _api_call(port, "GET", "/game-status")
        result = _api_call(
            arena_env["coordinator_port"],
            "POST",
            "/next-week",
            {
                "company_id": arena_env["company_id"],
                "display_name": arena_env["display_name"],
                "api_port": port,
                "day": int(status.get("day", 0)),
                **body,
            },
        )
        if result.get("success"):
            dashboard = result.get("dashboard", "")
            print(dashboard)
            _log_history(session_id, {
                "type": "arena_next_week",
                "day": result.get("day"),
                "rationale": rationale,
                "predictions": body["predictions"],
                "timestamp": time.time(),
            })
            return

        print(f"Error: {result.get('error', 'arena_next_week_failed')}", file=sys.stderr)
        if result.get("message"):
            print(result["message"], file=sys.stderr)
        sys.exit(1)

    result = _api_call(port, "POST", "/next-week", body)
    if result.get("success"):
        dashboard = result.get("dashboard", "")
        print(dashboard)
        _log_history(session_id, {
            "type": "next_week",
            "day": result.get("day"),
            "rationale": rationale,
            "predictions": body["predictions"],
            "timestamp": time.time(),
        })
    else:
        error = result.get("error", "Unknown error")
        print(f"Error: {error}", file=sys.stderr)
        if error in ("step_week_timeout", "step_day_timeout"):
            print("The simulation week took too long; the run will be terminated.", file=sys.stderr)
        sys.exit(1)


def cmd_python(args):
    session_id = _resolve_session(args.session)
    port = _ensure_server_running(session_id)
    _register_arena_company_if_needed(port)
    script_path = Path(args.script)
    if not script_path.exists():
        print(f"Error: Script not found: {script_path}", file=sys.stderr)
        sys.exit(1)
    code = script_path.read_text()
    _execute_python(session_id, port, code, source=str(script_path))


def cmd_python_c(args):
    session_id = _resolve_session(args.session)
    port = _ensure_server_running(session_id)
    _register_arena_company_if_needed(port)
    _execute_python(session_id, port, args.code, source="inline")


def _execute_python(session_id: str, port: int, code: str, source: str = "unknown"):
    """Run user Python code with ``novamind_api`` importable on PYTHONPATH.

    The SDK source lives at ``<base>/docs/novamind_api/``. We prepend
    ``<base>/docs/`` to PYTHONPATH so ``import novamind_api`` resolves there
    (not to the sealed zipapp), and append the session workspace for
    scripts + helpers the agent wrote. The zipapp itself is NEVER on
    PYTHONPATH in user scripts — that keeps ``saas_bench`` out of reach.
    """
    env = os.environ.copy()
    env["NOVAMIND_API_PORT"] = str(port)

    docs_dir = _base_dir() / "docs"
    if session_id == "__env__":
        workspace = str(_base_dir())
    else:
        workspace = str(_sessions_dir() / session_id / "workspace")

    pythonpath = os.pathsep.join([str(docs_dir), workspace])
    if "PYTHONPATH" in env:
        pythonpath = pythonpath + os.pathsep + env["PYTHONPATH"]
    env["PYTHONPATH"] = pythonpath

    # Strip the server-mode flag if present so the user script's child
    # processes (if any) don't accidentally invoke the engine.
    env.pop("NOVAMIND_SERVER_MODE", None)

    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(_base_dir()),
        timeout=300,
    )

    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)

    _log_history(session_id, {
        "type": "python_exec",
        "source": source,
        "code_preview": code[:200],
        "exit_code": result.returncode,
        "timestamp": time.time(),
    })

    sys.exit(result.returncode)


def cmd_query(args):
    session_id = _resolve_session(args.session)
    port = _ensure_server_running(session_id)
    result = _api_call(port, "POST", "/query", {"sql": args.sql})
    _log_history(session_id, {
        "type": "query",
        "sql": args.sql[:200],
        "row_count": result.get("row_count", 0),
        "success": result.get("success", False),
        "timestamp": time.time(),
    })
    if result.get("success"):
        rows = result.get("rows", [])
        columns = result.get("columns", [])
        if rows:
            print(json.dumps({"columns": columns, "rows": rows, "row_count": len(rows)}, indent=2, default=str))
        else:
            print(json.dumps({"columns": columns, "rows": [], "row_count": 0}))
    else:
        print(f"Error: {result.get('error', 'Unknown error')}", file=sys.stderr)
        sys.exit(1)


def cmd_status(args):
    session_id = _resolve_session(args.session)
    env_port = os.environ.get("NOVAMIND_API_PORT")
    if env_port:
        _register_arena_company_if_needed(int(env_port))
    if session_id == "__env__":
        real_id = args.session or _get_latest_session()
        if real_id:
            meta = _session_meta(real_id)
        else:
            meta = {"session_id": "__env__"}
        meta["server_running"] = True
        meta["server_port"] = int(os.environ.get("NOVAMIND_API_PORT", 0)) or None
        print(json.dumps(meta, indent=2))
        return

    meta = _session_meta(session_id)
    sdir = _sessions_dir() / session_id
    pid_file = sdir / ".server.pid"
    if pid_file.exists():
        pid = int(pid_file.read_text().strip())
        try:
            os.kill(pid, 0)
            meta["server_running"] = True
        except ProcessLookupError:
            meta["server_running"] = False
    else:
        meta["server_running"] = False
    print(json.dumps(meta, indent=2))


def _copy_public_bundle_to_company(company_dir: Path) -> None:
    company_dir.mkdir(parents=True, exist_ok=True)
    for name in ("novamind-operation", "requirements.txt", "README.md", ".gitignore"):
        src = _base_dir() / name
        if src.exists():
            shutil.copy2(src, company_dir / name)
    docs_src = _base_dir() / "docs"
    docs_dst = company_dir / "docs"
    if docs_src.exists():
        if docs_dst.exists():
            shutil.rmtree(docs_dst)
        shutil.copytree(docs_src, docs_dst, ignore=shutil.ignore_patterns("__pycache__"))


def _write_arena_env(company_dir: Path, *, company_id: str, display_name: str, coordinator_port: int | None) -> None:
    lines = [
        f"export CEOBENCH_ARENA_COMPANY_ID={company_id!r}",
        f"export CEOBENCH_ARENA_DISPLAY_NAME={display_name!r}",
    ]
    if coordinator_port is not None:
        lines.append(f"export CEOBENCH_ARENA_COORDINATOR_PORT={int(coordinator_port)}")
    else:
        lines.append("# CEOBENCH_ARENA_COORDINATOR_PORT is written by arena-start")
    (company_dir / "arena.env").write_text("\n".join(lines) + "\n")


def _arena_dir(args) -> Path:
    return Path(args.arena_dir).resolve()


def cmd_arena_init(args):
    from saas_bench.arena.company import make_company_specs

    arena_dir = _arena_dir(args)
    arena_dir.mkdir(parents=True, exist_ok=True)
    companies_root = arena_dir / "companies"
    companies_root.mkdir(exist_ok=True)

    specs = make_company_specs(args.companies, starting_cash=args.cash)
    company_entries = []
    for index, spec in enumerate(specs):
        company_dir = companies_root / spec.company_id
        _copy_public_bundle_to_company(company_dir)
        _write_arena_env(
            company_dir,
            company_id=spec.company_id,
            display_name=spec.display_name,
            coordinator_port=None,
        )

        session_result = subprocess.run(
            [
                sys.executable,
                str(company_dir / "novamind-operation"),
                "new-session",
                "--days",
                str(args.days),
                "--seed",
                str(args.seed + index),
                "--cash",
                str(args.cash),
            ],
            capture_output=True,
            text=True,
            cwd=str(company_dir),
        )
        if session_result.returncode != 0:
            print(session_result.stderr, file=sys.stderr, end="")
            sys.exit(session_result.returncode)
        try:
            session_info = json.loads(session_result.stdout)
        except json.JSONDecodeError:
            session_info = {"raw": session_result.stdout.strip()}

        company_entries.append({
            "company_id": spec.company_id,
            "display_name": spec.display_name,
            "dir": str(company_dir),
            "session": session_info,
            "seed": args.seed + index,
        })

    config = {
        "run_id": arena_dir.name,
        "arena": True,
        "company_count": args.companies,
        "seed": args.seed,
        "total_days": args.days,
        "initial_cash": args.cash,
        "companies": company_entries,
    }
    (arena_dir / "arena.json").write_text(json.dumps(config, indent=2))
    print(json.dumps({"success": True, "arena_dir": str(arena_dir), "companies": company_entries}, indent=2))


def _arena_process_alive(pid_file: Path) -> bool:
    if not pid_file.exists():
        return False
    try:
        os.kill(int(pid_file.read_text().strip()), 0)
        return True
    except (ProcessLookupError, ValueError):
        return False


def cmd_arena_start(args):
    arena_dir = _arena_dir(args)
    config_file = arena_dir / "arena.json"
    if not config_file.exists():
        print(f"Error: missing {config_file}; run arena-init first.", file=sys.stderr)
        sys.exit(1)

    pid_file = arena_dir / ".coordinator.pid"
    port_file = arena_dir / ".coordinator.port"
    if _arena_process_alive(pid_file) and port_file.exists():
        port = int(port_file.read_text().strip())
        _write_arena_envs_from_config(arena_dir, port)
        print(json.dumps({"success": True, "arena_dir": str(arena_dir), "coordinator_port": port, "already_running": True}, indent=2))
        return

    log_path = arena_dir / "coordinator.log"
    log_fd = open(log_path, "ab", buffering=0)
    try:
        proc = subprocess.Popen(
            [
                sys.executable,
                str(_zipapp_path()),
                "arena-coordinator",
                "--arena-dir",
                str(arena_dir),
            ],
            stdout=log_fd,
            stderr=log_fd,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            cwd=str(arena_dir),
        )
    finally:
        log_fd.close()

    pid_file.write_text(str(proc.pid))
    for _ in range(100):
        time.sleep(0.1)
        if port_file.exists():
            port = int(port_file.read_text().strip())
            _write_arena_envs_from_config(arena_dir, port)
            print(json.dumps({"success": True, "arena_dir": str(arena_dir), "coordinator_port": port, "pid": proc.pid}, indent=2))
            return

    print(f"Error: Arena coordinator did not start. See {log_path}", file=sys.stderr)
    sys.exit(1)


def _write_arena_envs_from_config(arena_dir: Path, coordinator_port: int) -> None:
    config = json.loads((arena_dir / "arena.json").read_text())
    for company in config.get("companies", []):
        company_dir = Path(company["dir"])
        _write_arena_env(
            company_dir,
            company_id=str(company["company_id"]),
            display_name=str(company["display_name"]),
            coordinator_port=coordinator_port,
        )


def cmd_arena_coordinator(args):
    from saas_bench.agents.bash_agent.arena_runner import ArenaBashAgentRunner
    from saas_bench.arena import ArenaCoordinatorHTTPServer, ArenaNextWeekCoordinator

    arena_dir = _arena_dir(args)
    config = json.loads((arena_dir / "arena.json").read_text())
    runner = ArenaBashAgentRunner(
        company_count=int(config["company_count"]),
        seed=int(config.get("seed", 42)),
        total_days=int(config.get("total_days", 365)),
        initial_cash=float(config.get("initial_cash", 1_000_000.0)),
        workspace_base=arena_dir,
    )
    company_ids = [company["company_id"] for company in config["companies"]]
    coordinator = ArenaNextWeekCoordinator(
        company_ids,
        runner._advance_submitted_week,
        acquisition_slot_callback=runner._advance_acquisition_slot,
    )
    runner._coordinator = coordinator
    server = ArenaCoordinatorHTTPServer(coordinator)
    server.start()
    runner._active_coordinator_port = server.port
    (arena_dir / ".coordinator.port").write_text(str(server.port))
    _write_arena_envs_from_config(arena_dir, server.port)
    print(json.dumps({"success": True, "coordinator_port": server.port}), flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass
    finally:
        server.stop()


def cmd_arena_stop(args):
    arena_dir = _arena_dir(args)
    pid_file = arena_dir / ".coordinator.pid"
    port_file = arena_dir / ".coordinator.port"
    if not pid_file.exists():
        print(json.dumps({"success": True, "message": "No Arena coordinator running"}))
        return
    try:
        pid = int(pid_file.read_text().strip())
        os.kill(pid, signal.SIGTERM)
    except (ProcessLookupError, ValueError):
        pass
    pid_file.unlink(missing_ok=True)
    port_file.unlink(missing_ok=True)
    print(json.dumps({"success": True}))


def cmd_arena_retire(args):
    company_id = args.company_id or os.environ.get("CEOBENCH_ARENA_COMPANY_ID")
    if not company_id:
        print(
            "Error: --company-id is required unless CEOBENCH_ARENA_COMPANY_ID is set.",
            file=sys.stderr,
        )
        sys.exit(1)

    if args.arena_dir:
        port_file = Path(args.arena_dir).resolve() / ".coordinator.port"
        if not port_file.exists():
            print(
                f"Error: missing {port_file}; run arena-start first.",
                file=sys.stderr,
            )
            sys.exit(1)
        coordinator_port = int(port_file.read_text().strip())
    else:
        coordinator_port_raw = os.environ.get("CEOBENCH_ARENA_COORDINATOR_PORT")
        if not coordinator_port_raw:
            print(
                "Error: --arena-dir is required unless CEOBENCH_ARENA_COORDINATOR_PORT is set.",
                file=sys.stderr,
            )
            sys.exit(1)
        coordinator_port = int(coordinator_port_raw)

    result = _api_call(
        coordinator_port,
        "POST",
        "/arena-retire-company",
        {
            "company_id": company_id,
            "outcome": args.outcome,
        },
    )
    if result.get("success"):
        print(json.dumps(result, indent=2))
        return

    print(f"Error: {result.get('error', 'arena_retire_failed')}", file=sys.stderr)
    if result.get("message"):
        print(result["message"], file=sys.stderr)
    sys.exit(1)


def cmd_history(args):
    session_id = _resolve_session(args.session)
    history_path = _sessions_dir() / session_id / "history.jsonl"
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
    tail = args.tail
    if len(entries) > tail:
        entries = entries[-tail:]
    print(json.dumps({"history": entries, "count": len(entries)}, indent=2, default=str))


def cmd_list_sessions(args):
    sessions_dir = _sessions_dir()
    sessions = []
    if sessions_dir.exists():
        for d in sorted(sessions_dir.iterdir()):
            meta_path = d / "session.json"
            if meta_path.exists():
                try:
                    data = json.loads(meta_path.read_text())
                    sessions.append({
                        "session_id": data.get("session_id", d.name),
                        "created_at": data.get("created_at"),
                        "current_day": data.get("current_day"),
                        "total_days": data.get("total_days"),
                        "status": data.get("status"),
                    })
                except Exception:
                    pass
    print(json.dumps({"sessions": sessions, "count": len(sessions)}, indent=2, default=str))


def cmd_stop(args):
    session_id = _resolve_session(args.session)
    sdir = _sessions_dir() / session_id
    pid_file = sdir / ".server.pid"
    if not pid_file.exists():
        print(json.dumps({"success": True, "message": "No server running"}))
        return
    pid = int(pid_file.read_text().strip())
    try:
        os.kill(pid, signal.SIGTERM)
        for _ in range(30):
            time.sleep(0.1)
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
        print(json.dumps({"success": True, "stopped_pid": pid}))
    except ProcessLookupError:
        pid_file.unlink(missing_ok=True)
        (sdir / ".server.port").unlink(missing_ok=True)
        print(json.dumps({"success": True, "message": "Server was not running"}))


def main():
    import argparse

    parser = argparse.ArgumentParser(
        prog="novamind-operation",
        description="CEOBench Simulation CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  ./novamind-operation new-session --days 365 --seed 42
  ./novamind-operation next-week "Holding prices, raising ad spend on E1 to push enterprise pipeline" \
                                  1050000 1000000 1100000  1200000 1050000 1400000  1800000 1400000 2300000  3000000 2000000 4500000
                                  # rationale (required, non-empty) + 12 cash forecasts:
                                  # per horizon (+7d/+28d/+84d/+182d), submit point + 95% CI low/high
  ./novamind-operation python my_strategy.py
  ./novamind-operation python-c "import novamind_api as nm; nm.pricing.set_prices(A=25)"
  ./novamind-operation query "SELECT * FROM subscriptions LIMIT 10"
  ./novamind-operation status
  ./novamind-operation history --tail 20
  ./novamind-operation list-sessions
  ./novamind-operation stop
""",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    p = subparsers.add_parser("new-session", help="Create a new simulation session")
    p.add_argument("--days", type=int, default=365, help="Total simulation days (default: 365)")
    p.add_argument("--seed", type=int, default=42, help="Random seed (default: 42)")
    p.add_argument("--cash", type=float, default=1_000_000.0, help="Initial cash (default: 1000000)")

    p = subparsers.add_parser(
        "next-week",
        help="Advance simulation by one week (7 days). Requires a rationale string + 12 cash forecasts.",
        description=(
            "Advance the simulation by 7 days. You MUST submit:\n"
            "  1. A rationale string (your strategic reasoning for this week's actions, non-empty).\n"
            "  2. Cash forecasts at four horizons (+7d, +28d, +84d, +182d). For EACH horizon submit a "
            "point estimate plus 95% CI lower and upper bounds (lower <= point <= upper). 12 numbers total. "
            "Scored on point-percent-error, CI coverage, and sharpness at each horizon.\n"
            "\n"
            "Rationale replaces the old standalone log_rationale tool — it is now a required argument here."
        ),
    )
    p.add_argument("rationale", type=str, help="Your strategic reasoning for this week's actions (required, non-empty)")
    p.add_argument("cash_1wk_point",  type=float, help="Point estimate of cash +7 days")
    p.add_argument("cash_1wk_lower",  type=float, help="95%% CI lower bound, +7 days")
    p.add_argument("cash_1wk_upper",  type=float, help="95%% CI upper bound, +7 days")
    p.add_argument("cash_4wk_point",  type=float, help="Point estimate of cash +28 days")
    p.add_argument("cash_4wk_lower",  type=float, help="95%% CI lower bound, +28 days")
    p.add_argument("cash_4wk_upper",  type=float, help="95%% CI upper bound, +28 days")
    p.add_argument("cash_12wk_point", type=float, help="Point estimate of cash +84 days")
    p.add_argument("cash_12wk_lower", type=float, help="95%% CI lower bound, +84 days")
    p.add_argument("cash_12wk_upper", type=float, help="95%% CI upper bound, +84 days")
    p.add_argument("cash_26wk_point", type=float, help="Point estimate of cash +182 days (~6 months)")
    p.add_argument("cash_26wk_lower", type=float, help="95%% CI lower bound, +182 days")
    p.add_argument("cash_26wk_upper", type=float, help="95%% CI upper bound, +182 days")
    p.add_argument("--session", type=str, default=None, help="Session ID (default: latest)")

    p = subparsers.add_parser("python", help="Execute a Python script with novamind_api")
    p.add_argument("script", type=str, help="Path to Python script")
    p.add_argument("--session", type=str, default=None, help="Session ID (default: latest)")

    p = subparsers.add_parser("python-c", help="Execute inline Python code with novamind_api")
    p.add_argument("code", type=str, help="Python code to execute")
    p.add_argument("--session", type=str, default=None, help="Session ID (default: latest)")

    p = subparsers.add_parser("query", help="Execute a SQL query")
    p.add_argument("sql", type=str, help="SQL query string")
    p.add_argument("--session", type=str, default=None, help="Session ID (default: latest)")

    p = subparsers.add_parser("status", help="Get session status")
    p.add_argument("--session", type=str, default=None, help="Session ID (default: latest)")

    p = subparsers.add_parser("history", help="View action history")
    p.add_argument("--session", type=str, default=None, help="Session ID (default: latest)")
    p.add_argument("--tail", type=int, default=50, help="Number of recent entries (default: 50)")

    subparsers.add_parser("list-sessions", help="List all sessions")

    p = subparsers.add_parser("stop", help="Stop the simulation server")
    p.add_argument("--session", type=str, default=None, help="Session ID (default: latest)")

    p = subparsers.add_parser("arena-init", help="Create an Arena directory with ordinary CEOBench company workspaces")
    p.add_argument("--arena-dir", type=str, required=True, help="Output Arena directory")
    p.add_argument("--companies", type=int, default=2, help="Number of companies")
    p.add_argument("--days", type=int, default=500, help="Total simulation days")
    p.add_argument("--seed", type=int, default=42, help="Base random seed")
    p.add_argument("--cash", type=float, default=1_000_000.0, help="Initial cash per company")

    p = subparsers.add_parser("arena-start", help="Start the Arena coordinator for an arena-init directory")
    p.add_argument("--arena-dir", type=str, required=True, help="Arena directory")

    p = subparsers.add_parser("arena-coordinator", help=argparse.SUPPRESS)
    p.add_argument("--arena-dir", type=str, required=True, help=argparse.SUPPRESS)

    p = subparsers.add_parser("arena-stop", help="Stop the Arena coordinator")
    p.add_argument("--arena-dir", type=str, required=True, help="Arena directory")

    p = subparsers.add_parser("arena-retire", help="Retire an Arena company from future barriers")
    p.add_argument("--arena-dir", type=str, default=None, help="Arena directory")
    p.add_argument("--company-id", type=str, default=None, help="Arena company id")
    p.add_argument("--outcome", type=str, default="terminal", help="Terminal outcome label")

    args = parser.parse_args()

    cmd_map = {
        "new-session": cmd_new_session,
        "next-week": cmd_next_week,
        "python": cmd_python,
        "python-c": cmd_python_c,
        "query": cmd_query,
        "status": cmd_status,
        "history": cmd_history,
        "list-sessions": cmd_list_sessions,
        "stop": cmd_stop,
        "arena-init": cmd_arena_init,
        "arena-start": cmd_arena_start,
        "arena-coordinator": cmd_arena_coordinator,
        "arena-stop": cmd_arena_stop,
        "arena-retire": cmd_arena_retire,
    }

    cmd_map[args.command](args)
