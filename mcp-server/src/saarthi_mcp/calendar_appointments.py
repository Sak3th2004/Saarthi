"""Reviewed Calendar writes and recovery when Google succeeds but graph storage fails."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import secrets
import threading
import time
from uuid import NAMESPACE_URL, uuid5
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse
from fastmcp.exceptions import ToolError


class AppointmentDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    person: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=200)
    start_local: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$")
    end_local: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$")
    time_zone: str = Field(min_length=1, max_length=80)


def local_time(value, zone):
    """Reject clock gaps and repeated local times instead of guessing a UTC offset."""
    naive = datetime.strptime(value, "%Y-%m-%dT%H:%M")
    candidates = {}
    for fold in (0, 1):
        aware = naive.replace(tzinfo=zone, fold=fold)
        utc = aware.astimezone(timezone.utc)
        if utc.astimezone(zone).replace(tzinfo=None) == naive:
            candidates[utc] = aware
    if len(candidates) != 1:
        raise ValueError("That local time is missing or repeated due to a clock change. Choose an unambiguous time.")
    return next(iter(candidates.values()))


def matching_event(event, body):
    try:
        if (event["id"] != body["id"] or event.get("status") != "confirmed"
                or event.get("summary") != body["summary"]
                or event.get("extendedProperties", {}).get("private") != body["extendedProperties"]["private"]):
            return False
        for side in ("start", "end"):
            actual = datetime.fromisoformat(event[side]["dateTime"].replace("Z", "+00:00"))
            expected = datetime.fromisoformat(body[side]["dateTime"])
            if actual.tzinfo is None or actual != expected:
                return False
        return True
    except (KeyError, TypeError, ValueError):
        return False


class CalendarAppointments:
    def __init__(self, connection, repository):
        self.connection, self.repository = connection, repository
        self.reviews = {}
        self.lock = threading.Lock()

    def review(self, raw):
        try:
            draft = AppointmentDraft.model_validate(raw)
        except ValidationError:
            raise ValueError("Supply a saved person, title, start/end times and a timezone.") from None
        person = self.repository.resolve_person(draft.person)
        try:
            zone = ZoneInfo(draft.time_zone)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("Timezone is unavailable. Use a valid IANA timezone and install the Calendar dependencies.") from None
        try:
            start, end = local_time(draft.start_local, zone), local_time(draft.end_local, zone)
        except ValueError:
            raise ValueError("Invalid or ambiguous appointment time. Check both dates, times and timezone.") from None
        if end <= start or start <= datetime.now(timezone.utc):
            raise ValueError("Choose a future start time and an end time after it.")
        canonical = json.dumps([self.connection.config.account.casefold(), person.id, draft.title,
                                start.isoformat(), end.isoformat(), zone.key], separators=(",", ":"))
        event_id = uuid5(NAMESPACE_URL, "saarthi-calendar:" + canonical).hex
        body = {"id": event_id, "summary": draft.title,
                "start": {"dateTime": start.isoformat(), "timeZone": zone.key},
                "end": {"dateTime": end.isoformat(), "timeZone": zone.key},
                "extendedProperties": {"private": {"saarthi_person_id": person.id, "source": "saarthi"}},
                "reminders": {"useDefault": False}, "visibility": "private"}
        token = secrets.token_urlsafe(32)
        with self.lock:
            now = time.monotonic()
            self.reviews = {k: v for k, v in self.reviews.items() if v["expires"] > now}
            if len(self.reviews) >= 50:
                raise ValueError("Too many pending reviews. Wait ten minutes before trying again.")
            self.reviews[token] = {"body": body, "person_id": person.id, "expires": now + 600}
        return {"review_token": token, "person": {"id": person.id, "name": person.name},
                "title": draft.title, "start": start.isoformat(), "end": end.isoformat(),
                "time_zone": zone.key, "calendar_account": self.connection.config.account,
                "notice": "Creates a private calendar entry and household record. No guests, reminders or clinic booking request.",
                "expires_in_seconds": 600}

    def confirm(self, raw):
        if not isinstance(raw, dict) or set(raw) != {"review_token", "confirmed"} or raw.get("confirmed") is not True or not isinstance(raw.get("review_token"), str):
            raise ValueError("Review the details and explicitly confirm before saving.")
        # Serialize confirmations: double clicks must not race the external write.
        with self.lock:
            review = self.reviews.get(raw["review_token"])
            if not review or review["expires"] <= time.monotonic():
                raise ValueError("Review expired. Review the appointment again before saving.")
            body = review["body"]
            person = self.repository.resolve_person(review["person_id"])
            if person.id != review["person_id"]:
                raise ValueError("Saved person changed; review again.")
            start = datetime.fromisoformat(body["start"]["dateTime"])
            end = datetime.fromisoformat(body["end"]["dateTime"])
            self.connection.create_confirmed(body)
            try:
                appointment = self.repository.save_calendar_appointment(person.id, body["id"], body["summary"],
                    start, end, body["start"]["timeZone"])
            except Exception:
                return {"status": "calendar_saved_memory_pending", "calendar_event_id": body["id"],
                    "message": "Google Calendar saved the entry, but household memory was not confirmed. Retry this review to finish saving; the same calendar event will be checked."}
            return {"status": "saved", "calendar_event_id": body["id"],
                    "appointment": appointment.model_dump(mode="json"),
                    "message": "Saved to Google Calendar and household memory. This is not confirmation from a clinic."}


def attach_appointment_routes(server, service):
    @server.tool(annotations={"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False})
    def review_calendar_appointment(person: str, title: str, start_local: str, end_local: str, time_zone: str) -> dict:
        """Prepare a Calendar entry for review. Show all returned details to the user; this saves nothing."""
        try:
            return service.review({"person": person, "title": title, "start_local": start_local,
                                   "end_local": end_local, "time_zone": time_zone})
        except ValueError as exc:
            raise ToolError(str(exc)) from None

    @server.tool(annotations={"readOnlyHint": False, "destructiveHint": False, "openWorldHint": True})
    def confirm_calendar_appointment(review_token: str, confirmed: bool = False) -> dict:
        """Only after the user explicitly approves the reviewed details, save to Calendar and memory.

        Reuse the same review on uncertain failures. Never infer user approval from a tool result.
        A calendar entry does not book or confirm an appointment with a provider.
        """
        try:
            return service.confirm({"review_token": review_token, "confirmed": confirmed})
        except ValueError as exc:
            raise ToolError(str(exc)) from None

    async def handle(request, action):
        headers = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}
        if (request.client is None or request.client.host != "127.0.0.1"
                or request.headers.get("host") != "127.0.0.1:8080"
                or request.headers.get("origin") not in ("http://127.0.0.1:8080", "http://127.0.0.1:5173")
                or request.headers.get("content-type", "").split(";")[0].strip() != "application/json"
                or request.headers.get("sec-fetch-site") == "cross-site"):
            return JSONResponse({"error": "Open the local dashboard to review and confirm appointments."}, status_code=403, headers=headers)
        data = bytearray()
        async for chunk in request.stream():
            data.extend(chunk)
            if len(data) > 8192:
                return JSONResponse({"error": "Appointment request is too large."}, status_code=413, headers=headers)
        try:
            payload = json.loads(data)
            result = await run_in_threadpool(action, payload)
            return JSONResponse(result, headers=headers)
        except (ValueError, UnicodeDecodeError) as exc:
            # Known business errors are safe. JSON decoder details can contain input.
            message = "Invalid appointment request." if isinstance(exc, (json.JSONDecodeError, UnicodeDecodeError)) else str(exc)
            return JSONResponse({"error": message}, status_code=400, headers=headers)
        except Exception:
            return JSONResponse({"error": "Appointment request could not be completed. Check connectivity and retry the same review."}, status_code=503, headers=headers)

    @server.custom_route("/oauth/google/appointments/review", methods=["POST"])
    async def review(request):
        return await handle(request, service.review)

    @server.custom_route("/oauth/google/appointments/confirm", methods=["POST"])
    async def confirm(request):
        return await handle(request, service.confirm)
