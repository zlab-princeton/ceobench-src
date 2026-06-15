"""Assert every tool and queryable table has proper documentation coverage.

Run: uv run python -m pytest tests/test_docs_coverage.py -v
"""
import sqlite3
import tempfile
from pathlib import Path

from saas_bench.tools import TOOL_DOCS
from saas_bench.database import TABLE_DOCS, init_database
from saas_bench.docs_generator import _EXCLUDED_TOOLS, _TOOL_TO_MODULE


# ── Full hidden tables list (must be kept in sync with tools.py _HIDDEN_TABLES) ──
_HIDDEN_TABLES = {
    'events', 'api_costs', 'customer_state', 'group_reputation',
    'group_awareness', 'reputation_history', 'global_state', 'feature_tests',
    'test_assignments', 'customer_personas', 'customer_persona_map',
    'group_characteristics', 'enterprise_thread_counter', 'world_context',
    'pending_group_research', 'group_parameters', 'competitor_events',
    'group_insight_snapshots',
    '_hidden_group_params_history', '_hidden_quality_snapshot',
    '_hidden_satisfaction_snapshot', '_hidden_lead_multiplier_snapshot',
    '_hidden_arena_allocation_log', '_hidden_arena_money_transfer_applications',
    '_hidden_arena_research_share_applications', '_hidden_arena_switching_log',
    'global_drift_state',
}


def test_all_tools_mapped_or_excluded():
    """Every tool in TOOL_DOCS must be in _TOOL_TO_MODULE or _EXCLUDED_TOOLS."""
    all_tools = set(TOOL_DOCS.keys())
    mapped = set(_TOOL_TO_MODULE.keys())
    excluded = _EXCLUDED_TOOLS
    unmapped = all_tools - mapped - excluded

    assert not unmapped, (
        f"Tools in TOOL_DOCS not in _TOOL_TO_MODULE or _EXCLUDED_TOOLS: {sorted(unmapped)}. "
        f"Add them to docs_generator.py _TOOL_TO_MODULE (for docs/api/) or _EXCLUDED_TOOLS."
    )


def test_no_stale_tool_mappings():
    """Every tool in _TOOL_TO_MODULE must exist in TOOL_DOCS."""
    for tool_name in _TOOL_TO_MODULE:
        assert tool_name in TOOL_DOCS, (
            f"Tool '{tool_name}' in _TOOL_TO_MODULE but not in TOOL_DOCS. Remove it or add the tool."
        )


def test_no_stale_excluded_tools():
    """Every tool in _EXCLUDED_TOOLS must exist in TOOL_DOCS."""
    for tool_name in _EXCLUDED_TOOLS:
        assert tool_name in TOOL_DOCS, (
            f"Tool '{tool_name}' in _EXCLUDED_TOOLS but not in TOOL_DOCS. Remove it or add the tool."
        )


def test_no_hidden_tables_in_table_docs():
    """Hidden tables must NOT have TABLE_DOCS entries (agent can't query them)."""
    hidden_with_docs = [t for t in TABLE_DOCS if t in _HIDDEN_TABLES]
    assert not hidden_with_docs, (
        f"Hidden tables have TABLE_DOCS entries (remove them): {hidden_with_docs}"
    )


def test_all_queryable_tables_have_docs():
    """Every non-hidden, non-internal table in the DB must have a TABLE_DOCS entry."""
    tmpdir = tempfile.mkdtemp()
    try:
        conn = init_database(Path(tmpdir) / 'test.db')
        tables = [
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            ).fetchall()
        ]
        conn.close()

        missing = []
        for t in tables:
            if t.startswith('_hidden') or t.startswith('_tmp') or t.startswith('sqlite_'):
                continue
            if t in _HIDDEN_TABLES:
                continue
            if t not in TABLE_DOCS:
                missing.append(t)

        assert not missing, (
            f"Queryable tables missing TABLE_DOCS entries: {missing}. "
            f"Add them to database.py TABLE_DOCS or add to _HIDDEN_TABLES in tools.py."
        )
    finally:
        import shutil
        shutil.rmtree(tmpdir)


def test_table_docs_match_real_tables():
    """Every TABLE_DOCS entry must correspond to a real table in the DB."""
    tmpdir = tempfile.mkdtemp()
    try:
        conn = init_database(Path(tmpdir) / 'test.db')
        tables = {
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        conn.close()

        stale = [t for t in TABLE_DOCS if t not in tables]
        assert not stale, (
            f"TABLE_DOCS entries with no matching DB table: {stale}. Remove them."
        )
    finally:
        import shutil
        shutil.rmtree(tmpdir)
