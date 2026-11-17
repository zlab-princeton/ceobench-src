"""Regression tests for CEOBench customer choice and acceptance rules."""

import math

import pytest

from saas_bench.config import BenchmarkConfig, compute_quota_quality_factor


def test_quality_price_curve_and_acceptance_rule(make_initialized_sim):
    _conn, sim, _config = make_initialized_sim()

    curve_kwargs = {
        "steepness_left": 0.8,
        "steepness_right": 1.6,
        "c_max": 100.0,
        "q_max": 0.8,
        "q_min": 0.2,
    }
    required_at_free = sim._compute_required_quality(cost=0.0, **curve_kwargs)
    required_at_mid = sim._compute_required_quality(cost=50.0, **curve_kwargs)
    required_at_budget = sim._compute_required_quality(cost=100.0, **curve_kwargs)
    required_above_budget = sim._compute_required_quality(cost=101.0, **curve_kwargs)

    assert 0.2 <= required_at_free < required_at_mid < required_at_budget <= 0.8
    assert required_above_budget == pytest.approx(0.8)

    affordable_required_quality = sim._compute_required_quality(
        cost=75.0, **curve_kwargs
    )
    assert sim._plan_acceptable(
        quality=affordable_required_quality, cost=75.0, **curve_kwargs
    )
    assert not sim._plan_acceptable(
        quality=math.nextafter(affordable_required_quality, 0.0),
        cost=75.0,
        **curve_kwargs,
    )
    assert not sim._plan_acceptable(quality=1.0, cost=101.0, **curve_kwargs)

    assert sim._compute_satisfaction(
        quality=affordable_required_quality + 0.05,
        cost=75.0,
        **curve_kwargs,
    ) == pytest.approx(0.05)


def test_new_customer_selects_best_plan_or_no_plan(make_initialized_sim):
    config = BenchmarkConfig(seed=123, base_product_quality=0.5)
    _conn, sim, _config = make_initialized_sim(config=config, seed=123)

    plan_config = {
        "price_A": 10.0,
        "price_B": 25.0,
        "price_C": 40.0,
        "tier_A": 1,
        "tier_B": 3,
        "tier_C": 5,
    }

    assert (
        sim._select_best_plan(
            steepness_left=0.8,
            steepness_right=1.6,
            c_max=100.0,
            config=plan_config,
            overload=0.0,
            outage=False,
            q_max=0.8,
            q_min=0.2,
        )
        == "C"
    )

    unaffordable_config = {
        "price_A": 101.0,
        "price_B": 125.0,
        "price_C": 140.0,
        "tier_A": 1,
        "tier_B": 3,
        "tier_C": 5,
    }

    assert (
        sim._select_best_plan(
            steepness_left=0.8,
            steepness_right=1.6,
            c_max=100.0,
            config=unaffordable_config,
            overload=0.0,
            outage=False,
            q_max=0.8,
            q_min=0.2,
        )
        is None
    )


def _make_plan_config(quota_a):
    return {
        "tier_A": 4,
        "tier_B": 4,
        "tier_C": 4,
        "quota_A": quota_a,
        "quota_B": 1_000,
        "quota_C": 1_000,
    }


def _create_subscribed_customer(conn, sim):
    customer_id = sim._create_customer(sim._generate_customer_from_group("S1"))
    sim._create_subscription(customer_id, "A", 10.0)
    conn.execute(
        """
        UPDATE subscriptions
        SET daily_usage_rate = 10.0, listed_price = 10.0, effective_price = 10.0
        WHERE customer_id = ?
        """,
        (customer_id,),
    )
    conn.execute(
        """
        UPDATE customer_state
        SET satisfaction = 0.0, relationship = 0.5, open_issue_days = 0
        WHERE customer_id = ?
        """,
        (customer_id,),
    )
    conn.commit()
    return customer_id


