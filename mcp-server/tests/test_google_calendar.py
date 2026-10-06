"""OAuth boundary checks use real library URL generation, never live Google tokens."""
import json
import time
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
from unittest.mock import Mock

import pytest
pytest.importorskip("google_auth_oauthlib")
from fastmcp import FastMCP
import httpx
from saarthi_mcp.google_calendar import CalendarConfig, CalendarConnection, SCOPES, COOKIE, attach_calendar_routes


@pytest.fixture
def connection():
    return CalendarConnection(CalendarConfig("test.apps.googleusercontent.com", "secret-test-only",
        "owner@example.com", "http://127.0.0.1:8080/oauth/google/callback"), Mock())


def begin(connection):
    url, browser = connection.start()
    return parse_qs(urlsplit(url).query)["state"][0], browser, url


def test_official_flow_has_pkce_state_exact_redirect_and_offline_access(connection):
    state, browser, url = begin(connection)
    q = parse_qs(urlsplit(url).query)
    assert urlsplit(url).hostname == "accounts.google.com"
    assert q["code_challenge_method"] == ["S256"]
    assert q["code_challenge"] and state and browser
    assert q["access_type"] == ["offline"]
    assert q["redirect_uri"] == [connection.config.redirect_uri]
    assert "secret-test-only" not in url
    assert "client_secret" not in q


@pytest.mark.parametrize("issue", ["state", "cookie", "expired", "denied", "missing_code"])
def test_invalid_callback_never_saves(connection, issue):
    state, browser, _ = begin(connection)
    if issue == "state": state = "wrong"
    if issue == "cookie": browser = "wrong"
    if issue == "expired":
        _, b, flow = connection.pending[state]
        connection.pending[state] = (time.monotonic() - 1, b, flow)
    with pytest.raises(ValueError):
        connection.finish(state, browser, None if issue == "missing_code" else "code", issue == "denied")
    connection.store.save.assert_not_called()


def fake_exchange(connection, *, email="owner@example.com", scopes=SCOPES, refresh="refresh-test", error=False):
    state, browser, _ = begin(connection)
    credentials = SimpleNamespace(granted_scopes=scopes, scopes=SCOPES, refresh_token=refresh,
        to_json=lambda: json.dumps({"refresh_token": refresh}))
    flow = Mock(credentials=credentials)
    if error: flow.fetch_token.side_effect = RuntimeError("SENSITIVE-TEST-TOKEN")
    session = flow.authorized_session.return_value.__enter__ = Mock()
    flow.authorized_session.return_value.__exit__ = Mock()
    session.return_value.get.return_value.json.return_value = {"email": email, "verified_email": True}
    expiry, b, _ = connection.pending[state]
    connection.pending[state] = (expiry, b, flow)
    return state, browser


def test_correct_account_saved_once_and_replay_rejected(connection):
    state, browser = fake_exchange(connection)
    connection.finish(state, browser, "code")
    connection.store.save.assert_called_once()
    with pytest.raises(ValueError): connection.finish(state, browser, "code")
    connection.store.save.assert_called_once()


@pytest.mark.parametrize("options", [{"email": "other@example.com"}, {"scopes": ["openid"]},
                                    {"refresh": None}, {"error": True}])
def test_wrong_account_missing_permission_and_provider_errors_are_not_saved(connection, options):
    state, browser = fake_exchange(connection, **options)
    with pytest.raises(ValueError) as exc:
        connection.finish(state, browser, "code")
    assert "SENSITIVE" not in str(exc.value)
    connection.store.save.assert_not_called()


async def test_local_routes_require_correct_host_and_origin(connection):
    server = FastMCP("calendar-test")
    attach_calendar_routes(server, connection)
    transport = httpx.ASGITransport(app=server.http_app(), client=("127.0.0.1", 32100))
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8080") as client:
        assert (await client.get("/oauth/google/connect")).status_code == 200
        assert (await client.post("/oauth/google/connect", headers={"origin": "https://other.example"})).status_code == 403
        assert (await client.get("/oauth/google/events", headers={"host": "other.example"})).status_code == 403
        result = await client.post("/oauth/google/connect")
        assert result.status_code == 303
        assert COOKIE in result.cookies
        assert "HttpOnly" in result.headers["set-cookie"]
        assert result.headers["cache-control"] == "no-store"
        bad = await client.get("/oauth/google/callback?state=wrong&code=private-code")
        assert bad.status_code == 400
        assert "private-code" not in bad.text
        connection.store.save.assert_not_called()


def test_missing_tokens_fail_without_fabricated_events(connection):
    connection.store.load.return_value = None
    with pytest.raises(ValueError, match="Could not read Calendar"):
        connection.upcoming()


def test_real_event_response_is_not_replaced_by_fixture_data(connection, monkeypatch):
    import google.auth.transport.requests as transport
    connection.store.load.return_value = {"account": "owner@example.com", "credentials": {
        "token": "test-access", "expiry": "2099-01-01T00:00:00Z", "refresh_token": "test-refresh", "token_uri": "https://oauth2.googleapis.com/token",
        "client_id": connection.config.client_id, "client_secret": "test-secret"}}
    session = Mock()
    session.__enter__ = Mock(return_value=session)
    session.__exit__ = Mock()
    session.get.return_value.json.return_value = {"items": [{"id": "arbitrary-id", "summary": "User supplied appointment",
        "start": {"date": "2030-03-04"}, "end": {"date": "2030-03-05"}, "status": "confirmed"}], "nextPageToken": "more"}
    monkeypatch.setattr(transport, "AuthorizedSession", lambda c: session)
    result = connection.upcoming()
    assert result["events"][0]["summary"] == "User supplied appointment"
    assert result["events"][0]["start"] == {"date": "2030-03-04"}
    assert result["more_available"] is True
    assert session.get.call_args.kwargs["params"]["maxResults"] == 25


@pytest.mark.parametrize("uri", ["https://public.example/oauth/google/callback", "http://127.0.0.1:8080/wrong"])
def test_callback_config_rejects_unimplemented_hosts_and_routes(uri):
    with pytest.raises(ValueError):
        CalendarConfig("test.apps.googleusercontent.com", "secret", "owner@example.com", uri)
