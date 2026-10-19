"""Opt-in, loopback-only Calendar connection. Never prints credentials or callback URLs.

The deployed application must provide authenticated per-household token storage before
enabling these routes remotely. This adapter deliberately rejects public bindings.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from html import escape
import json
import logging
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
    hosted: bool = False

    def __post_init__(self):
        uri = urlsplit(self.redirect_uri)
        local = uri.scheme == "http" and uri.hostname == "127.0.0.1" and uri.port == 8080
        remote = uri.scheme == "https" and bool(uri.hostname) and uri.port in (None, 443)
        if (not (remote if self.hosted else local) or uri.path != "/oauth/google/callback"
                or uri.query or uri.fragment or uri.username or uri.password):
            raise ValueError("Calendar requires an exact HTTPS hosted callback or the configured local callback.")
        if not self.client_id.endswith(".apps.googleusercontent.com") or not self.client_secret or "@" not in self.account:
            raise ValueError("Calendar client ID, client secret and account must be configured.")


class CalendarConnection:
    def __init__(self, config: CalendarConfig, store, *, subject: str | None = None):
        self.config, self.store = config, store
        self.subject = subject
        self.pending = {}
        self.lock = threading.Lock()
        self.io_lock = threading.Lock()

    def start(self, *, subject: str | None = None):
        bound_subject = subject if subject is not None else self.subject
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
            self.pending[state] = (now + 600, browser, flow, bound_subject)
        return url, browser

    def finish(self, state, browser, code, denied=False):
        with self.lock:
            pending = self.pending.get(state)
            if (not pending or pending[0] <= time.monotonic() or not browser
                    or not secrets.compare_digest(pending[1], browser)):
                raise ValueError("Connection expired or did not start in this browser. Start again.")
            flow_subject = pending[3] if len(pending) > 3 else self.subject
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
                self.store.save(
                    {"account": self.config.account, "credentials": json.loads(credentials.to_json())},
                    subject=flow_subject,
                )
        except Exception:
            raise ValueError("Calendar connection was not saved. Check the selected account and permissions, then reconnect.") from None

    def upcoming(self, *, subject: str | None = None):
        bound_subject = subject if subject is not None else self.subject
        """Read real upcoming events, bounded to 25. Never invent a duration or appointment."""
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request, AuthorizedSession
        with self.io_lock:
            try:
                saved = self.store.load(subject=bound_subject)
                if not saved or saved.get("account", "").casefold() != self.config.account.casefold():
                    raise ValueError("Not connected")
                credentials = Credentials.from_authorized_user_info(saved["credentials"], scopes=SCOPES)
                if credentials.client_id != self.config.client_id:
                    raise ValueError("Client changed")
                if not credentials.valid:
                    transport = Request()
                    credentials.refresh(lambda **kw: transport(**{**kw, "timeout": 15}))
                    self.store.save(
                        {"account": self.config.account, "credentials": json.loads(credentials.to_json())},
                        subject=bound_subject,
                    )
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

    def create_confirmed(self, body, *, subject: str | None = None):
        bound_subject = subject if subject is not None else self.subject
        """Insert one reviewed event, reconciling a retry by its stable Google ID.

        Caller supplies a validated body and collects explicit confirmation. No guests
        or emails are added. An uncertain write is never retried with a different ID.
        """
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request, AuthorizedSession
        from saarthi_mcp.calendar_appointments import matching_event
        with self.io_lock:
            try:
                saved = self.store.load(subject=bound_subject)
                if not saved or saved.get("account", "").casefold() != self.config.account.casefold():
                    raise ValueError("Not connected")
                credentials = Credentials.from_authorized_user_info(saved["credentials"], scopes=SCOPES)
                if credentials.client_id != self.config.client_id:
                    raise ValueError("Client changed")
                if not credentials.valid:
                    transport = Request()
                    credentials.refresh(lambda **kw: transport(**{**kw, "timeout": 15}))
                    self.store.save(
                        {"account": self.config.account, "credentials": json.loads(credentials.to_json())},
                        subject=bound_subject,
                    )
                with AuthorizedSession(credentials) as session:
                    url = "https://www.googleapis.com/calendar/v3/calendars/primary/events"
                    existing = session.get(url + "/" + body["id"], timeout=15)
                    if existing.status_code == 200:
                        event = existing.json()
                    elif existing.status_code == 404:
                        inserted = session.post(url, params={"sendUpdates": "none"}, json=body, timeout=15)
                        if inserted.status_code == 409:
                            inserted = session.get(url + "/" + body["id"], timeout=15)
                        inserted.raise_for_status()
                        event = inserted.json()
                    else:
                        existing.raise_for_status()
                        raise ValueError("Unexpected Calendar response")
                if not matching_event(event, body):
                    raise ValueError("Calendar returned different event details")
                return event
            except Exception:
                raise ValueError("Calendar save was not confirmed. Retry the same reviewed appointment to check its status; do not create another copy.") from None


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
            # Fixed categories only: never log request URLs, codes, cookies or credentials.
            origin = request.headers.get("origin")
            origin_kind = ("missing" if origin is None else "null" if origin == "null"
                           else "local" if origin == "http://127.0.0.1:8080" else "other")
            logging.getLogger(__name__).warning(
                "Calendar connect blocked: method=%s origin_kind=%s loopback=%s expected_host=%s cross_site=%s",
                "POST" if request.method == "POST" else "GET", origin_kind,
                request.client is not None and request.client.host == "127.0.0.1",
                request.headers.get("host") == "127.0.0.1:8080",
                request.headers.get("sec-fetch-site") == "cross-site",
            )
            return HTMLResponse("Local access required.", status_code=403, headers=headers)
        if request.method == "GET":
            return HTMLResponse("<h1>Connect Google Calendar</h1><p>Connect the saved account to Saarthi. "
                "Google will ask permission to manage events on calendars you own. "
                "This connection step does not create or change events.</p>"
                '<form method="post"><button>Continue to Google</button></form>',
                # no-referrer can turn a same-origin HTML form POST's Origin into
                # null. Preserve Origin for this local form; still omit cross-site
                # referrers. Callback responses retain the stricter no-referrer.
                headers={**headers, "Referrer-Policy": "same-origin"})
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


def configured_calendar(settings, *, authenticated: bool = False, subject: str | None = None):
    """Only load local secrets when explicitly enabled; never auto-enable through a file."""
    import os
    from pathlib import Path
    from dotenv import dotenv_values
    from saarthi_mcp.calendar_tokens import LocalTokenStore, SubjectTokenStore
    mode = os.getenv("SAARTHI_GOOGLE_MODE", "local")
    if mode not in {"local", "hosted"}:
        raise ValueError("SAARTHI_GOOGLE_MODE must be local or hosted.")
    if mode == "hosted":
        if not authenticated or settings.cognito is None:
            raise ValueError("Hosted Calendar requires Cognito household authentication.")
        from saarthi_mcp.cloud_calendar_tokens import SecretsManagerTokenStore
        origin = urlsplit(settings.cognito.resource_url)
        callback = os.getenv("GOOGLE_REDIRECT_URI", "")
        if callback != f"https://{origin.netloc}/oauth/google/callback" or origin.scheme != "https":
            raise ValueError("Hosted Calendar callback must share the authenticated dashboard's HTTPS origin.")
        config = CalendarConfig(
            client_id=os.getenv("GOOGLE_CLIENT_ID", ""), client_secret=os.getenv("GOOGLE_CLIENT_SECRET", ""),
            account=os.getenv("GOOGLE_CALENDAR_ACCOUNT", ""), redirect_uri=callback, hosted=True,
        )
        store = SecretsManagerTokenStore(os.getenv("SAARTHI_GOOGLE_TOKEN_PREFIX", ""), settings.aws_region)
        return CalendarConnection(config, store, subject=subject)
    path = os.getenv("SAARTHI_GOOGLE_CONFIG")
    if not path or not str(path).strip():
        return None
    if settings.host != "127.0.0.1" or settings.port != 8080:
        raise ValueError("Local Calendar setup requires a loopback server on port 8080.")
    config_path = Path(path.strip()).resolve()
    if not config_path.is_file():
        raise ValueError(
            f"SAARTHI_GOOGLE_CONFIG points to a missing file: {config_path}. "
            "Create the Google Calendar env file or clear SAARTHI_GOOGLE_CONFIG."
        )
    if os.name != "nt":
        raise ValueError("Local Calendar token protection currently requires Windows.")
    values = dotenv_values(config_path)
    config = CalendarConfig(client_id=values.get("GOOGLE_CLIENT_ID", ""),
        client_secret=values.get("GOOGLE_CLIENT_SECRET", ""), account=values.get("GOOGLE_CALENDAR_ACCOUNT", ""),
        redirect_uri=values.get("GOOGLE_REDIRECT_URI", ""))
    if authenticated:
        store = SubjectTokenStore(config_path.with_suffix(".subjects"))
        return CalendarConnection(config, store, subject=subject)
    return CalendarConnection(config, LocalTokenStore(config_path.with_suffix(".tokens")), subject=subject)


def attach_authenticated_calendar_routes(server, connection, verifier, resource_url):
    """Cognito-signed dashboard routes for Calendar connect, read and reviewed writes.

    Connect returns a one-time loopback handoff URL. The Vite dashboard (e.g. :5173) cannot
    set an HttpOnly cookie that Google's callback on 127.0.0.1:8080 will see when connect is
    proxied; the handoff page issues the OAuth cookie on the MCP host instead.
    """
    from urllib.parse import urlsplit
    from starlette.concurrency import run_in_threadpool
    from starlette.responses import JSONResponse, RedirectResponse, HTMLResponse
    from html import escape

    resource = urlsplit(resource_url)
    allowed_origin = f"{resource.scheme}://{resource.netloc}"
    headers = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
               "X-Content-Type-Options": "nosniff"}
    handoffs: dict[str, tuple[float, str]] = {}
    handoff_lock = threading.Lock()
    handoff_path = "/oauth/google/household-connect"
    callback_uri = urlsplit(connection.config.redirect_uri)
    callback_origin = f"{callback_uri.scheme}://{callback_uri.netloc}"
    if connection.config.hosted and callback_origin != allowed_origin:
        raise ValueError("Hosted Calendar must use the authenticated dashboard origin.")

    def callback_host_allowed(request):
        if connection.config.hosted:
            # HTTPS terminates at the configured hosting proxy. Never trust a supplied
            # X-Forwarded-Host to choose where credentials or handoffs are sent.
            return request.headers.get("host") == callback_uri.netloc
        return (request.client is not None and request.client.host == "127.0.0.1"
                and request.headers.get("host") == "127.0.0.1:8080")

    async def identity_from(request):
        authorization = request.headers.getlist("authorization")
        if len(authorization) != 1 or not authorization[0].startswith("Bearer "):
            return None
        try:
            verified = await verifier.verify_token(authorization[0][7:])
        except Exception:
            return None
        if verified is None:
            return None
        # Reject cross-site callers. Same-origin browser GETs often omit Origin;
        # accept those only when Sec-Fetch-Site reports same-origin.
        if request.headers.get("sec-fetch-site") == "cross-site":
            return None
        origin = request.headers.get("origin")
        if origin == allowed_origin:
            return verified.claims["sub"]
        if (origin is None and request.method == "GET"
                and request.headers.get("sec-fetch-site") == "same-origin"):
            return verified.claims["sub"]
        return None

    def mint_handoff(subject: str) -> str:
        token = secrets.token_urlsafe(32)
        now = time.monotonic()
        with handoff_lock:
            expired = [key for key, (expires, _) in handoffs.items() if expires <= now]
            for key in expired:
                del handoffs[key]
            if len(handoffs) >= 20:
                raise ValueError("Too many connection attempts. Please wait ten minutes.")
            handoffs[token] = (now + 600, subject)
        return f"{callback_origin}{handoff_path}?h={token}"

    def consume_handoff(token: str | None) -> str:
        if not token or not isinstance(token, str) or len(token) > 256:
            raise ValueError("Connection expired or did not start in this browser. Start again.")
        with handoff_lock:
            pending = handoffs.pop(token, None)
        if pending is None or pending[0] <= time.monotonic():
            raise ValueError("Connection expired or did not start in this browser. Start again.")
        return pending[1]

    @server.custom_route("/household/calendar/connect", methods=["POST"])
    async def connect_authenticated(request):
        subject = await identity_from(request)
        if subject is None:
            return JSONResponse({"error": "Sign-in required."}, status_code=401, headers=headers)
        try:
            if request.headers.get("content-type", "").split(";")[0].strip() != "application/json":
                return JSONResponse({"error": "Use a JSON request."}, status_code=415, headers=headers)
            body = bytearray()
            async for chunk in request.stream():
                if len(body) + len(chunk) > 1024:
                    return JSONResponse({"error": "Request too large."}, status_code=413, headers=headers)
                body.extend(chunk)
            if json.loads(bytes(body) or b"{}") != {}:
                return JSONResponse({"error": "Invalid connect request."}, status_code=400, headers=headers)
            handoff_url = await run_in_threadpool(mint_handoff, subject)
            return JSONResponse({"handoff_url": handoff_url}, headers=headers)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400, headers=headers)
        except Exception:
            return JSONResponse({"error": "Could not start Calendar connection."}, status_code=400, headers=headers)

    @server.custom_route(handoff_path, methods=["GET"])
    async def household_connect_handoff(request):
        # Document navigation on the MCP loopback host sets the OAuth cookie for the callback.
        if not callback_host_allowed(request):
            return HTMLResponse("Local access required.", status_code=403, headers=headers)
        try:
            q = request.query_params
            if len(q.getlist("h")) != 1:
                raise ValueError("Connection expired or did not start in this browser. Start again.")
            subject = await run_in_threadpool(consume_handoff, q.get("h"))
            url, browser = await run_in_threadpool(connection.start, subject=subject)
            response = RedirectResponse(url, status_code=303, headers=headers)
            response.set_cookie(COOKIE, browser, max_age=600, httponly=True, secure=connection.config.hosted,
                                samesite="lax", path="/oauth/google")
            return response
        except ValueError as exc:
            return HTMLResponse(escape(str(exc)), status_code=400, headers=headers)
        except Exception:
            return HTMLResponse("Could not start Calendar connection. Try again shortly.", status_code=400, headers=headers)

    @server.custom_route("/household/calendar/events", methods=["GET"])
    async def events_authenticated(request):
        subject = await identity_from(request)
        if subject is None:
            return JSONResponse({"error": "Sign-in required."}, status_code=401, headers=headers)
        try:
            return JSONResponse(await run_in_threadpool(connection.upcoming, subject=subject), headers=headers)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=503, headers=headers)

    @server.custom_route("/oauth/google/callback", methods=["GET"])
    async def callback_authenticated(request):
        if not callback_host_allowed(request):
            return HTMLResponse("Local access required.", status_code=403, headers=headers)
        try:
            q = request.query_params
            if any(len(q.getlist(k)) > 1 for k in ("state", "code", "error")):
                raise ValueError("Invalid connection response. Start again.")
            await run_in_threadpool(connection.finish, q.get("state", ""), request.cookies.get(COOKIE),
                                    q.get("code"), "error" in q)
            response = HTMLResponse("<h1>Calendar connected</h1><p>Return to the Saarthi dashboard.</p>", headers=headers)
        except ValueError as exc:
            response = HTMLResponse(escape(str(exc)), status_code=400, headers=headers)
        response.delete_cookie(COOKIE, path="/oauth/google")
        return response
