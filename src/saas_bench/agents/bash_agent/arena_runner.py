"""Arena runner for multiple bash agents sharing a weekly barrier."""

from __future__ import annotations

import json
import os
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Optional

import numpy as np

from saas_bench.arena import (
    ArenaAcquisitionSlotSubmission,
    ArenaCoordinatorHTTPServer,
    ArenaCompanySpec,
    ArenaNextWeekCoordinator,
    ArenaNextWeekSubmission,
    CompanyExposure,
    SharedArrival,
    choose_evaluated_company_plan,
    choose_evaluated_company_plan_with_source,
    make_company_specs,
    sample_shared_arrivals,
)
from saas_bench.arena.coordinator import http_post_json
from saas_bench.config import BenchmarkConfig, CUSTOMER_GROUPS

from .run_test import BashAgentRunner


@dataclass(frozen=True)
class ArenaModelSpec:
    provider: str | None
    model: str | None


def parse_arena_model_specs(
    raw: str | None,
    *,
    count: int,
    default_provider: str | None,
    default_model: str | None,
) -> list[ArenaModelSpec]:
    """Parse provider:model arena model specs, repeating defaults as needed."""

    entries = [item.strip() for item in (raw or "").split(",") if item.strip()]
    specs: list[ArenaModelSpec] = []
    for entry in entries:
        if ":" in entry:
            provider, model = entry.split(":", 1)
            specs.append(ArenaModelSpec(provider.strip() or None, model.strip() or None))
        else:
            specs.append(ArenaModelSpec(default_provider, entry))

    while len(specs) < count:
        specs.append(ArenaModelSpec(default_provider, default_model))

    if len(specs) > count:
        raise ValueError("--arena-models cannot list more models than --arena-companies")

    return specs


