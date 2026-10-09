"""Local setup must save entered records, preserve existing data and reject remote callers."""

from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import httpx
import pytest
from fastmcp import FastMCP, Client

from saarthi_mcp.config import load_settings
from saarthi_mcp.household_setup import HouseholdSetup, SetupError, attach_household_setup
from saarthi_mcp.repository import InMemoryRepository
from saarthi_mcp.server import build_server


def definition():
    parent, family = uuid4().hex, uuid4().hex
    return {"primary_person_id": parent, "people": [
        {"id": parent, "name": "Entered " + parent, "role": "elder"},
        {"id": family, "name": "Entered " + family, "role": "family"}],
        "relationships": [{"from_person": family, "to_person": parent, "relation": "child"}]}


def test_review_writes_nothing_confirmation_saves_once_and_rejects_replacement():
    repo = InMemoryRepository()
    service = HouseholdSetup(repo, persistent=False)
    data = definition()
    review = service.review(data)
    assert service.status({}) == {"configured": False, "persistence": "session"}
    # The saved review owns a validated copy, not mutable caller input.
    data["people"][0]["name"] = "Unreviewed replacement"
    confirm = {"review_token": review["review_token"], "confirmed": True}
    result = service.confirm(confirm)
    assert result == service.confirm(confirm)
    assert result["primary_person"]["name"] == review["household"]["people"][0]["name"]
    assert service.status({})["primary_person"] == result["primary_person"]
    assert len(repo.recent_events(data["primary_person_id"])) == 1
    assert len(repo._relationships) == 1
    assert repo.dose_logs(data["primary_person_id"]) == []
    with pytest.raises(SetupError, match="already saved"):
        service.review(definition())


@pytest.mark.parametrize("extra", [{"confirmed": False}, {"confirmed": "true"}, {"review_token": "unknown"}, {"household": {}}])
def test_confirmation_cannot_bypass_or_change_review(extra):
    service = HouseholdSetup(InMemoryRepository(), persistent=False)
    review = service.review(definition())
    with pytest.raises(SetupError):
        service.confirm({"review_token": review["review_token"], "confirmed": True, **extra})
    assert not service.status({})["configured"]


def test_expired_and_invalid_reviews_do_not_write_or_echo_input():
    service = HouseholdSetup(InMemoryRepository(), persistent=False)
    review = service.review(definition())
    service.reviews[review["review_token"]]["expires"] = 0
    with pytest.raises(SetupError, match="expired"):
        service.confirm({"review_token": review["review_token"], "confirmed": True})
    with pytest.raises(SetupError) as error:
        service.review({"private_input": "secret-value"})
    assert "secret-value" not in str(error.value)
    assert not service.status({})["configured"]


def test_two_administrators_cannot_merge_different_households():
    repo = InMemoryRepository()
    services = [HouseholdSetup(repo, persistent=False) for _ in range(2)]
    reviews = [s.review(definition()) for s in services]
    def confirm(pair):
        service, review = pair
        try:
            return service.confirm({"review_token": review["review_token"], "confirmed": True})
        except SetupError:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(confirm, zip(services, reviews)))
    assert sum(result is not None for result in results) == 1
    assert len(repo._people) == 2
    assert len(repo._relationships) == 1


@pytest.mark.parametrize("host,origin,peer,content_type,site", [
    ("127.0.0.1:8080", None, "127.0.0.1", "application/json", "same-origin"),
    ("127.0.0.1:8080", "null", "127.0.0.1", "application/json", "same-origin"),
    ("127.0.0.1:8080", "https://untrusted.example", "127.0.0.1", "application/json", "same-origin"),
    ("untrusted.example", "http://127.0.0.1:5173", "127.0.0.1", "application/json", "same-origin"),
    ("127.0.0.1:8080", "http://127.0.0.1:5173", "192.0.2.1", "application/json", "same-origin"),
    ("127.0.0.1:8080", "http://127.0.0.1:5173", "127.0.0.1", "text/plain", "same-origin"),
    ("127.0.0.1:8080", "http://127.0.0.1:5173", "127.0.0.1", "application/json", "cross-site"),
])
async def test_route_rejects_untrusted_requests(host, origin, peer, content_type, site):
    service = HouseholdSetup(InMemoryRepository(), persistent=False)
    server = FastMCP("setup")
    attach_household_setup(server, service)
    headers = {"host": host, "content-type": content_type, "sec-fetch-site": site}
    if origin is not None:
        headers["origin"] = origin
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.http_app(), client=(peer, 1)),
                                 base_url="http://127.0.0.1:8080") as client:
        for action in ("status", "review", "confirm"):
            response = await client.post("/local/household/" + action, json={}, headers=headers)
            assert response.status_code == 403
            assert response.headers["cache-control"] == "no-store"
    assert not service.status({})["configured"]


