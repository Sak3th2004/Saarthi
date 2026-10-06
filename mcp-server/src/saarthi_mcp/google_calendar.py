"""Opt-in, loopback-only Calendar connection. Never prints credentials or callback URLs.

The deployed application must provide authenticated per-household token storage before
enabling these routes remotely. This adapter deliberately rejects public bindings.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from html import escape
import json
import secrets
import threading
import time
from urllib.parse import urlsplit

from starlette.concurrency import run_in_threadpool
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse

SCOPES = ["openid", "https://www.googleapis.com/auth/userinfo.email",
          "https://www.googleapis.com/auth/calendar.events.owned"]
AUTH_URI = "https://accounts.google.com/o/oauth2/auth"
TOKEN_URI = "https://oauth2.googleapis.com/token"
COOKIE = "saarthi_calendar_flow"


@dataclass(frozen=True)
class CalendarConfig:
    client_id: str
    client_secret: str = field(repr=False)
    account: str
    redirect_uri: str

    def __post_init__(self):
        uri = urlsplit(self.redirect_uri)
        if (uri.scheme != "http" or uri.hostname != "127.0.0.1" or uri.port != 8080
                or uri.path != "/oauth/google/callback" or uri.query or uri.fragment or uri.username):
            raise ValueError("Calendar currently requires the configured loopback callback on port 8080.")
        if not self.client_id.endswith(".apps.googleusercontent.com") or not self.client_secret or "@" not in self.account:
            raise ValueError("Calendar client ID, client secret and account must be configured.")


class CalendarConnection:
    def __init__(self, config: CalendarConfig, store):
        self.config, self.store = config, store
        self.pending = {}
        self.lock = threading.Lock()
        self.io_lock = threading.Lock()

    def start(self):
        from google_auth_oauthlib.flow import Flow
        flow = Flow.from_client_config({"web": {
            "client_id": self.config.client_id, "client_secret": self.config.client_secret,
            "auth_uri": AUTH_URI, "token_uri": TOKEN_URI,
        }}, scopes=SCOPES, redirect_uri=self.config.redirect_uri, autogenerate_code_verifier=True)
        url, state = flow.authorization_url(access_type="offline", prompt="consent",
                                             login_hint=self.config.account)
        browser = secrets.token_urlsafe(32)
        now = time.monotonic()
        with self.lock:
            self.pending = {k: v for k, v in self.pending.items() if v[0] > now}
            if len(self.pending) >= 10:
                raise ValueError("Too many connection attempts. Please wait ten minutes.")
            self.pending[state] = (now + 600, browser, flow)
        return url, browser

    def finish(self, state, browser, code, denied=False):
        with self.lock:
            pending = self.pending.get(state)
            if (not pending or pending[0] <= time.monotonic() or not browser
                    or not secrets.compare_digest(pending[1], browser)):
                raise ValueError("Connection expired or did not start in this browser. Start again.")
            del self.pending[state]  # one use, including denial and failed exchanges
        if denied:
            raise ValueError("Calendar access was declined. No connection was saved.")
        if not code:
            raise ValueError("Google did not return an authorization code. Start again.")
        try:
            flow = pending[2]
            flow.fetch_token(code=code, timeout=15)
            credentials = flow.credentials
            granted = credentials.granted_scopes or credentials.scopes or []
            if not set(SCOPES).issubset(set(granted)) or not credentials.refresh_token:
                raise ValueError("Required permission or offline access missing")
            with flow.authorized_session() as session:
                response = session.get("https://www.googleapis.com/oauth2/v2/userinfo", timeout=15)
                response.raise_for_status()
                identity = response.json()
            if identity.get("verified_email") is not True or identity.get("email", "").casefold() != self.config.account.casefold():
                raise ValueError("Wrong calendar account")
            with self.io_lock:
                self.store.save({"account": self.config.account,
                                 "credentials": json.loads(credentials.to_json())})
        except Exception:
            raise ValueError("Calendar connection was not saved. Check the selected account and permissions, then reconnect.") from None

    def upcoming(self):
        """Read real upcoming events, bounded to 25. Never invent a duration or appointment."""
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request, AuthorizedSession
        with self.io_lock:
            try:
                saved = self.store.load()
                if not saved or saved.get("account", "").casefold() != self.config.account.casefold():
                    raise ValueError("Not connected")
                credentials = Credentials.from_authorized_user_info(saved["credentials"], scopes=SCOPES)
                if credentials.client_id != self.config.client_id:
                    raise ValueError("Client changed")
                if not credentials.valid:
                    transport = Request()
                    credentials.refresh(lambda **kw: transport(**{**kw, "timeout": 15}))
                    self.store.save({"account": self.config.account, "credentials": json.loads(credentials.to_json())})
                with AuthorizedSession(credentials) as session:
                    response = session.get("https://www.googleapis.com/calendar/v3/calendars/primary/events",
                        params={"timeMin": datetime.now(timezone.utc).isoformat(), "singleEvents": "true",
                                "orderBy": "startTime", "maxResults": 25}, timeout=15)
                    response.raise_for_status()
                    payload = response.json()
                return {"source": "google_calendar", "events": [{
                    "id": e["id"], "summary": e.get("summary", "Untitled event"),
                    "start": e.get("start", {}), "end": e.get("end", {}),
                    "status": e.get("status"),
                } for e in payload.get("items", [])], "more_available": bool(payload.get("nextPageToken"))}
            except Exception:
                raise ValueError("Could not read Calendar. Connect your account again if access expired or was revoked.") from None


def attach_calendar_routes(server, connection):
    """Register local setup routes. Cookie-bound requests, fixed host, no raw API errors."""
    def allowed(request, *, landing=False):
        # A link from chat opens a cross-site document navigation. The read-only
        # landing page may accept it; POST and calendar data still require same-site.
        navigation = (landing and request.method == "GET"
                      and request.headers.get("sec-fetch-mode") == "navigate"
                      and request.headers.get("sec-fetch-dest") == "document")
        return (request.client is not None and request.client.host == "127.0.0.1"
                and request.headers.get("host") == "127.0.0.1:8080"
                and request.headers.get("origin") in (None, "http://127.0.0.1:8080")
                and (request.headers.get("sec-fetch-site") != "cross-site" or navigation))

    headers = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
               "X-Content-Type-Options": "nosniff", "Content-Security-Policy": "default-src 'none'; form-action 'self' https://accounts.google.com; frame-ancestors 'none'"}

    @server.custom_route("/oauth/google/connect", methods=["GET", "POST"])
    async def connect(request):
        if not allowed(request, landing=True):
            return HTMLResponse("Local access required.", status_code=403, headers=headers)
        if request.method == "GET":
            return HTMLResponse("<h1>Connect Google Calendar</h1><p>Connect the saved account to Saarthi. "
                "Google will ask permission to manage events on calendars you own. "
                "This connection step does not create or change events.</p>"
                '<form method="post"><button>Continue to Google</button></form>', headers=headers)
        try:
            url, browser = connection.start()
            response = RedirectResponse(url, status_code=303, headers=headers)
            response.set_cookie(COOKIE, browser, max_age=600, httponly=True, samesite="lax", path="/oauth/google")
            return response
        except Exception:
            return HTMLResponse("Could not start Calendar connection. Try again shortly.", status_code=400, headers=headers)

    @server.custom_route("/oauth/google/callback", methods=["GET"])
    async def callback(request):
        # Google redirects are cross-site; state+cookie binding protects this endpoint.
        if request.client is None or request.client.host != "127.0.0.1" or request.headers.get("host") != "127.0.0.1:8080":
            return HTMLResponse("Local access required.", status_code=403, headers=headers)
        try:
            q = request.query_params
            if any(len(q.getlist(k)) > 1 for k in ("state", "code", "error")):
                raise ValueError("Invalid connection response. Start again.")
            await run_in_threadpool(connection.finish, q.get("state", ""), request.cookies.get(COOKIE),
                                    q.get("code"), "error" in q)
            response = HTMLResponse("<h1>Calendar connected</h1><p>You can return to Saarthi. No events were changed.</p>", headers=headers)
        except ValueError as exc:
            response = HTMLResponse(escape(str(exc)), status_code=400, headers=headers)
        response.delete_cookie(COOKIE, path="/oauth/google")
        return response

    @server.custom_route("/oauth/google/events", methods=["GET"])
    async def events(request):
        if not allowed(request):
            return JSONResponse({"error": "Local access required."}, status_code=403, headers=headers)
        try:
            return JSONResponse(await run_in_threadpool(connection.upcoming), headers=headers)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=503, headers=headers)


def configured_calendar(settings):
    """Only load local secrets when explicitly enabled; never auto-enable through a file."""
    import os
    from pathlib import Path
    from dotenv import dotenv_values
    from saarthi_mcp.calendar_tokens import LocalTokenStore
    path = os.getenv("SAARTHI_GOOGLE_CONFIG")
    if not path:
        return None
    if settings.host != "127.0.0.1" or settings.port != 8080:
        raise ValueError("Local Calendar setup requires a loopback server on port 8080.")
    if os.name != "nt":
        raise ValueError("Local Calendar token protection currently requires Windows.")
    config_path = Path(path).resolve()
    values = dotenv_values(config_path)
    config = CalendarConfig(client_id=values.get("GOOGLE_CLIENT_ID", ""),
        client_secret=values.get("GOOGLE_CLIENT_SECRET", ""), account=values.get("GOOGLE_CALENDAR_ACCOUNT", ""),
        redirect_uri=values.get("GOOGLE_REDIRECT_URI", ""))
    return CalendarConnection(config, LocalTokenStore(config_path.with_suffix(".tokens")))
