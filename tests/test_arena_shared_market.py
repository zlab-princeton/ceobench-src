"""Regression tests for CEOBench Arena shared-market primitives."""

import pytest
from numpy.random import default_rng

from saas_bench.arena import (
    ArenaCompanyMarketState,
    ArenaPlanOffer,
    CompanyExposure,
    CustomerChoiceProfile,
    SharedArrival,
    allocate_shared_arrivals,
    choose_company_plan,
    choose_evaluated_company_plan,
    choose_evaluated_company_plan_with_source,
    choose_for_shared_arrival,
    compute_group_arrival_rates,
    compute_required_quality,
    filter_offers_for_consideration_set,
    make_company_specs,
    plan_offers_from_company_config,
    sample_shared_arrivals,
)
from saas_bench.config import BenchmarkConfig
from saas_bench.database import init_database
from saas_bench.simulation import Simulator


class FakeRng:
    def __init__(self):
        self.poisson_lams = []
        self.choice_probabilities = []
        self.choice_results = [1, 0, 1]
        self.random_results = [1.0, 1.0, 1.0]

    def poisson(self, lam):
        self.poisson_lams.append(lam)
        return 3

    def choice(self, a, p=None):
        self.choice_probabilities.append(tuple(p))
        return self.choice_results.pop(0)

    def random(self):
        return self.random_results.pop(0)


def test_company_specs_are_deterministic_and_keep_novamind_for_single_company():
    assert [(spec.company_id, spec.display_name) for spec in make_company_specs(1)] == [
        ("company_0", "NovaMind")
    ]

    specs = make_company_specs(
        3,
        agent_models=["gpt-5", "claude-opus-4.1", "gemini-2.5-pro"],
    )
    assert [(spec.company_id, spec.display_name) for spec in specs] == [
        ("company_0", "NovaMind"),
        ("company_1", "AsterAI"),
        ("company_2", "LatticeWorks"),
    ]
    assert [spec.agent_model for spec in specs] == [
        "gpt-5",
        "claude-opus-4.1",
        "gemini-2.5-pro",
    ]


def test_arena_choice_matches_single_company_ceobench_plan_choice(tmp_path):
    config = BenchmarkConfig(seed=123, base_product_quality=0.5)
    conn = init_database(tmp_path / "ceobench.db")
    sim = Simulator(conn, config, default_rng(123))
    sim.initialize()
    ceobench_config = {
        "price_A": 10.0,
        "price_B": 25.0,
        "price_C": 40.0,
        "tier_A": 1,
        "tier_B": 3,
        "tier_C": 5,
    }
    profile = CustomerChoiceProfile(
        group_id="S1",
        steepness_left=0.8,
        steepness_right=1.6,
        c_max=100.0,
        q_max=0.8,
        q_min=0.2,
    )

    ceobench_plan = sim._select_best_plan(
        steepness_left=profile.steepness_left,
        steepness_right=profile.steepness_right,
        c_max=profile.c_max,
        config=ceobench_config,
        overload=0.0,
        outage=False,
        q_max=profile.q_max,
        q_min=profile.q_min,
    )
    arena_choice = choose_company_plan(
        profile,
        plan_offers_from_company_config(
            company_id="company_0",
            display_name="NovaMind",
            config=ceobench_config,
            base_product_quality=config.base_product_quality,
        ),
    )

    assert ceobench_plan == "C"
    assert arena_choice.company_id == "company_0"
    assert arena_choice.plan == ceobench_plan
    assert arena_choice.chose_product