def test_quota_quality_factor_boundaries():
    assert compute_quota_quality_factor(0, 300) == pytest.approx(0.0)
    assert compute_quota_quality_factor(150, 300) == pytest.approx(0.5)
    assert compute_quota_quality_factor(300, 300) == pytest.approx(1.0)
    assert compute_quota_quality_factor(500, 300) == pytest.approx(1.0)
    assert compute_quota_quality_factor(0, 0) == pytest.approx(1.0)
    assert compute_quota_quality_factor(-50, 300) == pytest.approx(0.0)


def test_quota_factor_multiplies_scalar_perceived_quality(make_initialized_sim):
    config = BenchmarkConfig(seed=123, base_product_quality=0.6)
    conn, sim, _config = make_initialized_sim(config=config, seed=123)
    customer_id = _create_subscribed_customer(conn, sim)

    full_quota_config = _make_plan_config(10)
    half_quota_config = _make_plan_config(5)
    zero_quota_config = _make_plan_config(0)

    sim._cache_step_day_globals(full_quota_config)
    assert sim._compute_comprehensive_quality(
        customer_id, "A", full_quota_config, overload=0.0, outage=False
    ) == pytest.approx(0.6)

    sim._cache_step_day_globals(half_quota_config)
    assert sim._compute_comprehensive_quality(
        customer_id, "A", half_quota_config, overload=0.0, outage=False
    ) == pytest.approx(0.3)

    sim._cache_step_day_globals(zero_quota_config)
    assert sim._compute_comprehensive_quality(
        customer_id, "A", zero_quota_config, overload=0.0, outage=False
    ) == pytest.approx(0.0)


def test_enterprise_plan_change_uses_weekly_quota_multiplier(
    make_initialized_sim, monkeypatch
):
    config = BenchmarkConfig(seed=123, base_product_quality=0.6)
    _conn, sim, _config = make_initialized_sim(config=config, seed=123)
    plan_config = {
        "price_A": 10.0,
        "price_B": 25.0,
        "price_C": 40.0,
        "tier_A": 4,
        "tier_B": 4,
        "tier_C": 4,
        "quota_A": 10,
        "quota_B": 20,
        "quota_C": 30,
    }
    sim._cache_step_day_globals(plan_config)
    monkeypatch.setattr(sim, "_create_plan_change_thread", lambda *args: None)

    enterprise = {
        "customer_id": 1,
        "seat_count": 1,
        "plan": "A",
        "listed_price": 10.0,
        "relationship": 0.5,
        "start_day": 0,
        "open_issue_days": 0,
        "ads_quality_sensitivity": 0.0,
        "group_id": "E1",
        "daily_usage_rate": 10.0,
    }

    sim._check_plan_change_opportunity(
        customer_id=1,
        ent=enterprise,
        current_quality=0.6,
        steepness_left=0.8,
        steepness_right=1.6,
        c_max=100.0,
        config=plan_config,
        q_max=0.8,
        q_min=0.2,
    )


def test_quota_factor_drives_daily_quota_event(make_initialized_sim):
    config = BenchmarkConfig(
        seed=123,
        base_product_quality=0.6,
        satisfaction_ema_alpha=1.0,
    )
    conn, sim, _config = make_initialized_sim(config=config, seed=123)
    customer_id = _create_subscribed_customer(conn, sim)

    half_quota_config = _make_plan_config(5)
    sim._cache_step_day_globals(half_quota_config)
    events = sim._update_customer_satisfaction(half_quota_config, overload=0.0, outage=False)

    assert "quota" in events[customer_id]["events"]
    assert events[customer_id]["penalties"]["quota"] == pytest.approx(0.5)

    full_quota_config = _make_plan_config(10)
    conn.execute(
        "UPDATE customer_state SET satisfaction = 0.0 WHERE customer_id = ?",
        (customer_id,),
    )
    conn.commit()
    sim._cache_step_day_globals(full_quota_config)
    events = sim._update_customer_satisfaction(full_quota_config, overload=0.0, outage=False)

    assert "quota" not in events[customer_id]["events"]
    assert events[customer_id]["penalties"]["quota"] == pytest.approx(0.0)


