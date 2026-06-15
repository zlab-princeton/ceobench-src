"""Company identities for CEOBench Arena."""

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence


DEFAULT_COMPANY_NAMES: tuple[str, ...] = (
    "NovaMind",
    "AsterAI",
    "LatticeWorks",
    "HelioStack",
    "VectorForge",
    "SignalNest",
    "OrbitLayer",
    "QuantaWorks",
)


@dataclass(frozen=True)
class ArenaCompanySpec:
    """Static participant assignment for one arena company."""

    company_id: str
    display_name: str
    agent_model: str | None = None
    starting_cash: float | None = None
    initial_config: Mapping[str, Any] = field(default_factory=dict)


def make_company_specs(
    count: int,
    *,
    agent_models: Sequence[str | None] | None = None,
    starting_cash: float | None = None,
    initial_configs: Sequence[Mapping[str, Any] | None] | None = None,
    names: Sequence[str] = DEFAULT_COMPANY_NAMES,
) -> list[ArenaCompanySpec]:
    """Create deterministic arena company specs.

    IDs are stable ``company_N`` labels. A one-company arena keeps NovaMind so
    the ordinary CEOBench identity is preserved.
    """

    if count < 1:
        raise ValueError("Arena requires at least one company")

    specs: list[ArenaCompanySpec] = []
    seen_names: set[str] = set()
    for index in range(count):
        display_name = names[index] if index < len(names) else f"Company {index}"
        if display_name in seen_names:
            raise ValueError(f"Duplicate arena company display name: {display_name}")
        seen_names.add(display_name)

        agent_model = (
            agent_models[index]
            if agent_models is not None and index < len(agent_models)
            else None
        )
        initial_config = (
            initial_configs[index]
            if initial_configs
            and index < len(initial_configs)
            and initial_configs[index]
            else {}
        )

        specs.append(
            ArenaCompanySpec(
                company_id=f"company_{index}",
                display_name=display_name,
                agent_model=agent_model,
                starting_cash=starting_cash,
                initial_config=dict(initial_config),
            )
        )

    return specs
