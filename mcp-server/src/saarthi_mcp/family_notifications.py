"""Explicitly reviewed family email with durable, actor-bound send claims."""

from __future__ import annotations

import hashlib
import json
import secrets
import time

from saarthi_mcp.family_recipients import candidate_channels, recipient_emails, scoped_family_contacts
from saarthi_mcp.models import NotifyResult, Person, Urgency
from saarthi_mcp.repository import HouseholdRepository, now_utc
from saarthi_mcp.ses_delivery import SesDeliveryError, SesDeliveryUncertain, SesMailer, SesSettings
from saarthi_mcp.weekly_digest import build_weekly_digest


class FamilyNotificationService:
    def __init__(self, repository: HouseholdRepository, *, ses_settings: SesSettings | None = None,
                 mailer: SesMailer | None = None):
        self.repository = repository
        self.ses_settings = ses_settings
        self.mailer = mailer or (SesMailer(ses_settings) if ses_settings else None)

    @staticmethod
    def _key(token: str) -> str:
        if not isinstance(token, str) or not 32 <= len(token) <= 128:
            raise ValueError("Review the family message again before sending.")
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def _start_review(
        self, *, person: str, message: str, urgency: Urgency, actor: str, kind: str, notice: str,
    ) -> dict:
        if not isinstance(message, str) or not message.strip() or len(message.strip()) > 8000:
            raise ValueError("Enter a family message of at most 8000 characters.")
        if not isinstance(person, str) or not person.strip() or not isinstance(actor, str) or not actor:
            raise ValueError("A saved person and signed-in caregiver are required.")
        urgency = Urgency(urgency)
        text = message.strip()
        resolved = self.repository.resolve_person(person)
        emails = recipient_emails(self.repository.household_directory(), resolved.id)
        if not emails:
            raise ValueError("No household family email is linked to this person. Save a family email and relationship first.")
        token = secrets.token_urlsafe(32)
        operation = {
            "key": self._key(token), "actor": actor, "state": "pending", "expires": time.time() + 600,
            "kind": kind, "person": resolved.model_dump(mode="json"), "message": text, "urgency": urgency.value,
            "recipient_emails": emails,
        }
        self.repository.create_notification_review(operation)
        return {
            "review_token": token, "person": {"id": resolved.id, "name": resolved.name},
            "message": text, "urgency": urgency.value, "recipient_emails": emails,
            "kind": kind, "delivery_status": "pending", "expires_in_seconds": 600, "notice": notice,
        }

    def review(self, person: str, message: str, urgency: Urgency = Urgency.info, *, actor: str = "local") -> dict:
        return self._start_review(
            person=person, message=message, urgency=urgency, actor=actor, kind="message",
            notice="Confirm to submit this message to the listed family addresses. Provider acceptance does not confirm inbox delivery.",
        )

    def review_weekly_digest(self, person: str, *, days: int = 7, actor: str = "local") -> dict:
        """Assemble a digest from saved records, then require the same confirm path as a message."""
        resolved = self.repository.resolve_person(person)
        body = build_weekly_digest(self.repository, resolved.id, days=days)
        return self._start_review(
            person=resolved.id, message=body, urgency=Urgency.info, actor=actor, kind="digest",
            notice="Confirm to email this weekly digest of saved household records. It is not medical advice.",
        )

    @staticmethod
    def _result(operation: dict, status: str, speech: str, *, submitted: bool = False) -> NotifyResult:
        channels = [f"email:{email}" for email in operation["recipient_emails"]]
        return NotifyResult(
            person=Person.model_validate(operation["person"]), delivered_to=[],
            submitted_to=channels if submitted else [], candidate_channels=channels,
            delivery_status=status, urgency=Urgency(operation["urgency"]), message=operation["message"], speech=speech,
        )

    def confirm(self, review_token: str, *, confirmed: bool, actor: str = "local") -> NotifyResult:
        if confirmed is not True:
            raise ValueError("Review the message and explicitly confirm before sending email.")
        key = self._key(review_token)
        operation, claimed = self.repository.claim_notification_review(key, actor)
        if operation.get("result") is not None:
            return NotifyResult.model_validate(operation["result"])
        uncertain = self._result(operation, "uncertain",
            "Email acceptance is uncertain or still being checked. This review will not send again; check the recipient before starting a new review.")
        if not claimed:
            return uncertain

        # Commit the claim before this non-idempotent external call. Never auto-retry.
        provider_id = None
        if self.mailer is None or self.ses_settings is None:
            result = self._result(operation, "recorded_only", "The message was recorded. Email is not configured; no email was submitted.")
        else:
            try:
                kind = operation.get("kind", "message")
                subject = (
                    f"Saarthi weekly digest · {operation['person']['name']}"
                    if kind == "digest"
                    else f"Saarthi family update ({operation['urgency']})"
                )
                body = (
                    f"{operation['message']}\n\nSent from your Saarthi household notebook."
                    if kind == "digest"
                    else (
                        f"Message about {operation['person']['name']}:\n\n{operation['message']}\n\n"
                        "Sent from your Saarthi household notebook. This is not medical advice."
                    )
                )
                provider_id = self.mailer.send(
                    to_addresses=operation["recipient_emails"],
                    subject=subject,
                    body_text=body,
                )
                result = self._result(operation, "accepted",
                    "The email provider accepted the family message. Inbox delivery has not been confirmed.", submitted=True)
            except SesDeliveryUncertain:
                result = uncertain
            except SesDeliveryError:
                result = self._result(operation, "failed", "Email submission failed. The message was recorded; no automatic retry will be made.")
            except Exception:
                result = uncertain
        detail = json.dumps({
            "operation": key, "status": result.delivery_status, "message": operation["message"],
            "urgency": operation["urgency"], "recipient_emails": operation["recipient_emails"],
            "provider_message_id": provider_id,
        }, ensure_ascii=False, separators=(",", ":"))
        try:
            saved = self.repository.finish_notification_review(key, actor, result.model_dump(mode="json"), detail)
        except Exception:
            # Keep the prior durable uncertain claim; no second send is permitted.
            return uncertain
        return NotifyResult.model_validate(saved)

    def record_only(self, person: Person, message: str, urgency: Urgency) -> NotifyResult:
        directory = self.repository.household_directory()
        channels = candidate_channels(directory, person.id)
        self.repository.add_event(person.id, type="notify", detail=f"[{urgency.value}] {message}", at=now_utc())
        contacts = scoped_family_contacts(directory, person.id)
        names = ", ".join(c.name for c in contacts) or "the family"
        return NotifyResult(person=person, delivered_to=[], candidate_channels=channels,
            delivery_status="recorded_only", urgency=urgency, message=message,
            speech=f"Recorded a {urgency.value} message about {person.name} for {names}. No SMS or email has been sent.")


