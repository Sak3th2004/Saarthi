"""Family notification review/confirm with mocked SES and scoped recipients."""

from __future__ import annotations

from unittest.mock import Mock
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from saarthi_mcp.models import Person, Role, Urgency
from saarthi_mcp.repository import InMemoryRepository
from saarthi_mcp.server import build_server
from saarthi_mcp.ses_delivery import SesMailer, SesSettings
from saarthi_mcp.family_notifications import FamilyNotificationService
from saarthi_mcp.directory import directory_revision


@pytest.fixture
def household_repo():
    repo = InMemoryRepository()
    repo.add_person(Person(id="parent-1", name="Ravi", role=Role.elder))
    repo.add_person(
        Person(id="caregiver-1", name="Saketh", role=Role.family, email="caregiver@example.test"),
    )
    repo.add_person(
        Person(id="fam-1", name="Sample family", role=Role.family, email="family@example.com"),
    )
    repo.apply_household_change({"kind": "set_relationship", "from_person": "caregiver-1",
                                "to_person": "parent-1", "relation": "son"},
                               directory_revision(repo.household_directory()))
    return repo


@pytest.fixture
def ses_service(household_repo):
    settings = SesSettings(sender="verified@example.test", region="us-east-1", approved_live_recipient=None)
    client = Mock()
    client.send_email.return_value = {"MessageId": "ses-msg-123"}
    mailer = SesMailer(settings, client=client)
    return FamilyNotificationService(household_repo, ses_settings=settings, mailer=mailer), client


def test_scoped_recipients_require_actual_relationship(ses_service):
    service, _ = ses_service
    preview = service.review("Ravi", "Dinner went well", Urgency.info)
    assert preview["recipient_emails"] == ["caregiver@example.test"]
    assert preview["delivery_status"] == "pending"


def test_confirm_sends_once_and_is_idempotent(ses_service):
    service, client = ses_service
    preview = service.review("Ravi", "Call when free", Urgency.warning)
    first = service.confirm(preview["review_token"], confirmed=True)
    restarted = FamilyNotificationService(service.repository, ses_settings=service.ses_settings, mailer=service.mailer)
    second = restarted.confirm(preview["review_token"], confirmed=True)
    assert first.delivery_status == "accepted"
    assert first.delivered_to == []
    assert first.submitted_to == ["email:caregiver@example.test"]
    assert second == first
    client.send_email.assert_called_once()


def test_confirm_without_approval_rejected(ses_service):
    service, client = ses_service
    preview = service.review("Ravi", "Hello", Urgency.info)
    with pytest.raises(ValueError, match="explicitly confirm"):
        service.confirm(preview["review_token"], confirmed=False)
    client.send_email.assert_not_called()


def test_ses_failure_is_honest(ses_service):
    service, client = ses_service
    client.send_email.side_effect = Exception("network")
    preview = service.review("Ravi", "Please check in", Urgency.urgent)
    result = service.confirm(preview["review_token"], confirmed=True)
    assert result.delivery_status == "uncertain"
    assert result.delivered_to == []


async def test_mcp_review_and_confirm_tools(household_repo, monkeypatch):
    monkeypatch.setenv("SES_SENDER_EMAIL", "verified@example.test")
    monkeypatch.setenv("AWS_REGION", "us-east-1")

    def fake_send(self, *, to_addresses, subject, body_text):
        assert to_addresses == ["caregiver@example.test"]
        return "ses-msg-999"

    monkeypatch.setattr("saarthi_mcp.ses_delivery.SesMailer.send", fake_send)

    async with Client(build_server(household_repo)) as mcp:
        review = await mcp.call_tool(
            "review_family_notification",
            {"person": "Ravi", "message": "Notebook test", "urgency": "info"},
        )
        assert review.data["delivery_status"] == "pending"
        confirm = await mcp.call_tool(
            "confirm_family_notification",
            {"review_token": review.data["review_token"], "confirmed": True},
        )
        assert confirm.data["delivery_status"] == "accepted"