def test_daily_usage_is_capped_by_daily_quota_not_billing_period_remaining(
    make_initialized_sim,
):
    config = BenchmarkConfig(seed=123, base_product_quality=0.6)
    conn, sim, _config = make_initialized_sim(config=config, seed=123)
    customer_id = _create_subscribed_customer(conn, sim)
    conn.execute(
        """
        UPDATE subscriptions
        SET daily_usage_rate = 10.0, billing_period_usage = 90.0
        WHERE customer_id = ?
        """,
        (customer_id,),
    )
    conn.commit()

    total_usage, usage_per_plan = sim._compute_usage(_make_plan_config(5))

    assert total_usage == 5
    assert usage_per_plan["A"] == 5
    assert conn.execute(
        "SELECT usage_units FROM daily_usage WHERE customer_id = ? AND day = ?",
        (customer_id, sim.current_day),
    ).fetchone()["usage_units"] == 5
    assert conn.execute(
        "SELECT billing_period_usage FROM subscriptions WHERE customer_id = ?",
        (customer_id,),
    ).fetchone()["billing_period_usage"] == pytest.approx(95.0)


def test_new_customer_plan_choice_uses_daily_quota_factor(make_initialized_sim):
    config = BenchmarkConfig(seed=123, base_product_quality=0.6)
    _conn, sim, _config = make_initialized_sim(config=config, seed=123)

    params = {
        "group_id": "S1",
        "usage_demand": 10.0,
        "seat_count": 1,
        "steepness_left": 0.8,
        "steepness_right": 1.6,
        "c_max": 100.0,
        "q_max": 0.8,
        "q_min": 0.2,
    }
    plan_config = {
        "price_A": 10.0,
        "price_B": 10.0,
        "price_C": 10.0,
        "tier_A": 4,
        "tier_B": 4,
        "tier_C": 5,
        "quota_A": 10,
        "quota_B": 0,
        "quota_C": 0,
    }
    sim._cache_step_day_globals(plan_config)

    assert sim._choose_plan_for_customer_curve(params, plan_config) == "A"

    plan_config["quota_A"] = 0
    plan_config["quota_C"] = 10
    sim._cache_step_day_globals(plan_config)

    assert sim._choose_plan_for_customer_curve(params, plan_config) == "C"


def test_first_billing_uses_stored_channel_lead_promotion(make_initialized_sim):
    config = BenchmarkConfig(
        seed=123,
        lead_promotion_by_channel_group={"content_marketing": {"S1": 90.0}},
    )
    conn, sim, _config = make_initialized_sim(config=config, seed=123)

    customer_id = sim._create_customer(
        {
            **sim._generate_customer_from_group("S1"),
            "acquisition_source": "content_marketing",
        }
    )
    conn.execute(
        """
        INSERT INTO subscriptions (
            customer_id, plan, listed_price, promotion, effective_price,
            effective_c_max, seat_count, start_day, status, billing_day_mod30,
            daily_usage_rate, billing_period_usage, first_billing_done
        ) VALUES (?, 'A', 100.0, 90.0, 10.0, 100.0, 1, ?, 'subscribed', ?, 10.0, 0, 0)
        """,
        (customer_id, sim.current_day, sim.current_day % 30),
    )
    conn.commit()

    payments = sim._process_billing({"price_A": 100.0, "tier_A": 4})

    assert payments == pytest.approx(10.0)
    assert conn.execute(
        """
        SELECT amount FROM ledger
        WHERE category = 'subscription_payment' AND note = ?
        """,
        (f"Subscription payment from customer {customer_id}",),
    ).fetchone()["amount"] == pytest.approx(10.0)
    sub = conn.execute(
        """
        SELECT promotion, effective_price, first_billing_done
        FROM subscriptions
        WHERE customer_id = ?
        """,
        (customer_id,),
    ).fetchone()
    assert sub["promotion"] == pytest.approx(0.0)
    assert sub["effective_price"] == pytest.approx(100.0)
    assert sub["first_billing_done"] == 1


