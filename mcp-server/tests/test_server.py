"""End-to-end tests: drive the MCP tools through a real FastMCP client (in-memory transport)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from saarthi_mcp.config import TARGET_MCP_SPEC
from saarthi_mcp.repository import seeded_repository
from saarthi_mcp.server import build_server

ALL_TOOLS = {
    "get_household_summary",
    "get_medication_schedule",
    "log_dose",
    "book_appointment",
    "list_appointments",
    "notify_family",
    "record_event",
    "query_memory",
    "check_in",
}


@pytest.fixture
def server():
    # Fresh seeded repository per test so writes don't leak across tests.
    return build_server(seeded_repository())


@pytest.fixture
async def client(server):
    async with Client(server) as c:
        yield c


async def test_protocol_version_meets_target(client):
    # The negotiated MCP spec version must be >= the target date (AGENTS.md §5, FRICTION #001).
    version = client.protocol_version
    assert version is not None
    assert version >= TARGET_MCP_SPEC, f"negotiated {version} < target {TARGET_MCP_SPEC}"


async def test_all_contract_tools_registered(client):
    tools = {t.name for t in await client.list_tools()}
    assert ALL_TOOLS <= tools, f"missing tools: {ALL_TOOLS - tools}"


async def test_get_household_summary(client):
    res = await client.call_tool("get_household_summary", {})
    data = res.data
    assert data["person"]["name"] == "Ramesh"
    assert data["medications"], "expected seeded medications"
    assert 0.0 <= data["adherence_7d"] <= 1.0
    assert isinstance(res.content[0].text, str) and res.content[0].text  # speech present


async def test_log_dose_then_recall_from_memory(client):
    logged = await client.call_tool(
        "log_dose", {"person": "dad", "med": "Metformin", "taken": True}
    )
    assert logged.data["dose"]["status"] == "taken"
    assert logged.data["already_logged"] is False

    recall = await client.call_tool(
        "query_memory", {"person": "dad", "question": "did dad take his Metformin?"}
    )
    assert "Metformin" in recall.data["answer"]
    assert "took" in recall.data["answer"].lower()


async def test_log_dose_duplicate_guard(client):
    at = datetime.now(timezone.utc).isoformat()
    first = await client.call_tool(
        "log_dose", {"person": "dad", "med": "Amlodipine", "taken": True, "at": at}
    )
    second = await client.call_tool(
        "log_dose", {"person": "dad", "med": "Amlodipine", "taken": True, "at": at}
    )
    assert first.data["already_logged"] is False
    assert second.data["already_logged"] is True


async def test_cross_session_recall_of_seeded_event(client):
    # The physio-call event was seeded (a prior "session"); recall must find it.
    res = await client.call_tool(
        "query_memory", {"person": "dad", "question": "did the physio call back?"}
    )
    answer = res.data["answer"].lower()
    assert "meera" in answer or "call back" in answer


async def test_recall_is_punctuation_insensitive(client):
    # Regression: a real code-mixed request surfaced that "water?" (with punctuation) failed to
    # match "water" in the stored event. Recording an instruction must be recallable with a
    # question that ends in punctuation.
    await client.call_tool(
        "record_event",
        {
            "person": "dad",
            "type": "care_instruction",
            "detail": "Drink 5 liters of water daily; he has kidney stones.",
        },
    )
    res = await client.call_tool(
        "query_memory", {"person": "dad", "question": "what did we note about dad's water?"}
    )
    assert "water" in res.data["answer"].lower()


async def test_medical_advice_is_refused_and_deferred(client):
    res = await client.call_tool(
        "query_memory",
        {"person": "dad", "question": "should I increase his Metformin dose?"},
    )
    answer = res.data["answer"].lower()
    assert "doctor" in answer
    assert "medical advice" in answer


async def test_book_and_list_appointment(client):
    when = (datetime.now(timezone.utc) + timedelta(days=5)).isoformat()
    booked = await client.call_tool(
        "book_appointment", {"person": "dad", "kind": "Eye check", "when": when}
    )
    assert booked.data["appointment"]["kind"] == "Eye check"

    listed = await client.call_tool("list_appointments", {"person": "dad"})
    kinds = {a["kind"] for a in listed.data["appointments"]}
    assert "Eye check" in kinds


async def test_check_in_flags_low_supply(client):
    res = await client.call_tool("check_in", {"person": "dad"})
    data = res.data
    assert isinstance(data["missed_doses_today"], int)
    # Atorvastatin is seeded with supply_count=8 (<=10) → must be flagged.
    assert any("Atorvastatin" in c for c in data["concerns"])
    assert data["ok"] is False


async def test_notify_family_records_contacts(client):
    res = await client.call_tool(
        "notify_family",
        {"person": "dad", "message": "Please call this evening", "urgency": "warning"},
    )
    assert res.data["urgency"] == "warning"
    assert res.data["delivered_to"], "expected at least one family channel"


async def test_unknown_person_raises_tool_error(client):
    with pytest.raises(ToolError):
        await client.call_tool("get_medication_schedule", {"person": "nobody"})