async def test_record_only_never_sends_even_when_sample_has_relationship(monkeypatch):
    from saarthi_mcp.repository import seeded_repository

    # Avoid live Bedrock for this recipient-scoping assertion (local .env may set bedrock).
    monkeypatch.setenv("SAARTHI_AGENTS", "off")

    async with Client(build_server(seeded_repository())) as mcp:
        res = await mcp.call_tool(
            "notify_family",
            {"person": "dad", "message": "Sample only", "urgency": "info"},
        )
    assert res.data["delivery_status"] == "recorded_only"
    assert all(channel.startswith("email:") for channel in res.data["candidate_channels"])
    assert res.data["delivered_to"] == []


async def test_review_without_recipients_raises(household_repo):
    repo = InMemoryRepository()
    repo.add_person(Person(id="solo", name="Solo", role=Role.elder))
    async with Client(build_server(repo)) as mcp:
        with pytest.raises(ToolError, match="No household family email"):
            await mcp.call_tool(
                "review_family_notification",
                {"person": "Solo", "message": "Hello", "urgency": "info"},
            )


def test_distinct_explicit_reviews_can_send_same_message_again(ses_service):
    service, client = ses_service
    for _ in range(2):
        preview = service.review("parent-1", "Please call")
        assert service.confirm(preview["review_token"], confirmed=True).delivery_status == "accepted"
    assert client.send_email.call_count == 2


def test_actor_binding_and_expiry(ses_service, monkeypatch):
    service, client = ses_service
    preview = service.review("parent-1", "Please call", actor="caregiver-a")
    with pytest.raises(ValueError, match="unavailable"):
        service.confirm(preview["review_token"], confirmed=True, actor="caregiver-b")
    monkeypatch.setattr("saarthi_mcp.family_recipients.time.time", lambda: float("inf"))
    with pytest.raises(ValueError, match="expired"):
        service.confirm(preview["review_token"], confirmed=True, actor="caregiver-a")
    client.send_email.assert_not_called()


def test_recipient_edit_invalidates_existing_review(ses_service):
    service, client = ses_service
    preview = service.review("parent-1", "Please call")
    repo = service.repository
    repo.apply_household_change({"kind": "set_person_email", "person_id": "caregiver-1", "email": "different@example.test"},
                               directory_revision(repo.household_directory()))
    with pytest.raises(ValueError, match="recipients changed"):
        service.confirm(preview["review_token"], confirmed=True)
    client.send_email.assert_not_called()


def test_two_services_cannot_submit_same_review_twice(ses_service):
    service, client = ses_service
    started, release = Event(), Event()

    def slow_send(**kwargs):
        started.set()
        assert release.wait(5)
        return {"MessageId": "accepted"}

    client.send_email.side_effect = slow_send
    preview = service.review("parent-1", "Please call")
    other = FamilyNotificationService(service.repository, ses_settings=service.ses_settings, mailer=service.mailer)
    with ThreadPoolExecutor(2) as pool:
        pending = pool.submit(service.confirm, preview["review_token"], confirmed=True)
        assert started.wait(5)
        try:
            assert other.confirm(preview["review_token"], confirmed=True).delivery_status == "uncertain"
        finally:
            release.set()
        assert pending.result().delivery_status == "accepted"
    client.send_email.assert_called_once()


def test_after_send_persistence_failure_never_sends_again(ses_service, monkeypatch):
    service, client = ses_service
    preview = service.review("parent-1", "Please call")
    monkeypatch.setattr(service.repository, "finish_notification_review", Mock(side_effect=RuntimeError("database down")))
    assert service.confirm(preview["review_token"], confirmed=True).delivery_status == "uncertain"
    restarted = FamilyNotificationService(service.repository, ses_settings=service.ses_settings, mailer=service.mailer)
    assert restarted.confirm(preview["review_token"], confirmed=True).delivery_status == "uncertain"
    client.send_email.assert_called_once()


