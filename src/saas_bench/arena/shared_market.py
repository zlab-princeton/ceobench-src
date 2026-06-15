"""Shared-market customer allocation for CEOBench Arena."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable, Mapping, Protocol, Sequence

from saas_bench.config import MODEL_TIERS


class ArenaRng(Protocol):
    """RNG methods used by shared-market sampling."""

    def poisson(self, lam: float) -> int:
        ...

    def choice(self, a, p=None):
        ...

    def random(self) -> float:
        ...


@dataclass(frozen=True)
class CustomerChoiceProfile:
    """Customer-side quality-price curve parameters."""

    group_id: str
    steepness_left: float
    steepness_right: float
    c_max: float
    q_max: float = 0.75
    q_min: float = 0.25


@dataclass(frozen=True)
class ArenaPlanOffer:
    """One company-plan alternative in a shared customer choice set."""

    company_id: str
    display_name: str
    plan: str
    price: float
    tier: int
    base_product_quality: float
    q_shared_bonus: float = 0.0
    q_group_bonus: float = 0.0
    lead_promotion: float = 0.0
    perceived_quality_adjustment: float = 0.0

    @property
    def effective_price(self) -> float:
        return max(0.0, self.price - self.lead_promotion)

    @property
    def delivered_quality(self) -> float:
        tier_config = MODEL_TIERS[self.tier]
        product_quality = (
            self.base_product_quality + self.q_shared_bonus + self.q_group_bonus
        )
        return product_quality * tier_config.quality_multiplier

    @property
    def perceived_quality(self) -> float:
        return self.delivered_quality + self.perceived_quality_adjustment


@dataclass(frozen=True)
class ArenaCompanyMarketState:
    """A company's CEOBench A/B/C state as seen by shared-market allocation."""

    company_id: str
    display_name: str
    config: Mapping[str, float | int]
    base_product_quality: float
    q_shared_bonus: float = 0.0
    q_group_bonuses: Mapping[str, float] = field(default_factory=dict)
    lead_promotions_by_group: Mapping[str, float] = field(default_factory=dict)

    def offers_for_group(self, group_id: str) -> list[ArenaPlanOffer]:
        return plan_offers_from_company_config(
            company_id=self.company_id,
            display_name=self.display_name,
            config=self.config,
            base_product_quality=self.base_product_quality,
            q_shared_bonus=self.q_shared_bonus,
            q_group_bonus=self.q_group_bonuses.get(group_id, 0.0),
            lead_promotion=self.lead_promotions_by_group.get(group_id, 0.0),
        )


@dataclass(frozen=True)
class ArenaChoiceResult:
    """Selected company-plan pair, or no product."""

    company_id: str | None
    display_name: str | None
    plan: str | None
    satisfaction: float | None
    required_quality: float | None
    perceived_quality: float | None
    effective_price: float | None

    @property
    def chose_product(self) -> bool:
        return self.company_id is not None and self.plan is not None


NO_PRODUCT_CHOICE = ArenaChoiceResult(
    company_id=None,
    display_name=None,
    plan=None,
    satisfaction=None,
    required_quality=None,
    perceived_quality=None,
    effective_price=None,
)


@dataclass(frozen=True)
class CompanyExposure:
    """Expected leads contributed by one company for one customer group."""

    company_id: str
    group_id: str
    expected_leads: float


@dataclass(frozen=True)
class SharedArrival:
    """One customer arrival sampled from the shared market."""

    group_id: str
    source_company_id: str
    consideration_set: tuple[str, ...]


@dataclass(frozen=True)
class SharedAllocation:
    """Outcome for one shared arrival after company-plan evaluation."""

    arrival: SharedArrival
    choice: ArenaChoiceResult


def compute_required_quality(
    cost: float,
    profile: CustomerChoiceProfile,
) -> float:
    """Compute CEOBench's asymmetric quality-price participation curve."""

    if profile.c_max <= 0:
        raise ValueError("c_max must be positive")
    if cost > profile.c_max:
        return profile.q_max

    normalized_cost = cost / profile.c_max
    q_range = profile.q_max - profile.q_min
    if normalized_cost < 0.5:
        sigmoid_input = profile.steepness_left * (normalized_cost - 0.25) * 10
        return profile.q_min + (q_range / 2.0) * _sigmoid(sigmoid_input)

    sigmoid_input = profile.steepness_right * (normalized_cost - 0.75) * 10
    return (
        profile.q_min
        + (q_range / 2.0)
        + (q_range / 2.0) * _sigmoid(sigmoid_input)
    )