class ArenaBashAgentRunner:
    """Run multiple ordinary bash-agent CEOBench companies in arena mode."""

    def __init__(
        self,
        *,
        company_count: int,
        arena_models: str | None = None,
        model: Optional[str] = None,
        provider: Optional[str] = None,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        seed: int = 42,
        scenario: str = "default",
        total_days: int = 3650,
        initial_cash: float = 1_000_000.0,
        workspace_base: Optional[Path] = None,
        reasoning_effort: Optional[str] = None,
        label: Optional[str] = None,
        continue_from: Optional[Path] = None,
    ):
        config_from_resume: dict[str, Any] = {}
        if continue_from:
            resume_dir = Path(continue_from).resolve()
            if not resume_dir.exists():
                raise FileNotFoundError(f"Arena run directory not found: {resume_dir}")
            config_file = resume_dir / "config.json"
            if config_file.exists():
                config_from_resume = json.loads(config_file.read_text())
                if not config_from_resume.get("arena"):
                    raise ValueError(f"Not an arena run directory: {resume_dir}")
                company_count = int(config_from_resume.get("company_count") or company_count)

        if company_count < 2:
            raise ValueError("ArenaBashAgentRunner requires at least two companies")

        default_config = BenchmarkConfig()
        self.company_count = company_count
        self.default_model = model or default_config.agent_llm_model
        self.default_provider = provider or default_config.agent_llm_provider
        self.base_url = base_url
        self.api_key = api_key
        self.seed = seed
        self.scenario = scenario
        self.total_days = (total_days // 7) * 7
        self.initial_cash = initial_cash
        self.reasoning_effort = reasoning_effort or default_config.agent_llm_reasoning_effort
        self.label = label
        self.continue_from = Path(continue_from).resolve() if continue_from else None

        if self.continue_from:
            self.workspace_dir = self.continue_from
            self.run_id = str(config_from_resume.get("run_id") or self.workspace_dir.name.replace("arena_", ""))
        else:
            self.run_id = str(uuid.uuid4())[:8]
            base = (workspace_base or Path("./bash_agent_runs")).resolve()
            self.workspace_dir = base / f"arena_{self.run_id}"
        self.workspace_dir.mkdir(parents=True, exist_ok=True)
        self._runners: dict[str, BashAgentRunner] = {}
        self._coordinator: ArenaNextWeekCoordinator | None = None
        self._active_coordinator_port: int | None = None
        self._market_rng = np.random.default_rng(seed + 7919)
        self._shared_competitor_rng = np.random.default_rng(seed + 15485863)
        self._shared_market_config = BenchmarkConfig(
            seed=seed,
            total_days=self.total_days,
            initial_cash=initial_cash,
        )
        self._shared_competitor_last_event_day = (
            -self._shared_market_config.competitor_event_mean_interval
        )
        self._arena_rfp_sequence = 0

        if self.continue_from and config_from_resume.get("companies") and not arena_models:
            companies_config = list(config_from_resume.get("companies") or [])
            self.model_specs = [
                ArenaModelSpec(
                    provider=company.get("provider") or self.default_provider,
                    model=company.get("model") or self.default_model,
                )
                for company in companies_config[:company_count]
            ]
            while len(self.model_specs) < company_count:
                self.model_specs.append(ArenaModelSpec(self.default_provider, self.default_model))
            self.company_specs = [
                ArenaCompanySpec(
                    company_id=str(company.get("company_id") or f"company_{index}"),
                    display_name=str(company.get("display_name") or f"Company {index}"),
                    agent_model=self.model_specs[index].model,
                    starting_cash=initial_cash,
                )
                for index, company in enumerate(companies_config[:company_count])
            ]
            while len(self.company_specs) < company_count:
                index = len(self.company_specs)
                self.company_specs.append(
                    ArenaCompanySpec(
                        company_id=f"company_{index}",
                        display_name=f"Company {index}",
                        agent_model=self.model_specs[index].model,
                        starting_cash=initial_cash,
                    )
                )
        else:
            self.model_specs = parse_arena_model_specs(
                arena_models,
                count=company_count,
                default_provider=self.default_provider,
                default_model=self.default_model,
            )
            self.company_specs = make_company_specs(
                company_count,
                agent_models=[spec.model for spec in self.model_specs],
                starting_cash=initial_cash,
            )

    def run(self, verbose: bool = True) -> dict[str, Any]:
        coordinator = ArenaNextWeekCoordinator(
            [spec.company_id for spec in self.company_specs],
            self._advance_submitted_week,
            acquisition_slot_callback=self._advance_acquisition_slot,
        )
        self._coordinator = coordinator
        server = ArenaCoordinatorHTTPServer(coordinator)
        server.start()
        self._active_coordinator_port = server.port

        if verbose:
            print(f"\n{'='*60}")
            print("Starting CEOBench Arena")
            print(f"Arena Run ID: {self.run_id}")
            print(f"Companies: {self.company_count}")
            print(f"Coordinator Port: {server.port}")
            print(f"Workspace: {self.workspace_dir}")
            print(f"{'='*60}\n")

        try:
            self._write_config(server.port)
            self._create_company_runners(server.port)
            results = self._run_company_threads(verbose=verbose)
        finally:
            server.stop()
            self._coordinator = None
            self._active_coordinator_port = None

        outcomes = {company_id: result.get("outcome") for company_id, result in results.items()}
        return {
            "run_id": self.run_id,
            "arena": True,
            "companies": results,
            "outcomes": outcomes,
            "workspace_dir": str(self.workspace_dir),
        }

    def _create_company_runners(self, coordinator_port: int) -> None:
        companies_root = self.workspace_dir / "companies"
        companies_root.mkdir(exist_ok=True)

        for index, spec in enumerate(self.company_specs):
            model_spec = self.model_specs[index]
            runner = BashAgentRunner(
                model=model_spec.model,
                provider=model_spec.provider,
                base_url=self.base_url,
                api_key=self.api_key,
                seed=self.seed + index,
                scenario=self.scenario,
                total_days=self.total_days,
                initial_cash=spec.starting_cash or self.initial_cash,
                workspace_base=companies_root / spec.company_id,
                reasoning_effort=self.reasoning_effort,
                continue_from=self._company_resume_dir(spec.company_id),
                label=self.label or f"arena:{spec.display_name}",
                arena_company_id=spec.company_id,
                arena_display_name=spec.display_name,
                arena_coordinator_port=coordinator_port,
                arena_company_count=self.company_count,
            )
            self._runners[spec.company_id] = runner

    def _company_resume_dir(self, company_id: str) -> Path | None:
        if not self.continue_from:
            return None

        company_root = self.workspace_dir / "companies" / company_id
        if not company_root.exists():
            raise FileNotFoundError(f"Arena company directory not found: {company_root}")

        candidates = [
            path
            for path in company_root.iterdir()
            if path.is_dir() and path.name.startswith("run_")
        ]
        if not candidates:
            if (company_root / "agent_workspace").exists():
                return company_root
            raise FileNotFoundError(f"No resumable run directory found under: {company_root}")
        return max(candidates, key=lambda path: path.stat().st_mtime)

    def _run_company_threads(self, *, verbose: bool) -> dict[str, dict]:
        results: dict[str, dict] = {}
        with ThreadPoolExecutor(max_workers=self.company_count) as executor:
            future_by_company = {
                executor.submit(runner.run, verbose=verbose): company_id
                for company_id, runner in self._runners.items()
            }
            for future in as_completed(future_by_company):
                company_id = future_by_company[future]
                try:
                    result = future.result()
                except Exception as exc:
                    self._retire_finished_company(
                        company_id,
                        outcome=type(exc).__name__,
                    )
                    raise
                results[company_id] = result
                self._retire_finished_company(
                    company_id,
                    outcome=str(result.get("outcome") or "completed"),
                )
        return results

    def _retire_finished_company(self, company_id: str, *, outcome: str) -> None:
        if self._coordinator is None:
            return
        self._coordinator.retire_company(company_id, outcome=outcome)

    def _advance_submitted_week(
        self,
        submissions: dict[str, ArenaNextWeekSubmission],
    ) -> dict[str, dict]:
        """Advance all companies one day at a time with a shared customer market.

        Agents still submit the ordinary ``next-week`` command. Internally,
        each company enters the ordinary CEOBench ``step_day`` flow and blocks
        at the customer-acquisition slot; the coordinator then samples and
        allocates that day's shared market arrivals before the day continues.
        """

        results: dict[str, dict] = {}
        for day_offset in range(7):
            start_day = min(int(submission.day) for submission in submissions.values()) + day_offset + 1
            event = self._sample_shared_competitor_event(start_day)
            event_results = self._apply_shared_competitor_event(submissions, event)
            if any(not result.get("success") for result in event_results.values()):
                return event_results

            results = self._advance_one_day_with_shared_acquisition(
                submissions,
                day_offset=day_offset,
            )
            if any(not result.get("success") for result in results.values()):
                return results

            public_states = self._fetch_market_states(submissions)
            publish_results = self._publish_public_snapshots(submissions, public_states)
            if any(not result.get("success") for result in publish_results.values()):
                return publish_results

        return results

    def _sample_shared_competitor_event(self, start_day: int) -> dict | None:
        """Sample the Arena-wide version of CEOBench's competitor event process."""
        config = self._shared_market_config

        grace = getattr(config, "drift_grace_period_days", 0)
        if grace > 0 and start_day < grace:
            return None

        late_cutoff = getattr(config, "competitor_event_late_cutoff_days", 0)
        if late_cutoff > 0 and start_day > config.total_days - late_cutoff:
            return None

        mean_interval = config.competitor_event_mean_interval
        min_interval = config.competitor_event_min_interval
        half_sim = max(config.total_days // 2, 1)
        if start_day < half_sim:
            mean_interval *= 1.5
            min_interval *= 1.5

        days_since_last = start_day - self._shared_competitor_last_event_day
        if days_since_last < min_interval:
            return None

        daily_prob = 1.0 / mean_interval
        if self._shared_competitor_rng.random() >= daily_prob:
            return None

        raw_boost = float(
            self._shared_competitor_rng.lognormal(
                config.competitor_event_boost_mu,
                config.competitor_event_boost_sigma,
            )
        )
        base_boost = max(
            config.competitor_event_boost_min,
            min(raw_boost, config.competitor_event_boost_max),
        )

        scale_min = getattr(config, "competitor_event_magnitude_scale_min", 1.0)
        scale_max = getattr(config, "competitor_event_magnitude_scale_max", 16.0)
        ramp_end_day = max(config.total_days - late_cutoff, 2)
        day_frac = max(0.0, min((start_day - 1) / max(ramp_end_day - 1, 1), 1.0))
        magnitude_scale = scale_min + (scale_max - scale_min) * day_frac
        boost = base_boost * magnitude_scale

        if boost < config.competitor_severity_minor_max:
            description = "A competitor released an incremental product update."
        elif boost < config.competitor_severity_moderate_max:
            description = "A competitor launched a significant feature upgrade."
        elif boost < config.competitor_severity_major_max:
            description = "A competitor launched a major product overhaul with advanced features."
        else:
            description = "A competitor made a breakthrough product launch that redefines market expectations."

        segment_drain_u_by_group = {
            group_id: float(
                self._shared_competitor_rng.uniform(
                    config.competitor_segment_drain_u_min,
                    config.competitor_segment_drain_u_max,
                )
            )
            for group_id in CUSTOMER_GROUPS
        }
        self._shared_competitor_last_event_day = start_day
        return {
            "start_day": start_day,
            "boost_amount": float(boost),
            "post_end_day": start_day + config.competitor_event_post_days,
            "description": description,
            "sampled_boost": float(boost),
            "feedback_u": float(
                self._shared_competitor_rng.uniform(
                    config.competitor_feedback_u_min,
                    config.competitor_feedback_u_max,
                )
            ),
            "unreleased_pre": 0.0,
            "feedback_term": 0.0,
            "winner": "sampled",
            "segment_drain_u_by_group": segment_drain_u_by_group,
        }

    def _apply_shared_competitor_event(
        self,
        submissions: dict[str, ArenaNextWeekSubmission],
        event: dict | None,
    ) -> dict[str, dict]:
        if event is None:
            return {company_id: {"success": True, "applied": False} for company_id in submissions}

        results: dict[str, dict] = {}
        with ThreadPoolExecutor(max_workers=len(submissions)) as executor:
            future_by_company = {
                executor.submit(
                    http_post_json,
                    submission.api_port,
                    "/arena-apply-shared-competitor-event",
                    {"event": event},
                ): company_id
                for company_id, submission in submissions.items()
            }
            for future in as_completed(future_by_company):
                company_id = future_by_company[future]
                result = future.result()
                if result.get("success"):
                    results[company_id] = result
                else:
                    results[company_id] = {
                        "success": False,
                        "error": result.get("error", "arena_shared_competitor_event_failed"),
                        "message": result.get("message", ""),
                    }
        return results

    def _advance_acquisition_slot(
        self,
        day: int,
        slot_submissions: dict[str, ArenaAcquisitionSlotSubmission],
    ) -> dict[str, dict]:
        initial_states = self._fetch_market_states(slot_submissions)
        market_subscriber_counts = self._sum_market_subscriber_counts(initial_states)
        market_states = self._fetch_market_states(
            slot_submissions,
            market_subscriber_counts=market_subscriber_counts,
        )
        arrivals = self._sample_daily_arrivals(market_states)
        leads_by_company = self._allocate_shared_arrivals(
            slot_submissions,
            market_states,
            arrivals,
        )

        switching_results = self._process_cross_company_switching(
            slot_submissions,
            market_states,
        )
        if any(not result.get("success") for result in switching_results.values()):
            return switching_results

        return self._apply_allocated_acquisition(
            slot_submissions,
            leads_by_company,
        )

    def _fetch_market_states(
        self,
        submissions: dict[str, Any],
        *,
        market_subscriber_counts: dict[str, int] | None = None,
    ) -> dict[str, dict]:
        states: dict[str, dict] = {}
        with ThreadPoolExecutor(max_workers=len(submissions)) as executor:
            future_by_company = {
                executor.submit(
                    http_post_json,
                    submission.api_port,
                    "/arena-market-state",
                    {
                        "company_id": company_id,
                        "display_name": self._display_name(company_id),
                        "market_subscriber_counts_by_group": market_subscriber_counts,
                    },
                ): company_id
                for company_id, submission in submissions.items()
            }
            for future in as_completed(future_by_company):
                company_id = future_by_company[future]
                result = future.result()
                if result.get("success"):
                    states[company_id] = result["state"]
                else:
                    raise RuntimeError(
                        f"arena market state failed for {company_id}: "
                        f"{result.get('error', 'unknown_error')}"
                    )
        return states

    def _sum_market_subscriber_counts(self, states: dict[str, dict]) -> dict[str, int]:
        totals: dict[str, int] = {}
        for state in states.values():
            for group_id, count in state.get("subscriber_counts_by_group", {}).items():
                totals[group_id] = totals.get(group_id, 0) + int(count)
        return totals

    def _sample_daily_arrivals(self, states: dict[str, dict]) -> list[SharedArrival]:
        exposures: list[CompanyExposure] = []
        for company_id, state in states.items():
            for group_id, exposure in state.get("exposures_by_group", {}).items():
                expected_daily = float(exposure.get("expected_leads", 0.0))
                if expected_daily <= 0:
                    continue
                exposures.append(
                    CompanyExposure(
                        company_id=company_id,
                        group_id=group_id,
                        expected_leads=expected_daily,
                    )
                )
        return sample_shared_arrivals(exposures, self._market_rng)

    def _advance_one_day_without_private_acquisition(
        self,
        submissions: dict[str, ArenaNextWeekSubmission],
        *,
        day_offset: int,
    ) -> dict[str, dict]:
        results: dict[str, dict] = {}
        with ThreadPoolExecutor(max_workers=len(submissions)) as executor:
            future_by_company = {
                executor.submit(
                    http_post_json,
                    submission.api_port,
                    "/arena-next-day-no-acquisition",
                    {
                        **submission.next_week_body,
                        "first_day": day_offset == 0,
                        "final_day": day_offset == 6,
                        "suppress_customer_posts": day_offset < 6,
                    },
                ): company_id
                for company_id, submission in submissions.items()
            }
            for future in as_completed(future_by_company):
                company_id = future_by_company[future]
                result = future.result()
                if result.get("success"):
                    results[company_id] = result
                else:
                    results[company_id] = {
                        "success": False,
                        "error": result.get("error", "arena_company_advance_failed"),
                        "message": result.get("message", ""),
                    }
        return results

    def _advance_one_day_with_shared_acquisition(
        self,
        submissions: dict[str, ArenaNextWeekSubmission],
        *,
        day_offset: int,
    ) -> dict[str, dict]:
        if self._coordinator is None:
            return {
                company_id: {
                    "success": False,
                    "error": "arena_coordinator_not_running",
                }
                for company_id in submissions
            }

        results: dict[str, dict] = {}
        with ThreadPoolExecutor(max_workers=len(submissions)) as executor:
            future_by_company = {
                executor.submit(
                    http_post_json,
                    submission.api_port,
                    "/arena-next-day-shared-acquisition",
                    {
                        **submission.next_week_body,
                        "company_id": company_id,
                        "display_name": self._display_name(company_id),
                        "arena_coordinator_port": self._coordinator_port(),
                        "first_day": day_offset == 0,
                        "final_day": day_offset == 6,
                        "suppress_customer_posts": day_offset < 6,
                    },
                ): company_id
                for company_id, submission in submissions.items()
            }
            for future in as_completed(future_by_company):
                company_id = future_by_company[future]
                result = future.result()
                if result.get("success"):
                    results[company_id] = result
                else:
                    results[company_id] = {
                        "success": False,
                        "error": result.get("error", "arena_company_advance_failed"),
                        "message": result.get("message", ""),
                    }
        return results

    def _allocate_shared_arrivals(
        self,
        submissions: dict[str, Any],
        states: dict[str, dict],
        arrivals: list[SharedArrival],
    ) -> dict[str, list[dict]]:
        leads_by_company = {company_id: [] for company_id in submissions}

        for raw_arrival in arrivals:
            arrival = self._apply_introduction_visibility(raw_arrival)
            source_state = states[arrival.source_company_id]
            exposure = source_state.get("exposures_by_group", {}).get(arrival.group_id, {})
            params = self._generate_source_lead_params(
                submissions[arrival.source_company_id],
                arrival.group_id,
                exposure.get("acquisition_weights", {}),
            )
            offers = self._evaluate_arrival_offers(
                submissions,
                arrival,
                params,
            )
            choice = choose_evaluated_company_plan_with_source(
                offers,
                source_company_id=arrival.source_company_id,
                comparison_hurdle=float(params.get("_arena_comparison_hurdle", 0.0) or 0.0),
            )
            is_enterprise = params.get("customer_type") == "large"
            rfp_id = self._next_rfp_id(arrival) if is_enterprise else None

            if choice.chose_product and choice.company_id in leads_by_company:
                target_company_id = str(choice.company_id)
                chosen_offer = self._offer_for_choice(offers, target_company_id, str(choice.plan))
                plan = str(choice.plan or "A")
                outcome = "enterprise" if is_enterprise else "subscribe"
                lead_spec = {
                    "params": params,
                    "outcome": outcome,
                    "plan": plan,
                    "price": self._offer_price(
                        chosen_offer,
                        states[target_company_id],
                        plan,
                    ),
                    "source_company_id": arrival.source_company_id,
                    "target_company_id": target_company_id,
                    "chosen_company_id": target_company_id,
                    "consideration_set": list(arrival.consideration_set),
                    "chosen_offer": chosen_offer or {},
                    "offers": offers,
                }
                if rfp_id:
                    lead_spec.update({
                        "arena_competitive_rfp": True,
                        "arena_rfp_id": rfp_id,
                    })
                leads_by_company[target_company_id].append(lead_spec)
            else:
                source_company_id = arrival.source_company_id
                source_offer = self._best_offer_for_company(offers, source_company_id)
                source_plan = str(source_offer.get("plan", "A")) if source_offer else "A"
                outcome = "enterprise_skip" if is_enterprise else "lost"
                lead_spec = {
                    "params": params,
                    "outcome": outcome,
                    "plan": source_plan,
                    "price": self._offer_price(
                        source_offer,
                        source_state,
                        source_plan,
                    ),
                    "source_company_id": source_company_id,
                    "target_company_id": source_company_id,
                    "chosen_company_id": None,
                    "consideration_set": list(arrival.consideration_set),
                    "chosen_offer": source_offer or {},
                    "offers": offers,
                }
                if rfp_id:
                    lead_spec.update({
                        "arena_competitive_rfp": True,
                        "arena_rfp_id": rfp_id,
                    })
                leads_by_company[source_company_id].append(lead_spec)

        return leads_by_company

    def _next_rfp_id(self, arrival: SharedArrival) -> str:
        self._arena_rfp_sequence += 1
        return (
            f"{self.run_id}:rfp:{self._arena_rfp_sequence}:"
            f"{arrival.group_id}:{arrival.source_company_id}"
        )

    def _process_cross_company_switching(
        self,
        submissions: dict[str, Any],
        states: dict[str, dict],
    ) -> dict[str, dict]:
        candidates_by_company = self._fetch_switching_candidates(submissions)
        results = {company_id: {"success": True, "switches": 0} for company_id in submissions}

        for source_company_id, candidates in candidates_by_company.items():
            for candidate in candidates:
                group_id = str(candidate.get("group_id") or "")
                rival_company_ids = [
                    company_id
                    for company_id, state in states.items()
                    if company_id != source_company_id
                    and group_id in state.get("exposures_by_group", {})
                ]
                if not rival_company_ids:
                    continue

                arrival = SharedArrival(
                    group_id=group_id,
                    source_company_id=source_company_id,
                    consideration_set=tuple(rival_company_ids),
                )
                offers = self._evaluate_arrival_offers(
                    submissions,
                    arrival,
                    dict(candidate.get("params") or {}),
                )
                choice = choose_evaluated_company_plan(offers)
                if not choice.chose_product or not choice.company_id:
                    continue
                current_satisfaction = float(candidate.get("current_satisfaction", 0.0) or 0.0)
                switching_hurdle = max(0.0, float(candidate.get("switching_hurdle", 0.0) or 0.0))
                if float(choice.satisfaction) <= current_satisfaction + switching_hurdle:
                    continue

                target_company_id = str(choice.company_id)
                chosen_offer = self._offer_for_choice(offers, target_company_id, str(choice.plan))
                switch_spec = {
                    **candidate,
                    "target_company_id": target_company_id,
                    "chosen_offer": chosen_offer or {},
                    "plan": str(choice.plan or "A"),
                    "price": self._offer_price(
                        chosen_offer,
                        states[target_company_id],
                        str(choice.plan or "A"),
                    ),
                }
                insert_result = http_post_json(
                    submissions[target_company_id].api_port,
                    "/arena-insert-switched-customer",
                    switch_spec,
                )
                if not insert_result.get("success"):
                    results[target_company_id] = {
                        "success": False,
                        "error": insert_result.get("error", "arena_insert_switch_failed"),
                        "message": insert_result.get("message", ""),
                    }
                    return results

                cancel_result = http_post_json(
                    submissions[source_company_id].api_port,
                    "/arena-cancel-switched-customer",
                    switch_spec,
                )
                if not cancel_result.get("success"):
                    results[source_company_id] = {
                        "success": False,
                        "error": cancel_result.get("error", "arena_cancel_switch_failed"),
                        "message": cancel_result.get("message", ""),
                    }
                    return results
                results[source_company_id]["switches"] += 1
                results[target_company_id]["switches"] += 1

        return results

    def _fetch_switching_candidates(
        self,
        submissions: dict[str, Any],
    ) -> dict[str, list[dict]]:
        candidates: dict[str, list[dict]] = {}
        with ThreadPoolExecutor(max_workers=len(submissions)) as executor:
            future_by_company = {
                executor.submit(
                    http_post_json,
                    submission.api_port,
                    "/arena-switching-candidates",
                    {
                        "company_id": company_id,
                        "limit": 25,
                    },
                ): company_id
                for company_id, submission in submissions.items()
            }
            for future in as_completed(future_by_company):
                company_id = future_by_company[future]
                result = future.result()
                if result.get("success"):
                    candidates[company_id] = [
                        dict(candidate)
                        for candidate in result.get("candidates", [])
                    ]
                else:
                    raise RuntimeError(
                        f"arena switching candidates failed for {company_id}: "
                        f"{result.get('error', 'unknown_error')}"
                    )
        return candidates

    def _apply_introduction_visibility(self, arrival: SharedArrival) -> SharedArrival:
        if self._coordinator is None:
            return arrival

        introduced = self._coordinator.consume_introduction_visibility(
            source_company_id=arrival.source_company_id,
            group_id=arrival.group_id,
        )
        if not introduced:
            return arrival

        consideration = list(arrival.consideration_set)
        for company_id in introduced:
            if company_id not in consideration:
                consideration.append(company_id)
        if tuple(consideration) == arrival.consideration_set:
            return arrival
        return replace(arrival, consideration_set=tuple(consideration))

    def _evaluate_arrival_offers(
        self,
        submissions: dict[str, Any],
        arrival: SharedArrival,
        params: dict,
    ) -> list[dict]:
        offers: list[dict] = []
        for company_id in arrival.consideration_set:
            submission = submissions.get(company_id)
            if submission is None:
                continue

            company_params = dict(params)
            if company_id != arrival.source_company_id:
                company_params["_lead_channel"] = None

            result = http_post_json(
                submission.api_port,
                "/arena-evaluate-lead-offers",
                {
                    "company_id": company_id,
                    "display_name": self._display_name(company_id),
                    "params": company_params,
                    "source_company_id": arrival.source_company_id,
                },
            )
            if not result.get("success"):
                raise RuntimeError(
                    f"arena offer evaluation failed for {company_id}: "
                    f"{result.get('error', 'unknown_error')}"
                )
            offers.extend(dict(offer) for offer in result.get("offers", []))
        return offers

    def _offer_for_choice(
        self,
        offers: list[dict],
        company_id: str,
        plan: str,
    ) -> dict | None:
        for offer in offers:
            if offer.get("company_id") == company_id and offer.get("plan") == plan:
                return offer
        return None

    def _best_offer_for_company(
        self,
        offers: list[dict],
        company_id: str,
    ) -> dict | None:
        company_offers = [
            offer for offer in offers if offer.get("company_id") == company_id
        ]
        if not company_offers:
            return None
        return max(
            company_offers,
            key=lambda offer: float(offer.get("satisfaction", float("-inf"))),
        )

    def _offer_price(self, offer: dict | None, state: dict, plan: str) -> float:
        if offer is not None:
            try:
                return float(offer["price"])
            except (KeyError, TypeError, ValueError):
                pass
        return self._price_for_plan(state, plan)

    def _generate_source_lead_params(
        self,
        submission: Any,
        group_id: str,
        acquisition_weights: dict,
    ) -> dict:
        result = http_post_json(
            submission.api_port,
            "/arena-generate-lead",
            {
                "group_id": group_id,
                "acquisition_weights": acquisition_weights,
            },
        )
        if not result.get("success"):
            raise RuntimeError(
                f"arena lead generation failed for {submission.company_id}: "
                f"{result.get('error', 'unknown_error')}"
            )
        return dict(result["params"])

    def _apply_allocated_acquisition(
        self,
        submissions: dict[str, Any],
        leads_by_company: dict[str, list[dict]],
    ) -> dict[str, dict]:
        results: dict[str, dict] = {}
        with ThreadPoolExecutor(max_workers=len(submissions)) as executor:
            future_by_company = {
                executor.submit(
                    http_post_json,
                    submission.api_port,
                    "/arena-apply-acquisition-result",
                    {
                        "leads": leads_by_company.get(company_id, []),
                        "company_id": company_id,
                        "display_name": self._display_name(company_id),
                    },
                ): company_id
                for company_id, submission in submissions.items()
            }
            for future in as_completed(future_by_company):
                company_id = future_by_company[future]
                result = future.result()
                if result.get("success"):
                    results[company_id] = result
                else:
                    results[company_id] = {
                        "success": False,
                        "error": result.get("error", "arena_apply_acquisition_failed"),
                        "message": result.get("message", ""),
                    }
        return results

    def _insert_allocated_leads(
        self,
        submissions: dict[str, ArenaNextWeekSubmission],
        leads_by_company: dict[str, list[dict]],
        *,
        finalize_week: bool = True,
    ) -> dict[str, dict]:
        results: dict[str, dict] = {}
        with ThreadPoolExecutor(max_workers=len(submissions)) as executor:
            future_by_company = {
                executor.submit(
                    http_post_json,
                    submission.api_port,
                    "/arena-insert-allocated-leads",
                    {
                        "leads": leads_by_company.get(company_id, []),
                        "finalize_week": finalize_week,
                        "company_id": company_id,
                        "display_name": self._display_name(company_id),
                    },
                ): company_id
                for company_id, submission in submissions.items()
            }
            for future in as_completed(future_by_company):
                company_id = future_by_company[future]
                result = future.result()
                if result.get("success"):
                    results[company_id] = result
                else:
                    results[company_id] = {
                        "success": False,
                        "error": result.get("error", "arena_insert_leads_failed"),
                        "message": result.get("message", ""),
                    }
        return results

    def _publish_public_snapshots(
        self,
        submissions: dict[str, ArenaNextWeekSubmission],
        states: dict[str, dict],
    ) -> dict[str, dict]:
        snapshots = [
            self._public_snapshot_from_state(state)
            for state in states.values()
        ]
        results: dict[str, dict] = {}
        with ThreadPoolExecutor(max_workers=len(submissions)) as executor:
            future_by_company = {
                executor.submit(
                    http_post_json,
                    submission.api_port,
                    "/arena-upsert-public-snapshots",
                    {"snapshots": snapshots},
                ): company_id
                for company_id, submission in submissions.items()
            }
            for future in as_completed(future_by_company):
                company_id = future_by_company[future]
                result = future.result()
                if result.get("success"):
                    results[company_id] = result
                else:
                    results[company_id] = {
                        "success": False,
                        "error": result.get("error", "arena_public_snapshot_failed"),
                        "message": result.get("message", ""),
                    }
        return results

    def _public_snapshot_from_state(self, state: dict) -> dict:
        return {
            "day": int(state.get("day", 0)),
            "company_id": str(state["company_id"]),
            "display_name": str(state["display_name"]),
            "config": {
                key: state.get("config", {}).get(key)
                for key in (
                    "price_A",
                    "price_B",
                    "price_C",
                    "tier_A",
                    "tier_B",
                    "tier_C",
                )
            },
        }

    def _display_name(self, company_id: str) -> str:
        for spec in self.company_specs:
            if spec.company_id == company_id:
                return spec.display_name
        return company_id

    def _coordinator_port(self) -> int:
        if self._active_coordinator_port is None:
            raise RuntimeError("Arena coordinator port is not available")
        return self._active_coordinator_port

    def _price_for_plan(self, state: dict, plan: str) -> float:
        config = state.get("config", {})
        return float(config.get(f"price_{plan}", config.get("price_A", 0.0)))

    def _write_config(self, coordinator_port: int) -> None:
        config = {
            "run_id": self.run_id,
            "arena": True,
            "company_count": self.company_count,
            "companies": [
                {
                    "company_id": spec.company_id,
                    "display_name": spec.display_name,
                    "provider": self.model_specs[index].provider,
                    "model": self.model_specs[index].model,
                }
                for index, spec in enumerate(self.company_specs)
            ],
            "seed": self.seed,
            "scenario": self.scenario,
            "total_days": self.total_days,
            "initial_cash": self.initial_cash,
            "coordinator_port": coordinator_port,
            "public_dir_override": os.environ.get("NOVAMIND_PUBLIC_DIR") or None,
        }
        (self.workspace_dir / "config.json").write_text(json.dumps(config, indent=2))
