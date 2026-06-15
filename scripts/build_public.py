#!/usr/bin/env python3
"""Build the public/ directory from source.

After this runs, public/ contains exactly two artifacts:

    novamind-operation    Single-file zipapp — bundles compiled engine + CLI
    docs/                 Reference material (docs/api, docs/tables, cli.md,
                          docs/novamind_api source)

The zipapp's ``__main__`` dispatches by environment variable:

    NOVAMIND_SERVER_MODE=1  → saas_bench.server_entry.main() (engine)
    (unset)                 → _public_cli.main() (user-facing CLI)

The agent's SDK source lives at docs/novamind_api/ — readable reference material
the agent can open with ``cat`` and import via PYTHONPATH at runtime. The
compiled ``_engine`` sits inside the zipapp at the archive root so that
``import saas_bench.X`` only works for code running *inside* the zipapp (the
engine itself); agent-spawned child processes never see it.

Usage:
    uv run python scripts/build_public.py
"""

import os
import py_compile
import shutil
import stat
import subprocess
import sys
import tempfile
import zipapp
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
PUBLIC_DIR = PROJECT_ROOT / "public"
SRC_DIR = PROJECT_ROOT / "src" / "saas_bench"
_FIXED_ZIP_MTIME = 315619200  # 1980-01-02 00:00:00 UTC, avoids local TZ underflow.

# All simulator-engine modules (compiled into the zipapp as .pyc)
_ENGINE_MODULES = [
    "__init__",
    "_embedded_key",
    "_sql_chunk",
    "api_server",
    "config",
    "customer_llm",
    "database",
    "db_protection",
    "docs_generator",
    "enterprise",
    "environment",
    "event_logger",
    "llm",
    "llm_replay",
    "novamind_cli",
    "personas",
    "server_entry",
    "shocks",
    "simulation",
    "tools",
]

# novamind_api subpackage used by the engine internally (docs_generator →
# novamind_cli → novamind_api._client). Bundled as bytecode for engine use;
# the *agent-readable* copy lives in docs/novamind_api/ as plain .py source.
_ENGINE_API_MODULES = [
    "__init__",
    "_client",
    "analytics",
    "arena",
    "enterprise",
    "infrastructure",
    "market",
    "marketing",
    "pricing",
    "research",
]

_ENGINE_SUBPACKAGE_MODULES = {
    "arena": [
        "__init__",
        "company",
        "coordinator",
        "interactions",
        "shared_market",
    ],
    "agents": [
        "__init__",
        "base",
    ],
    "agents/bash_agent": [
        "__init__",
        "arena_runner",
        "run_test",
    ],
}


def step(msg: str):
    print(f"\n{'='*60}\n  {msg}\n{'='*60}")


