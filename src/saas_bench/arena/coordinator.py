"""Runtime coordination for CEOBench Arena weekly barriers."""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from dataclasses import asdict
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Mapping

from .interactions import ArenaInteractionLog, ArenaMoneyTransfer, ArenaResearchShare


PredictionBody = Mapping[str, Mapping[str, float]]
AdvanceCallback = Callable[[Mapping[str, "ArenaNextWeekSubmission"]], Mapping[str, dict]]
AcquisitionSlotCallback = Callable[
    [int, Mapping[str, "ArenaAcquisitionSlotSubmission"]],
    Mapping[str, dict],
]


@dataclass(frozen=True)
class ArenaNextWeekSubmission:
    """One company's arena next-week submission."""

    company_id: str
    api_port: int
    day: int
    rationale: str
    predictions: PredictionBody

    @property
    def next_week_body(self) -> dict:
        return {
            "rationale": self.rationale,
            "predictions": self.predictions,
        }


@dataclass(frozen=True)
class ArenaAcquisitionSlotSubmission:
    """One company paused at the daily CEOBench acquisition slot."""

    company_id: str
    api_port: int
    day: int
    display_name: str


class ArenaNextWeekCoordinator:
    """Synchronize arena companies at the weekly next-week barrier."""

    def __init__(
        self,
        company_ids: list[str],
        advance_callback: AdvanceCallback,
        *,
        acquisition_slot_callback: AcquisitionSlotCallback | None = None,
        wait_timeout_s: float = 7200.0,
    ):
        if not company_ids:
            raise ValueError("Arena coordinator requires at least one company")
        if len(set(company_ids)) != len(company_ids):
            raise ValueError("Arena coordinator company_ids must be unique")

        self.company_ids = tuple(company_ids)
        self._company_id_set = set(company_ids)
        self._advance_callback = advance_callback
        self._acquisition_slot_callback = acquisition_slot_callback
        self._wait_timeout_s = wait_timeout_s
        self._condition = threading.Condition()
        self._submissions_by_day: dict[int, dict[str, ArenaNextWeekSubmission]] = {}
        self._results_by_day: dict[int, dict[str, dict]] = {}
        self._advancing_days: set[int] = set()
        self._slot_submissions_by_day: dict[int, dict[str, ArenaAcquisitionSlotSubmission]] = {}
        self._slot_results_by_day: dict[int, dict[str, dict]] = {}
        self._slot_advancing_days: set[int] = set()
        self._api_ports_by_company: dict[str, int] = {}
        self._retired_company_ids: set[str] = set()
        self._display_names_by_company: dict[str, str] = {
            company_id: company_id for company_id in company_ids
        }
        self._consumed_introduction_ids: set[str] = set()
        self.interaction_log = ArenaInteractionLog.for_companies(company_ids)

    def submit(self, submission: ArenaNextWeekSubmission) -> dict:
        """Submit one company's week and block until the shared week advances."""

        if submission.company_id not in self._company_id_set:
            return {
                "success": False,
                "error": f"Unknown arena company_id: {submission.company_id}",
            }

        registration = self.register_company(
            company_id=submission.company_id,
            api_port=submission.api_port,
        )
        if not registration.get("success"):
            return registration

        deadline = time.monotonic() + self._wait_timeout_s
        with self._condition:
            day_submissions = self._submissions_by_day.setdefault(submission.day, {})
            day_submissions[submission.company_id] = submission

            if self._all_submitted_locked(submission.day):
                self._advance_locked(submission.day)

            while submission.day not in self._results_by_day:
                if submission.company_id in self._retired_company_ids:
                    return self._retired_result_locked(submission.company_id)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return {
                        "success": False,
                        "error": "arena_next_week_timeout",
                        "message": "Timed out waiting for other arena companies to submit next-week.",
                    }
                self._condition.wait(timeout=remaining)

            return self._results_by_day[submission.day].get(
                submission.company_id,
                {
                    "success": False,
                    "error": "arena_missing_company_result",
                },
            )

    def submit_acquisition_slot(self, submission: ArenaAcquisitionSlotSubmission) -> dict:
        """Submit one company at the hidden daily acquisition slot.

        The coordinator waits until every company reaches the same simulation
        day, computes the shared market allocation, applies the resulting
        leads, and returns this company's ordinary CEOBench generation result.
        """

        if submission.company_id not in self._company_id_set:
            return {
                "success": False,
                "error": f"Unknown arena company_id: {submission.company_id}",
            }
        if self._acquisition_slot_callback is None:
            return {
                "success": False,
                "error": "arena_acquisition_slot_unconfigured",
            }

        registration = self.register_company(
            company_id=submission.company_id,
            api_port=submission.api_port,
            display_name=submission.display_name,
        )
        if not registration.get("success"):
            return registration

        deadline = time.monotonic() + self._wait_timeout_s
        with self._condition:
            day_submissions = self._slot_submissions_by_day.setdefault(submission.day, {})
            day_submissions[submission.company_id] = submission

            if self._all_slots_submitted_locked(submission.day):
                self._advance_slot_locked(submission.day)

            while submission.day not in self._slot_results_by_day:
                if submission.company_id in self._retired_company_ids:
                    return self._retired_result_locked(submission.company_id)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return {
                        "success": False,
                        "error": "arena_acquisition_slot_timeout",
                        "message": "Timed out waiting for other arena companies at the daily acquisition slot.",
                    }
                self._condition.wait(timeout=remaining)

            return self._slot_results_by_day[submission.day].get(
                submission.company_id,
                {
                    "success": False,
                    "error": "arena_missing_acquisition_slot_result",
                },
            )

    def _all_submitted_locked(self, day: int) -> bool:
        active_company_ids = self._active_company_ids_locked()
        if not active_company_ids:
            return False
        return active_company_ids.issubset(set(self._submissions_by_day.get(day, {})))

    def _all_slots_submitted_locked(self, day: int) -> bool:
        active_company_ids = self._active_company_ids_locked()
        if not active_company_ids:
            return False
        return active_company_ids.issubset(set(self._slot_submissions_by_day.get(day, {})))

    def _advance_locked(self, day: int) -> None:
        if day in self._results_by_day or day in self._advancing_days:
            return

        self._advancing_days.add(day)
        active_company_ids = self._active_company_ids_locked()
        submissions = {
            company_id: submission
            for company_id, submission in self._submissions_by_day[day].items()
            if company_id in active_company_ids
        }
        if not submissions:
            self._advancing_days.discard(day)
            self._condition.notify_all()
            return
        self._condition.release()
        try:
            try:
                results = dict(self._advance_callback(submissions))
            except Exception as exc:
                results = {
                    company_id: {
                        "success": False,
                        "error": "arena_advance_failed",
                        "message": f"{type(exc).__name__}: {exc}",
                    }
                    for company_id in submissions
                }
        finally:
            self._condition.acquire()

        self._results_by_day[day] = results
        self._advancing_days.discard(day)
        self._condition.notify_all()

    def _advance_slot_locked(self, day: int) -> None:
        if day in self._slot_results_by_day or day in self._slot_advancing_days:
            return

        self._slot_advancing_days.add(day)
        active_company_ids = self._active_company_ids_locked()
        submissions = {
            company_id: submission
            for company_id, submission in self._slot_submissions_by_day[day].items()
            if company_id in active_company_ids
        }
        if not submissions:
            self._slot_advancing_days.discard(day)
            self._condition.notify_all()
            return
        self._condition.release()
        try:
            try:
                results = dict(self._acquisition_slot_callback(day, submissions))
            except Exception as exc:
                results = {
                    company_id: {
                        "success": False,
                        "error": "arena_acquisition_slot_failed",
                        "message": f"{type(exc).__name__}: {exc}",
                    }
                    for company_id in submissions
                }
        finally:
            self._condition.acquire()

        self._slot_results_by_day[day] = results
        self._slot_advancing_days.discard(day)
        self._condition.notify_all()

    def register_company(
        self,
        *,
        company_id: str,
        api_port: int,
        display_name: str | None = None,
    ) -> dict:
        """Register a company's current simulator API port with the coordinator."""

        if company_id not in self._company_id_set:
            return {
                "success": False,
                "error": f"Unknown arena company_id: {company_id}",
            }
        with self._condition:
            if company_id in self._retired_company_ids:
                return self._retired_result_locked(company_id)
            self._api_ports_by_company[company_id] = int(api_port)
            if display_name:
                self._display_names_by_company[company_id] = display_name
        return {"success": True}

    def retire_company(self, company_id: str, *, outcome: str | None = None) -> dict:
        """Remove a terminal company from future arena barriers."""

        if company_id not in self._company_id_set:
            return {
                "success": False,
                "error": f"Unknown arena company_id: {company_id}",
            }

        with self._condition:
            already_retired = company_id in self._retired_company_ids
            self._retired_company_ids.add(company_id)
            self._api_ports_by_company.pop(company_id, None)

            for day in sorted(list(self._submissions_by_day)):
                if self._all_submitted_locked(day):
                    self._advance_locked(day)
            for day in sorted(list(self._slot_submissions_by_day)):
                if self._all_slots_submitted_locked(day):
                    self._advance_slot_locked(day)

            active_company_ids = sorted(self._active_company_ids_locked())
            self._condition.notify_all()

        return {
            "success": True,
            "company_id": company_id,
            "retired": True,
            "already_retired": already_retired,
            "outcome": outcome or "",
            "active_company_ids": active_company_ids,
        }

    def _active_company_ids_locked(self) -> set[str]:
        return self._company_id_set - self._retired_company_ids

    def _retired_result_locked(self, company_id: str) -> dict:
        return {
            "success": False,
            "error": "arena_company_retired",
            "company_id": company_id,
            "message": "This arena company has already reached a terminal outcome.",
        }

    def record_interaction(self, action: str, body: Mapping) -> dict:
        """Record one structured company-to-company interaction."""

        sender_company_id = str(body.get("sender_company_id") or body.get("company_id") or "")
        day = int(body.get("day", 0) or 0)
        try:
            if action == "send_email":
                event = self.interaction_log.send_email(
                    sender_company_id=sender_company_id,
                    recipient_company_id=str(body["recipient_company_id"]),
                    day=day,
                    subject=str(body.get("subject", "")),
                    body=str(body.get("body", "")),
                )
                return {"success": True, "event": asdict(event)}

            if action == "transfer_money":
                event = self.interaction_log.transfer_money(
                    sender_company_id=sender_company_id,
                    recipient_company_id=str(body["recipient_company_id"]),
                    day=day,
                    amount=float(body["amount"]),
                    memo=str(body.get("memo", "")),
                )
                apply_result = self._apply_money_transfer(event)
                if not apply_result.get("success"):
                    self.interaction_log.money_transfers.remove(event)
                    return apply_result
                return {"success": True, "event": asdict(event)}

            if action == "share_research":
                recipient = body.get("recipient_company_id")
                event = self.interaction_log.share_research(
                    sender_company_id=sender_company_id,
                    recipient_company_id=str(recipient) if recipient is not None else None,
                    day=day,
                    scope=str(body.get("scope", "")),
                    artifact_id=str(body.get("artifact_id", "")),
                    group_id=body.get("group_id"),
                    memo=str(body.get("memo", "")),
                )
                effects = self._apply_research_share(event)
                return {"success": True, "event": asdict(event), "effects": effects}

            if action == "introduce_customer":
                event = self.interaction_log.introduce_customer(
                    sender_company_id=sender_company_id,
                    recipient_company_id=str(body["recipient_company_id"]),
                    day=day,
                    customer_ref=str(body.get("customer_ref", "")),
                    group_id=body.get("group_id"),
                    memo=str(body.get("memo", "")),
                )
                return {"success": True, "event": asdict(event)}
        except (KeyError, TypeError, ValueError) as exc:
            return {
                "success": False,
                "error": "invalid_arena_interaction",
                "message": str(exc),
            }

        return {
            "success": False,
            "error": f"Unknown arena interaction action: {action}",
        }

    def inbox_for(self, company_id: str) -> dict:
        if company_id not in self._company_id_set:
            return {
                "success": False,
                "error": f"Unknown arena company_id: {company_id}",
            }
        return {
            "success": True,
            "company_id": company_id,
            "inbox": self.interaction_log.inbox_dict_for(company_id),
            "event_counts": self.interaction_log.event_counts(),
        }

    def consume_introduction_visibility(
        self,
        *,
        source_company_id: str,
        group_id: str,
    ) -> list[str]:
        """Return recipient companies introduced to the next matching arrival.

        Each introduction is a one-use visibility primitive. It can add the
        recipient to a customer's consideration set, but the customer still
        evaluates the recipient's offer through CEOBench satisfaction.
        """

        introduced: list[str] = []
        with self._condition:
            for introduction in self.interaction_log.customer_introductions:
                if introduction.interaction_id in self._consumed_introduction_ids:
                    continue
                if introduction.sender_company_id != source_company_id:
                    continue
                if introduction.group_id and introduction.group_id != group_id:
                    continue
                self._consumed_introduction_ids.add(introduction.interaction_id)
                introduced.append(introduction.recipient_company_id)
        return introduced

    def _apply_money_transfer(self, transfer: ArenaMoneyTransfer) -> dict:
        with self._condition:
            sender_port = self._api_ports_by_company.get(transfer.sender_company_id)
            recipient_port = self._api_ports_by_company.get(transfer.recipient_company_id)

        if sender_port is None or recipient_port is None:
            return {
                "success": False,
                "error": "arena_company_not_registered",
                "message": "Both companies must have active simulator API servers for money transfers.",
            }

        common = {
            "transfer_id": transfer.interaction_id,
            "amount": transfer.amount,
            "day": transfer.day,
            "memo": transfer.memo,
        }
        sender_result = http_post_json(
            sender_port,
            "/arena-apply-money-transfer",
            {
                **common,
                "direction": "out",
                "counterparty_company_id": transfer.recipient_company_id,
            },
        )
        if not sender_result.get("success"):
            return sender_result

        recipient_result = http_post_json(
            recipient_port,
            "/arena-apply-money-transfer",
            {
                **common,
                "direction": "in",
                "counterparty_company_id": transfer.sender_company_id,
            },
        )
        if not recipient_result.get("success"):
            return recipient_result

        return {"success": True}

    def _apply_research_share(self, share: ArenaResearchShare) -> dict:
        if not share.group_id:
            return {"applied": [], "skipped": "no_group_id"}

        with self._condition:
            sender_port = self._api_ports_by_company.get(share.sender_company_id)
            if share.scope == "company":
                recipient_company_ids = (
                    [share.recipient_company_id]
                    if share.recipient_company_id is not None
                    else []
                )
            else:
                recipient_company_ids = [
                    company_id
                    for company_id in self.company_ids
                    if company_id != share.sender_company_id
                ]
            recipient_ports = {
                company_id: self._api_ports_by_company.get(company_id)
                for company_id in recipient_company_ids
                if company_id is not None
            }

        if sender_port is None:
            return {"applied": [], "skipped": "sender_not_registered"}

        snapshot = http_post_json(
            sender_port,
            "/arena-research-share-snapshot",
            {"group_id": share.group_id},
        )
        if not snapshot.get("success"):
            return {
                "applied": [],
                "skipped": snapshot.get("error", "research_snapshot_failed"),
            }

        applied = []
        for company_id, port in recipient_ports.items():
            if port is None:
                applied.append({
                    "company_id": company_id,
                    "success": False,
                    "error": "recipient_not_registered",
                })
                continue
            result = http_post_json(
                port,
                "/arena-apply-research-share",
                {
                    "share_id": share.interaction_id,
                    "sender_company_id": share.sender_company_id,
                    "group_id": share.group_id,
                    "source_info_level": int(snapshot.get("info_level", 0) or 0),
                    "day": share.day,
                    "memo": share.memo,
                },
            )
            applied.append({"company_id": company_id, **result})

        return {
            "group_id": share.group_id,
            "source_info_level": int(snapshot.get("info_level", 0) or 0),
            "applied": applied,
        }