def test_shared_market_choice_selects_best_company_plan_pair():
    profile = CustomerChoiceProfile(
        group_id="S1",
        steepness_left=0.8,
        steepness_right=1.6,
        c_max=100.0,
        q_max=0.8,
        q_min=0.2,
    )
    offers = [
        ArenaPlanOffer(
            company_id="company_0",
            display_name="NovaMind",
            plan="A",
            price=20.0,
            tier=1,
            base_product_quality=0.5,
        ),
        ArenaPlanOffer(
            company_id="company_1",
            display_name="AsterAI",
            plan="A",
            price=30.0,
            tier=5,
            base_product_quality=0.5,
        ),
    ]

    choice = choose_company_plan(profile, offers)

    assert choice.company_id == "company_1"
    assert choice.display_name == "AsterAI"
    assert choice.plan == "A"


def test_customer_can_choose_no_product():
    profile = CustomerChoiceProfile(
        group_id="S1",
        steepness_left=0.8,
        steepness_right=1.6,
        c_max=100.0,
        q_max=0.8,
        q_min=0.2,
    )
    offers = [
        ArenaPlanOffer(
            company_id="company_0",
            display_name="NovaMind",
            plan="A",
            price=101.0,
            tier=5,
            base_product_quality=1.0,
        ),
        ArenaPlanOffer(
            company_id="company_1",
            display_name="AsterAI",
            plan="A",
            price=10.0,
            tier=1,
            base_product_quality=0.1,
        ),
    ]

    choice = choose_company_plan(profile, offers)

    assert not choice.chose_product
    assert choice.company_id is None


def test_evaluated_offer_choice_uses_simulator_terms(tmp_path):
    config = BenchmarkConfig(
        seed=123,
        base_product_quality=0.6,
        default_price_A=50.0,
        default_price_B=50.0,
        default_price_C=200.0,
        default_tier_A=1,
        default_tier_B=1,
        default_tier_C=1,
        default_quota_A=1,
        default_quota_B=100_000,
        default_quota_C=100_000,
        lead_promotion_by_channel={"social_media": 40.0},
        quota_dissatisfaction_scale=0.1,
    )
    conn = init_database(tmp_path / "ceobench.db")
    sim = Simulator(conn, config, default_rng(123))
    sim.initialize()

    result = sim.arena_evaluate_lead_offers(
        {
            "customer_type": "small",
            "group_id": "S1",
            "steepness_left": 0.8,
            "steepness_right": 1.6,
            "c_max": 100.0,
            "q_max": 0.8,
            "q_min": 0.2,
            "usage_demand": 50.0,
            "seat_count": 1,
            "_lead_channel": "social_media",
            "_arena_quality_noise": 1.0,
        },
        company_id="company_0",
        display_name="NovaMind",
    )

    offers = result["offers"]
    by_plan = {offer["plan"]: offer for offer in offers}
    assert by_plan["A"]["lead_promotion"] == pytest.approx(40.0)
    assert by_plan["A"]["effective_price"] == pytest.approx(10.0)
    assert by_plan["A"]["quota_penalty"] > 0.09
    assert by_plan["B"]["quota_penalty"] == pytest.approx(0.0)

    choice = choose_evaluated_company_plan(offers)
    assert choice.chose_product
    assert choice.plan == "B"


