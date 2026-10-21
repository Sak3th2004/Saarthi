"""Grounded conversational retrieval with no model-controlled side effects."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime
from hashlib import sha256
import json
import re
from threading import BoundedSemaphore, RLock
from time import monotonic
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from saarthi_mcp.models import Person
from saarthi_mcp.graph import graph_node_identity
from saarthi_mcp.repository import HouseholdRepository, ensure_aware, now_utc


class ConversationError(ValueError):
    """Safe user-facing conversation failure."""


@dataclass
class _Session:
    actor: str
    person: str
    touched: float
    turns: int = 0
    history: list = field(default_factory=list)
    busy: bool = False


class ConversationService:
    """Session context is private to an authenticated actor and selected person.

    Only a metadata-only agent_action audit may be written. Excerpts are explicitly
    saved records, not verified real-world truth. Provider prose is never displayed.
    """

    def __init__(self, repo: HouseholdRepository, model=None, *, clock=monotonic):
        self.repo, self.model, self.clock = repo, model, clock
        self._sessions: OrderedDict[str, _Session] = OrderedDict()
        self._lock, self._slots = RLock(), BoundedSemaphore(2)

    def _session(self, actor, person, conversation_id):
        now = self.clock()
        with self._lock:
            for key in list(self._sessions):
                entry = self._sessions[key]
                if not entry.busy and now - entry.touched > 1200:
                    del self._sessions[key]
            if conversation_id is not None:
                session = self._sessions.get(conversation_id)
                if session is None or session.actor != actor or session.person != person:
                    raise ConversationError("This conversation is unavailable. Start a new conversation.")
                if session.busy:
                    raise ConversationError("Wait for the current answer before asking again.")
                if session.turns >= 8:
                    raise ConversationError("Start a new conversation to continue.")
            else:
                if len(self._sessions) >= 100:
                    raise ConversationError("Conversation capacity is busy. Please try again later.")
                conversation_id = uuid4().hex
                session = _Session(actor, person, now)
                self._sessions[conversation_id] = session
            session.busy = True
            return conversation_id, session

    def talk(self, actor: str, person: Person, question: str, time_zone: str, conversation_id=None):
        if not isinstance(question, str) or not question.strip() or len(question) > 2000:
            raise ConversationError("Ask a question between 1 and 2000 characters.")
        if not isinstance(actor, str) or not actor:
            raise ConversationError("Sign in before using Talk to Saarthi.")
        if not isinstance(time_zone, str) or len(time_zone) > 80:
            raise ConversationError("Choose a valid IANA timezone.")
        try:
            zone = ZoneInfo(time_zone)
        except (ValueError, ZoneInfoNotFoundError):
            raise ConversationError("Choose a valid IANA timezone.") from None
        if conversation_id is not None and (
            not isinstance(conversation_id, str) or not re.fullmatch(r"[0-9a-f]{32}", conversation_id)
        ):
            raise ConversationError("This conversation is unavailable. Start a new conversation.")
        if self.model is None:
            raise ConversationError("Talk to Saarthi needs the configured Bedrock connection.")
        if not self._slots.acquire(blocking=False):
            raise ConversationError("Saarthi is answering other questions. Please try again shortly.")
        session = None
        try:
            key, session = self._session(actor, person.id, conversation_id)
            result = self._turn(person, question.strip(), zone, session.history)
            retrievals = result.pop("_retrievals")
            result["conversation_id"] = key
            with self._lock:
                session.turns += 1
                session.touched = self.clock()
                session.history = (session.history + [{
                    "question": question.strip(),
                    "answer": result["answer"][:2500],
                    "retrievals": retrievals,
                }])[-4:]
            try:
                self.repo.add_event(person.id, type="agent_action", at=now_utc(), detail=json.dumps({
                    "operation": "talk_to_saarthi", "status": "completed",
                    "model_id": self.model.model_id,
                    "sources_checked": result["sources_checked"],
                    "evidence_count": len(result["evidence"]),
                    "draft_type": result["action"]["type"] if result["action"] else None,
                }))
            except Exception:
                result["notice"] += " The activity audit could not be saved."
            return result
        except ConversationError:
            raise
        except Exception:
            raise ConversationError(
                "Saarthi could not verify an answer. No calendar event or message was created. Please try again."
            ) from None
        finally:
            if session:
                with self._lock:
                    session.busy = False
                    if session.turns == 0:
                        self._sessions.pop(key, None)
            self._slots.release()

    def _turn(self, person, question, zone, history):
        evidence, checked, retrievals = {}, [], []
        reads, finished, selection, invalid = 0, False, None, False
        characters = 0
        tool_lock = RLock()

        def item(kind, text, at=None, node_ids=()):
            nonlocal characters
            # Whole records only: do not truncate a fact and accidentally change its meaning.
            if len(text) > 1200 or characters + len(text) > 24000:
                return None
            stamp = ensure_aware(at).isoformat() if at else None
            identity = sha256(json.dumps([person.id, kind, text, stamp]).encode()).hexdigest()[:24]
            value = {"id": identity, "kind": kind, "text": text, "at": stamp, "graph_node_ids": list(node_ids)}
            evidence[identity] = value
            characters += len(text)
            return value

        def read(source, query=""):
            nonlocal reads, invalid
            with tool_lock:
                if finished or reads >= 4 or source not in {"notes", "appointments", "medications", "relationships"}:
                    invalid = True
                    raise ConversationError("The answer exceeded its safe retrieval limits.")
                if not isinstance(query, str) or len(query) > 2000:
                    invalid = True
                    raise ConversationError("The answer exceeded its safe retrieval limits.")
                reads += 1
                retrievals.append({"source": source, "query": query})
                if source not in checked:
                    checked.append(source)
                values = []
                if source == "notes":
                    events = (
                        self.repo.search_events(person.id, query, limit=15) if query.strip()
                        else self.repo.recent_events(
                            person.id, limit=15,
                            excluded_types=("agent_action", "household_setup"), until=now_utc(),
                        )
                    )
                    for event in events:
                        if event.type not in {"agent_action", "household_setup"} and ensure_aware(event.at) <= now_utc():
                            values.append(item("note", event.detail, event.at, [
                                graph_node_identity(person.id, "event", event.model_dump(mode="json")),
                            ]))
                elif source == "appointments":
                    for appointment in self.repo.upcoming_appointments(person.id, limit=15):
                        start = ensure_aware(appointment.when).astimezone(zone).isoformat()
                        end = f" to {ensure_aware(appointment.end).astimezone(zone).isoformat()}" if appointment.end else ""
                        values.append(item(
                            "appointment", f"{appointment.kind}: {start}{end}. "
                            "Saved schedule record; not confirmation from a provider.", appointment.when,
                            [graph_node_identity(person.id, "appointment", appointment.model_dump(mode="json"))],
                        ))
                elif source == "medications":
                    for medication in self.repo.medications_for(person.id)[:15]:
                        values.append(item(
                            "medication", f"Caregiver-saved schedule: {medication.name}, {medication.dose}, "
                            f"at {', '.join(medication.schedule)}. Saved supply: {medication.supply_count}.",
                            node_ids=[graph_node_identity(person.id, "medication", medication.model_dump(mode="json"))],
                        ))
                else:
                    for relation in self.repo.relationships_for(person.id, limit=15):
                        values.append(item(
                            "relationship", f"{relation.source.name} is {relation.relation} of {relation.target.name}.",
                            node_ids=[graph_node_identity(person.id, "person", {"id": member.id})
                                      for member in (relation.source, relation.target)],
                        ))
                return [value for value in values if value is not None]

        def finish(ids, intent="answer", title="", start_local="", end_local=""):
            nonlocal finished, selection, invalid
            with tool_lock:
                if finished or intent not in {"answer", "family_message", "appointment", "clarify"}:
                    invalid = True
                    raise ConversationError("The answer selection could not be verified.")
                if not isinstance(ids, list) or len(ids) > 6 or any(
                    not isinstance(identity, str) or identity not in evidence for identity in ids
                ):
                    invalid = True
                    raise ConversationError("The answer's supporting records could not be verified.")
                finished = True
                selection = (list(dict.fromkeys(ids)), intent, title, start_local, end_local)

        # Follow-ups re-read prior sources on the server before handing context to
        # the model. Only fresh records get evidence IDs; history is never proof.
        if history:
            for previous in history[-1].get("retrievals", [])[:2]:
                read(previous["source"], previous["query"])
        self.model.run({
            "selected_person": {"id": person.id, "name": person.name},
            "current_time": now_utc().astimezone(zone).isoformat(),
            "timezone": zone.key, "question": question, "previous_turns": history,
            "fresh_evidence": list(evidence.values()),
            "remaining_read_calls": 4 - reads,
        }, read, finish)
        if invalid or not finished or selection is None:
            raise ConversationError("The answer's supporting records could not be verified.")
        ids, intent, title, start, end = selection
        selected = [evidence[identity] for identity in ids]
        if intent == "family_message":
            selected = selected[:3]
        lines = [
            (f"Recorded {ensure_aware(datetime.fromisoformat(record['at'])).astimezone(zone):%d %b %Y, %H:%M}: "
             if record["at"] else "") + record["text"]
            for record in selected
        ]
        answer = (
            f"Here is what is saved for {person.name}:\n" + "\n".join(lines)
            if selected else
            f"I couldn't find a supporting record for that in the sources checked for {person.name}."
        )
        action = None
        if intent == "family_message" and selected:
            action = {"type": "family_message", "message": f"Saved update for {person.name}:\n" + "\n".join(lines)}
            answer += "\nI've prepared an editable family update. Review the recipients and confirm before sending."
        elif intent == "appointment":
            # Parsed natural-language dates are draft suggestions, never authoritative
            # facts. The existing independent review and confirmation flow owns writes.
            valid = all(isinstance(value, str) for value in (title, start, end))
            valid = valid and 1 <= len(title.strip()) <= 200 and all(
                re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::00)?", value)
                for value in (start, end)
            )
            if valid:
                try:
                    valid = datetime.fromisoformat(end) > datetime.fromisoformat(start)
                except ValueError:
                    valid = False
            if valid:
                start, end = start[:16], end[:16]
                action = {"type": "appointment", "title": title, "start_local": start,
                          "end_local": end, "time_zone": zone.key}
                answer = "I've prepared an editable appointment draft from your request. Check the interpreted title, dates and timezone before saving."
            else:
                answer = (
                    "Please provide the appointment title, date, start time and end time or duration, "
                    "or use the appointment form. I haven't created an event."
                )
        elif intent == "clarify":
            answer = "I can help recall saved records or prepare an appointment or family update for your review. Please clarify what you want to do."
        notice = (
            "Answers quote saved household records, not verified wellbeing or medical advice. "
            "Talk to your doctor about treatment. Retrieval is bounded and may not include every record. "
            "No appointment or family message has been sent."
        )
        if any(value["kind"] == "medication" for value in selected):
            answer += "\nThese are saved facts, not medical advice. Talk to your doctor about treatment."
        # Speak a concise, verbatim saved fact rather than reading every timestamp
        # and evidence record aloud. Full provenance remains visible on screen.
        speech = answer
        if selected and intent in {"answer", "family_message"}:
            first = selected[0]
            if len(first["text"]) <= 600:
                speech = "The saved record says: " + first["text"]
                if len(selected) > 1:
                    speech += " There are more supporting records on screen."
            else:
                speech = "I found a detailed saved record. You can read it in the supporting records below."
            if intent == "family_message":
                speech += " Your family update is ready to review. Nothing has been sent."
            if any(value["kind"] == "medication" for value in selected):
                speech += " These are saved facts, not medical advice. Talk to your doctor about treatment."
        return {
            "person": {"id": person.id, "name": person.name, "role": person.role.value},
            "question": question, "conversation_id": "", "answer": answer, "speech": speech,
            "evidence": selected, "action": action, "sources_checked": checked,
            "notice": notice, "model_id": self.model.model_id,
            "_retrievals": retrievals,
        }
