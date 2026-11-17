import json

import pytest

from saas_bench.api_server import NovaMindAPIServer
from saas_bench.config import BenchmarkConfig
from saas_bench.database import close_enterprise_thread, get_cash
from saas_bench.enterprise import add_customer_message, create_negotiation_thread, get_negotiation_state


def make_lead(conn, sim):
    params = sim._generate_customer_from_group('E1')
    customer = sim._create_customer(params)
    sim._create_enterprise_lead(customer, params)
    thread = conn.execute('SELECT thread_id FROM enterprise_turns WHERE customer_id=?', (customer,)).fetchone()[0]
    return customer, thread


@pytest.mark.parametrize('sign_day,first_days', [(3, 27), (30, 30)])
def test_enterprise_first_invoice_and_live_retention(make_initialized_sim, sign_day, first_days):
    conn, sim, config = make_initialized_sim()
    customer, thread = make_lead(conn, sim)
    state = get_negotiation_state(conn, thread)
    sim.current_day = sign_day
    config.promotion_global = 9999
    sim._finalize_deal(thread, state, 30, {'plan': 'C', 'price_per_seat': 30})
    terms = sim.get_current_config()
    assert sim._process_billing(terms) == pytest.approx(first_days * state.seat_count)
    sim.current_day = sign_day + first_days
    thread, _ = create_negotiation_thread(conn, customer, 'churn_prevention', sim.current_day, 'churn_risk')
    assert sim._process_billing(terms) == 0
    close_enterprise_thread(conn, thread, 'accepted')
    assert sim._process_billing(terms) == pytest.approx(30 * state.seat_count)


@pytest.mark.parametrize('start,horizon', [(0, 1), (497, 500)])
def test_final_week_stops_before_any_extra_shock_or_action(make_initialized_sim, make_agent_tools, start, horizon):
    conn, sim, config = make_initialized_sim(config=BenchmarkConfig(total_days=horizon))
    sim.current_day = start
    tools = make_agent_tools(conn, config, day=start)
    days = []

    class Shocks:
        def get_inbox_items(self, day):
            return []

        def check_and_generate_shocks(self, day):
            days.append(day)
            return []

    api = NovaMindAPIServer(tools, sim, conn, shock_manager=Shocks())
    result = api.advance_week()
    assert (result['day'], result['game_over']) == (horizon, 'horizon')
    cash = get_cash(conn)
    assert not api.advance_week()['success']
    assert not api.execute_tool('set_daily_spend', {'development': 4}).success
    with pytest.raises(ValueError):
        sim.step_day()
    assert get_cash(conn) == cash
    assert days == list(range(start + 1, horizon + 1))


@pytest.mark.parametrize('invalid', [float('nan'), float('inf'), -1, 10**400])
def test_spend_and_promotion_rejection_are_atomic(make_initialized_sim, make_agent_tools, invalid):
    conn, sim, config = make_initialized_sim()
    tools = make_agent_tools(conn, config)
    assert not tools.set_targeted_dev_spend({'S1': 200, 'E2': invalid}).success
    assert not config.targeted_dev_spend
    assert not tools.set_promotion(global_promotion=100, by_group={'S1': invalid}).success
    assert config.promotion_global == 0
    assert tools.set_targeted_dev_spend({'S1': 200, 'E2': 100}).success
    sim.step_day()
    assert conn.execute("SELECT SUM(amount) FROM ledger WHERE category LIKE '%dev%'").fetchone()[0] == -300


def test_enterprise_offer_is_immutable_until_reply(make_initialized_sim, make_agent_tools):
    conn, sim, config = make_initialized_sim()
    tools = make_agent_tools(conn, config)
    customer, thread = make_lead(conn, sim)
    offer = [customer, [['A', 10]]]
    assert not tools.send_enterprise_deal([offer, offer]).success
    assert tools.send_enterprise_deal([offer]).success
    assert not tools.send_enterprise_deal([[customer, [['C', 30]]]]).success
    assert not tools.reject_enterprise_deal([customer]).success
    row = conn.execute('SELECT offer_json FROM enterprise_turns WHERE thread_id=? ORDER BY message_id DESC LIMIT 1', (thread,)).fetchone()
    assert json.loads(row[0])[0]['price_per_seat'] == 10
    add_customer_message(conn, thread, 1, 'Counter offer')
    assert tools.reject_enterprise_deal([customer]).success
    assert conn.execute('SELECT status FROM subscriptions WHERE customer_id=?', (customer,)).fetchone()[0] == 'lost'

def test_retention_preempts_seller_on_billing_day(make_initialized_sim, make_agent_tools, monkeypatch):
    conn, sim, config = make_initialized_sim()
    customer, thread = make_lead(conn, sim)
    sim._finalize_deal(thread, get_negotiation_state(conn, thread), 30, {'plan': 'C', 'price_per_seat': 30})
    sim.current_day = 30
    tools = make_agent_tools(conn, config, day=30)
    assert tools.send_enterprise_deal([[customer, [['C', 40]]]]).success
    terms = sim.get_current_config()
    sim._cache_step_day_globals(terms)
    monkeypatch.setattr(sim, '_get_involuntary_churn_mu', lambda group: 0)
    sim._check_negotiation_triggers(terms)
    assert sim._live_enterprise_threads()[customer][1] == 'churn_prevention'
    assert sim._process_billing(terms) == 0
    assert tools.reject_enterprise_deal([customer]).success
    assert not sim._live_enterprise_threads()
    assert sim._process_billing(terms) == 0


def test_prospect_service_penalty_and_seat_quota(make_initialized_sim):
    _, sim, config = make_initialized_sim()
    params = {'group_id': 'E1', 'usage_demand': 10, 'seat_count': 100, 'ads_quality_sensitivity': 0.5}
    assert sim._prospect_quality(params, 1, 10) == 1
    assert sim._prospect_quality(params, 1, 5) == 0.5
    sim._today_overload = 0.5
    assert sim._prospect_quality(params, 1, 10) == 1 - 0.5 * config.service_overload_weight
    config.ads_strength_by_group = {'E1': 1.0}
    assert sim._prospect_quality(params, 1, 10) == pytest.approx(0.5 - 0.5 * config.service_overload_weight)
