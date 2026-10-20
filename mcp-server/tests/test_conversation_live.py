"""Explicitly paid, isolated Bedrock smoke: never touches the household graph."""
import os
from uuid import uuid4

from fastmcp import Client
import pytest

from saarthi_mcp.repository import now_utc, seeded_repository
from saarthi_mcp.server import build_server


@pytest.mark.skipif(os.getenv("SAARTHI_RUN_CONVERSATION_TESTS") != "1", reason="Explicit paid model opt-in")
async def test_real_bedrock_finds_new_fact_and_carries_followup(monkeypatch):
    from saarthi_agents.conversation import ConversationModel
    monkeypatch.setenv("SAARTHI_AGENTS", "off")
    monkeypatch.setenv("SAARTHI_AUTH", "local")
    repo = seeded_repository()
    person = repo.resolve_person("dad")
    marker = uuid4().hex[:10]
    detail = f"The travel pouch labelled {marker} is in the blue cabinet beside the kitchen."
    repo.add_event(person.id, "note", detail, now_utc())
    class TracedModel(ConversationModel):
        def run(self, prompt, read, finish):
            def traced_read(source, query=""):
                records = read(source, query)
                print("Read", source, "records", len(records))
                return records
            def traced_finish(ids, *args):
                print("Finish", len(ids), args)
                return finish(ids, *args)
            return super().run(prompt, traced_read, traced_finish)
    model = TracedModel(os.environ["BEDROCK_MODEL_ID"], "us-east-1")
    async with Client(build_server(repo, conversation_model=model)) as client:
        answer = (await client.call_tool("talk_to_saarthi", {
            "person": person.id, "question": f"Where should I look for the travel pouch labelled {marker}?",
            "time_zone": "Asia/Calcutta",
        }, timeout=110)).data
        assert detail in answer["answer"]
        assert answer["action"] is None
        followup = (await client.call_tool("talk_to_saarthi", {
            "person": person.id, "question": "Prepare that as an update for my family, without sending it.",
            "time_zone": "Asia/Calcutta", "conversation_id": answer["conversation_id"],
        }, timeout=110)).data
        assert followup["action"]["type"] == "family_message"
        assert detail in followup["action"]["message"]
        assert not repo._notifications
        unknown = (await client.call_tool("talk_to_saarthi", {
            "person": person.id, "question": "Where did I put my silver suitcase?",
            "time_zone": "Asia/Calcutta",
        }, timeout=110)).data
        assert not unknown["evidence"]
        assert "couldn't find" in unknown["answer"]
        appointment = (await client.call_tool("talk_to_saarthi", {
            "person": person.id,
            "question": "Prepare a family walk on October 22, 2026 from 12 noon to 12:15 pm. Do not save it yet.",
            "time_zone": "Asia/Calcutta",
        }, timeout=110)).data
        assert appointment["action"]["type"] == "appointment"
        assert appointment["action"]["start_local"] == "2026-10-22T12:00"
        assert appointment["action"]["end_local"] == "2026-10-22T12:15"
