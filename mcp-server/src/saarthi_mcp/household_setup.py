"""Opt-in local administrator setup, sharing the atomic household importer.

This is not account authentication. Keep these routes on loopback until the
separate authenticated deployment is ready. No setup tool is exposed to agents.
"""

from __future__ import annotations

import json
import secrets
import threading
import time

from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse

from saarthi_mcp.household import HouseholdDefinition
from saarthi_mcp.repository import PersonNotFoundError


class SetupError(ValueError):
    """Safe user-facing setup error (never raw database or validation details)."""


class HouseholdSetup:
    def __init__(self, repository, *, persistent: bool):
        self.repository = repository
        self.persistence = "persistent" if persistent else "session"
        self.reviews = {}
        self.lock = threading.Lock()

    def status(self, payload):
        if payload != {}:
            raise SetupError("The status request must be empty.")
        try:
            person = self.repository.primary_elder()
        except PersonNotFoundError:
            return {"configured": False, "persistence": self.persistence}
        return {"configured": True, "primary_person": person.model_dump(include={"id", "name", "role"}, mode="json"),
                "persistence": self.persistence}

    def review(self, payload):
        if self.status({})["configured"]:
            raise SetupError("A household is already saved. Open its notebook; setup cannot replace existing records.")
        try:
            definition = HouseholdDefinition.model_validate(payload)
        except ValidationError:
            raise SetupError("Check the household details. Names must be unique, and relationships must connect saved members.") from None
        with self.lock:
            now = time.monotonic()
            self.reviews = {k: v for k, v in self.reviews.items() if v["expires"] > now}
            if len(self.reviews) >= 20:
                raise SetupError("Too many pending reviews. Wait ten minutes and try again.")
            token = secrets.token_urlsafe(32)
            self.reviews[token] = {"definition": definition, "expires": now + 600, "result": None}
        return {"review_token": token, "household": definition.model_dump(mode="json"),
                "persistence": self.persistence, "expires_in_seconds": 600}

    def confirm(self, payload):
        if (not isinstance(payload, dict) or set(payload) != {"review_token", "confirmed"}
                or payload.get("confirmed") is not True or not isinstance(payload.get("review_token"), str)):
            raise SetupError("Review the household and explicitly confirm before saving.")
        with self.lock:
            review = self.reviews.get(payload["review_token"])
            if review is None or review["expires"] <= time.monotonic():
                raise SetupError("This review expired. Check household status before reviewing again.")
            if review["result"] is not None:
                return review["result"]
            try:
                self.repository.import_household(review["definition"])
            except ValueError:
                raise SetupError("Setup could not be applied to this store. Check household status; existing records are preserved.") from None
            person = next(p for p in review["definition"].people if p.id == review["definition"].primary_person_id)
            review["result"] = {"status": "saved", "persistence": self.persistence,
                                "primary_person": person.model_dump(include={"id", "name", "role"}, mode="json")}
            return review["result"]


def attach_household_setup(server, service):
    async def handle(request, action):
        headers = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}
        if (request.client is None or request.client.host != "127.0.0.1"
                or request.headers.get("host") != "127.0.0.1:8080"
                or request.headers.get("origin") not in {"http://127.0.0.1:8080", "http://127.0.0.1:5173"}
                or request.headers.get("content-type", "").split(";")[0].strip() != "application/json"
                or request.headers.get("sec-fetch-site") == "cross-site"):
            return JSONResponse({"error": "Open the local dashboard on this computer to set up a household."}, status_code=403, headers=headers)
        data = bytearray()
        async for chunk in request.stream():
            if len(data) + len(chunk) > 1_000_000:
                return JSONResponse({"error": "Household request is too large."}, status_code=413, headers=headers)
            data.extend(chunk)
        try:
            payload = json.loads(data)
            result = await run_in_threadpool(action, payload)
            return JSONResponse(result, headers=headers)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return JSONResponse({"error": "Invalid household request."}, status_code=400, headers=headers)
        except SetupError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400, headers=headers)
        except Exception:
            return JSONResponse({"error": "Household request was not confirmed. Check status before retrying; no records will be overwritten."}, status_code=503, headers=headers)

    @server.custom_route("/local/household/status", methods=["POST"])
    async def status(request):
        return await handle(request, service.status)

    @server.custom_route("/local/household/review", methods=["POST"])
    async def review(request):
        return await handle(request, service.review)

    @server.custom_route("/local/household/confirm", methods=["POST"])
    async def confirm(request):
        return await handle(request, service.confirm)
