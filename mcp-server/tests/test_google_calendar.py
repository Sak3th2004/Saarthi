"""OAuth boundary checks use real library URL generation, never live Google tokens."""
import json
import time
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
pytest.importorskip("google_auth_oauthlib")
from fastmcp import FastMCP
from fastmcp.server.auth.providers.jwt import RSAKeyPair
import httpx
from saarthi_mcp.auth import CognitoSettings, HouseholdTokenVerifier
from saarthi_mcp.google_calendar import (
    CalendarConfig, CalendarConnection, SCOPES, COOKIE,
    attach_calendar_routes, attach_authenticated_calendar_routes,
    configured_calendar,
)


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
        _, b, flow, subject = connection.pending[state]
        connection.pending[state] = (time.monotonic() - 1, b, flow, subject)
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
    expiry, b, _, subject = connection.pending[state]
    connection.pending[state] = (expiry, b, flow, subject)
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


async def test_link_from_chat_can_open_landing_but_not_authorize_or_read_data(connection):
    server = FastMCP("calendar-link-test")
    attach_calendar_routes(server, connection)
    navigation = {"sec-fetch-site": "cross-site", "sec-fetch-mode": "navigate", "sec-fetch-dest": "document"}
    transport = httpx.ASGITransport(app=server.http_app(), client=("127.0.0.1", 32100))
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8080") as client:
        landing = await client.get("/oauth/google/connect", headers=navigation)
        assert landing.status_code == 200
        assert "Continue to Google" in landing.text
        assert not connection.pending  # viewing the page cannot start authorization
        assert (await client.post("/oauth/google/connect", headers=navigation)).status_code == 403
        assert (await client.get("/oauth/google/events", headers=navigation)).status_code == 403
        assert (await client.get("/oauth/google/connect", headers={**navigation, "sec-fetch-dest": "iframe"})).status_code == 403
        assert (await client.get("/oauth/google/connect", headers={**navigation, "host": "external.example"})).status_code == 403
        result = await client.post("/oauth/google/connect", headers={"sec-fetch-site": "same-origin"})
        assert result.status_code == 303
        assert "https://accounts.google.com" in result.headers["content-security-policy"]
        connection.store.load.assert_not_called()


async def test_consent_form_preserves_origin_but_callback_keeps_codes_private(connection, caplog):
    server = FastMCP("calendar-origin-test")
    attach_calendar_routes(server, connection)
    transport = httpx.ASGITransport(app=server.http_app(), client=("127.0.0.1", 32100))
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8080") as client:
        page = await client.get("/oauth/google/connect")
        assert page.headers["referrer-policy"] == "same-origin"
        denied = await client.post("/oauth/google/connect", headers={"origin": "null"})
        assert denied.status_code == 403
        assert "origin_kind=null" in caplog.text
        sent = await client.post("/oauth/google/connect", headers={
            "origin": "http://127.0.0.1:8080", "sec-fetch-site": "same-origin",
            "sec-fetch-mode": "navigate", "sec-fetch-dest": "document"})
        assert sent.status_code == 303
        assert urlsplit(sent.headers["location"]).hostname == "accounts.google.com"
        assert sent.headers["referrer-policy"] == "no-referrer"
        callback = await client.get("/oauth/google/callback")
        assert callback.headers["referrer-policy"] == "no-referrer"


def test_configured_calendar_empty_path_skips(monkeypatch):
    monkeypatch.setenv("SAARTHI_GOOGLE_CONFIG", "")
    settings = SimpleNamespace(host="127.0.0.1", port=8080, cognito=None)
    assert configured_calendar(settings) is None


def test_configured_calendar_missing_file_fails_clearly(monkeypatch, tmp_path):
    missing = tmp_path / "absent-google-calendar.env"
    monkeypatch.setenv("SAARTHI_GOOGLE_CONFIG", str(missing))
    settings = SimpleNamespace(host="127.0.0.1", port=8080, cognito=None)
    with pytest.raises(ValueError, match="missing file"):
        configured_calendar(settings)