def test_create_subscription_snapshots_channel_lead_promotion(make_initialized_sim):
    config = BenchmarkConfig(
        seed=123,
        lead_promotion_by_channel_group={"content_marketing": {"S1": 90.0}},
    )
    conn, sim, _config = make_initialized_sim(config=config, seed=123)
    customer_id = sim._create_customer(
        {
            **sim._generate_customer_from_group("S1"),
            "acquisition_source": "content_marketing",
        }
    )

    sim._create_subscription(
        customer_id,
        "A",
        100.0,
        lead_channel="content_marketing",
    )
    conn.commit()

    sub = conn.execute(
        """
        SELECT listed_price, promotion, effective_price, first_billing_done
        FROM subscriptions
        WHERE customer_id = ?
        """,
        (customer_id,),
    ).fetchone()
    assert sub["listed_price"] == pytest.approx(100.0)
    assert sub["promotion"] == pytest.approx(90.0)
    assert sub["effective_price"] == pytest.approx(10.0)
    assert sub["first_billing_done"] == 0

    payments = sim._process_billing({"price_A": 100.0, "tier_A": 4})

    assert payments == pytest.approx(10.0)


def test_generated_paid_channel_leads_bill_at_promoted_price(
    make_initialized_sim, monkeypatch,
):
    config = BenchmarkConfig(
        seed=123,
        base_product_quality=1.0,
        lead_acquisition_cost=0.0,
        lead_promotion_by_channel_group={"content_marketing": {"S1": 999_999.0}},
    )
    config.targeted_ad_spend = {"content_marketing": {"S1": 10_000.0}}
    conn, sim, _config = make_initialized_sim(config=config, seed=123)

    def fixed_customer(group_id):
        return {
            "customer_type": "small",
            "group_id": group_id,
            "steepness_left": 0.8,
            "steepness_right": 1.6,
            "c_max": 10.0,
            "q_max": 0.8,
            "q_min": 0.2,
            "usage_demand": 10.0,
            "quality_sensitivity": 1.0,
            "price_sensitivity": 1.0,
            "willingness_to_pay": 10.0,
            "usage_scale": 10.0,
            "patience": 1.0,
            "seat_count": 1,
            "ads_quality_sensitivity": 0.1,
            "ads_return_sensitivity": 0.15,
        }

    monkeypatch.setattr(sim, "_generate_customer_from_group", fixed_customer)
    plan_config = {
        "price_A": 1_000_000.0,
        "price_B": 1_000_000.0,
        "price_C": 1_000_000.0,
        "tier_A": 5,
        "tier_B": 5,
        "tier_C": 5,
        "quota_A": 1_000,
        "quota_B": 1_000,
        "quota_C": 1_000,
        "capacity_tier": 1,
    }
    sim._cache_step_day_globals(plan_config)

    generated = sim._generate_new_customers(plan_config)

    assert generated["new_individual_subscribers"] > 0
    sub = conn.execute(
        """
        SELECT COUNT(*) AS n, AVG(listed_price) AS avg_listed,
               AVG(promotion) AS avg_promo, AVG(effective_price) AS avg_effective
        FROM subscriptions
        WHERE status = 'subscribed'
        """
    ).fetchone()
    assert sub["n"] == generated["new_individual_subscribers"]
    assert sub["avg_listed"] == pytest.approx(1_000_000.0)
    assert sub["avg_promo"] == pytest.approx(999_999.0)
    assert sub["avg_effective"] == pytest.approx(1.0)

    payments = sim._process_billing(plan_config)

    assert payments == pytest.approx(generated["new_individual_subscribers"])
    ledger = conn.execute(
        """
        SELECT COUNT(*) AS n, AVG(amount) AS avg_amount
        FROM ledger
        WHERE category = 'subscription_payment'
        """
    ).fetchone()
    assert ledger["n"] == generated["new_individual_subscribers"]
    assert ledger["avg_amount"] == pytest.approx(1.0)