def attach_family_notification_routes(server, service, verifier, resource_url):
    from urllib.parse import urlsplit
    from starlette.concurrency import run_in_threadpool
    from starlette.responses import JSONResponse

    resource = urlsplit(resource_url)
    allowed_origin = f"{resource.scheme}://{resource.netloc}"
    headers = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}

    async def handle(request, action):
        authorization = request.headers.getlist("authorization")
        if len(authorization) != 1 or not authorization[0].startswith("Bearer ") or len(authorization[0]) > 16384:
            return JSONResponse({"error": "Sign-in required."}, status_code=401, headers=headers)
        try:
            identity = await verifier.verify_token(authorization[0][7:])
        except Exception:
            identity = None
        if identity is None:
            return JSONResponse({"error": "Sign-in required."}, status_code=401, headers=headers)
        if request.headers.get("origin") != allowed_origin or request.headers.get("sec-fetch-site") == "cross-site":
            return JSONResponse({"error": "Open the household dashboard to continue."}, status_code=403, headers=headers)
        if request.headers.get("content-type", "").split(";")[0].strip() != "application/json":
            return JSONResponse({"error": "Use a JSON request."}, status_code=415, headers=headers)
        data = bytearray()
        async for chunk in request.stream():
            data.extend(chunk)
            if len(data) > 16384:
                return JSONResponse({"error": "Request too large."}, status_code=413, headers=headers)
        try:
            payload = json.loads(data)
            result = await run_in_threadpool(action, payload, identity.claims["sub"])
            if isinstance(result, NotifyResult):
                result = result.model_dump(mode="json")
            return JSONResponse(result, headers=headers)
        except (ValueError, TypeError, KeyError):
            return JSONResponse({"error": "The message review is invalid, expired, or its recipients changed. Reload the family details and review again."}, status_code=400, headers=headers)
        except Exception:
            return JSONResponse({"error": "Family message could not be completed."}, status_code=503, headers=headers)

    def review_payload(raw, actor):
        if not isinstance(raw, dict) or set(raw) != {"person", "message", "urgency"}:
            raise ValueError("Invalid review request.")
        return service.review(raw["person"], raw["message"], Urgency(raw["urgency"]), actor=actor)

    def digest_payload(raw, actor):
        if not isinstance(raw, dict) or "person" not in raw or set(raw) - {"person", "days"}:
            raise ValueError("Invalid digest review request.")
        days = raw.get("days", 7)
        if type(days) is not int:
            raise ValueError("Invalid digest review request.")
        return service.review_weekly_digest(raw["person"], days=days, actor=actor)

    def confirm_payload(raw, actor):
        if not isinstance(raw, dict) or set(raw) != {"review_token", "confirmed"}:
            raise ValueError("Invalid confirm request.")
        return service.confirm(raw["review_token"], confirmed=raw["confirmed"], actor=actor)

    @server.custom_route("/household/notify/review", methods=["POST"])
    async def review_route(request):
        return await handle(request, review_payload)

    @server.custom_route("/household/notify/digest/review", methods=["POST"])
    async def digest_route(request):
        return await handle(request, digest_payload)

    @server.custom_route("/household/notify/confirm", methods=["POST"])
    async def confirm_route(request):
        return await handle(request, confirm_payload)