async def test_cognito_connect_returns_loopback_handoff_that_sets_cookie_on_8080(connection, monkeypatch):
    subject = str(uuid4())
    settings = CognitoSettings('us-east-1', 'us-east-1_test', 'client123',
                               'https://notebook.example/mcp', frozenset([subject]))
    key = RSAKeyPair.generate()
    verifier = HouseholdTokenVerifier(settings)
    monkeypatch.setattr(verifier, '_get_verification_key', AsyncMock(return_value=key.public_key))
    token = key.create_token(subject=subject, issuer=settings.issuer, audience=settings.resource_url,
                             scopes=[settings.scope], additional_claims={'token_use': 'access', 'client_id': settings.client_id})
    server = FastMCP('calendar-handoff-test', auth=verifier)
    attach_authenticated_calendar_routes(server, connection, verifier, settings.resource_url)
    transport = httpx.ASGITransport(app=server.http_app(), client=('127.0.0.1', 32100))
    async with httpx.AsyncClient(transport=transport, base_url='http://127.0.0.1:8080',
                                 headers={'Origin': 'https://notebook.example', 'Authorization': f'Bearer {token}'}) as client:
        started = await client.post('/household/calendar/connect', json={},
                                    headers={'Content-Type': 'application/json'})
        assert started.status_code == 200
        handoff = started.json()['handoff_url']
        assert handoff.startswith('http://127.0.0.1:8080/oauth/google/household-connect?h=')
        assert 'set-cookie' not in {k.lower() for k in started.headers.keys()}
        path = urlsplit(handoff).path + '?' + urlsplit(handoff).query
        redirected = await client.get(path)
        assert redirected.status_code == 303
        assert urlsplit(redirected.headers['location']).hostname == 'accounts.google.com'
        assert COOKIE in redirected.headers.get('set-cookie', '')
        assert connection.pending
        assert (await client.get(path)).status_code == 400


async def test_cognito_events_accepts_same_origin_get_without_origin_header(connection, monkeypatch):
    """Browsers often omit Origin on same-origin GET; Bearer + Sec-Fetch-Site must still work."""
    subject = str(uuid4())
    settings = CognitoSettings('us-east-1', 'us-east-1_test', 'client123',
                               'https://notebook.example/mcp', frozenset([subject]))
    key = RSAKeyPair.generate()
    verifier = HouseholdTokenVerifier(settings)
    monkeypatch.setattr(verifier, '_get_verification_key', AsyncMock(return_value=key.public_key))
    token = key.create_token(subject=subject, issuer=settings.issuer, audience=settings.resource_url,
                             scopes=[settings.scope], additional_claims={'token_use': 'access', 'client_id': settings.client_id})
    connection.upcoming = Mock(return_value={
        'source': 'google_calendar', 'events': [], 'more_available': False,
    })
    server = FastMCP('calendar-events-origin-test', auth=verifier)
    attach_authenticated_calendar_routes(server, connection, verifier, settings.resource_url)
    transport = httpx.ASGITransport(app=server.http_app(), client=('127.0.0.1', 32100))
    async with httpx.AsyncClient(transport=transport, base_url='http://test') as client:
        missing_origin = await client.get(
            '/household/calendar/events',
            headers={'Authorization': f'Bearer {token}', 'Sec-Fetch-Site': 'same-origin'},
        )
        assert missing_origin.status_code == 200
        assert missing_origin.json()['source'] == 'google_calendar'
        connection.upcoming.assert_called_with(subject=subject)

        wrong_origin = await client.get(
            '/household/calendar/events',
            headers={
                'Authorization': f'Bearer {token}',
                'Origin': 'https://attacker.example',
                'Sec-Fetch-Site': 'same-origin',
            },
        )
        assert wrong_origin.status_code == 401

        cross_site = await client.get(
            '/household/calendar/events',
            headers={'Authorization': f'Bearer {token}', 'Sec-Fetch-Site': 'cross-site'},
        )
        assert cross_site.status_code == 401


def test_oauth_completion_saves_tokens_for_starting_subject_not_connection_default(connection):
    owner, other = str(uuid4()), str(uuid4())
    connection.subject = other
    state, browser = fake_exchange(connection)
    expiry, cookie, flow, _ = connection.pending[state]
    connection.pending[state] = (expiry, cookie, flow, owner)
    connection.finish(state, browser, "synthetic-code")
    assert connection.store.save.call_args.kwargs == {"subject": owner}


def test_authenticated_calendar_start_binds_explicit_subject(connection):
    owner, other = str(uuid4()), str(uuid4())
    connection.subject = other
    connection.start(subject=owner)
    assert len(connection.pending) == 1
    assert next(iter(connection.pending.values()))[3] == owner
