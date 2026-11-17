"""Regression tests for CEOBench R&D project accounting and completion."""

import pytest

from saas_bench.config import RESEARCH_TIERS_BY_ID
from saas_bench.database import get_cash, get_global_state


def test_research_project_completion_applies_sampled_quality_boost(
    make_initialized_sim,
    make_agent_tools,
):
    conn, sim, config = make_initialized_sim()
    tools = make_agent_tools(conn, config)
    research_tier = RESEARCH_TIERS_BY_ID[1]

    cash_before = get_cash(conn)
    result = tools.start_research_project(1)

    assert result.success
    assert get_cash(conn) == pytest.approx(cash_before - research_tier.cost)

    project = conn.execute(
        """
        SELECT project_id, tier, status, started_day, expected_completion_day,
               expected_quality_boost, actual_completion_day, quality_boost_applied
        FROM research_projects
        WHERE tier = 1
        """
    ).fetchone()
    assert project["project_id"] == "t1_1"
    assert project["status"] == "in_progress"
    assert project["started_day"] == 0
    assert project["expected_completion_day"] == research_tier.mean_days
    assert project["expected_quality_boost"] >= 0.001
    assert project["actual_completion_day"] is None
    assert project["quality_boost_applied"] == pytest.approx(0.0)

    actual = conn.execute("SELECT completion_day, quality_boost FROM _hidden_research_outcomes").fetchone()
    sim.current_day = actual["completion_day"] - 1
    sim._process_research_projects({})
    assert get_global_state(conn, "q_shared_bonus") == pytest.approx(0.0)

    sim.current_day = actual["completion_day"]
    sim._process_research_projects({})

    completed = conn.execute(
        """
        SELECT status, actual_completion_day, quality_boost_applied
        FROM research_projects
        WHERE project_id = 't1_1'
        """
    ).fetchone()
    expected_boost = actual["quality_boost"]
    assert completed["status"] == "completed"
    assert completed["actual_completion_day"] == actual["completion_day"]
    assert completed["quality_boost_applied"] == pytest.approx(expected_boost)
    assert get_global_state(conn, "q_shared_bonus") == pytest.approx(expected_boost)
    assert get_global_state(
        conn, "unreleased_base_quality_improvement"
    ) == pytest.approx(expected_boost)
