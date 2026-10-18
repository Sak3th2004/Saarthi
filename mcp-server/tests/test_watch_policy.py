"""WatchPolicy window / grace — never invents medical meaning from silence."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from saarthi_mcp.watch_policy import WatchPolicy


IST = ZoneInfo("Asia/Calcutta")


def test_defaults_match_demo_household():
    policy = WatchPolicy.from_env()
    assert policy.timezone == "Asia/Calcutta"
    assert policy.window_start == "08:00"
    assert policy.window_end == "21:00"
    assert policy.grace_hours == 2
    assert policy.low_supply_threshold == 10


def test_invalid_timezone_rejected(monkeypatch):
    monkeypatch.setenv("SAARTHI_WATCH_TIMEZONE", "Not/AZone")
    with pytest.raises(ValueError, match="IANA"):
        WatchPolicy.from_env()


def test_activity_stale_respects_grace_and_window():
    policy = WatchPolicy()
    # Before window open — never stale.
    assert policy.activity_stale(None, instant=datetime(2026, 10, 12, 7, 0, tzinfo=IST)) is False
    # Inside grace (08:00–10:00) — not stale yet.
    assert policy.activity_stale(None, instant=datetime(2026, 10, 12, 9, 0, tzinfo=IST)) is False
    # After grace, no activity — stale.
    assert policy.activity_stale(None, instant=datetime(2026, 10, 12, 11, 0, tzinfo=IST)) is True
    # Activity after window open clears the concern.
    recent = datetime(2026, 10, 12, 8, 30, tzinfo=IST)
    assert policy.activity_stale(recent, instant=datetime(2026, 10, 12, 11, 0, tzinfo=IST)) is False
    # Yesterday's activity is stale after grace.
    yesterday = datetime(2026, 10, 11, 20, 0, tzinfo=IST)
    assert policy.activity_stale(yesterday, instant=datetime(2026, 10, 12, 11, 0, tzinfo=IST)) is True
    # Outside the active window — do not escalate from silence alone.
    assert policy.activity_stale(None, instant=datetime(2026, 10, 12, 22, 0, tzinfo=IST)) is False


def test_overnight_window_uses_previous_evening_after_midnight():
    policy = WatchPolicy(window_start="21:00", window_end="06:00")
    night = datetime(2026, 10, 13, 2, 0, tzinfo=IST)
    assert policy.activity_stale(None, instant=night) is True
    assert policy.activity_stale(datetime(2026, 10, 12, 22, 0, tzinfo=IST), instant=night) is False
    assert policy.activity_stale(datetime(2026, 10, 12, 20, 0, tzinfo=IST), instant=night) is True
    assert policy.activity_stale(None, instant=datetime(2026, 10, 12, 22, 0, tzinfo=IST)) is False
    assert policy.activity_stale(None, instant=datetime(2026, 10, 13, 7, 0, tzinfo=IST)) is False


async def test_checking_watch_does_not_clear_missing_care_activity(monkeypatch):
    from datetime import timedelta, timezone
    from fastmcp import Client
    from saarthi_mcp.models import Person, Role
    from saarthi_mcp.repository import InMemoryRepository
    from saarthi_mcp.server import build_server

    monkeypatch.setenv("SAARTHI_AGENTS", "off")
    monkeypatch.setenv("SAARTHI_AUTH", "local")
    fixed = datetime(2026, 10, 12, 5, 30, tzinfo=timezone.utc)
    monkeypatch.setattr("saarthi_mcp.server.now_utc", lambda: fixed)
    repo = InMemoryRepository()
    repo.add_person(Person(id="p", name="Parent", role=Role.elder))
    for kind in ("household_setup", "notify"):
        repo.add_event("p", kind, "Internal record", fixed)
    repo.add_event("p", "note", "Future record", fixed + timedelta(days=1))

    class AuditingOrchestrator:
        def execute(self, *, operation, arguments, action, record):
            result = action()
            record({"operation": operation, "status": "completed"})
            return result

    async with Client(build_server(repo, orchestrator=AuditingOrchestrator())) as client:
        for _ in range(2):
            result = (await client.call_tool("check_in", {"person": "p"})).data
            assert result["last_activity"] is None
            assert any("no saved activity" in item for item in result["concerns"])
        repo.add_event("p", "note", "Family visit recorded", fixed)
        result = (await client.call_tool("check_in", {"person": "p"})).data
        assert result["concerns"] == []
        assert result["ok"] is None  # Records never prove wellbeing.


async def test_check_in_flags_stale_activity_after_grace(monkeypatch):
    from datetime import timezone

    from fastmcp import Client

    from saarthi_mcp.models import Person, Role
    from saarthi_mcp.repository import InMemoryRepository
    from saarthi_mcp.server import build_server

    monkeypatch.setenv("SAARTHI_AGENTS", "off")
    # 11:00 Asia/Calcutta = 05:30 UTC — after the 2h grace.
    fixed = datetime(2026, 10, 12, 5, 30, tzinfo=timezone.utc)
    monkeypatch.setattr("saarthi_mcp.server.now_utc", lambda: fixed)
    repo = InMemoryRepository()
    repo.add_person(Person(id="elder-1", name="Ravi", role=Role.elder))
    async with Client(build_server(repo)) as client:
        check = (await client.call_tool("check_in", {"person": "elder-1"})).data
    assert check["ok"] is False
    assert any("no saved activity" in c for c in check["concerns"])
    assert "Not enough information" not in check["speech"]
    assert "looks fine" not in check["speech"]
    assert "not proof of an emergency" in check["speech"]
