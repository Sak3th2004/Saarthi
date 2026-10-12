"""Weekly digest assembly from saved records only."""

from datetime import timedelta

from saarthi_mcp.models import DoseStatus, Person, Role, Urgency
from saarthi_mcp.repository import InMemoryRepository, now_utc
from saarthi_mcp.weekly_digest import build_weekly_digest
from saarthi_mcp.family_notifications import FamilyNotificationService
from saarthi_mcp.directory import directory_revision
from saarthi_mcp.ses_delivery import SesMailer, SesSettings
from unittest.mock import Mock


def _household():
    repo = InMemoryRepository()
    repo.add_person(Person(id="parent-1", name="Ravi", role=Role.elder))
    repo.add_person(Person(id="caregiver-1", name="Saketh", role=Role.family, email="caregiver@example.test"))
    repo.apply_household_change(
        {"kind": "set_relationship", "from_person": "caregiver-1", "to_person": "parent-1", "relation": "son"},
        directory_revision(repo.household_directory()),
    )
    return repo


def test_digest_summarizes_saved_records_without_medical_claims():
    repo = _household()
    repo.add_dose("parent-1", "Metformin", DoseStatus.taken, now_utc() - timedelta(days=1))
    repo.add_dose("parent-1", "Metformin", DoseStatus.missed, now_utc() - timedelta(hours=5))
    repo.add_event("parent-1", "note", "Physio called back", now_utc() - timedelta(days=2))
    body = build_weekly_digest(repo, "parent-1", days=7)
    assert "Saarthi weekly household digest for Ravi" in body
    assert "1 taken, 1 missed" in body
    assert "Physio called back" in body
    assert "not medical advice" in body
    assert "how someone is doing" in body


def test_digest_review_confirm_uses_digest_subject():
    repo = _household()
    settings = SesSettings(sender="verified@example.test", region="us-east-1", approved_live_recipient=None)
    client = Mock()
    client.send_email.return_value = {"MessageId": "digest-1"}
    service = FamilyNotificationService(repo, ses_settings=settings, mailer=SesMailer(settings, client=client))
    preview = service.review_weekly_digest("parent-1", days=7)
    assert preview["kind"] == "digest"
    assert "weekly household digest" in preview["message"]
    result = service.confirm(preview["review_token"], confirmed=True)
    assert result.delivery_status == "accepted"
    assert client.send_email.call_args.kwargs["Message"]["Subject"]["Data"] == "Saarthi weekly digest · Ravi"