class ArenaCoordinatorHTTPServer:
    """Small localhost HTTP server used by arena operation wrappers."""

    def __init__(
        self,
        coordinator: ArenaNextWeekCoordinator,
        *,
        host: str = "127.0.0.1",
        port: int = 0,
    ):
        self.coordinator = coordinator
        self.host = host
        self._server = _ArenaHTTPServer((host, port), _ArenaRequestHandler, coordinator)
        self.port = int(self._server.server_address[1])
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="ceobench-arena-coordinator",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None


class _ArenaHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, server_address, handler_class, coordinator: ArenaNextWeekCoordinator):
        super().__init__(server_address, handler_class)
        self.coordinator = coordinator


class _ArenaRequestHandler(BaseHTTPRequestHandler):
    server: _ArenaHTTPServer

    def log_message(self, format: str, *args) -> None:
        return

    def do_GET(self) -> None:
        if self.path == "/health":
            self._send_json({"success": True})
            return
        self._send_json({"success": False, "error": "not_found"}, status=404)

    def do_POST(self) -> None:
        if self.path == "/arena-register-company":
            body = self._read_json()
            result = self.server.coordinator.register_company(
                company_id=str(body.get("company_id", "")),
                display_name=body.get("display_name"),
                api_port=int(body.get("api_port", 0) or 0),
            )
            self._send_json(result, status=200 if result.get("success") else 400)
            return

        if self.path == "/arena-retire-company":
            body = self._read_json()
            result = self.server.coordinator.retire_company(
                str(body.get("company_id", "")),
                outcome=str(body.get("outcome", "")) or None,
            )
            self._send_json(result, status=200 if result.get("success") else 400)
            return

        if self.path == "/arena-interaction":
            body = self._read_json()
            result = self.server.coordinator.record_interaction(
                str(body.get("action", "")),
                body,
            )
            self._send_json(result, status=200 if result.get("success") else 400)
            return

        if self.path == "/arena-inbox":
            body = self._read_json()
            result = self.server.coordinator.inbox_for(str(body.get("company_id", "")))
            self._send_json(result, status=200 if result.get("success") else 400)
            return

        if self.path == "/arena-acquisition-slot":
            body = self._read_json()
            try:
                submission = ArenaAcquisitionSlotSubmission(
                    company_id=str(body["company_id"]),
                    api_port=int(body["api_port"]),
                    day=int(body["day"]),
                    display_name=str(body.get("display_name") or body["company_id"]),
                )
            except (KeyError, TypeError, ValueError) as exc:
                self._send_json(
                    {
                        "success": False,
                        "error": "invalid_arena_acquisition_slot",
                        "message": str(exc),
                    },
                    status=400,
                )
                return

            result = self.server.coordinator.submit_acquisition_slot(submission)
            self._send_json(result, status=200 if result.get("success") else 500)
            return

        if self.path != "/next-week":
            self._send_json({"success": False, "error": "not_found"}, status=404)
            return

        body = self._read_json()
        try:
            submission = ArenaNextWeekSubmission(
                company_id=str(body["company_id"]),
                api_port=int(body["api_port"]),
                day=int(body["day"]),
                rationale=str(body["rationale"]),
                predictions=body["predictions"],
            )
        except (KeyError, TypeError, ValueError) as exc:
            self._send_json(
                {
                    "success": False,
                    "error": "invalid_arena_submission",
                    "message": str(exc),
                },
                status=400,
            )
            return

        result = self.server.coordinator.submit(submission)
        self._send_json(result, status=200 if result.get("success") else 500)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        data = self.rfile.read(length) if length else b"{}"
        return json.loads(data.decode("utf-8"))

    def _send_json(self, payload: dict, *, status: int = 200) -> None:
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def http_get_json(port: int, path: str, *, timeout: float = 30.0) -> dict:
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def http_post_json(
    port: int,
    path: str,
    body: Mapping,
    *,
    timeout: float = 7200.0,
) -> dict:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        try:
            return json.loads(exc.read())
        except Exception:
            return {"success": False, "error": f"HTTP {exc.code}"}