def build():
    # ── Step 1: Run assertion checks FIRST (fail fast) ──
    step("1. Running docs coverage checks")
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/test_docs_coverage.py", "-v"],
        cwd=PROJECT_ROOT,
        capture_output=True, text=True,
    )
    print(result.stdout)
    if result.returncode != 0:
        print(result.stderr)
        print("\n❌ Docs coverage checks FAILED. Fix issues before rebuilding.")
        sys.exit(1)
    print("✅ All checks passed")

    # ── Step 2: Render docs (the public-facing reference) ──
    step("2. Rendering docs from TOOL_DOCS and TABLE_DOCS")

    # Add source to path for imports
    sys.path.insert(0, str(PROJECT_ROOT / "src"))
    from saas_bench.docs_generator import render_api_docs, render_table_docs, render_cli_docs

    docs_dir = PUBLIC_DIR / "docs"
    api_dir = docs_dir / "api"
    tables_dir = docs_dir / "tables"
    examples_dir = docs_dir / "examples"
    if api_dir.exists():
        shutil.rmtree(api_dir)
    if tables_dir.exists():
        shutil.rmtree(tables_dir)
    if examples_dir.exists():
        shutil.rmtree(examples_dir)

    render_api_docs(api_dir)
    render_table_docs(tables_dir)
    render_cli_docs(docs_dir)

    # Drop any stale empty JSON
    import json as _json
    for f in api_dir.glob("*.json"):
        data = _json.loads(f.read_text())
        if isinstance(data, list) and len(data) == 0:
            f.unlink()
            print(f"  Removed empty: {f.name}")

    # Copy SDK source (readable reference) → docs/novamind_api/
    src_api = SRC_DIR / "novamind_api"
    dst_api = docs_dir / "novamind_api"
    if dst_api.exists():
        shutil.rmtree(dst_api)
    shutil.copytree(
        src_api, dst_api,
        ignore=shutil.ignore_patterns('__pycache__', '*.pyc'),
    )
    sdk_files = list(dst_api.glob("*.py"))
    print(f"✅ docs/novamind_api: {len(sdk_files)} files — {[f.name for f in sdk_files]}")

    # Copy README.md + requirements.txt to public root (optional, kept for
    # user-facing install instructions). If those sources are missing, skip
    # silently — they're not required for the zipapp to function.
    for fname in ("README.md", "requirements.txt"):
        src = PROJECT_ROOT / "public_sources" / fname
        if src.exists():
            shutil.copy2(src, PUBLIC_DIR / fname)
            print(f"  Copied {fname}")

    api_files = list(api_dir.glob("*.json"))
    table_files = list(tables_dir.glob("*.json"))
    print(f"✅ API docs: {len(api_files)} modules")
    print(f"✅ Table docs: {len(table_files)} tables")
    print(f"✅ CLI docs: docs/cli.md")

    # ── Step 3: Build the novamind-operation zipapp ──
    step("3. Building novamind-operation zipapp")
    _build_zipapp()
    print("✅ Wrote public/novamind-operation (zipapp)")

    # ── Step 4: Purge legacy artifacts left by the pre-zipapp layout ──
    step("4. Purging legacy layout")
    legacy_names = [
        # Pre-zipapp: engine bytecode lived here; now embedded in the zipapp.
        "_engine",
        # Pre-zipapp: separate server launcher; now merged into the zipapp.
        "novamind-server",
        # Pre-zipapp: top-level SDK copy; now lives at docs/novamind_api/.
        "novamind_api",
        # Pre-zipapp: top-level examples (also removed entirely 2026-05-10).
        "examples",
        # Pre-zipapp: PyInstaller install flow.
        "install.sh",
        "bin",
        "saas-bench-cli",
    ]
    for stale_name in legacy_names:
        stale_path = PUBLIC_DIR / stale_name
        if stale_path.is_dir():
            shutil.rmtree(stale_path)
            print(f"  Removed {stale_name}/")
        elif stale_path.is_file() or stale_path.is_symlink():
            stale_path.unlink()
            print(f"  Removed {stale_name}")

    # ── Step 5: Summary ──
    step("5. Build complete")
    print(f"public/ contents:")
    for p in sorted(PUBLIC_DIR.rglob("*")):
        if "__pycache__" in str(p):
            continue
        rel = p.relative_to(PUBLIC_DIR)
        if p.is_file():
            print(f"  {rel}")

    print(f"\n✅ public/ is ready — single-file CLI + docs.")
    print(f"   novamind-operation: zipapp with bundled engine")
    print(f"   docs/: api/, tables/, cli.md, novamind_api/ source")