def choose_company_plan(
    profile: CustomerChoiceProfile,
    offers: Iterable[ArenaPlanOffer],
) -> ArenaChoiceResult:
    """Choose among company-plan offers using CEOBench's curve.

    This is the arena generalization of CEOBench's new-customer plan choice:
    the evaluated alternatives are company-plan pairs instead of only plans
    A/B/C from one company.
    """

    best: ArenaChoiceResult | None = None
    for offer in offers:
        required_quality = compute_required_quality(offer.effective_price, profile)
        satisfaction = offer.perceived_quality - required_quality
        acceptable = offer.effective_price <= profile.c_max and satisfaction >= 0.0
        if not acceptable:
            continue

        result = ArenaChoiceResult(
            company_id=offer.company_id,
            display_name=offer.display_name,
            plan=offer.plan,
            satisfaction=satisfaction,
            required_quality=required_quality,
            perceived_quality=offer.perceived_quality,
            effective_price=offer.effective_price,
        )
        if best is None or result.satisfaction > best.satisfaction:
            best = result

    return best or NO_PRODUCT_CHOICE


def choose_evaluated_company_plan(
    offers: Iterable[Mapping[str, object]],
) -> ArenaChoiceResult:
    """Choose among pre-evaluated company-plan offers.

    The simulator owns CEOBench offer evaluation. Arena uses this helper when
    offers already include perceived quality, required quality, satisfaction,
    effective price, and an acceptable flag.
    """

    best_offer, best_satisfaction = _best_evaluated_offer(offers)

    if best_offer is None:
        return NO_PRODUCT_CHOICE

    return _choice_from_evaluated_offer(best_offer, best_satisfaction)


def choose_evaluated_company_plan_with_source(
    offers: Iterable[Mapping[str, object]],
    *,
    source_company_id: str,
    comparison_hurdle: float = 0.0,
) -> ArenaChoiceResult:
    """Choose among offers with first-contact advantage for the source company.

    If the source company's best offer is unacceptable, the best acceptable
    rival can win without a hurdle. If source is acceptable, a rival must clear
    ``source_satisfaction + comparison_hurdle``.
    """

    source_company_id = str(source_company_id)
    comparison_hurdle = max(0.0, float(comparison_hurdle or 0.0))
    normalized_offers = list(offers)
    source_offer, source_satisfaction = _best_evaluated_offer(
        offer for offer in normalized_offers
        if str(offer.get("company_id")) == source_company_id
    )
    rival_offer, rival_satisfaction = _best_evaluated_offer(
        offer for offer in normalized_offers
        if str(offer.get("company_id")) != source_company_id
    )

    if source_offer is None:
        if rival_offer is None:
            return NO_PRODUCT_CHOICE
        return _choice_from_evaluated_offer(rival_offer, rival_satisfaction)

    if rival_offer is None:
        return _choice_from_evaluated_offer(source_offer, source_satisfaction)

    if rival_satisfaction > source_satisfaction + comparison_hurdle:
        return _choice_from_evaluated_offer(rival_offer, rival_satisfaction)

    return _choice_from_evaluated_offer(source_offer, source_satisfaction)


def _best_evaluated_offer(
    offers: Iterable[Mapping[str, object]],
) -> tuple[Mapping[str, object] | None, float]:
    best_offer: Mapping[str, object] | None = None
    best_satisfaction = float("-inf")
    for offer in offers:
        if not bool(offer.get("acceptable")):
            continue
        try:
            satisfaction = float(offer["satisfaction"])
        except (KeyError, TypeError, ValueError):
            continue
        if satisfaction > best_satisfaction:
            best_satisfaction = satisfaction
            best_offer = offer
    return best_offer, best_satisfaction


def _choice_from_evaluated_offer(
    offer: Mapping[str, object],
    satisfaction: float,
) -> ArenaChoiceResult:
    return ArenaChoiceResult(
        company_id=str(offer.get("company_id")),
        display_name=str(offer.get("display_name")),
        plan=str(offer.get("plan")),
        satisfaction=satisfaction,
        required_quality=float(offer.get("required_quality", 0.0)),
        perceived_quality=float(offer.get("perceived_quality", 0.0)),
        effective_price=float(offer.get("effective_price", 0.0)),
    )


def plan_offers_from_company_config(
    *,
    company_id: str,
    display_name: str,
    config: Mapping[str, float | int],
    base_product_quality: float,
    q_shared_bonus: float = 0.0,
    q_group_bonus: float = 0.0,
    lead_promotion: float = 0.0,
) -> list[ArenaPlanOffer]:
    """Create A/B/C company-plan offers from CEOBench config state."""

    return [
        ArenaPlanOffer(
            company_id=company_id,
            display_name=display_name,
            plan=plan,
            price=float(config[f"price_{plan}"]),
            tier=int(config[f"tier_{plan}"]),
            base_product_quality=base_product_quality,
            q_shared_bonus=q_shared_bonus,
            q_group_bonus=q_group_bonus,
            lead_promotion=lead_promotion,
        )
        for plan in ("A", "B", "C")
    ]


