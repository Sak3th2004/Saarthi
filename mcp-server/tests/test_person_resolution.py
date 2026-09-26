"""Ambiguous identity must not choose the first/last matching household member."""

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from saarthi_mcp.models import Person, Role
from saarthi_mcp.repository import AmbiguousPersonError, InMemoryRepository, PersonNotFoundError
from saarthi_mcp.server import build_server


@pytest.fixture
def ambiguous_repo():
    repo = InMemoryRepository()
    for key in ("first", "second"):
        repo.add_person(Person(id=key, name="Shared name", role=Role.elder), aliases=[" Parent "])
    return repo


@pytest.mark.parametrize("label", ["Shared name", "SHARED NAME", " parent "])
def test_duplicate_name_or_alias_requires_unique_id(ambiguous_repo, label):
    with pytest.raises(AmbiguousPersonError, match="unique person ID"):
        ambiguous_repo.resolve_person(label)
    assert ambiguous_repo.resolve_person("first").id == "first"
    assert ambiguous_repo.resolve_person("second").id == "second"


def test_replacing_person_removes_stale_aliases_without_removing_other_matches(ambiguous_repo):
    ambiguous_repo.add_person(Person(id="first", name="Updated name", role=Role.elder), aliases=["new alias"])
    assert ambiguous_repo.resolve_person("Parent").id == "second"
    assert ambiguous_repo.resolve_person("Shared name").id == "second"
    assert ambiguous_repo.resolve_person("new alias").id == "first"
    ambiguous_repo.add_person(Person(id="second", name="Second updated", role=Role.elder))
    with pytest.raises(PersonNotFoundError):
        ambiguous_repo.resolve_person("Shared name")


async def test_ambiguous_write_does_not_invoke_agent_or_change_anyones_memory(ambiguous_repo):
    class MustNotRun:
        def execute(self, **kwargs):
            pytest.fail("Ambiguous identity reached the paid agent")

    async with Client(build_server(ambiguous_repo, orchestrator=MustNotRun())) as client:
        with pytest.raises(ToolError, match="unique person ID"):
            await client.call_tool("book_appointment", {
                "person": "parent", "kind": "visit", "when": "2026-10-01T09:00:00Z",
            })
    assert ambiguous_repo.recent_events("first") == []
    assert ambiguous_repo.recent_events("second") == []


def test_failed_lookup_does_not_disclose_other_people(ambiguous_repo):
    with pytest.raises(PersonNotFoundError) as error:
        ambiguous_repo.resolve_person("unknown")
    assert "Shared name" not in str(error.value)
