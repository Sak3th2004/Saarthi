"""Durable notification claims against an opt-in disposable Neo4j only."""

from concurrent.futures import ThreadPoolExecutor
import os
from threading import Barrier
from unittest.mock import Mock
from urllib.parse import urlparse

from neo4j import ManagedTransaction
import pytest

from saarthi_mcp.config import load_settings
from saarthi_mcp.directory import directory_revision
from saarthi_mcp.family_notifications import FamilyNotificationService
from saarthi_mcp.models import Person, Role
from saarthi_mcp.neo4j_repo import Neo4jRepository
from saarthi_mcp.ses_delivery import SesSettings

pytestmark = pytest.mark.skipif(os.getenv("SAARTHI_RUN_NEO4J_TESTS") != "1", reason="isolated Neo4j opt-in")


@pytest.fixture
def repo():
    settings = load_settings()
    assert settings.backend == "neo4j" and settings.neo4j is not None
    target = urlparse(settings.neo4j.uri)
    assert target.hostname in {"127.0.0.1", "localhost"} and target.port == 17687
    assert settings.neo4j.database == "neo4j"
    repository = Neo4jRepository.from_settings(settings.neo4j)
    repository.wipe()
    repository.upsert_person(Person(id="parent", name="Parent", role=Role.elder))
    repository.upsert_person(Person(id="family", name="Family", role=Role.family, email="family@example.test"))
    repository.apply_household_change(
        {"kind": "set_relationship", "from_person": "family", "to_person": "parent", "relation": "child"},
        directory_revision(repository.household_directory()),
    )
    try:
        yield repository
    finally:
        repository.wipe()
        repository.close()


def service(repo, mailer):
    return FamilyNotificationService(repo, mailer=mailer, ses_settings=SesSettings(
        sender="sender@example.test", region="us-east-1", approved_live_recipient=None))


def test_review_and_result_survive_database_reconnect(repo):
    mailer = Mock()
    mailer.send.return_value = "provider-id"
    first = service(repo, mailer)
    preview = first.review("parent", "A real arbitrary detail", actor="owner-sub")
    reopened = Neo4jRepository.from_settings(load_settings().neo4j)
    try:
        second = service(reopened, mailer)
        with pytest.raises(ValueError, match="unavailable"):
            second.confirm(preview["review_token"], confirmed=True, actor="other-sub")
        saved = second.confirm(preview["review_token"], confirmed=True, actor="owner-sub")
        assert saved.delivery_status == "accepted" and saved.delivered_to == []
        assert first.confirm(preview["review_token"], confirmed=True, actor="owner-sub") == saved
        mailer.send.assert_called_once()
        audits = repo._read("MATCH (:Person {id:'parent'})-[:EXPERIENCED]->(e:Event {type:'notify'}) RETURN e")
        assert len(audits) == 1 and audits[0]["e"]["actor"] == "owner-sub"
    finally:
        reopened.close()


def test_parallel_claims_have_one_winner(repo):
    first = service(repo, Mock())
    review = first.review("parent", "Call tomorrow", actor="owner-sub")
    barrier = Barrier(2)

    def claim(_):
        barrier.wait(timeout=10)
        return repo.claim_notification_review(first._key(review["review_token"]), "owner-sub")[1]

    with ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(claim, range(2))) == [False, True]


def test_claim_survives_restart_without_send_or_finish(repo):
    mailer = Mock()
    first = service(repo, mailer)
    preview = first.review("parent", "Call tomorrow")
    repo.claim_notification_review(first._key(preview["review_token"]), "local")
    reopened = Neo4jRepository.from_settings(load_settings().neo4j)
    try:
        result = service(reopened, mailer).confirm(preview["review_token"], confirmed=True)
        assert result.delivery_status == "uncertain" and result.submitted_to == []
        mailer.send.assert_not_called()
    finally:
        reopened.close()


def test_recipient_change_is_checked_in_claim_transaction(repo):
    mailer = Mock()
    notification = service(repo, mailer)
    review = notification.review("parent", "Hello")
    repo.apply_household_change({"kind": "set_person_email", "person_id": "family", "email": "new@example.test"},
                               directory_revision(repo.household_directory()))
    with pytest.raises(ValueError, match="recipients changed"):
        notification.confirm(review["review_token"], confirmed=True)
    mailer.send.assert_not_called()


def test_audit_failure_rolls_back_result_but_preserves_claim(repo, monkeypatch):
    mailer = Mock()
    mailer.send.return_value = "provider-id"
    notification = service(repo, mailer)
    review = notification.review("parent", "Hello")
    original = ManagedTransaction.run

    def fail_audit(self, query, *args, **kwargs):
        if "CREATE" in query and "type:'notify'" in query:
            raise RuntimeError("Injected audit failure")
        return original(self, query, *args, **kwargs)

    monkeypatch.setattr(ManagedTransaction, "run", fail_audit)
    assert notification.confirm(review["review_token"], confirmed=True).delivery_status == "uncertain"
    assert notification.confirm(review["review_token"], confirmed=True).delivery_status == "uncertain"
    mailer.send.assert_called_once()
    assert repo._read("MATCH (e:Event {type:'notify'}) RETURN e") == []