async def test_http_setup_is_visible_through_real_mcp_tools(monkeypatch):
    monkeypatch.setenv("SAARTHI_AGENTS", "off")
    repo = InMemoryRepository()
    service = HouseholdSetup(repo, persistent=False)
    server = build_server(repo)
    attach_household_setup(server, service)
    data = definition()
    headers = {"origin": "http://127.0.0.1:5173"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.http_app(), client=("127.0.0.1", 1)),
                                 base_url="http://127.0.0.1:8080", headers=headers) as client:
        assert (await client.post("/local/household/status", json={})).json()["configured"] is False
        review = (await client.post("/local/household/review", json=data)).json()
        response = await client.post("/local/household/confirm", json={"review_token": review["review_token"], "confirmed": True})
        assert response.status_code == 200
        assert response.json()["status"] == "saved"
        bad = await client.post("/local/household/review", content=b'{"private":bad}', headers={"content-type": "application/json"})
        assert bad.status_code == 400 and "private" not in bad.text
        large = await client.post("/local/household/review", content=b" " * 1_000_001, headers={"content-type": "application/json"})
        assert large.status_code == 413
    async with Client(server) as mcp:
        tools = await mcp.list_tools()
        assert all("household_setup" not in tool.name for tool in tools)
        graph = (await mcp.call_tool("get_memory_graph", {"person": data["primary_person_id"]})).structured_content
        assert graph["person_id"] == data["primary_person_id"]
        assert data["people"][0]["name"] in str(graph)
        from datetime import datetime, timedelta, timezone
        marker = "callback" + uuid4().hex
        repo.add_event(data["primary_person_id"], "call", marker, datetime.now(timezone.utc) - timedelta(days=1))
        today = (await mcp.call_tool("query_memory", {"person": data["primary_person_id"], "question": marker + " today", "time_zone": "Asia/Kolkata"})).data
        yesterday = (await mcp.call_tool("query_memory", {"person": data["primary_person_id"], "question": marker + " yesterday", "time_zone": "Asia/Kolkata"})).data
        assert today["supporting_events"] == []
        assert yesterday["supporting_events"][0]["detail"] == marker


async def test_failed_database_write_is_not_claimed_saved_or_leaked(monkeypatch):
    repo = InMemoryRepository()
    service = HouseholdSetup(repo, persistent=True)
    server = FastMCP("setup")
    attach_household_setup(server, service)
    review = service.review(definition())
    def fail(_):
        raise RuntimeError("private database credentials")
    monkeypatch.setattr(repo, "import_household", fail)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.http_app(), client=("127.0.0.1", 1)),
                                 base_url="http://127.0.0.1:8080") as client:
        response = await client.post("/local/household/confirm", json={"review_token": review["review_token"], "confirmed": True},
                                     headers={"origin": "http://127.0.0.1:5173"})
    assert response.status_code == 503
    assert "credentials" not in response.text
    assert service.reviews[review["review_token"]]["result"] is None


@pytest.mark.parametrize("host,port", [("0.0.0.0", "8080"), ("localhost", "8080"), ("127.0.0.1", "9000")])
def test_local_setup_cannot_bind_to_public_or_unexpected_address(monkeypatch, host, port):
    monkeypatch.setenv("SAARTHI_LOCAL_SETUP", "1")
    monkeypatch.setenv("SAARTHI_HOST", host)
    monkeypatch.setenv("SAARTHI_PORT", port)
    with pytest.raises(ValueError, match="127.0.0.1:8080"):
        load_settings()
