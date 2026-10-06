"""No observations must not become positive health claims."""
from datetime import timedelta

from fastmcp import Client
import pytest

from saarthi_mcp.models import DoseStatus, Person, Role
from saarthi_mcp.repository import InMemoryRepository, now_utc
from saarthi_mcp.server import build_server


@pytest.fixture
def empty_household():
    repo = InMemoryRepository()
    repo.add_person(Person(id="person-x", name="Test member", role=Role.elder))
    return repo


async def test_empty_summary_and_check_in_do_not_claim_adherence_or_wellbeing(empty_household):
    async with Client(build_server(empty_household)) as client:
        summary = (await client.call_tool("get_household_summary", {})).data
        assert summary["adherence_7d"] is None
        assert "Not enough information" in summary["speech"]
        assert "100%" not in summary["speech"]
        check = (await client.call_tool("check_in", {"person": "person-x"})).data
        assert check["ok"] is None
        assert "Not enough information" in check["speech"]
        assert "looks fine" not in check["speech"]


async def test_recorded_taken_dose_does_not_prove_schedule_coverage(empty_household):
    empty_household.add_dose("person-x", "Saved medication", DoseStatus.taken, now_utc())
    async with Client(build_server(empty_household)) as client:
        summary = (await client.call_tool("get_household_summary", {})).data
        assert summary["adherence_7d"] == 1.0
        assert summary["adherence_basis"] == "recorded_taken_and_missed_doses_only"
        assert "100% of recorded" in summary["speech"]
        assert "does not confirm" in summary["speech"]
        assert (await client.call_tool("check_in", {"person": "person-x"})).data["ok"] is None


@pytest.mark.parametrize("status,offset", [
    (DoseStatus.skipped, timedelta()),
    (DoseStatus.taken, timedelta(days=-8)),
    (DoseStatus.taken, timedelta(days=1)),
])
def test_non_observations_do_not_become_taken_percentage(empty_household, status, offset):
    empty_household.add_dose("person-x", "Saved medication", status, now_utc() + offset)
    assert empty_household.adherence("person-x") is None


async def test_missed_record_is_reported_without_medical_action(empty_household):
    empty_household.add_dose("person-x", "Saved medication", DoseStatus.missed, now_utc())
    async with Client(build_server(empty_household)) as client:
        check = (await client.call_tool("check_in", {"person": "person-x"})).data
        assert check["ok"] is False
        assert check["missed_doses_today"] == 1
        assert "recorded as missed" in check["speech"]
        assert "check with the family" in check["speech"]


async def test_future_missed_record_is_not_reported_as_today(empty_household):
    empty_household.add_dose("person-x", "Saved medication", DoseStatus.missed, now_utc() + timedelta(days=1))
    async with Client(build_server(empty_household)) as client:
        check = (await client.call_tool("check_in", {"person": "person-x"})).data
        assert check["missed_doses_today"] == 0
        assert check["ok"] is None