def test_arena_insert_allocated_leads_records_hidden_allocation_log(tmp_path):
    config = BenchmarkConfig(
        seed=123,
        base_product_quality=0.6,
        default_price_A=20.0,
        default_tier_A=1,
        default_quota_A=10_000,
    )
    conn = init_database(tmp_path / "ceobench.db")
    sim = Simulator(conn, config, default_rng(123))
    sim.initialize()

    chosen_offer = {
        "company_id": "company_1",
        "display_name": "AsterAI",
        "plan": "A",
        "price": 20.0,
        "effective_price": 15.0,
        "perceived_quality": 0.61,
        "required_quality": 0.31,
        "satisfaction": 0.30,
        "acceptable": True,
    }
    result = sim.arena_insert_allocated_leads(
        [
            {
                "params": {
                    "customer_type": "small",
                    "group_id": "S1",
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
                "outcome": "subscribe",
                "plan": "A",
                "price": 20.0,
                "source_company_id": "company_0",
                "target_company_id": "company_1",
                "chosen_company_id": "company_1",
                "consideration_set": ["company_0", "company_1"],
                "chosen_offer": chosen_offer,
                "offers": [chosen_offer],
                "arena_competitive_rfp": True,
                "arena_rfp_id": "arena123:rfp:1:S1:company_0",
            }
        ],
        target_company_id="company_1",
    )

    assert result["total_leads"] == 1
    row = conn.execute(
        "SELECT * FROM _hidden_arena_allocation_log"
    ).fetchone()
    assert row["source_company_id"] == "company_0"
    assert row["target_company_id"] == "company_1"
    assert row["chosen_company_id"] == "company_1"
    assert row["outcome"] == "subscribe"
    assert row["satisfaction"] == pytest.approx(0.30)
    assert '"company_0"' in row["consideration_set_json"]
    assert '"AsterAI"' in row["chosen_offer_json"]
    assert '"arena_competitive_rfp": true' in row["chosen_offer_json"]
    assert '"arena123:rfp:1:S1:company_0"' in row["chosen_offer_json"]


def test_arena_insert_allocated_leads_records_ad_channel_rows(tmp_path):
    config = BenchmarkConfig(
        seed=123,
        base_product_quality=0.6,
        default_price_A=20.0,
        default_tier_A=1,
        default_quota_A=10_000,
        targeted_ad_spend={"social_media": {"S1": 120.0}},
        lead_promotion_by_channel={"social_media": 5.0},
    )
    conn = init_database(tmp_path / "ceobench.db")
    sim = Simulator(conn, config, default_rng(123))
    sim.initialize()

    result = sim.arena_insert_allocated_leads(
        [
            {
                "params": {
                    "customer_type": "small",
                    "group_id": "S1",
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
                "outcome": "subscribe",
                "plan": "A",
                "price": 20.0,
                "source_company_id": "company_0",
                "target_company_id": "company_0",
                "chosen_company_id": "company_0",
                "chosen_offer": {"plan": "A", "satisfaction": 0.2},
            }
        ],
        target_company_id="company_0",
    )

    assert result["total_leads"] == 1
    row = conn.execute(
        """
        SELECT channel_id, group_id, leads_generated, spend
        FROM ad_channel_leads
        """
    ).fetchone()
    assert row["channel_id"] == "social_media"
    assert row["group_id"] == "S1"
    assert row["leads_generated"] == 1
    assert row["spend"] == pytest.approx(120.0)

    sub = conn.execute(
        "SELECT promotion, effective_price FROM subscriptions WHERE status = 'subscribed'"
    ).fetchone()
    assert sub["promotion"] == pytest.approx(5.0)
    assert sub["effective_price"] == pytest.approx(15.0)


def test_arena_rival_sourced_win_does_not_apply_target_channel_promo(tmp_path):
    config = BenchmarkConfig(
        seed=123,
        default_price_A=20.0,
        default_tier_A=1,
        default_quota_A=10_000,
        targeted_ad_spend={"social_media": {"S1": 120.0}},
        lead_promotion_by_channel={"social_media": 5.0},
    )
    conn = init_database(tmp_path / "ceobench.db")
    sim = Simulator(conn, config, default_rng(123))
    sim.initialize()

    sim.arena_insert_allocated_leads(
        [
            {
                "params": {
                    "customer_type": "small",
                    "group_id": "S1",
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
                "outcome": "subscribe",
                "plan": "A",
                "price": 20.0,
                "source_company_id": "company_0",
                "target_company_id": "company_1",
                "chosen_company_id": "company_1",
                "chosen_offer": {"plan": "A", "satisfaction": 0.2},
            }
        ],
        target_company_id="company_1",
    )

    ad_row = conn.execute(
        "SELECT leads_generated, spend FROM ad_channel_leads"
    ).fetchone()
    assert ad_row["leads_generated"] == 0
    assert ad_row["spend"] == pytest.approx(120.0)

    customer = conn.execute(
        "SELECT acquisition_source FROM customers ORDER BY customer_id DESC LIMIT 1"
    ).fetchone()
    assert customer["acquisition_source"] == "arena_consideration"

    sub = conn.execute(
        "SELECT promotion, effective_price FROM subscriptions WHERE status = 'subscribed'"
    ).fetchone()
    assert sub["promotion"] == pytest.approx(0.0)
    assert sub["effective_price"] == pytest.approx(20.0)


def test_arena_public_market_snapshots_are_queryable(tmp_path):
    config = BenchmarkConfig(seed=123)
    conn = init_database(tmp_path / "ceobench.db")
    sim = Simulator(conn, config, default_rng(123))
    sim.initialize()

    result = sim.arena_upsert_public_market_snapshots(
        [
            {
                "day": 7,
                "company_id": "company_0",
                "display_name": "NovaMind",
                "config": {
                    "price_A": 10.0,
                    "price_B": 20.0,
                    "price_C": 30.0,
                    "tier_A": 1,
                    "tier_B": 2,
                    "tier_C": 3,
                    # Private company-side controls may be present in hidden
                    # coordinator state but must not be published.
                    "quota_A": 100,
                    "quota_B": 200,
                    "quota_C": 300,
                },
                "subscriber_counts_by_group": {"S1": 3, "S2": 4},
            }
        ]
    )

    assert result == {"snapshots_written": 1}
    columns = {
        row[1]
        for row in conn.execute(
            "PRAGMA table_info(arena_public_market_snapshots)"
        ).fetchall()
    }
    assert "quota_A" not in columns
    assert "quota_B" not in columns
    assert "quota_C" not in columns
    assert "public_total_subscribers" not in columns
    assert "public_subscribers_by_group_json" not in columns

    row = conn.execute(
        """
        SELECT company_id, display_name, price_A, tier_C
        FROM arena_public_market_snapshots
        """
    ).fetchone()
    assert row["company_id"] == "company_0"
    assert row["display_name"] == "NovaMind"
    assert row["price_A"] == pytest.approx(10.0)
    assert row["tier_C"] == 3


def test_required_quality_matches_current_simulator_formula(tmp_path):
    config = BenchmarkConfig(seed=123)
    conn = init_database(tmp_path / "ceobench.db")
    sim = Simulator(conn, config, default_rng(123))
    sim.initialize()
    profile = CustomerChoiceProfile(
        group_id="S1",
        steepness_left=0.8,
        steepness_right=1.6,
        c_max=100.0,
        q_max=0.8,
        q_min=0.2,
    )

    for cost in (0.0, 10.0, 50.0, 75.0, 100.0, 101.0):
        assert compute_required_quality(cost, profile) == pytest.approx(
            sim._compute_required_quality(
                cost=cost,
                steepness_left=profile.steepness_left,
                steepness_right=profile.steepness_right,
                c_max=profile.c_max,
                q_max=profile.q_max,
                q_min=profile.q_min,
            )
        )


def test_shared_arrivals_sum_company_exposure_into_one_pool():
    exposures = [
        CompanyExposure("company_0", "S1", 2.0),
        CompanyExposure("company_1", "S1", 3.0),
    ]
    rng = FakeRng()

    arrivals = sample_shared_arrivals(exposures, rng)

    assert compute_group_arrival_rates(exposures) == {"S1": pytest.approx(5.0)}
    assert rng.poisson_lams == [pytest.approx(5.0)]
    assert rng.choice_probabilities == [
        (pytest.approx(0.4), pytest.approx(0.6)),
        (pytest.approx(0.4), pytest.approx(0.6)),
        (pytest.approx(0.4), pytest.approx(0.6)),
    ]
    assert [arrival.source_company_id for arrival in arrivals] == [
        "company_1",
        "company_0",
        "company_1",
    ]
    assert [arrival.consideration_set for arrival in arrivals] == [
        ("company_1",),
        ("company_0",),
        ("company_1",),
    ]


def test_consideration_set_filters_company_plan_offers():
    config = {
        "price_A": 20.0,
        "price_B": 40.0,
        "price_C": 60.0,
        "tier_A": 1,
        "tier_B": 3,
        "tier_C": 5,
    }
    offers = []
    for company_id, display_name in (
        ("company_0", "NovaMind"),
        ("company_1", "AsterAI"),
    ):
        offers.extend(
            plan_offers_from_company_config(
                company_id=company_id,
                display_name=display_name,
                config=config,
                base_product_quality=0.5,
            )
        )

    filtered = filter_offers_for_consideration_set(offers, {"company_1"})

    assert len(filtered) == 3
    assert {offer.company_id for offer in filtered} == {"company_1"}
    assert [offer.plan for offer in filtered] == ["A", "B", "C"]


def test_source_aware_choice_requires_rival_to_clear_hurdle():
    source_offer = {
        "company_id": "company_0",
        "display_name": "NovaMind",
        "plan": "A",
        "effective_price": 20.0,
        "satisfaction": 0.10,
        "perceived_quality": 0.60,
        "required_quality": 0.50,
        "acceptable": True,
    }
    rival_offer = {
        "company_id": "company_1",
        "display_name": "AsterAI",
        "plan": "A",
        "effective_price": 20.0,
        "satisfaction": 0.12,
        "perceived_quality": 0.62,
        "required_quality": 0.50,
        "acceptable": True,
    }

    sticky_choice = choose_evaluated_company_plan_with_source(
        [source_offer, rival_offer],
        source_company_id="company_0",
        comparison_hurdle=0.03,
    )
    rival_choice = choose_evaluated_company_plan_with_source(
        [source_offer, rival_offer],
        source_company_id="company_0",
        comparison_hurdle=0.01,
    )

    assert sticky_choice.company_id == "company_0"
    assert rival_choice.company_id == "company_1"


def test_source_aware_choice_drops_hurdle_when_source_unacceptable():
    source_offer = {
        "company_id": "company_0",
        "display_name": "NovaMind",
        "plan": "A",
        "effective_price": 20.0,
        "satisfaction": -0.01,
        "perceived_quality": 0.49,
        "required_quality": 0.50,
        "acceptable": False,
    }
    rival_offer = {
        "company_id": "company_1",
        "display_name": "AsterAI",
        "plan": "A",
        "effective_price": 20.0,
        "satisfaction": 0.02,
        "perceived_quality": 0.52,
        "required_quality": 0.50,
        "acceptable": True,
    }

    choice = choose_evaluated_company_plan_with_source(
        [source_offer, rival_offer],
        source_company_id="company_0",
        comparison_hurdle=0.10,
    )

    assert choice.company_id == "company_1"


def test_source_aware_choice_can_still_choose_no_product():
    choice = choose_evaluated_company_plan_with_source(
        [
            {
                "company_id": "company_0",
                "display_name": "NovaMind",
                "plan": "A",
                "effective_price": 20.0,
                "satisfaction": -0.01,
                "perceived_quality": 0.49,
                "required_quality": 0.50,
                "acceptable": False,
            }
        ],
        source_company_id="company_0",
        comparison_hurdle=0.0,
    )

    assert not choice.chose_product


def test_company_market_state_adapts_ceobench_abc_config_for_group():
    state = ArenaCompanyMarketState(
        company_id="company_1",
        display_name="AsterAI",
        config={
            "price_A": 20.0,
            "price_B": 40.0,
            "price_C": 60.0,
            "tier_A": 1,
            "tier_B": 3,
            "tier_C": 5,
        },
        base_product_quality=0.5,
        q_shared_bonus=0.02,
        q_group_bonuses={"S1": 0.03},
        lead_promotions_by_group={"S1": 5.0},
    )

    offers = state.offers_for_group("S1")

    assert [offer.plan for offer in offers] == ["A", "B", "C"]
    assert {offer.company_id for offer in offers} == {"company_1"}
    assert offers[0].effective_price == pytest.approx(15.0)
    assert offers[0].delivered_quality == pytest.approx((0.5 + 0.02 + 0.03) * 0.6)


def test_shared_arrival_allocates_to_best_considered_company_plan():
    profile = CustomerChoiceProfile(
        group_id="S1",
        steepness_left=0.8,
        steepness_right=1.6,
        c_max=100.0,
        q_max=0.8,
        q_min=0.2,
    )
    states = [
        ArenaCompanyMarketState(
            company_id="company_0",
            display_name="NovaMind",
            config={
                "price_A": 20.0,
                "price_B": 40.0,
                "price_C": 60.0,
                "tier_A": 1,
                "tier_B": 1,
                "tier_C": 1,
            },
            base_product_quality=0.5,
        ),
        ArenaCompanyMarketState(
            company_id="company_1",
            display_name="AsterAI",
            config={
                "price_A": 30.0,
                "price_B": 50.0,
                "price_C": 70.0,
                "tier_A": 5,
                "tier_B": 5,
                "tier_C": 5,
            },
            base_product_quality=0.5,
        ),
    ]
    arrival = SharedArrival(
        group_id="S1",
        source_company_id="company_0",
        consideration_set=("company_0", "company_1"),
    )

    choice = choose_for_shared_arrival(arrival, profile, states)

    assert choice.company_id == "company_1"
    assert choice.display_name == "AsterAI"
    assert choice.plan == "A"


def test_shared_arrival_respects_consideration_set_and_no_product_choice():
    profile = CustomerChoiceProfile(
        group_id="S1",
        steepness_left=0.8,
        steepness_right=1.6,
        c_max=100.0,
        q_max=0.8,
        q_min=0.2,
    )
    states = [
        ArenaCompanyMarketState(
            company_id="company_0",
            display_name="NovaMind",
            config={
                "price_A": 20.0,
                "price_B": 40.0,
                "price_C": 60.0,
                "tier_A": 1,
                "tier_B": 1,
                "tier_C": 1,
            },
            base_product_quality=0.1,
        ),
        ArenaCompanyMarketState(
            company_id="company_1",
            display_name="AsterAI",
            config={
                "price_A": 30.0,
                "price_B": 50.0,
                "price_C": 70.0,
                "tier_A": 5,
                "tier_B": 5,
                "tier_C": 5,
            },
            base_product_quality=0.8,
        ),
    ]
    arrival = SharedArrival(
        group_id="S1",
        source_company_id="company_0",
        consideration_set=("company_0",),
    )

    allocations = allocate_shared_arrivals(
        [arrival],
        profiles_by_group={"S1": profile},
        company_states=states,
    )

    assert len(allocations) == 1
    assert allocations[0].arrival == arrival
    assert not allocations[0].choice.chose_product


def test_arena_money_transfer_updates_ledger_idempotently(tmp_path):
    config = BenchmarkConfig(seed=321)
    conn = init_database(tmp_path / "ceobench.db")
    sim = Simulator(conn, config, default_rng(321))
    sim.initialize()

    out = sim.arena_apply_money_transfer(
        transfer_id="transfer_000001",
        direction="out",
        counterparty_company_id="company_1",
        amount=2500,
        day=7,
        memo="shared research reimbursement",
    )
    duplicate = sim.arena_apply_money_transfer(
        transfer_id="transfer_000001",
        direction="out",
        counterparty_company_id="company_1",
        amount=2500,
        day=7,
        memo="shared research reimbursement",
    )
    incoming = sim.arena_apply_money_transfer(
        transfer_id="transfer_000002",
        direction="in",
        counterparty_company_id="company_1",
        amount=1000,
        day=7,
        memo="comarketing advance",
    )

    assert out == {
        "success": True,
        "transfer_id": "transfer_000001",
        "applied": True,
    }
    assert duplicate == {
        "success": True,
        "transfer_id": "transfer_000001",
        "applied": False,
    }
    assert incoming == {
        "success": True,
        "transfer_id": "transfer_000002",
        "applied": True,
    }
    rows = conn.execute(
        """
        SELECT category, amount
        FROM ledger
        WHERE category LIKE 'arena_transfer_%'
        ORDER BY category
        """
    ).fetchall()
    assert [(row["category"], row["amount"]) for row in rows] == [
        ("arena_transfer_in", 1000.0),
        ("arena_transfer_out", -2500.0),
    ]
    assert conn.execute(
        "SELECT COUNT(*) FROM _hidden_arena_money_transfer_applications"
    ).fetchone()[0] == 2


def test_arena_research_share_moves_recipient_one_info_level(tmp_path):
    config = BenchmarkConfig(seed=654)
    conn = init_database(tmp_path / "ceobench.db")
    sim = Simulator(conn, config, default_rng(654))
    sim.initialize()

    before = conn.execute(
        "SELECT info_level FROM group_info_levels WHERE group_id = 'D_S01'"
    ).fetchone()["info_level"]
    result = sim.arena_apply_research_share(
        share_id="research_000001",
        sender_company_id="company_0",
        group_id="D_S01",
        source_info_level=4,
        day=14,
        memo="public segment report",
    )
    duplicate = sim.arena_apply_research_share(
        share_id="research_000001",
        sender_company_id="company_0",
        group_id="D_S01",
        source_info_level=4,
        day=14,
        memo="public segment report",
    )

    assert before == 0
    assert result == {
        "success": True,
        "share_id": "research_000001",
        "applied": True,
        "old_info_level": 0,
        "new_info_level": 1,
    }
    assert duplicate == {
        "success": True,
        "share_id": "research_000001",
        "applied": False,
        "old_info_level": 0,
        "new_info_level": 1,
    }
    assert conn.execute(
        "SELECT info_level FROM group_info_levels WHERE group_id = 'D_S01'"
    ).fetchone()["info_level"] == 1
    assert conn.execute(
        "SELECT COUNT(*) FROM _hidden_arena_research_share_applications"
    ).fetchone()[0] == 1


def test_arena_switching_candidate_insert_and_cancel(tmp_path):
    config = BenchmarkConfig(seed=987)
    conn = init_database(tmp_path / "ceobench.db")
    sim = Simulator(conn, config, default_rng(987))
    sim.initialize()
    params = sim._generate_customer_from_group("S1")
    params["_lead_channel"] = None
    customer_id = sim._create_customer(params)
    sim._create_subscription(customer_id, "A", 12.0)
    conn.commit()

    candidates = sim.arena_switching_candidates(company_id="company_0")["candidates"]
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate["source_customer_id"] == customer_id
    assert candidate["switching_hurdle"] >= params.get("contract_lockin_penalty", 0.100)

    switch_spec = {
        **candidate,
        "target_company_id": "company_1",
        "chosen_offer": {
            "plan": "B",
            "price": 30.0,
            "satisfaction": 0.5,
        },
    }
    inserted = sim.arena_insert_switched_customer(switch_spec)
    cancelled = sim.arena_cancel_switched_customer(switch_spec)

    assert inserted["success"]
    assert inserted["applied"]
    assert cancelled == {"success": True, "applied": True}
    assert conn.execute(
        """
        SELECT status, churn_reason
        FROM subscriptions
        WHERE subscription_id = ?
        """,
        (candidate["source_subscription_id"],),
    ).fetchone()["churn_reason"] == "competitive_switch"
    assert conn.execute(
        "SELECT COUNT(*) FROM _hidden_arena_switching_log"
    ).fetchone()[0] == 1
