"""Structured company-to-company primitives for CEOBench Arena."""

from __future__ import annotations

import threading
from dataclasses import asdict, dataclass, field
from typing import Literal, Sequence


@dataclass(frozen=True)
class ArenaEmail:
    """Direct company-to-company message.

    Text is observable communication only. It does not alter customers,
    product quality, contracts, or market state unless another structured arena
    primitive or ordinary CEOBench tool records an enforceable action.
    """

    sender_company_id: str
    recipient_company_id: str
    day: int
    subject: str
    body: str
    interaction_id: str = ""


@dataclass(frozen=True)
class ArenaMoneyTransfer:
    """Structured money transfer between companies."""

    sender_company_id: str
    recipient_company_id: str
    day: int
    amount: float
    memo: str = ""
    interaction_id: str = ""


@dataclass(frozen=True)
class ArenaResearchShare:
    """Structured market/R&D research artifact sharing event."""

    sender_company_id: str
    day: int
    scope: Literal["company", "public"]
    artifact_id: str
    group_id: str | None = None
    recipient_company_id: str | None = None
    memo: str = ""
    interaction_id: str = ""


@dataclass(frozen=True)
class ArenaCustomerIntroduction:
    """Structured introduction of a customer, lead, or enterprise thread.

    This records that another company may become visible to the customer. It
    does not force the customer to buy, switch, or accept an offer.
    """

    sender_company_id: str
    recipient_company_id: str
    day: int
    customer_ref: str
    group_id: str | None = None
    memo: str = ""
    interaction_id: str = ""


@dataclass
class ArenaInteractionLog:
    """Append-only interaction records for one arena run."""

    company_ids: set[str]
    _next_event_index: int = 1
    _event_id_lock: threading.Lock = field(
        default_factory=threading.Lock,
        init=False,
        repr=False,
        compare=False,
    )
    emails: list[ArenaEmail] = field(default_factory=list)
    money_transfers: list[ArenaMoneyTransfer] = field(default_factory=list)
    research_shares: list[ArenaResearchShare] = field(default_factory=list)
    customer_introductions: list[ArenaCustomerIntroduction] = field(default_factory=list)

    @classmethod
    def for_companies(cls, company_ids: Sequence[str]) -> "ArenaInteractionLog":
        if not company_ids:
            raise ValueError("Arena interactions require at least one company")
        if len(set(company_ids)) != len(company_ids):
            raise ValueError("Duplicate company_id in arena interaction log")
        return cls(company_ids=set(company_ids))

    def send_email(
        self,
        *,
        sender_company_id: str,
        recipient_company_id: str,
        day: int,
        subject: str,
        body: str,
    ) -> ArenaEmail:
        self._validate_direct_interaction(sender_company_id, recipient_company_id)
        subject = subject.strip()
        body = body.strip()
        if not subject:
            raise ValueError("email subject is required")
        if not body:
            raise ValueError("email body is required")

        email = ArenaEmail(
            sender_company_id=sender_company_id,
            recipient_company_id=recipient_company_id,
            day=day,
            subject=subject,
            body=body,
            interaction_id=self._new_interaction_id("email"),
        )
        self.emails.append(email)
        return email

    def transfer_money(
        self,
        *,
        sender_company_id: str,
        recipient_company_id: str,
        day: int,
        amount: float,
        memo: str = "",
    ) -> ArenaMoneyTransfer:
        self._validate_direct_interaction(sender_company_id, recipient_company_id)
        amount = float(amount)
        if amount <= 0:
            raise ValueError("money transfer amount must be positive")

        transfer = ArenaMoneyTransfer(
            sender_company_id=sender_company_id,
            recipient_company_id=recipient_company_id,
            day=day,
            amount=amount,
            memo=memo,
            interaction_id=self._new_interaction_id("transfer"),
        )
        self.money_transfers.append(transfer)
        return transfer

    def share_research(
        self,
        *,
        sender_company_id: str,
        day: int,
        scope: Literal["company", "public"],
        artifact_id: str,
        group_id: str | None = None,
        recipient_company_id: str | None = None,
        memo: str = "",
    ) -> ArenaResearchShare:
        self._validate_company(sender_company_id)
        artifact_id = artifact_id.strip()
        if not artifact_id:
            raise ValueError("research artifact_id is required")
        if scope not in {"company", "public"}:
            raise ValueError("research share scope must be 'company' or 'public'")
        if scope == "company":
            if not recipient_company_id:
                raise ValueError("company-scoped research share requires recipient_company_id")
            self._validate_direct_interaction(sender_company_id, recipient_company_id)
        elif recipient_company_id is not None:
            raise ValueError("public research share cannot have recipient_company_id")

        share = ArenaResearchShare(
            sender_company_id=sender_company_id,
            day=day,
            scope=scope,
            artifact_id=artifact_id,
            group_id=group_id,
            recipient_company_id=recipient_company_id,
            memo=memo,
            interaction_id=self._new_interaction_id("research"),
        )
        self.research_shares.append(share)
        return share

    def introduce_customer(
        self,
        *,
        sender_company_id: str,
        recipient_company_id: str,
        day: int,
        customer_ref: str,
        group_id: str | None = None,
        memo: str = "",
    ) -> ArenaCustomerIntroduction:
        self._validate_direct_interaction(sender_company_id, recipient_company_id)
        customer_ref = customer_ref.strip()
        if not customer_ref:
            raise ValueError("customer_ref is required")

        introduction = ArenaCustomerIntroduction(
            sender_company_id=sender_company_id,
            recipient_company_id=recipient_company_id,
            day=day,
            customer_ref=customer_ref,
            group_id=group_id,
            memo=memo,
            interaction_id=self._new_interaction_id("intro"),
        )
        self.customer_introductions.append(introduction)
        return introduction

    def inbox_for(self, company_id: str) -> dict[str, list]:
        self._validate_company(company_id)
        return {
            "emails": [
                email
                for email in self.emails
                if email.recipient_company_id == company_id
            ],
            "money_transfers": [
                transfer
                for transfer in self.money_transfers
                if transfer.recipient_company_id == company_id
            ],
            "research_shares": [
                share
                for share in self.research_shares
                if share.scope == "public" or share.recipient_company_id == company_id
            ],
            "customer_introductions": [
                introduction
                for introduction in self.customer_introductions
                if introduction.recipient_company_id == company_id
            ],
        }

    def inbox_dict_for(self, company_id: str) -> dict[str, list[dict]]:
        """Return a JSON-serializable inbox for one company."""

        inbox = self.inbox_for(company_id)
        return {
            key: [asdict(item) for item in items]
            for key, items in inbox.items()
        }

    def event_counts(self) -> dict[str, int]:
        """Small public summary useful for coordinator health checks."""

        return {
            "emails": len(self.emails),
            "money_transfers": len(self.money_transfers),
            "research_shares": len(self.research_shares),
            "customer_introductions": len(self.customer_introductions),
        }

    def _new_interaction_id(self, prefix: str) -> str:
        with self._event_id_lock:
            interaction_id = f"{prefix}_{self._next_event_index:06d}"
            self._next_event_index += 1
            return interaction_id

    def _validate_direct_interaction(
        self,
        sender_company_id: str,
        recipient_company_id: str,
    ) -> None:
        self._validate_company(sender_company_id)
        self._validate_company(recipient_company_id)
        if sender_company_id == recipient_company_id:
            raise ValueError("direct arena interaction requires two different companies")

    def _validate_company(self, company_id: str) -> None:
        if company_id not in self.company_ids:
            raise ValueError(f"Unknown arena company_id: {company_id}")
