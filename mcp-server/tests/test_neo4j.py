"""Live Neo4j tests — real cross-session recall against a real graph (AGENTS.md §6).

Destructive (wipes + seeds the dedicated local test DB) and needs a reachable Neo4j, so skipped unless
``SAARTHI_RUN_NEO4J_TESTS=1`` and the ``neo4j`` backend is configured. Run against the dev/demo
container on port 17687 only (see memory/README.md):

    SAARTHI_RUN_NEO4J_TESTS=1 pytest tests/test_neo4j.py -q
"""

from __future__ import annotations

import os
import asyncio
import socket
import subprocess
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import pytest
from fastmcp import Client

from saarthi_mcp.config import load_settings
from saarthi_mcp.models import DoseStatus
from saarthi_mcp.repository import PersonNotFoundError

pytestmark = pytest.mark.skipif(
    os.getenv("SAARTHI_RUN_NEO4J_TESTS") != "1",
    reason="set SAARTHI_RUN_NEO4J_TESTS=1 to run (destructive; needs a reachable Neo4j)",
)


@pytest.fixture(scope="module")
def repo():
    from saarthi_mcp.neo4j_repo import Neo4jRepository, seed_neo4j

    s = load_settings()
    assert s.backend == "neo4j" and s.neo4j is not None, "configure SAARTHI_BACKEND=neo4j + NEO4J_*"
    target = urlparse(s.neo4j.uri)
    assert target.hostname in {"localhost", "127.0.0.1"} and target.port == 17687, (
        "Destructive tests only run against the dedicated local test database on port 17687; "
        "start memory/docker-compose.test.yml. Aura and the development database are prohibited."
    )
    r = Neo4jRepository.from_settings(s.neo4j)
    seed_neo4j(r, wipe=True)
    yield r
    r.close()


def test_resolve_by_alias_and_primary_elder(repo):
    assert repo.resolve_person("dad").name == "Ramesh"
    assert repo.primary_elder().name == "Ramesh"


def test_seeded_medications(repo):
    names = {m.name for m in repo.medications_for("elder-1")}
    assert {"Metformin", "Amlodipine", "Atorvastatin"} <= names


def test_adherence_reflects_missed_dose(repo):
    a = repo.adherence("elder-1")
    assert 0.0 <= a < 1.0  # one seeded evening dose was missed


def test_write_then_recall_from_graph(repo):
    at = datetime.now(timezone.utc)
    _, already = repo.add_dose("elder-1", "Atorvastatin", DoseStatus.taken, at)
    assert already is False
    answer, _ = repo.query_memory("elder-1", "did dad take his Atorvastatin?")
    assert "Atorvastatin" in answer and "took" in answer.lower()


def test_cross_session_recall_of_physio_call(repo):
    answer, events = repo.query_memory("elder-1", "did the physio call back?")
    assert "Meera" in answer or "call back" in answer.lower()
    assert events


def test_duplicate_dose_guard(repo):
    at = datetime.now(timezone.utc) - timedelta(hours=1)
    _, first = repo.add_dose("elder-1", "Amlodipine", DoseStatus.taken, at)
    _, second = repo.add_dose("elder-1", "Amlodipine", DoseStatus.taken, at + timedelta(minutes=5))
    assert first is False
    assert second is True


def test_upcoming_appointments_sorted(repo):
    appts = repo.upcoming_appointments("elder-1", limit=10)
    assert len(appts) >= 2
    whens = [a.when for a in appts]
    assert whens == sorted(whens)


def test_new_appointment_records_history_without_claiming_provider_booking(repo):
    when = datetime.now(timezone.utc) + timedelta(days=4)
    appt = repo.add_appointment("elder-1", "Audit calendar entry", when)
    assert any(a.id == appt.id for a in repo.upcoming_appointments("elder-1", limit=20))
    events = repo.recent_events("elder-1", limit=50)
    assert any(e.type == "appointment_recorded" and "Audit calendar entry" in e.detail for e in events)


def test_unknown_person_raises(repo):
    with pytest.raises(PersonNotFoundError):
        repo.resolve_person("stranger")


@asynccontextmanager
async def fresh_http_server(agent_mode="off"):
    """Run a new OS process, so recall cannot accidentally use an old Python repository."""
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    env = dict(os.environ, SAARTHI_HOST="127.0.0.1", SAARTHI_PORT=str(port),
               SAARTHI_MCP_PATH="/mcp", SAARTHI_AGENTS=agent_mode)
    process = subprocess.Popen(
        [sys.executable, "-m", "saarthi_mcp"], env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(150):
            if process.poll() is not None:
                raise RuntimeError("MCP subprocess exited before startup")
            try:
                _, writer = await asyncio.open_connection("127.0.0.1", port)
                writer.close()
                await writer.wait_closed()
                break
            except OSError:
                await asyncio.sleep(0.2)
        else:
            raise TimeoutError("MCP subprocess did not start within 30 seconds")
        async with Client(f"http://127.0.0.1:{port}/mcp") as client:
            yield client
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


async def test_new_event_and_dose_survive_server_process_restart(repo):
    # The module fixture enforces the disposable DB before either process starts.
    from uuid import uuid4

    marker = "persistence" + uuid4().hex
    async with fresh_http_server() as writer:
        assert writer.protocol_version >= "2025-11-25"
        recorded = await writer.call_tool("record_event", {
            "person": "dad", "type": "call", "detail": f"Physio called back: {marker}",
        })
        logged = await writer.call_tool("log_dose", {
            "person": "dad", "med": "Metformin", "taken": True,
        })
        event_at = recorded.data["event"]["at"]
        dose_at = logged.data["dose"]["at"]

    # Relevant history must survive both restarts and more than 50 later entries.
    repo._write(
        "UNWIND range(1, 75) AS number MATCH (p:Person {id:'elder-1'}) "
        "CREATE (p)-[:EXPERIENCED]->(:Event {type:'note', detail:'Unrelated subsequent entry', at:$at})",
        at=datetime.now(timezone.utc),
    )

    # First process has exited; second process creates a new Neo4j connection.
    async with fresh_http_server() as reader:
        event = await reader.call_tool("query_memory", {"person": "dad", "question": marker})
        assert marker in event.data["answer"]
        assert any(e["at"] == event_at for e in event.data["supporting_events"])
        dose = await reader.call_tool("query_memory", {
            "person": "dad", "question": "did dad take his Metformin?",
        })
        assert "Metformin" in dose.data["answer"] and "took" in dose.data["answer"]
        assert any(e["at"] == dose_at for e in dose.data["supporting_events"])


@pytest.mark.skipif(os.getenv("SAARTHI_RUN_BEDROCK_TESTS") != "1",
                    reason="opt-in live Bedrock invocation uses credits")
async def test_live_strands_delegation_writes_to_graph_over_http(repo):
    before = len(repo.dose_logs("elder-1"))
    async with fresh_http_server(agent_mode="bedrock") as client:
        result = await client.call_tool("log_dose", {
            "person": "dad", "med": "Amlodipine", "taken": True,
        })
        assert result.data["dose"]["status"] == "taken"
        assert result.data["already_logged"] is False
    assert len(repo.dose_logs("elder-1")) == before + 1
    events = repo.recent_events("elder-1", limit=10)
    assert any(e.type == "agent_action" and "MedGuardian" in e.detail for e in events)
    async with fresh_http_server() as client:
        recalled = await client.call_tool("query_memory", {
            "person": "dad", "question": "did dad take Amlodipine?",
        })
        assert "took Amlodipine" in recalled.data["answer"]
