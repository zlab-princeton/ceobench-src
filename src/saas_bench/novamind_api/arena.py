"""CEOBench Arena public API helpers.

These helpers are active only inside Arena runs. Ordinary CEOBench sessions can
import this module, but calls that require an Arena coordinator will raise a
clear error.
"""

import json
import os
import urllib.error
import urllib.request
from typing import Dict, Optional

from . import _client


def _arena_env(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise _client.NovaMindAPIError(
            "Arena API is only available inside a CEOBench Arena run."
        )
    return value


def _coordinator_port() -> int:
    return int(_arena_env("CEOBENCH_ARENA_COORDINATOR_PORT"))


def _company_id() -> str:
    return _arena_env("CEOBENCH_ARENA_COMPANY_ID")


def _display_name() -> str:
    return os.environ.get("CEOBENCH_ARENA_DISPLAY_NAME", _company_id())


def _api_port() -> int:
    return int(_arena_env("NOVAMIND_API_PORT"))


def _post_coordinator(path: str, body: Dict) -> Dict:
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        f"http://127.0.0.1:{_coordinator_port()}{path}",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        try:
            result = json.loads(exc.read())
        except Exception:
            raise _client.NovaMindAPIError(f"Arena coordinator HTTP {exc.code}")
    except urllib.error.URLError as exc:
        raise _client.NovaMindAPIError(f"Failed to reach Arena coordinator: {exc}")

    if not result.get("success"):
        message = result.get("message") or result.get("error") or "Arena request failed"
        raise _client.NovaMindAPIError(message)
    return result


def _register() -> None:
    _post_coordinator(
        "/arena-register-company",
        {
            "company_id": _company_id(),
            "display_name": _display_name(),
            "api_port": _api_port(),
        },
    )


def _base_body() -> Dict:
    _register()
    return {
        "company_id": _company_id(),
        "sender_company_id": _company_id(),
        "display_name": _display_name(),
        "api_port": _api_port(),
        "day": _client.vars.current_day,
    }


def get_inbox() -> Dict:
    """Return Arena interactions visible to this company."""

    _register()
    return _post_coordinator(
        "/arena-inbox",
        {"company_id": _company_id()},
    )


def send_email(recipient_company_id: str, subject: str, body: str) -> Dict:
    """Send an inert company-to-company Arena email.

    Email text is visible communication only. It does not alter customers,
    quality, contracts, cash, or market state.
    """

    payload = {
        **_base_body(),
        "action": "send_email",
        "recipient_company_id": recipient_company_id,
        "subject": subject,
        "body": body,
    }
    return _post_coordinator("/arena-interaction", payload)


def transfer_money(recipient_company_id: str, amount: float, memo: str = "") -> Dict:
    """Transfer cash to another Arena company.

    The sender receives an ``arena_transfer_out`` ledger entry and the recipient
    receives an ``arena_transfer_in`` ledger entry. Transfers fail if the sender
    lacks cash.
    """

    payload = {
        **_base_body(),
        "action": "transfer_money",
        "recipient_company_id": recipient_company_id,
        "amount": amount,
        "memo": memo,
    }
    return _post_coordinator("/arena-interaction", payload)


def share_research(
    artifact_id: str,
    *,
    scope: str,
    recipient_company_id: Optional[str] = None,
    group_id: Optional[str] = None,
    memo: str = "",
) -> Dict:
    """Share a research artifact with one company or publicly.

    This records an auditable sharing event. It does not directly set product
    quality.
    """

    payload = {
        **_base_body(),
        "action": "share_research",
        "artifact_id": artifact_id,
        "scope": scope,
        "recipient_company_id": recipient_company_id,
        "group_id": group_id,
        "memo": memo,
    }
    return _post_coordinator("/arena-interaction", payload)


def introduce_customer(
    recipient_company_id: str,
    customer_ref: str,
    group_id: Optional[str] = None,
    memo: str = "",
) -> Dict:
    """Record a customer or lead introduction for another company.

    The introduction can add the recipient to the next matching customer's
    consideration set. It never forces a customer to buy.
    """

    payload = {
        **_base_body(),
        "action": "introduce_customer",
        "recipient_company_id": recipient_company_id,
        "customer_ref": customer_ref,
        "group_id": group_id,
        "memo": memo,
    }
    return _post_coordinator("/arena-interaction", payload)


def public_market() -> Dict:
    """Return public Arena competitor snapshots from the local database."""

    return _client.query(
        """
        SELECT day, company_id, display_name,
               price_A, price_B, price_C,
               tier_A, tier_B, tier_C
        FROM arena_public_market_snapshots
        ORDER BY day DESC, company_id ASC
        """
    )
