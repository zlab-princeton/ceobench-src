"""Accumulated market drift is a read-time offset, not a cohort baseline."""
import pytest
from numpy.random import default_rng
from saas_bench.config import BenchmarkConfig
from saas_bench.database import init_database
from saas_bench.simulation import Simulator


def world():
    conn = init_database(':memory:')
    cfg = BenchmarkConfig(seed=123, enable_involuntary_churn=False, base_product_quality=1.5)
    sim = Simulator(conn, cfg, default_rng(123))
    sim.initialize()
    sim._group_sub_counts = {}
    return conn, sim


def drift(conn, sim, group, sign=1):
    conn.execute('UPDATE global_drift_state SET global_q_bias_total = ? WHERE id = 1', (.4 * sign,))
    conn.execute('UPDATE group_parameters SET drift_q_bias_total = ?, drift_c_max_total = ? WHERE group_id = ?', (.2 * sign, 20 * sign, group))
    conn.commit()
    sim._cache_step_day_globals(sim.get_current_config())


@pytest.mark.parametrize('group', ['S1', 'S2', 'E1', 'E2'])
@pytest.mark.parametrize('sign', [-1, 1])
def test_same_customer_baseline_independent_of_acquisition_date(group, sign):
    a, early = world()
    b, late = world()
    early_params = early._generate_customer_from_group(group)
    drift(a, early, group, sign)
    drift(b, late, group, sign)
    late_params = late._generate_customer_from_group(group)
    fields = ('q_min', 'q_max', 'c_max')
    early_baseline = tuple(early_params[k] for k in fields)
    late_baseline = tuple(late_params[k] for k in fields)
    assert late_baseline == pytest.approx(early_baseline)
    assert late._apply_drift_offsets(group, *late_baseline) == pytest.approx(early._apply_drift_offsets(group, *early_baseline))

@pytest.mark.parametrize('eligible', [True, False])
def test_late_enterprise_reply_evaluates_market_drift_once(tmp_path, eligible):
    from saas_bench.tools import AgentTools
    conn, sim = world()
    drift(conn, sim, 'E2')
    params = sim._generate_customer_from_group('E2')
    cid = sim._create_customer(params)
    sim._create_enterprise_lead(cid, params)
    # Delivered quality lies above the one-offset threshold and below two offsets.
    from saas_bench.config import MODEL_TIERS
    conn.execute('UPDATE config_history SET quota_A=1000000, quota_B=1000000, quota_C=1000000')
    config = sim.get_current_config()
    quality = 1.6 if eligible else .01
    sim.config.base_product_quality = quality / MODEL_TIERS[config['tier_C']].quality_multiplier
    sim._customer_quality_noise[cid] = 1.0
    tools = AgentTools(conn, 0, tmp_path, rng=sim.rng, config=sim.config, seed=123)
    assert tools.send_enterprise_deal(deals=[[cid, [['C', .01, 1]]]]).success
    sim.current_day = conn.execute('SELECT MAX(next_reply_day) FROM enterprise_turns').fetchone()[0]
    sim._process_scheduled_replies(config, [])
    reply = conn.execute("SELECT message_text FROM enterprise_turns WHERE sender='customer' AND customer_id=?", (cid,)).fetchone()
    assert (reply is not None) is eligible
    if eligible:
        assert reply[0].startswith('Accepted:')
        assert conn.execute("SELECT status FROM subscriptions WHERE customer_id=?", (cid,)).fetchone()[0] == 'subscribed'
    else:
        assert conn.execute('SELECT _internal_status FROM enterprise_turns ORDER BY message_id DESC LIMIT 1').fetchone()[0] == 'timeout'


def test_first_renewal_uses_same_market_threshold_as_acquisition():
    from saas_bench.config import MODEL_TIERS
    conn, sim = world()
    drift(conn, sim, 'S2')
    params = sim._generate_customer_from_group('S2')
    cid = sim._create_customer(params)
    sim._create_subscription(cid, 'A', .01)
    config = sim.get_current_config()
    for plan in 'ABC':
        config[f'price_{plan}'] = .01
        config[f'tier_{plan}'] = config['tier_A']
        config[f'quota_{plan}'] = 100000
    sim.config.base_product_quality = 1.15 / MODEL_TIERS[config['tier_A']].quality_multiplier
    sim._customer_quality_noise[cid] = 1.0
    sim._cache_step_day_globals(config)
    conn.execute('UPDATE subscriptions SET billing_day_mod30=0, daily_usage_rate=1 WHERE customer_id=?', (cid,))
    sim.current_day = 30
    sim._process_billing_decisions(config, 0.0, False)
    assert conn.execute('SELECT status FROM subscriptions WHERE customer_id=?', (cid,)).fetchone()[0] == 'subscribed'

@pytest.mark.parametrize('sign', [-1, 1])
def test_billing_budget_snapshot_does_not_bake_in_market_drift(sign):
    conn, sim = world()
    params = sim._generate_customer_from_group('S2')
    cid = sim._create_customer(params)
    sim._create_subscription(cid, 'A', .01)
    drift(conn, sim, 'S2', sign)
    sim._process_billing(sim.get_current_config())
    snapshot = conn.execute('SELECT effective_c_max FROM subscriptions WHERE customer_id=?', (cid,)).fetchone()[0]
    assert snapshot == pytest.approx(params['c_max'])
    assert sim._apply_drift_offsets('S2', params['q_min'], params['q_max'], snapshot)[2] == pytest.approx(max(15, params['c_max'] + 20 * sign))