def compute_group_arrival_rates(
    exposures: Iterable[CompanyExposure],
) -> dict[str, float]:
    """Sum company exposures into one shared arrival rate per group."""

    rates: dict[str, float] = {}
    for exposure in exposures:
        if exposure.expected_leads < 0:
            raise ValueError("expected_leads cannot be negative")
        rates[exposure.group_id] = (
            rates.get(exposure.group_id, 0.0) + exposure.expected_leads
        )
    return rates


def sample_shared_arrivals(
    exposures: Sequence[CompanyExposure],
    rng: ArenaRng,
    *,
    rival_consideration_scale: float = 1.0,
    max_considered_companies: int | None = None,
) -> list[SharedArrival]:
    """Sample customers from one shared market.

    For each group, the arena samples arrivals from ``sum_i exposure[i, group]``.
    Each arrival gets a source company proportional to exposure, then a
    consideration set that always includes that source company.
    """

    if rival_consideration_scale < 0:
        raise ValueError("rival_consideration_scale cannot be negative")
    if max_considered_companies is not None and max_considered_companies < 1:
        raise ValueError("max_considered_companies must be at least 1")

    by_group: dict[str, list[CompanyExposure]] = {}
    for exposure in exposures:
        if exposure.expected_leads < 0:
            raise ValueError("expected_leads cannot be negative")
        if exposure.expected_leads == 0:
            continue
        by_group.setdefault(exposure.group_id, []).append(exposure)

    arrivals: list[SharedArrival] = []
    for group_id in sorted(by_group):
        group_exposures = sorted(
            by_group[group_id],
            key=lambda item: item.company_id,
        )
        total_exposure = sum(item.expected_leads for item in group_exposures)
        if total_exposure <= 0:
            continue

        n_arrivals = int(rng.poisson(total_exposure))
        probabilities = [
            item.expected_leads / total_exposure for item in group_exposures
        ]
        company_ids = [item.company_id for item in group_exposures]

        for _ in range(n_arrivals):
            source_index = int(rng.choice(len(group_exposures), p=probabilities))
            source_company_id = company_ids[source_index]
            consideration = [source_company_id]

            for item in group_exposures:
                if item.company_id == source_company_id:
                    continue
                include_probability = min(
                    1.0,
                    rival_consideration_scale
                    * (item.expected_leads / total_exposure),
                )
                if rng.random() < include_probability:
                    consideration.append(item.company_id)

            arrivals.append(
                SharedArrival(
                    group_id=group_id,
                    source_company_id=source_company_id,
                    consideration_set=_cap_consideration_set(
                        consideration,
                        exposures=group_exposures,
                        max_considered_companies=max_considered_companies,
                    ),
                )
            )

    return arrivals


def filter_offers_for_consideration_set(
    offers: Iterable[ArenaPlanOffer],
    consideration_set: Iterable[str],
) -> list[ArenaPlanOffer]:
    considered = set(consideration_set)
    return [offer for offer in offers if offer.company_id in considered]


def choose_for_shared_arrival(
    arrival: SharedArrival,
    profile: CustomerChoiceProfile,
    company_states: Iterable[ArenaCompanyMarketState],
) -> ArenaChoiceResult:
    """Evaluate one shared-market arrival across its consideration set."""

    if profile.group_id != arrival.group_id:
        raise ValueError("arrival group_id and profile group_id must match")

    offers: list[ArenaPlanOffer] = []
    for company_state in company_states:
        if company_state.company_id in arrival.consideration_set:
            offers.extend(company_state.offers_for_group(arrival.group_id))

    return choose_company_plan(profile, offers)


def allocate_shared_arrivals(
    arrivals: Iterable[SharedArrival],
    profiles_by_group: Mapping[str, CustomerChoiceProfile],
    company_states: Iterable[ArenaCompanyMarketState],
) -> list[SharedAllocation]:
    """Allocate shared arrivals to company-plan choices or no product."""

    states = list(company_states)
    allocations: list[SharedAllocation] = []
    for arrival in arrivals:
        try:
            profile = profiles_by_group[arrival.group_id]
        except KeyError as exc:
            raise ValueError(f"Missing customer profile for group {arrival.group_id}") from exc
        allocations.append(
            SharedAllocation(
                arrival=arrival,
                choice=choose_for_shared_arrival(arrival, profile, states),
            )
        )
    return allocations


def _cap_consideration_set(
    consideration: Sequence[str],
    *,
    exposures: Sequence[CompanyExposure],
    max_considered_companies: int | None,
) -> tuple[str, ...]:
    if max_considered_companies is None or len(consideration) <= max_considered_companies:
        return tuple(consideration)

    source = consideration[0]
    exposure_by_company = {item.company_id: item.expected_leads for item in exposures}
    rivals = sorted(
        consideration[1:],
        key=lambda company_id: (-exposure_by_company.get(company_id, 0.0), company_id),
    )
    return tuple([source] + rivals[: max_considered_companies - 1])


def _sigmoid(value: float) -> float:
    value = max(-500.0, min(500.0, value))
    return 1.0 / (1.0 + math.exp(-value))