def _build_zipapp():
    """Create public/novamind-operation as a Python zipapp.

    Staging layout (before zipping):
        staging/
            __main__.py                     # entry point (HASHSEED + dispatch)
            _public_cli.pyc                 # user-facing CLI body
            saas_bench/                     # engine (compiled to .pyc)
                *.pyc
                novamind_api/*.pyc
    """
    with tempfile.TemporaryDirectory() as tmp:
        staging = Path(tmp) / "stage"
        staging.mkdir()

        # __main__.py (entry point, stays as source — it's tiny and must run
        # *before* any saas_bench import happens because of PYTHONHASHSEED).
        (staging / "__main__.py").write_text(_ZIPAPP_MAIN_SOURCE)

        # Compile _public_cli.py → _public_cli.pyc at the archive root
        src_cli = SRC_DIR / "_public_cli.py"
        _compile_pyc(src_cli, staging / "_public_cli.pyc", "_public_cli.py")

        # Build saas_bench/ package inside the archive
        engine_dir = staging / "saas_bench"
        engine_dir.mkdir()
        # Minimal __init__.py (no heavy side-effect imports). We compile it
        # from a temp file so only .pyc lands in the archive.
        init_tmp = Path(tmp) / "saas_bench_init.py"
        init_tmp.write_text('"""NovaMind simulation engine (compiled)."""\n__version__ = "0.1.0"\n')
        compiled = 0
        for mod_name in _ENGINE_MODULES:
            if mod_name == "__init__":
                src_file = init_tmp
            else:
                src_file = SRC_DIR / f"{mod_name}.py"
            if not src_file.exists():
                print(f"  ⚠️  Missing: {src_file}")
                continue
            dst_file = engine_dir / f"{mod_name}.pyc"
            _compile_pyc(src_file, dst_file, f"saas_bench/{mod_name}.py")
            compiled += 1

        for package_name, module_names in _ENGINE_SUBPACKAGE_MODULES.items():
            package_src_dir = SRC_DIR / package_name
            package_dst_dir = engine_dir / package_name
            package_dst_dir.mkdir(parents=True, exist_ok=True)
            for mod_name in module_names:
                src_file = package_src_dir / f"{mod_name}.py"
                if not src_file.exists():
                    print(f"  ⚠️  Missing: {src_file}")
                    continue
                dst_file = package_dst_dir / f"{mod_name}.pyc"
                _compile_pyc(
                    src_file,
                    dst_file,
                    f"saas_bench/{package_name}/{mod_name}.py",
                )
                compiled += 1

        # saas_bench/novamind_api/*.pyc for engine internal use
        engine_api_dir = engine_dir / "novamind_api"
        engine_api_dir.mkdir()
        for mod_name in _ENGINE_API_MODULES:
            src_file = SRC_DIR / "novamind_api" / f"{mod_name}.py"
            if not src_file.exists():
                print(f"  ⚠️  Missing: {src_file}")
                continue
            dst_file = engine_api_dir / f"{mod_name}.pyc"
            _compile_pyc(
                src_file,
                dst_file,
                f"saas_bench/novamind_api/{mod_name}.py",
            )
            compiled += 1

        print(f"  Compiled {compiled} modules into zipapp")

        # Create the zipapp with shebang
        target = PUBLIC_DIR / "novamind-operation"
        if target.exists():
            target.unlink()
        PUBLIC_DIR.mkdir(parents=True, exist_ok=True)

        _normalize_tree_mtime(staging)
        zipapp.create_archive(
            source=str(staging),
            target=str(target),
            interpreter='/usr/bin/env python3',
            compressed=False,
        )

        # chmod +x
        target.chmod(target.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
        size_kb = target.stat().st_size / 1024
        print(f"  Zipapp size: {size_kb:.1f} KB")


def _compile_pyc(src_file: Path, dst_file: Path, archive_name: str):
    """Compile a module reproducibly for inclusion in the public zipapp."""
    py_compile.compile(
        str(src_file),
        str(dst_file),
        dfile=archive_name,
        doraise=True,
        invalidation_mode=py_compile.PycInvalidationMode.CHECKED_HASH,
    )


def _normalize_tree_mtime(root: Path):
    """Normalize timestamps so zip metadata is stable across rebuilds."""
    for path in sorted(root.rglob("*")):
        os.utime(path, (_FIXED_ZIP_MTIME, _FIXED_ZIP_MTIME))
    os.utime(root, (_FIXED_ZIP_MTIME, _FIXED_ZIP_MTIME))


def _ensure_deterministic_hash_seed():
    """Restart with a fixed hash seed before compiling bytecode."""
    if os.environ.get("PYTHONHASHSEED") == "0":
        return
    os.environ["PYTHONHASHSEED"] = "0"
    os.execv(sys.executable, [sys.executable, *sys.argv])


_ZIPAPP_MAIN_SOURCE = '''\
"""Zipapp entry for novamind-operation.

Runs BEFORE any saas_bench import. Two responsibilities:

1. Ensure PYTHONHASHSEED=0 for deterministic simulator runs. Python reads this
   at interpreter startup, so we may need to re-exec.
2. Dispatch to either the server (compiled engine) or the client-side CLI.
   ``NOVAMIND_SERVER_MODE=1`` is set by ``_public_cli._run_server_cmd`` when
   it spawns this zipapp to do engine work.
"""
import os
import sys

if os.environ.get("PYTHONHASHSEED") != "0":
    os.environ["PYTHONHASHSEED"] = "0"
    os.execv(sys.executable, [sys.executable, *sys.argv])

if os.environ.get("NOVAMIND_SERVER_MODE") == "1":
    from saas_bench.server_entry import main as _run
else:
    from _public_cli import main as _run

_run()
'''


if __name__ == "__main__":
    _ensure_deterministic_hash_seed()
    build()