def test_enterprise_quality_keeps_growth_above_one_and_existing_lower_floor():
    from saas_bench.enterprise import get_quality_for_plan, get_qualities_for_all_plans_batch
    from saas_bench.config import MODEL_TIERS
    conn, sim = world()
    params = sim._generate_customer_from_group('E2')
    cid = sim._create_customer(params)
    sim._create_enterprise_lead(cid, params)
    conn.execute('UPDATE config_history SET quota_A=1000000, quota_B=1000000, quota_C=1000000')
    tier = sim.get_current_config()['tier_C']
    conn.execute('UPDATE customer_state SET relationship=.5 WHERE customer_id=?', (cid,))
    for quality, expected in [(2.5, 2.5), (-3, -1)]:
        sim.config.base_product_quality = quality / MODEL_TIERS[tier].quality_multiplier
        assert get_quality_for_plan(conn, 'C', cid, sim.config) == pytest.approx(expected)
        assert get_qualities_for_all_plans_batch(conn, [cid], sim.config)[cid]['C'] == pytest.approx(expected)


def test_batch_acquisition_stores_baseline_and_evaluates_drift_once(monkeypatch):
    conn, sim = world()
    sim.config.targeted_ad_spend = {'content_marketing': {'S2': 10000.0}}
    sim.config.base_product_quality = 100.0
    drift(conn, sim, 'S2')
    original = sim._generate_customer_from_group
    def fixed_baseline(group):
        params = original(group)
        params.update(q_min=.2, q_max=.8, c_max=400.)
        return params
    monkeypatch.setattr(sim, '_generate_customer_from_group', fixed_baseline)
    config = sim.get_current_config()
    for plan in 'ABC':
        config[f'price_{plan}'] = 1.
        config[f'quota_{plan}'] = 1000000
    sim._cache_step_day_globals(config)
    observed = []
    original_gate = sim._plan_acceptable
    def gate(sl, sr, budget, quality, price, upper, lower):
        observed.append((lower, upper, budget))
        return original_gate(sl, sr, budget, quality, price, upper, lower)
    monkeypatch.setattr(sim, '_plan_acceptable', gate)
    result = sim._generate_new_customers(config)
    assert result['new_individual_subscribers'] > 0
    rows = conn.execute("SELECT c.q_min,c.q_max,c.c_max,s.effective_c_max,c.acquisition_source FROM customers c JOIN subscriptions s USING(customer_id) WHERE c.group_id='S2'").fetchall()
    assert rows
    assert any(r['acquisition_source'] == 'content_marketing' for r in rows)
    for row in rows:
        assert tuple(row)[:4] == pytest.approx((.2, .8, 400., 400.))
    assert any(values == pytest.approx((.8, 1.4, 420.)) for values in observed)
    assert not any(values == pytest.approx((1.4, 2., 440.)) for values in observed)


def test_renewal_evaluates_the_grandfathered_price_it_will_actually_bill():
    conn, sim = world()
    cid = sim._create_customer(sim._generate_customer_from_group('S1'))
    sim._create_subscription(cid, 'A', 1.)
    config = sim.get_current_config()
    for plan in 'ABC':
        config[f'price_{plan}'] = 10000.
        config[f'quota_{plan}'] = 1000000
    sim.config.base_product_quality = 100.
    sim._cache_step_day_globals(config)
    sim.current_day = 30
    conn.execute('UPDATE subscriptions SET billing_day_mod30=0,first_billing_done=1 WHERE customer_id=?',(cid,))
    sim._process_billing_decisions(config, 0., False)
    assert conn.execute('SELECT status FROM subscriptions WHERE customer_id=?',(cid,)).fetchone()[0] == 'subscribed'
    assert sim._process_billing(config) == pytest.approx(1.)

@pytest.mark.parametrize('global_price,expected', [(.5,.5),(10000.,1.)])
def test_renewal_current_plan_price_matches_billing_and_alternatives_do_not_inherit_discount(monkeypatch, global_price, expected):
    conn, sim = world()
    cid = sim._create_customer(sim._generate_customer_from_group('S1'))
    sim._create_subscription(cid, 'A', 1.)
    config = sim.get_current_config()
    config.update(price_A=global_price, price_B=10000., price_C=20000., quota_A=1000000, quota_B=1000000, quota_C=1000000)
    sim.config.base_product_quality = 100.
    sim._cache_step_day_globals(config)
    sim.current_day=30
    conn.execute('UPDATE subscriptions SET billing_day_mod30=0, first_billing_done=1 WHERE customer_id=?',(cid,))
    observed=[]
    original=sim._plan_acceptable
    def gate(sl,sr,budget,quality,price,upper,lower):
        observed.append(price)
        return original(sl,sr,budget,quality,price,upper,lower)
    monkeypatch.setattr(sim,'_plan_acceptable',gate)
    sim._process_billing_decisions(config,0.,False)
    assert observed == pytest.approx([expected,10000.,20000.])
    assert sim._process_billing(config) == pytest.approx(expected)
