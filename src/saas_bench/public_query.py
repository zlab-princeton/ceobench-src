"""Public SQL boundary. SQLite resolves identifiers; no regex SQL security policy."""
import sqlite3
from contextlib import contextmanager
from typing import Dict, Set
from .database import TABLE_DOCS
try:
    import sqlcipher3
    QUERY_ERRORS = (sqlite3.Error, sqlcipher3.Error)
    QUERY_OPERATIONAL_ERRORS = (sqlite3.OperationalError, sqlcipher3.OperationalError)
except ImportError:  # Source-only environments can use ordinary SQLite.
    QUERY_ERRORS = (sqlite3.Error,)
    QUERY_OPERATIONAL_ERRORS = (sqlite3.OperationalError,)

_HIDDEN_TABLES: Set[str] = {
    'events',             # Internal shock/event tracking
    'api_costs',          # Meta-simulation API cost tracking
    'customer_state',     # Internal satisfaction/relationship state
    'group_reputation',   # Internal reputation tracking
    'group_awareness',    # Internal awareness tracking
    'reputation_history', # Internal reputation history
    'global_state',       # Internal simulation state
    'feature_tests',      # Internal feature test tracking
    'test_assignments',   # Internal test assignments
    'customer_personas',  # Internal persona templates
    'customer_persona_map', # Internal persona mapping
    'group_characteristics', # Internal group characteristics
    'enterprise_thread_counter',  # Internal thread ID counter
    'world_context',      # Internal world context
    'pending_group_research', # Internal async research tracking
    'group_parameters',       # V2.1: Internal preference drift tracking
    'competitor_events',      # V4: Hidden — agent should not see internal competitor boost mechanics
    '_hidden_leads_per_1k_snapshot',  # v3.4ai: monthly leads_per_1000_dollars snapshot (engine-only)
}

_HIDDEN_COLUMNS: Set[str] = {
    # Social media hidden columns
    'sentiment', 'reputation_impact', 'influence_score',
    # Latent customer satisfaction curve parameters (customers table)
    'steepness_left', 'steepness_right', 'c_max',
    # Latent customer preferences (customers table)
    'usage_demand', 'quality_sensitivity', 'price_sensitivity',
    'willingness_to_pay', 'usage_scale', 'patience',
    # Enterprise negotiation parameters (customers table)
    'reply_delay_mean', 'reply_delay_std', 'negotiation_rate', 'max_negotiation_turns',
    # Thread hidden columns - customer/VC reply timing is internal simulation state
    'next_reply_day',
    # Internal tracking columns
    'current_offer_price',
    # Usage rate hidden - agent should only see actual (quota-capped) usage from daily_usage table
    'daily_usage_rate', 'billing_period_usage',
    # Customer state hidden columns (customer_state table) - internal satisfaction tracking
    'satisfaction', 'relationship', 'open_issue_days',
    'current_steepness_left', 'current_steepness_right', 'current_c_max', 'current_slope',
    'last_drift_day', 'plan_was_acceptable', 'last_quality', 'last_satisfaction', 'shock_event_id',
    # Group-level hidden state (group_reputation, group_awareness tables)
    'reputation', 'awareness', 'last_updated_day', 'last_marketing_day',
    # Reputation history internals
    'change_reason',
    # R&D project internals
    'actual_completion_day',
    # Enterprise negotiation internal parameter (customers table)
    'initial_offer_factor',
    # Customer persona internal attribute (customers table)
    'persona_communication',
    # Internal thread status tracking (enterprise_turns)
    '_internal_status',
    # V4: Latent customer quality parameters (customers table)
    'q_max', 'q_min', 'contract_lockin_penalty',
    # V4: Internal ads sensitivity parameters (customers table)
    'ads_quality_sensitivity', 'ads_return_sensitivity',
    # V4: Subscription internals
    'effective_c_max',        # Willingness-to-pay at subscription time
    'churn_reason',           # Internal churn categorization
    # V4: Social media internals (agent sees content but not engagement mechanics)
    'likes', 'shares', 'virality_score',
    # V4: R&D internals
    'current_decay_reduction', 'decay_reduction_expiry_day',
    # V4: Ads revenue internals
    'sensitivity',            # Per-customer ads return sensitivity
    # V4: Segment discovery internals
    'remaining_undiscovered',
}

# Table-specific hidden columns (hidden only when querying these tables)
_TABLE_HIDDEN_COLUMNS: Dict[str, Set[str]] = {
    # seat_count hidden from customers/ads_revenue (internal float for drift)
    # but visible on subscriptions table (floored integer for agent)
    'customers': {'seat_count'},
    'ads_revenue': {'seat_count'},
    'social_media_posts': {'customer_id'},  # V4: Hide which customer posted
}


PUBLIC_TABLE_DOCS = {
    table: {"description": doc.get("description", ""), "columns": {
        col: description for col, description in doc.get("columns", {}).items()
        if col not in _HIDDEN_COLUMNS and col not in _TABLE_HIDDEN_COLUMNS.get(table, set())
    }}
    for table, doc in TABLE_DOCS.items() if table not in _HIDDEN_TABLES
}
PUBLIC_COLUMNS = {table: frozenset(doc["columns"]) for table, doc in PUBLIC_TABLE_DOCS.items()}


def _quote(name):
    return '"' + name.replace('"', '""') + '"'


@contextmanager
def public_query_boundary(conn):
    """Install public views and an authorizer while the caller holds its DB lock.

    Views make stars expand only to published columns. The authorizer checks the
    resolved underlying reads, including predicates, CTEs, aliases and subqueries.
    All writes, introspection and extension functions are denied. No filtering of
    result aliases is necessary: only authorized values can enter the result.
    """
    created = []
    # Only SQLite-built-in functions; application or extension functions may read
    # files or hidden state. Read this list before enabling the query authorizer.
    functions = {row[0].lower() for row in conn.execute("PRAGMA function_list")
                 if row[1] and row[0].lower() != "load_extension"}
    try:
        for table, doc in PUBLIC_TABLE_DOCS.items():
            # Missing tables are not public views on an older schema. Crucially,
            # never expose any newly added column merely because it exists.
            existing = {row[1] for row in conn.execute("PRAGMA main.table_info(" + _quote(table) + ")")}
            columns = [col for col in doc["columns"] if col in existing]
            if not columns:
                continue
            conn.execute("CREATE TEMP VIEW " + _quote(table) + " AS SELECT " +
                         ", ".join(map(_quote, columns)) + " FROM main." + _quote(table))
            created.append(table)

        def authorize(action, first, second, database, source):
            if action in (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_RECURSIVE):
                return sqlite3.SQLITE_OK
            if action == sqlite3.SQLITE_READ:
                table = (first or "").lower()
                column = (second or "").lower()
                if database in ("main", "temp") and table in PUBLIC_COLUMNS:
                    # SQLite uses an empty column for COUNT(*) table reads.
                    if not column or column in PUBLIC_COLUMNS[table]:
                        return sqlite3.SQLITE_OK
            if action == sqlite3.SQLITE_FUNCTION and (second or "").lower() in functions:
                return sqlite3.SQLITE_OK
            return sqlite3.SQLITE_DENY

        conn.set_authorizer(authorize)
        yield
    finally:
        conn.set_authorizer(None)
        for table in reversed(created):
            conn.execute("DROP VIEW temp." + _quote(table))
