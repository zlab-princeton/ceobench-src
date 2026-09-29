"""Analytics, monitoring, and operations tools."""

from typing import Dict, Optional
from . import _client


def get_social_posts(days: int = 7, limit: int = 50) -> Dict:
    """Get recent social media posts from customers.

    Args:
        days: Number of days to look back (default 7).
        limit: Maximum number of posts to return (default 50).

    Returns:
        Dict with social media post data.
    """
    return _client.call('get_social_posts', {'days': days, 'limit': limit})


def set_targeted_ops_spend(
    targeted_spend: Optional[Dict[str, float]] = None,
    *,
    by_group: Optional[Dict[str, float]] = None,
    by_plan: Optional[Dict[str, float]] = None,
    by_group_plan: Optional[Dict[str, Dict[str, float]]] = None,
    by_customer: Optional[Dict[str, float]] = None,
) -> Dict:
    """Set additional daily operations spend for independent targeting scopes.

    Omitted scopes remain unchanged; pass an empty dict to clear a scope.
    ``targeted_spend`` is the legacy alias for ``by_group``. Do not pass both.

    Args:
        targeted_spend: Legacy {group_id: daily_amount} dict.
        by_group: {group_id: daily_amount} dict.
        by_plan: {plan: daily_amount} dict; plans are A, B, C.
        by_group_plan: {group_id: {plan: daily_amount}} dict.
        by_customer: {customer_id_as_string: daily_amount} dict.

    Returns:
        Dict with all current scopes and total extra daily spending.
    """
    args = {
        name: value for name, value in {
            'targeted_spend': targeted_spend,
            'by_group': by_group,
            'by_plan': by_plan,
            'by_group_plan': by_group_plan,
            'by_customer': by_customer,
        }.items() if value is not None
    }
    return _client.call('set_targeted_ops_spend', args)


def set_targeted_dev_spend(targeted_spend: Dict[str, float]) -> Dict:
    """Set per-group targeted development spend.

    Per-group dev spend ACCUMULATES a quality bonus daily. Persists after spending stops.

    Args:
        targeted_spend: {group_id: daily_amount} dict.

    Returns:
        Dict with update confirmation.
    """
    return _client.call('set_targeted_dev_spend', {'targeted_spend': targeted_spend})
