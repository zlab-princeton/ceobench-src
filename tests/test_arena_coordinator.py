"""Regression tests for CEOBench Arena runtime coordination."""

from __future__ import annotations

import json
from argparse import Namespace
import threading
import time
import urllib.request

from saas_bench import _public_cli
from saas_bench.agents.bash_agent.agent import BashAgent
from saas_bench.agents.bash_agent.arena_runner import (
    ArenaBashAgentRunner,
    parse_arena_model_specs,
)
from saas_bench.arena.coordinator import (
    ArenaAcquisitionSlotSubmission,
    ArenaCoordinatorHTTPServer,
    ArenaNextWeekCoordinator,
    ArenaNextWeekSubmission,
)
from saas_bench.arena.shared_market import SharedArrival
from saas_bench.novamind_api import _client, arena as arena_api


def _wait_until(predicate, *, timeout_s: float = 5.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    assert predicate()


def _weekly_submission(company_id: str, *, day: int = 0, api_port: int = 12345):
    return ArenaNextWeekSubmission(
        company_id=company_id,
        api_port=api_port,
        day=day,
        rationale="test",
        predictions={},
    )


def test_weekly_dashboard_header_marks_bash_agent_day_advanced():
    agent = BashAgent(
        tool_descriptions=[],
        client=object(),
        model="test-model",
    )

    assert agent.check_day_advanced("=== Week 3 Dashboard (Day 21) ===\nCash: $1")
    assert agent.day_advanced
    assert agent.new_dashboard.startswith("=== Week 3 Dashboard (Day 21) ===")


def test_arena_model_specs_parse_provider_model_pairs():
    specs = parse_arena_model_specs(
        "anthropic:claude-sonnet-4-6,gpt-5",
        count=3,
        default_provider="openai",
        default_model="gpt-5-mini",
    )

    assert [(spec.provider, spec.model) for spec in specs] == [
        ("anthropic", "claude-sonnet-4-6"),
        ("openai", "gpt-5"),
        ("openai", "gpt-5-mini"),
    ]


def test_public_cli_next_week_forwards_to_arena_coordinator(monkeypatch, capsys):
    calls = []

    def fake_api_call(port, method, path, body=None):
        calls.append((port, method, path, body))
        if path == "/arena-register-company":
            return {"success": True}
        if path == "/game-status":
            return {"day": 7}
        if path == "/next-week" and port == 7000:
            return {"success": True, "day": 14, "dashboard": "arena dashboard"}
        raise AssertionError((port, method, path, body))

    monkeypatch.setenv("NOVAMIND_API_PORT", "6000")
    monkeypatch.setenv("CEOBENCH_ARENA_COMPANY_ID", "company_0")
    monkeypatch.setenv("CEOBENCH_ARENA_DISPLAY_NAME", "NovaMind")
    monkeypatch.setenv("CEOBENCH_ARENA_COORDINATOR_PORT", "7000")
    monkeypatch.setattr(_public_cli, "_api_call", fake_api_call)

    _public_cli.cmd_next_week(Namespace(
        session=None,
        rationale="week plan",
        cash_1wk_point=1,
        cash_1wk_lower=0,
        cash_1wk_upper=2,
        cash_4wk_point=1,
        cash_4wk_lower=0,
        cash_4wk_upper=2,
        cash_12wk_point=1,
        cash_12wk_lower=0,
        cash_12wk_upper=2,
        cash_26wk_point=1,
        cash_26wk_lower=0,
        cash_26wk_upper=2,
    ))

    assert capsys.readouterr().out.strip() == "arena dashboard"
    assert calls[0] == (
        7000,
        "POST",
        "/arena-register-company",
        {
            "company_id": "company_0",
            "display_name": "NovaMind",
            "api_port": 6000,
        },
    )
    assert calls[1] == (6000, "GET", "/game-status", None)
    assert calls[2][0:3] == (7000, "POST", "/next-week")
    assert calls[2][3]["company_id"] == "company_0"
    assert calls[2][3]["api_port"] == 6000
    assert calls[2][3]["day"] == 7


def test_public_cli_arena_retire_forwards_to_coordinator(monkeypatch, capsys):
    calls = []

    def fake_api_call(port, method, path, body=None):
        calls.append((port, method, path, body))
        assert path == "/arena-retire-company"
        return {
            "success": True,
            "company_id": body["company_id"],
            "outcome": body["outcome"],
            "active_company_ids": ["company_1"],
        }

    monkeypatch.setenv("CEOBENCH_ARENA_COMPANY_ID", "company_0")
    monkeypatch.setenv("CEOBENCH_ARENA_COORDINATOR_PORT", "7000")
    monkeypatch.setattr(_public_cli, "_api_call", fake_api_call)

    _public_cli.cmd_arena_retire(Namespace(
        arena_dir=None,
        company_id=None,
        outcome="bankrupt",
    ))

    output = json.loads(capsys.readouterr().out)
    assert output["success"]
    assert output["company_id"] == "company_0"
    assert calls == [
        (
            7000,
            "POST",
            "/arena-retire-company",
            {
                "company_id": "company_0",
                "outcome": "bankrupt",
            },
        )
    ]


def test_arena_runner_resume_reattaches_company_run_dirs(monkeypatch, tmp_path):
    arena_dir = tmp_path / "arena_resume"
    arena_dir.mkdir()
    (arena_dir / "config.json").write_text(json.dumps({
        "run_id": "resume123",
        "arena": True,
        "company_count": 2,
        "companies": [
            {
                "company_id": "company_0",
                "display_name": "NovaMind",
                "provider": "anthropic",
                "model": "claude-sonnet-4-6",
            },
            {
                "company_id": "company_1",
                "display_name": "AsterAI",
                "provider": "openai",
                "model": "gpt-5",
            },
        ],
    }))
    company0_run = arena_dir / "companies" / "company_0" / "run_old0"
    company1_run = arena_dir / "companies" / "company_1" / "run_old1"
    company0_run.mkdir(parents=True)
    company1_run.mkdir(parents=True)

    created = []

    class FakeBashAgentRunner:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            created.append(kwargs)

    monkeypatch.setattr(
        "saas_bench.agents.bash_agent.arena_runner.BashAgentRunner",
        FakeBashAgentRunner,
    )

    runner = ArenaBashAgentRunner(
        company_count=99,
        continue_from=arena_dir,
        total_days=14,
        workspace_base=tmp_path,
    )
    runner._create_company_runners(coordinator_port=7777)

    assert runner.run_id == "resume123"
    assert runner.company_count == 2
    assert [spec.display_name for spec in runner.company_specs] == ["NovaMind", "AsterAI"]
    assert [item["continue_from"] for item in created] == [company0_run, company1_run]
    assert [item["arena_coordinator_port"] for item in created] == [7777, 7777]


def test_arena_runner_applies_shared_competitor_events_before_daily_advance(monkeypatch, tmp_path):
    runner = ArenaBashAgentRunner(
        company_count=2,
        total_days=14,
        workspace_base=tmp_path,
    )
    submissions = {
        "company_0": ArenaNextWeekSubmission(
            company_id="company_0",
            api_port=1000,
            day=0,
            rationale="week",
            predictions={},
        ),
        "company_1": ArenaNextWeekSubmission(
            company_id="company_1",
            api_port=1001,
            day=0,
            rationale="week",
            predictions={},
        ),
    }
    applied_events = []
    advanced_days = []

    def fake_sample(start_day):
        if start_day == 1:
            return {"start_day": 1, "boost_amount": 0.01}
        return None

    def fake_apply(submissions_arg, event):
        if event is not None:
            applied_events.append((event["start_day"], tuple(sorted(submissions_arg))))
        return {company_id: {"success": True} for company_id in submissions_arg}

    def fake_advance(submissions_arg, *, day_offset):
        advanced_days.append(day_offset)
        return {
            company_id: {
                "success": True,
                "day": submission.day + day_offset + 1,
                "dashboard": f"day {day_offset}",
            }
            for company_id, submission in submissions_arg.items()
        }

    def fake_fetch(submissions_arg, *, market_subscriber_counts=None):
        return {
            company_id: {
                "company_id": company_id,
                "display_name": company_id,
                "day": 0,
                "config": {},
                "subscriber_counts_by_group": {},
            }
            for company_id in submissions_arg
        }

    monkeypatch.setattr(runner, "_sample_shared_competitor_event", fake_sample)
    monkeypatch.setattr(runner, "_apply_shared_competitor_event", fake_apply)
    monkeypatch.setattr(runner, "_advance_one_day_with_shared_acquisition", fake_advance)
    monkeypatch.setattr(runner, "_fetch_market_states", fake_fetch)
    monkeypatch.setattr(
        runner,
        "_publish_public_snapshots",
        lambda submissions_arg, states: {
            company_id: {"success": True} for company_id in submissions_arg
        },
    )

    result = runner._advance_submitted_week(submissions)

    assert applied_events == [(1, ("company_0", "company_1"))]
    assert advanced_days == list(range(7))
    assert result["company_0"]["success"]
    assert result["company_1"]["success"]


def test_next_week_coordinator_blocks_until_all_companies_submit():
    callback_calls = []

    def advance(submissions):
        callback_calls.append(set(submissions))
        return {
            company_id: {
                "success": True,
                "day": submission.day + 7,
                "dashboard": f"=== Week 1 Dashboard (Day {submission.day + 7}) ===\n{company_id}",
            }
            for company_id, submission in submissions.items()
        }

    coordinator = ArenaNextWeekCoordinator(
        ["company_0", "company_1"],
        advance,
        wait_timeout_s=5,
    )
    server = ArenaCoordinatorHTTPServer(coordinator)
    server.start()
    try:
        results = {}

        def submit(company_id):
            payload = {
                "company_id": company_id,
                "api_port": 12345,
                "day": 0,
                "rationale": "test",
                "predictions": {
                    "cash_1wk": {"point": 1, "lower": 0, "upper": 2},
                    "cash_4wk": {"point": 1, "lower": 0, "upper": 2},
                    "cash_12wk": {"point": 1, "lower": 0, "upper": 2},
                    "cash_26wk": {"point": 1, "lower": 0, "upper": 2},
                },
            }
            data = json.dumps(payload).encode()
            req = urllib.request.Request(
                f"http://127.0.0.1:{server.port}/next-week",
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                results[company_id] = json.loads(resp.read())

        threads = [
            threading.Thread(target=submit, args=("company_0",)),
            threading.Thread(target=submit, args=("company_1",)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        assert callback_calls == [{"company_0", "company_1"}]
        assert results["company_0"]["success"]
        assert results["company_1"]["success"]
        assert "company_0" in results["company_0"]["dashboard"]
        assert "company_1" in results["company_1"]["dashboard"]
    finally:
        server.stop()


def test_next_week_coordinator_retire_releases_waiting_active_companies():
    callback_calls = []

    def advance(submissions):
        callback_calls.append(tuple(sorted(submissions)))
        return {
            company_id: {
                "success": True,
                "day": submission.day + 7,
                "dashboard": f"day {submission.day + 7}",
            }
            for company_id, submission in submissions.items()
        }

    coordinator = ArenaNextWeekCoordinator(
        ["company_0", "company_1", "company_2"],
        advance,
        wait_timeout_s=5,
    )
    results = {}

    def submit(company_id):
        results[company_id] = coordinator.submit(_weekly_submission(company_id))

    threads = [
        threading.Thread(target=submit, args=("company_0",)),
        threading.Thread(target=submit, args=("company_1",)),
    ]
    for thread in threads:
        thread.start()

    _wait_until(lambda: len(coordinator._submissions_by_day.get(0, {})) == 2)
    retire_result = coordinator.retire_company("company_2", outcome="bankrupt")

    for thread in threads:
        thread.join(timeout=5)

    assert not any(thread.is_alive() for thread in threads)
    assert callback_calls == [("company_0", "company_1")]
    assert retire_result["active_company_ids"] == ["company_0", "company_1"]
    assert results["company_0"]["success"]
    assert results["company_1"]["success"]


def test_next_week_coordinator_rejects_retired_company_submission():
    coordinator = ArenaNextWeekCoordinator(
        ["company_0", "company_1"],
        lambda submissions: {},
        wait_timeout_s=1,
    )

    assert coordinator.retire_company("company_1", outcome="bankrupt")["success"]
    result = coordinator.submit(_weekly_submission("company_1"))

    assert result["success"] is False
    assert result["error"] == "arena_company_retired"


def test_acquisition_slot_coordinator_retire_releases_waiting_active_companies():
    callback_calls = []

    def advance_slot(day, submissions):
        callback_calls.append((day, tuple(sorted(submissions))))
        return {
            company_id: {"success": True, "generation_result": {"total_leads": 0}}
            for company_id in submissions
        }

    coordinator = ArenaNextWeekCoordinator(
        ["company_0", "company_1", "company_2"],
        lambda submissions: {},
        acquisition_slot_callback=advance_slot,
        wait_timeout_s=5,
    )
    results = {}

    def submit_slot(company_id):
        results[company_id] = coordinator.submit_acquisition_slot(
            ArenaAcquisitionSlotSubmission(
                company_id=company_id,
                api_port=12345,
                day=8,
                display_name=company_id,
            )
        )

    threads = [
        threading.Thread(target=submit_slot, args=("company_0",)),
        threading.Thread(target=submit_slot, args=("company_1",)),
    ]
    for thread in threads:
        thread.start()

    _wait_until(lambda: len(coordinator._slot_submissions_by_day.get(8, {})) == 2)
    coordinator.retire_company("company_2", outcome="bankrupt")

    for thread in threads:
        thread.join(timeout=5)

    assert not any(thread.is_alive() for thread in threads)
    assert callback_calls == [(8, ("company_0", "company_1"))]
    assert results["company_0"]["success"]
    assert results["company_1"]["success"]


def test_arena_runner_retires_company_when_thread_finishes(tmp_path):
    runner = ArenaBashAgentRunner(
        company_count=2,
        total_days=7,
        workspace_base=tmp_path,
        model="test-model",
        provider="test-provider",
    )
    retired = []

    class FakeCoordinator:
        def retire_company(self, company_id, *, outcome=None):
            retired.append((company_id, outcome))
            return {"success": True}

    class FakeCompanyRunner:
        def __init__(self, outcome):
            self.outcome = outcome

        def run(self, *, verbose):
            return {"outcome": self.outcome}

    runner._coordinator = FakeCoordinator()
    runner._runners = {
        "company_0": FakeCompanyRunner("bankrupt"),
        "company_1": FakeCompanyRunner("timeout"),
    }

    results = runner._run_company_threads(verbose=False)

    assert results["company_0"]["outcome"] == "bankrupt"
    assert results["company_1"]["outcome"] == "timeout"
    assert set(retired) == {
        ("company_0", "bankrupt"),
        ("company_1", "timeout"),
    }


def test_arena_coordinator_routes_interaction_inbox():
    coordinator = ArenaNextWeekCoordinator(
        ["company_0", "company_1"],
        lambda submissions: {},
    )
    server = ArenaCoordinatorHTTPServer(coordinator)
    server.start()
    try:
        payload = {
            "action": "send_email",
            "company_id": "company_0",
            "sender_company_id": "company_0",
            "recipient_company_id": "company_1",
            "day": 7,
            "subject": "Co-marketing",
            "body": "Want to compare segment notes?",
        }
        req = urllib.request.Request(
            f"http://127.0.0.1:{server.port}/arena-interaction",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())

        inbox_req = urllib.request.Request(
            f"http://127.0.0.1:{server.port}/arena-inbox",
            data=json.dumps({"company_id": "company_1"}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(inbox_req, timeout=10) as resp:
            inbox = json.loads(resp.read())

        assert result["success"]
        assert result["event"]["interaction_id"].startswith("email_")
        assert inbox["success"]
        assert inbox["inbox"]["emails"][0]["subject"] == "Co-marketing"
        assert inbox["event_counts"]["emails"] == 1
    finally:
        server.stop()


def test_arena_sdk_sends_email_to_coordinator(monkeypatch):
    coordinator = ArenaNextWeekCoordinator(
        ["company_0", "company_1"],
        lambda submissions: {},
    )
    server = ArenaCoordinatorHTTPServer(coordinator)
    server.start()
    try:
        monkeypatch.setenv("CEOBENCH_ARENA_COMPANY_ID", "company_0")
        monkeypatch.setenv("CEOBENCH_ARENA_DISPLAY_NAME", "NovaMind")
        monkeypatch.setenv("CEOBENCH_ARENA_COORDINATOR_PORT", str(server.port))
        monkeypatch.setenv("NOVAMIND_API_PORT", "12345")
        monkeypatch.setattr(_client, "get_vars", lambda: {"current_day": 5})

        result = arena_api.send_email(
            "company_1",
            "Segment notes",
            "Want to compare public market snapshots?",
        )

        assert result["success"]
        inbox = coordinator.inbox_for("company_1")
        assert inbox["inbox"]["emails"][0]["day"] == 5
        assert inbox["inbox"]["emails"][0]["subject"] == "Segment notes"
    finally:
        server.stop()


def test_arena_coordinator_transfer_calls_registered_company_servers(monkeypatch):
    coordinator = ArenaNextWeekCoordinator(
        ["company_0", "company_1"],
        lambda submissions: {},
    )
    coordinator.register_company(company_id="company_0", api_port=1000)
    coordinator.register_company(company_id="company_1", api_port=1001)

    calls = []

    def fake_post(port, path, body, *, timeout=7200.0):
        calls.append((port, path, body))
        return {"success": True}

    monkeypatch.setattr(
        "saas_bench.arena.coordinator.http_post_json",
        fake_post,
    )

    result = coordinator.record_interaction(
        "transfer_money",
        {
            "sender_company_id": "company_0",
            "recipient_company_id": "company_1",
            "day": 14,
            "amount": 2500,
            "memo": "shared report reimbursement",
        },
    )

    assert result["success"]
    assert len(coordinator.interaction_log.money_transfers) == 1
    assert calls == [
        (
            1000,
            "/arena-apply-money-transfer",
            {
                "transfer_id": result["event"]["interaction_id"],
                "amount": 2500.0,
                "day": 14,
                "memo": "shared report reimbursement",
                "direction": "out",
                "counterparty_company_id": "company_1",
            },
        ),
        (
            1001,
            "/arena-apply-money-transfer",
            {
                "transfer_id": result["event"]["interaction_id"],
                "amount": 2500.0,
                "day": 14,
                "memo": "shared report reimbursement",
                "direction": "in",
                "counterparty_company_id": "company_0",
            },
        ),
    ]


def test_arena_coordinator_research_share_applies_bounded_recipient_credit(monkeypatch):
    coordinator = ArenaNextWeekCoordinator(
        ["company_0", "company_1", "company_2"],
        lambda submissions: {},
    )
    coordinator.register_company(company_id="company_0", api_port=1000)
    coordinator.register_company(company_id="company_1", api_port=1001)
    coordinator.register_company(company_id="company_2", api_port=1002)

    calls = []

    def fake_post(port, path, body, *, timeout=7200.0):
        calls.append((port, path, body))
        if path == "/arena-research-share-snapshot":
            return {"success": True, "group_id": body["group_id"], "info_level": 4}
        if path == "/arena-apply-research-share":
            return {
                "success": True,
                "share_id": body["share_id"],
                "applied": True,
                "old_info_level": 2,
                "new_info_level": 3,
            }
        raise AssertionError(path)

    monkeypatch.setattr(
        "saas_bench.arena.coordinator.http_post_json",
        fake_post,
    )

    result = coordinator.record_interaction(
        "share_research",
        {
            "sender_company_id": "company_0",
            "recipient_company_id": "company_1",
            "day": 21,
            "scope": "company",
            "artifact_id": "S1-report",
            "group_id": "S1",
            "memo": "Sharing a customer-segment readout.",
        },
    )

    assert result["success"]
    assert result["effects"]["group_id"] == "S1"
    assert result["effects"]["source_info_level"] == 4
    assert result["effects"]["applied"][0]["company_id"] == "company_1"
    assert calls == [
        (1000, "/arena-research-share-snapshot", {"group_id": "S1"}),
        (
            1001,
            "/arena-apply-research-share",
            {
                "share_id": result["event"]["interaction_id"],
                "sender_company_id": "company_0",
                "group_id": "S1",
                "source_info_level": 4,
                "day": 21,
                "memo": "Sharing a customer-segment readout.",
            },
        ),
    ]


def test_arena_introduction_adds_one_matching_consideration_visibility():
    coordinator = ArenaNextWeekCoordinator(
        ["company_0", "company_1", "company_2"],
        lambda submissions: {},
    )
    result = coordinator.record_interaction(
        "introduce_customer",
        {
            "sender_company_id": "company_0",
            "recipient_company_id": "company_2",
            "day": 3,
            "customer_ref": "warm-S1-lead",
            "group_id": "S1",
            "memo": "They should see another quote.",
        },
    )

    assert result["success"]
    assert coordinator.consume_introduction_visibility(
        source_company_id="company_0",
        group_id="S2",
    ) == []
    assert coordinator.consume_introduction_visibility(
        source_company_id="company_0",
        group_id="S1",
    ) == ["company_2"]
    assert coordinator.consume_introduction_visibility(
        source_company_id="company_0",
        group_id="S1",
    ) == []


def test_arena_runner_applies_introduction_visibility(tmp_path):
    runner = ArenaBashAgentRunner(
        company_count=3,
        total_days=7,
        workspace_base=tmp_path,
        model="test-model",
        provider="test-provider",
    )
    coordinator = ArenaNextWeekCoordinator(
        ["company_0", "company_1", "company_2"],
        lambda submissions: {},
    )
    runner._coordinator = coordinator
    coordinator.record_interaction(
        "introduce_customer",
        {
            "sender_company_id": "company_0",
            "recipient_company_id": "company_2",
            "day": 1,
            "customer_ref": "lead-1",
            "group_id": "S1",
        },
    )

    arrival = runner._apply_introduction_visibility(
        SharedArrival(
            group_id="S1",
            source_company_id="company_0",
            consideration_set=("company_0", "company_1"),
        )
    )

    assert arrival.consideration_set == ("company_0", "company_1", "company_2")


def test_arena_runner_allocates_enterprise_arrival_as_competitive_rfp(tmp_path):
    runner = ArenaBashAgentRunner(
        company_count=2,
        total_days=7,
        workspace_base=tmp_path,
        model="test-model",
        provider="test-provider",
    )
    runner._generate_source_lead_params = lambda submission, group_id, weights: {
        "customer_type": "large",
        "group_id": group_id,
        "steepness_left": 0.8,
        "steepness_right": 1.6,
        "c_max": 1_000.0,
        "q_max": 0.9,
        "q_min": 0.2,
        "seat_count": 50,
        "_lead_channel": "search",
    }
    runner._evaluate_arrival_offers = lambda submissions, arrival, params: [
        {
            "company_id": "company_0",
            "display_name": "NovaMind",
            "plan": "A",
            "price": 500.0,
            "effective_price": 500.0,
            "perceived_quality": 0.45,
            "required_quality": 0.35,
            "satisfaction": 0.10,
            "acceptable": True,
        },
        {
            "company_id": "company_1",
            "display_name": "AsterAI",
            "plan": "B",
            "price": 700.0,
            "effective_price": 700.0,
            "perceived_quality": 0.80,
            "required_quality": 0.40,
            "satisfaction": 0.40,
            "acceptable": True,
        },
    ]
    submissions = {
        "company_0": ArenaNextWeekSubmission("company_0", 1000, 0, "r", {}),
        "company_1": ArenaNextWeekSubmission("company_1", 1001, 0, "r", {}),
    }
    states = {
        "company_0": {
            "config": {"price_A": 500.0, "price_B": 700.0},
            "exposures_by_group": {"E1": {"acquisition_weights": {"search": 1.0}}},
        },
        "company_1": {
            "config": {"price_A": 500.0, "price_B": 700.0},
            "exposures_by_group": {"E1": {"acquisition_weights": {"search": 1.0}}},
        },
    }

    leads_by_company = runner._allocate_shared_arrivals(
        submissions,
        states,
        [
            SharedArrival(
                group_id="E1",
                source_company_id="company_0",
                consideration_set=("company_0", "company_1"),
            )
        ],
    )

    assert leads_by_company["company_0"] == []
    assert len(leads_by_company["company_1"]) == 1
    lead = leads_by_company["company_1"][0]
    assert lead["outcome"] == "enterprise"
    assert lead["plan"] == "B"
    assert lead["arena_competitive_rfp"] is True
    assert lead["arena_rfp_id"].startswith(f"{runner.run_id}:rfp:")
    assert lead["consideration_set"] == ["company_0", "company_1"]


def test_arena_runner_processes_cross_company_switch(monkeypatch, tmp_path):
    runner = ArenaBashAgentRunner(
        company_count=2,
        total_days=7,
        workspace_base=tmp_path,
        model="test-model",
        provider="test-provider",
    )
    calls = []

    def fake_post(port, path, body, *, timeout=7200.0):
        calls.append((port, path, body))
        if path == "/arena-switching-candidates":
            if body["company_id"] == "company_0":
                return {
                    "success": True,
                    "candidates": [
                        {
                            "switch_id": "company_0:42:30",
                            "source_company_id": "company_0",
                            "source_customer_id": 99,
                            "source_subscription_id": 42,
                            "group_id": "S1",
                            "current_plan": "A",
                            "current_price": 20.0,
                            "current_satisfaction": -0.1,
                            "params": {
                                "customer_type": "small",
                                "group_id": "S1",
                                "steepness_left": 0.8,
                                "steepness_right": 1.6,
                                "c_max": 100.0,
                                "q_max": 0.8,
                                "q_min": 0.2,
                                "_lead_channel": None,
                                "_arena_quality_noise": 1.0,
                            },
                        }
                    ],
                }
            return {"success": True, "candidates": []}
        if path == "/arena-evaluate-lead-offers":
            return {
                "success": True,
                "offers": [
                    {
                        "company_id": body["company_id"],
                        "display_name": body["display_name"],
                        "plan": "B",
                        "price": 30.0,
                        "effective_price": 30.0,
                        "satisfaction": 0.25,
                        "perceived_quality": 0.7,
                        "required_quality": 0.45,
                        "acceptable": True,
                    }
                ],
            }
        if path in {"/arena-insert-switched-customer", "/arena-cancel-switched-customer"}:
            return {"success": True, "applied": True}
        raise AssertionError(path)

    monkeypatch.setattr(
        "saas_bench.agents.bash_agent.arena_runner.http_post_json",
        fake_post,
    )
    submissions = {
        "company_0": ArenaNextWeekSubmission("company_0", 1000, 30, "r", {}),
        "company_1": ArenaNextWeekSubmission("company_1", 1001, 30, "r", {}),
    }
    states = {
        "company_0": {
            "config": {"price_B": 30.0, "price_A": 20.0},
            "exposures_by_group": {"S1": {"expected_leads": 1.0}},
        },
        "company_1": {
            "config": {"price_B": 30.0, "price_A": 20.0},
            "exposures_by_group": {"S1": {"expected_leads": 1.0}},
        },
    }

    result = runner._process_cross_company_switching(submissions, states)

    assert result["company_0"]["success"]
    assert result["company_1"]["success"]
    paths = [path for _port, path, _body in calls]
    assert paths.count("/arena-switching-candidates") == 2
    assert "/arena-insert-switched-customer" in paths
    assert "/arena-cancel-switched-customer" in paths


def test_arena_runner_switching_requires_rival_to_clear_hurdle(monkeypatch, tmp_path):
    runner = ArenaBashAgentRunner(
        company_count=2,
        total_days=7,
        workspace_base=tmp_path,
        model="test-model",
        provider="test-provider",
    )
    calls = []

    def fake_post(port, path, body, *, timeout=7200.0):
        calls.append((port, path, body))
        if path == "/arena-switching-candidates":
            if body["company_id"] == "company_0":
                return {
                    "success": True,
                    "candidates": [
                        {
                            "switch_id": "company_0:42:30",
                            "source_company_id": "company_0",
                            "source_customer_id": 99,
                            "source_subscription_id": 42,
                            "group_id": "S1",
                            "current_plan": "A",
                            "current_price": 20.0,
                            "current_satisfaction": 0.20,
                            "switching_hurdle": 0.10,
                            "params": {
                                "customer_type": "small",
                                "group_id": "S1",
                                "steepness_left": 0.8,
                                "steepness_right": 1.6,
                                "c_max": 100.0,
                                "q_max": 0.8,
                                "q_min": 0.2,
                                "_lead_channel": None,
                                "_arena_quality_noise": 1.0,
                            },
                        }
                    ],
                }
            return {"success": True, "candidates": []}
        if path == "/arena-evaluate-lead-offers":
            return {
                "success": True,
                "offers": [
                    {
                        "company_id": body["company_id"],
                        "display_name": body["display_name"],
                        "plan": "B",
                        "price": 30.0,
                        "effective_price": 30.0,
                        "satisfaction": 0.25,
                        "perceived_quality": 0.7,
                        "required_quality": 0.45,
                        "acceptable": True,
                    }
                ],
            }
        if path in {"/arena-insert-switched-customer", "/arena-cancel-switched-customer"}:
            raise AssertionError("switch should not clear hurdle")
        raise AssertionError(path)

    monkeypatch.setattr(
        "saas_bench.agents.bash_agent.arena_runner.http_post_json",
        fake_post,
    )
    submissions = {
        "company_0": ArenaNextWeekSubmission("company_0", 1000, 30, "r", {}),
        "company_1": ArenaNextWeekSubmission("company_1", 1001, 30, "r", {}),
    }
    states = {
        "company_0": {"exposures_by_group": {"S1": {"expected_leads": 1.0}}},
        "company_1": {"exposures_by_group": {"S1": {"expected_leads": 1.0}}},
    }

    result = runner._process_cross_company_switching(submissions, states)

    assert result["company_0"]["success"]
    assert result["company_0"]["switches"] == 0
    assert result["company_1"]["switches"] == 0
    paths = [path for _port, path, _body in calls]
    assert "/arena-insert-switched-customer" not in paths
    assert "/arena-cancel-switched-customer" not in paths


def test_arena_runner_acquisition_slot_uses_shared_market_endpoints(monkeypatch, tmp_path):
    runner = ArenaBashAgentRunner(
        company_count=2,
        total_days=7,
        workspace_base=tmp_path,
        model="test-model",
        provider="test-provider",
    )
    runner._sample_daily_arrivals = lambda states: [
        SharedArrival(
            group_id="S1",
            source_company_id="company_0",
            consideration_set=("company_0", "company_1"),
        )
    ]

    calls = []
    inserted = {1000: [], 1001: []}

    def fake_post(port, path, body, *, timeout=7200.0):
        calls.append((port, path, body))
        company_id = body.get("company_id")
        if path == "/arena-market-state":
            config = {
                "price_A": 10.0,
                "price_B": 20.0,
                "price_C": 30.0,
                "tier_A": 1,
                "tier_B": 1,
                "tier_C": 1,
            }
            base_quality = 0.2
            if company_id == "company_1":
                config = {**config, "tier_A": 5}
                base_quality = 1.0
            return {
                "success": True,
                "state": {
                    "company_id": company_id,
                    "display_name": company_id,
                    "day": 0,
                    "config": config,
                    "base_product_quality": base_quality,
                    "q_shared_bonus": 0.0,
                    "q_group_bonuses": {"S1": 0.0},
                    "lead_promotions_by_group": {"S1": 0.0},
                    "subscriber_counts_by_group": {"S1": 0},
                    "exposures_by_group": {
                        "S1": {
                            "expected_leads": 1.0,
                            "acquisition_weights": {"social_media": 1.0},
                        }
                    },
                },
            }
        if path == "/arena-generate-lead":
            return {
                "success": True,
                "params": {
                    "customer_type": "small",
                    "group_id": body["group_id"],
                    "steepness_left": 0.8,
                    "steepness_right": 1.6,
                    "c_max": 100.0,
                    "q_max": 0.8,
                    "q_min": 0.2,
                    "usage_scale": 50.0,
                    "usage_demand": 50.0,
                    "quality_sensitivity": 0.5,
                    "price_sensitivity": 0.5,
                    "willingness_to_pay": 100.0,
                    "patience": 0.5,
                    "seat_count": None,
                    "acquisition_source": "social_media",
                    "_lead_channel": "social_media",
                },
            }
        if path == "/arena-evaluate-lead-offers":
            company_id = body["company_id"]
            if company_id == "company_0":
                assert body["params"]["_lead_channel"] == "social_media"
            else:
                assert body["params"]["_lead_channel"] is None
            quality = 0.1 if company_id == "company_0" else 0.9
            return {
                "success": True,
                "offers": [
                    {
                        "company_id": company_id,
                        "display_name": body["display_name"],
                        "plan": "A",
                        "price": 10.0,
                        "effective_price": 10.0,
                        "perceived_quality": quality,
                        "required_quality": 0.2,
                        "satisfaction": quality - 0.2,
                        "acceptable": quality >= 0.2,
                    }
                ],
            }
        if path == "/arena-apply-acquisition-result":
            inserted[port].extend(body["leads"])
            return {
                "success": True,
                "generation_result": {
                    "total_new": len(body["leads"]),
                    "total_leads": len(body["leads"]),
                    "new_individual_leads": len(body["leads"]),
                    "new_enterprise_leads": 0,
                    "new_individual_subscribers": len(body["leads"]),
                },
            }
        if path == "/arena-switching-candidates":
            return {"success": True, "candidates": []}
        raise AssertionError(path)

    monkeypatch.setattr(
        "saas_bench.agents.bash_agent.arena_runner.http_post_json",
        fake_post,
    )

    slot_submissions = {
        "company_0": ArenaAcquisitionSlotSubmission(
            company_id="company_0",
            api_port=1000,
            day=1,
            display_name="NovaMind",
        ),
        "company_1": ArenaAcquisitionSlotSubmission(
            company_id="company_1",
            api_port=1001,
            day=1,
            display_name="AsterAI",
        ),
    }

    results = runner._advance_acquisition_slot(1, slot_submissions)

    paths = [path for _port, path, _body in calls]
    assert paths.count("/arena-evaluate-lead-offers") == 2
    assert paths.count("/arena-apply-acquisition-result") == 2
    assert paths.count("/arena-switching-candidates") == 2
    assert inserted[1000] == []
    assert len(inserted[1001]) == 1
    assert inserted[1001][0]["outcome"] == "subscribe"
    assert inserted[1001][0]["source_company_id"] == "company_0"
    assert results["company_0"]["success"]
    assert results["company_1"]["success"]
    assert results["company_1"]["generation_result"]["total_leads"] == 1


def test_arena_runner_advances_week_with_shared_acquisition_endpoint(monkeypatch, tmp_path):
    runner = ArenaBashAgentRunner(
        company_count=2,
        total_days=7,
        workspace_base=tmp_path,
        model="test-model",
        provider="test-provider",
    )
    runner._coordinator = object()
    runner._active_coordinator_port = 7777

    calls = []
    advanced_days = {1000: 0, 1001: 0}

    def fake_post(port, path, body, *, timeout=7200.0):
        calls.append((port, path, body))
        company_id = body.get("company_id")
        if path == "/arena-next-day-shared-acquisition":
            assert body["arena_coordinator_port"] == 7777
            advanced_days[port] += 1
            result = {"success": True, "day": advanced_days[port]}
            if body.get("final_day"):
                result["dashboard"] = f"dashboard {company_id}"
            return result
        if path == "/arena-market-state":
            return {
                "success": True,
                "state": {
                    "company_id": company_id,
                    "display_name": company_id,
                    "day": advanced_days[port],
                    "config": {
                        "price_A": 10.0,
                        "price_B": 20.0,
                        "price_C": 30.0,
                        "tier_A": 1,
                        "tier_B": 1,
                        "tier_C": 1,
                        "quota_A": 100,
                        "quota_B": 200,
                        "quota_C": 300,
                    },
                    "subscriber_counts_by_group": {"S1": 0},
                    "exposures_by_group": {},
                },
            }
        if path == "/arena-upsert-public-snapshots":
            assert len(body["snapshots"]) == 2
            for snapshot in body["snapshots"]:
                assert "subscriber_counts_by_group" not in snapshot
                assert "quota_A" not in snapshot["config"]
                assert "quota_B" not in snapshot["config"]
                assert "quota_C" not in snapshot["config"]
            return {"success": True, "snapshots_written": len(body["snapshots"])}
        raise AssertionError(path)

    monkeypatch.setattr(
        "saas_bench.agents.bash_agent.arena_runner.http_post_json",
        fake_post,
    )

    submissions = {
        "company_0": ArenaNextWeekSubmission("company_0", 1000, 0, "rationale", {}),
        "company_1": ArenaNextWeekSubmission("company_1", 1001, 0, "rationale", {}),
    }

    results = runner._advance_submitted_week(submissions)

    paths = [path for _port, path, _body in calls]
    assert "/next-week" not in paths
    assert "/arena-next-day-no-acquisition" not in paths
    assert paths.count("/arena-next-day-shared-acquisition") == 14
    assert paths.count("/arena-upsert-public-snapshots") == 14
    assert results["company_0"]["success"]
    assert results["company_1"]["success"]
    assert results["company_0"]["day"] == 7
    assert results["company_1"]["day"] == 7
    assert results["company_0"]["dashboard"] == "dashboard company_0"
