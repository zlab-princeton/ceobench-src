"""Enterprise customer management tools."""

from typing import Dict, List
from . import _client


def send_enterprise_deal(deals: List) -> Dict:
    """Send enterprise deal offerings to one or more customers.

    Compact tuple format: deals=[[customer_id, [["plan", price_per_seat, contract_months], ...]]]

    Reply to the customer's latest turn, or initiate a seller renegotiation. An offer awaiting a reply cannot be replaced.

    Args:
        deals: List of [customer_id, [offerings]] pairs.

    Returns:
        Dict with per-customer results.
    """
    return _client.call('send_enterprise_deal', {'deals': deals})


def reject_enterprise_deal(deals: List) -> Dict:
    """Reject enterprise negotiation threads.

    Rejecting a renewal or churn-prevention request ends the subscription. Declining a plan change or seller renegotiation leaves existing terms unchanged.

    Args:
        deals: List of customer deal rejection specs.

    Returns:
        Dict with per-customer results.
    """
    return _client.call('reject_enterprise_deal', {'deals': deals})