def test_same_plan_renewal_billing_uses_decreased_evaluated_price(
    make_initialized_sim, monkeypatch,
):
    config = BenchmarkConfig(seed=123, promotion_global=2.0)
    conn, sim, _config = make_initialized_sim(config=config, seed=123)
    customer_id = _create_subscribed_customer(conn, sim)
    conn.execute(
        """
        UPDATE subscriptions
        SET listed_price = 100.0, promotion = 2.0, effective_price = 98.0,
            first_billing_done = 1
        WHERE customer_id = ?
        """,
        (customer_id,),
    )
    conn.commit()

    plan_config = {"price_A": 10.0, "tier_A": 4}
    monkeypatch.setattr(sim, "_get_involuntary_churn_mu", lambda _group_id: 0.0)
    monkeypatch.setattr(
        sim, "_select_best_plan_inline", lambda *_args, **_kwargs: "A"
    )

    sim._process_billing_decisions(plan_config, overload=0.0, outage=False)
    payments = sim._process_billing(plan_config)

    assert payments == pytest.approx(8.0)
    sub = conn.execute(
        """
        SELECT listed_price, promotion, effective_price
        FROM subscriptions
        WHERE customer_id = ?
        """,
        (customer_id,),
    ).fetchone()
    assert sub["listed_price"] == pytest.approx(100.0)
    assert sub["promotion"] == pytest.approx(2.0)
    assert sub["effective_price"] == pytest.approx(8.0)


def test_same_plan_renewal_billing_preserves_stored_price_when_evaluated_is_higher(
    make_initialized_sim, monkeypatch,
):
    conn, sim, _config = make_initialized_sim(seed=123)
    customer_id = _create_subscribed_customer(conn, sim)
    conn.execute(
        """
        UPDATE subscriptions
        SET listed_price = 10.0, promotion = 0.0, effective_price = 10.0,
            first_billing_done = 1
        WHERE customer_id = ?
        """,
        (customer_id,),
    )
    conn.commit()

    plan_config = {"price_A": 100.0, "tier_A": 4}
    monkeypatch.setattr(sim, "_get_involuntary_churn_mu", lambda _group_id: 0.0)
    monkeypatch.setattr(
        sim, "_select_best_plan_inline", lambda *_args, **_kwargs: "A"
    )

    sim._process_billing_decisions(plan_config, overload=0.0, outage=False)
    payments = sim._process_billing(plan_config)

    assert payments == pytest.approx(10.0)
    sub = conn.execute(
        """
        SELECT listed_price, effective_price
        FROM subscriptions
        WHERE customer_id = ?
        """,
        (customer_id,),
    ).fetchone()
    assert sub["listed_price"] == pytest.approx(10.0)
    assert sub["effective_price"] == pytest.approx(10.0)


def test_enterprise_refuses_offers_above_budget_at_any_quality():
    from saas_bench.enterprise import NegotiationState, evaluate_offerings

    state = NegotiationState(
        thread_id=1, customer_id=1, thread_type='new_lead', state='open', negotiation_turn=1,
        current_offer_price=None, next_reply_day=None, steepness_left=0.8, steepness_right=1.6,
        c_max=20.0, q_max=0.8, q_min=0.2, negotiation_rate=0.1, initial_offer_factor=0.75,
        reply_delay_mean=1.0, reply_delay_std=0.0, max_negotiation_turns=5, seat_count=10,
        relationship=0.5, current_plan=None, current_price=None,
    )
    config = BenchmarkConfig()
    for price in (100.0, 1e250):
        evaluation = evaluate_offerings(
            state, [{'plan': 'C', 'price': price, 'contract_months': 1}], {'C': 2.0}, config
        )
        assert evaluation.decision != 'accept'
        if evaluation.decision == 'counter':
            assert evaluation.counter_offer_price <= state.c_max

    within_budget = evaluate_offerings(
        state, [{'plan': 'C', 'price': 15.0, 'contract_months': 1}], {'C': 2.0}, config
    )
    assert within_budget.decision == 'accept'
