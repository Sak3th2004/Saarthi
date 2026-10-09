"""Arbitrary reviewed appointments, rejected changes, and recovery without duplicate writes."""
from datetime import datetime, timedelta, timezone
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import httpx
import pytest
from fastmcp import FastMCP, Client

from saarthi_mcp.calendar_appointments import CalendarAppointments, attach_appointment_routes, local_time, matching_event
from saarthi_mcp.google_calendar import CalendarConfig, CalendarConnection
from saarthi_mcp.models import Person, Role
from saarthi_mcp.repository import InMemoryRepository
from zoneinfo import ZoneInfo


@pytest.fixture
def setup():
    repo = InMemoryRepository()
    person = Person(id=uuid4().hex, name="Entered member " + uuid4().hex, role=Role.elder)
    repo.add_person(person)
    connection = Mock(config=SimpleNamespace(account="calendar@example.com"))
    service = CalendarAppointments(connection, repo)
    start = (datetime.now(timezone.utc) + timedelta(days=10)).replace(second=0, microsecond=0)
    draft = {"person": person.id, "title": "User note " + uuid4().hex,
             "start_local": start.strftime("%Y-%m-%dT%H:%M"),
             "end_local": (start + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M"), "time_zone": "Asia/Kolkata"}
    return service, connection, repo, draft


def test_review_only_then_confirm_with_stable_id_and_actual_memory(setup):
    service, connection, repo, draft = setup
    review = service.review(draft)
    assert review["start"].endswith("+05:30")
    assert review["title"] == draft["title"]
    connection.create_confirmed.assert_not_called()
    assert repo.upcoming_appointments(draft["person"]) == []
    result = service.confirm({"review_token": review["review_token"], "confirmed": True})
    assert result["status"] == "saved"
    again = service.review(draft)
    second = service.confirm({"review_token": again["review_token"], "confirmed": True})
    assert second["calendar_event_id"] == result["calendar_event_id"]
    assert len(repo.upcoming_appointments(draft["person"])) == 1
    assert len(repo.recent_events(draft["person"])) == 1
    answer, evidence = repo.query_memory(draft["person"], draft["title"])
    assert draft["title"] in answer and evidence


@pytest.mark.parametrize("change", [{"confirmed": False}, {"confirmed": "true"}, {"title": "Unreviewed"}, {"review_token": "unknown"}])
def test_no_external_write_without_matching_explicit_review(setup, change):
    service, connection, _, draft = setup
    review = service.review(draft)
    with pytest.raises(ValueError):
        service.confirm({"review_token": review["review_token"], "confirmed": True, **change})
    connection.create_confirmed.assert_not_called()


def test_expired_review_does_not_write(setup):
    service, connection, _, draft = setup
    review = service.review(draft)
    service.reviews[review["review_token"]]["expires"] = 0
    with pytest.raises(ValueError, match="expired"):
        service.confirm({"review_token": review["review_token"], "confirmed": True})
    connection.create_confirmed.assert_not_called()


def test_calendar_success_graph_failure_recovers_with_same_event(setup, monkeypatch):
    service, connection, repo, draft = setup
    review = service.review(draft)
    confirm = {"review_token": review["review_token"], "confirmed": True}
    save = repo.save_calendar_appointment
    monkeypatch.setattr(repo, "save_calendar_appointment", Mock(side_effect=RuntimeError("private error")))
    partial = service.confirm(confirm)
    assert partial["status"] == "calendar_saved_memory_pending"
    monkeypatch.setattr(repo, "save_calendar_appointment", save)
    success = service.confirm(confirm)
    assert success["status"] == "saved"
    assert success["calendar_event_id"] == partial["calendar_event_id"]
    assert connection.create_confirmed.call_args_list[0] == connection.create_confirmed.call_args_list[1]


@pytest.mark.parametrize("date", ["2030-03-10T02:30", "2030-11-03T01:30"])
def test_daylight_saving_clock_gap_or_repeat_is_not_guessed(date):
    with pytest.raises(ValueError): local_time(date, ZoneInfo("America/New_York"))


async def test_route_requires_local_json_origin_and_review_does_not_write(setup):
    service, connection, _, draft = setup
    server = FastMCP("appointments")
    attach_appointment_routes(server, service)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.http_app(), client=("127.0.0.1", 1)),
                                 base_url="http://127.0.0.1:8080") as client:
        path = "/oauth/google/appointments/review"
        assert (await client.post(path, json=draft)).status_code == 403
        assert (await client.post(path, json=draft, headers={"origin": "https://other.example"})).status_code == 403
        result = await client.post(path, json=draft, headers={"origin": "http://127.0.0.1:5173"})
        assert result.status_code == 200
        assert result.json()["title"] == draft["title"]
        connection.create_confirmed.assert_not_called()


async def test_mcp_review_and_confirmation_use_same_calendar_memory_service(setup):
    service, connection, repo, draft = setup
    server = FastMCP("appointment-tools")
    attach_appointment_routes(server, service)
    async with Client(server) as client:
        review = (await client.call_tool("review_calendar_appointment", draft)).data
        connection.create_confirmed.assert_not_called()
        refused = await client.call_tool("confirm_calendar_appointment", {"review_token": review["review_token"]}, raise_on_error=False)
        assert refused.is_error
        connection.create_confirmed.assert_not_called()
        saved = (await client.call_tool("confirm_calendar_appointment", {"review_token": review["review_token"], "confirmed": True})).data
        assert saved["status"] == "saved"
        assert len(repo.upcoming_appointments(draft["person"])) == 1


@pytest.mark.parametrize("scenario", ["insert", "existing", "conflict", "mismatched", "timeout"])
def test_google_insert_reconciles_existing_id_without_duplicate_posts(setup, monkeypatch, scenario):
    pytest.importorskip("google_auth_oauthlib")
    import google.auth.transport.requests as transport
    service, _, _, draft = setup
    review = service.review(draft)
    body = service.reviews[review["review_token"]]["body"]
    existing = {**deepcopy(body), "status": "confirmed"}
    if scenario == "mismatched": existing["summary"] = "Changed externally"
    store = Mock()
    config = CalendarConfig("test.apps.googleusercontent.com", "secret", "calendar@example.com", "http://127.0.0.1:8080/oauth/google/callback")
    store.load.return_value = {"account": config.account, "credentials": {"token": "access", "refresh_token": "refresh",
        "expiry": "2099-01-01T00:00:00Z", "client_id": config.client_id, "client_secret": "secret"}}
    connection = CalendarConnection(config, store)
    session = Mock()
    session.__enter__ = Mock(return_value=session)
    session.__exit__ = Mock()
    first = Mock(status_code=200 if scenario in {"existing", "mismatched"} else 404)
    first.json.return_value = existing
    retry = Mock(status_code=200)
    retry.json.return_value = existing
    session.get.side_effect = [first, retry]
    inserted = Mock(status_code=409 if scenario == "conflict" else 200)
    inserted.json.return_value = existing
    session.post.return_value = inserted
    if scenario == "timeout": session.post.side_effect = TimeoutError("token must not leak")
    monkeypatch.setattr(transport, "AuthorizedSession", lambda c: session)
    if scenario in {"timeout", "mismatched"}:
        with pytest.raises(ValueError, match="not confirmed") as exc:
            connection.create_confirmed(body)
        assert "must not leak" not in str(exc.value)
    else:
        assert matching_event(connection.create_confirmed(body), body)
    assert session.post.call_count == (0 if scenario in {"existing", "mismatched"} else 1)