def test_claim_failure_never_submits(ses_service, monkeypatch):
    service, client = ses_service
    preview = service.review("parent-1", "Please call")
    monkeypatch.setattr(service.repository, "claim_notification_review", Mock(side_effect=RuntimeError("database down")))
    with pytest.raises(RuntimeError):
        service.confirm(preview["review_token"], confirmed=True)
    client.send_email.assert_not_called()


def test_ses_explicit_rejection_is_failed_and_not_retried(ses_service):
    from botocore.exceptions import ClientError

    service, client = ses_service
    client.send_email.side_effect = ClientError({"Error": {"Code": "MessageRejected", "Message": "private"}}, "SendEmail")
    preview = service.review("parent-1", "Please call")
    result = service.confirm(preview["review_token"], confirmed=True)
    assert result.delivery_status == "failed" and result.submitted_to == result.delivered_to == []
    assert service.confirm(preview["review_token"], confirmed=True) == result
    client.send_email.assert_called_once()


def test_recorded_only_review_has_no_submission(household_repo):
    service = FamilyNotificationService(household_repo)
    review = service.review("parent-1", "Hello")
    result = service.confirm(review["review_token"], confirmed=True)
    assert result.delivery_status == "recorded_only" and result.submitted_to == result.delivered_to == []


def test_ses_constructed_client_disables_retries(monkeypatch):
    client_factory = Mock()
    monkeypatch.setattr("boto3.client", client_factory)
    settings = SesSettings(sender="verified@example.test", region="us-east-1", approved_live_recipient=None)
    SesMailer(settings)._client_or_create()
    config = client_factory.call_args.kwargs["config"]
    assert config.retries["total_max_attempts"] == 1


async def test_notification_routes_bind_actor_and_enforce_origin(ses_service):
    from types import SimpleNamespace
    from fastmcp import FastMCP
    from httpx import ASGITransport, AsyncClient
    from saarthi_mcp.family_notifications import attach_family_notification_routes

    service, mailer = ses_service

    class Verifier:
        async def verify_token(self, token):
            return SimpleNamespace(claims={"sub": token}) if token in {"owner", "other"} else None

    app = FastMCP("notifications-test")
    attach_family_notification_routes(app, service, Verifier(), "https://notebook.example/mcp")
    headers = {"Authorization": "Bearer owner", "Origin": "https://notebook.example"}
    async with AsyncClient(transport=ASGITransport(app=app.http_app()), base_url="https://notebook.example") as client:
        payload = {"person": "parent-1", "message": "Hello", "urgency": "info"}
        assert (await client.post("/household/notify/review", json=payload)).status_code == 401
        assert (await client.post("/household/notify/review", json=payload,
                                 headers={**headers, "Authorization": "Bearer invalid"})).status_code == 401
        assert (await client.post("/household/notify/review", json=payload,
                                 headers={**headers, "Origin": "https://attacker.example"})).status_code == 403
        assert (await client.post("/household/notify/review", json=payload,
                                 headers={**headers, "Sec-Fetch-Site": "cross-site"})).status_code == 403
        assert (await client.post("/household/notify/review", json={**payload, "actor": "other"}, headers=headers)).status_code == 400
        preview = await client.post("/household/notify/review", json=payload, headers=headers)
        assert preview.status_code == 200
        confirm = {"review_token": preview.json()["review_token"], "confirmed": True}
        assert (await client.post("/household/notify/confirm", json=confirm,
                                 headers={**headers, "Authorization": "Bearer other"})).status_code == 400
        mailer.send_email.assert_not_called()
        assert (await client.post("/household/notify/confirm", json={**confirm, "confirmed": "yes"}, headers=headers)).status_code == 400
        result = await client.post("/household/notify/confirm", json=confirm, headers=headers)
        assert result.status_code == 200 and result.json()["delivery_status"] == "accepted"
        assert result.headers["cache-control"] == "no-store"
        replay = await client.post("/household/notify/confirm", json=confirm, headers=headers)
        assert replay.json() == result.json()
    mailer.send_email.assert_called_once()
