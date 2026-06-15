"""Regression tests for CEOBench Arena interaction primitives."""

import pytest

from saas_bench.arena import ArenaInteractionLog


def test_email_is_recorded_as_message_only():
    log = ArenaInteractionLog.for_companies(["company_0", "company_1"])

    email = log.send_email(
        sender_company_id="company_0",
        recipient_company_id="company_1",
        day=7,
        subject="Co-marketing",
        body="Want to compare notes next week?",
    )

    assert email.subject == "Co-marketing"
    assert log.inbox_for("company_1")["emails"] == [email]
    assert log.inbox_for("company_0")["emails"] == []


def test_money_transfer_records_enforceable_structured_action():
    log = ArenaInteractionLog.for_companies(["company_0", "company_1"])

    transfer = log.transfer_money(
        sender_company_id="company_0",
        recipient_company_id="company_1",
        day=14,
        amount=25_000,
        memo="shared report reimbursement",
    )

    assert transfer.amount == pytest.approx(25_000)
    assert log.money_transfers == [transfer]
    assert log.inbox_for("company_1")["money_transfers"] == [transfer]


def test_research_share_supports_company_and_public_scope():
    log = ArenaInteractionLog.for_companies(["company_0", "company_1", "company_2"])

    private_share = log.share_research(
        sender_company_id="company_0",
        recipient_company_id="company_1",
        day=21,
        scope="company",
        artifact_id="snapshot-S1-l2",
        group_id="S1",
    )
    public_share = log.share_research(
        sender_company_id="company_1",
        day=21,
        scope="public",
        artifact_id="benchmark-note-001",
    )

    assert private_share in log.inbox_for("company_1")["research_shares"]
    assert private_share not in log.inbox_for("company_2")["research_shares"]
    assert public_share in log.inbox_for("company_0")["research_shares"]
    assert public_share in log.inbox_for("company_2")["research_shares"]


def test_customer_introduction_records_visibility_not_forced_conversion():
    log = ArenaInteractionLog.for_companies(["company_0", "company_1"])

    introduction = log.introduce_customer(
        sender_company_id="company_0",
        recipient_company_id="company_1",
        day=28,
        customer_ref="enterprise-thread-42",
        memo="They may want a second quote.",
    )

    assert introduction.customer_ref == "enterprise-thread-42"
    assert log.customer_introductions == [introduction]
    assert log.inbox_for("company_1")["customer_introductions"] == [introduction]


def test_interactions_validate_company_ids_and_required_fields():
    log = ArenaInteractionLog.for_companies(["company_0", "company_1"])

    with pytest.raises(ValueError, match="different companies"):
        log.transfer_money(
            sender_company_id="company_0",
            recipient_company_id="company_0",
            day=1,
            amount=1,
        )

    with pytest.raises(ValueError, match="Unknown"):
        log.send_email(
            sender_company_id="company_0",
            recipient_company_id="missing",
            day=1,
            subject="hi",
            body="there",
        )

    with pytest.raises(ValueError, match="positive"):
        log.transfer_money(
            sender_company_id="company_0",
            recipient_company_id="company_1",
            day=1,
            amount=0,
        )

    with pytest.raises(ValueError, match="recipient_company_id"):
        log.share_research(
            sender_company_id="company_0",
            day=1,
            scope="company",
            artifact_id="snapshot",
        )
