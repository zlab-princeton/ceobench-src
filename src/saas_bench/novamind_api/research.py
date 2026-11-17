"""R&D research project tools."""

from typing import Dict
from . import _client


def start_research_project(tier: int) -> Dict:
    """Start an R&D research project.

    Ten independent tiers. The returned duration and quality boost are forecasts.

    Args:
        tier: Research tier (1-10).

    Returns:
        Dict with project start confirmation.
    """
    return _client.call('start_research_project', {'tier': tier})


def list_research_projects() -> Dict:
    """List all R&D research tiers and their status.

    Returns:
        Dict with all research project details.
    """
    return _client.call('list_research_projects')
