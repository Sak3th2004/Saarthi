"""User-entered setup validation, empty startup and non-destructive sample tooling."""

import json
from uuid import uuid4

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError
from pydantic import ValidationError

from saarthi_mcp.config import Neo4jSettings, Settings
from saarthi_mcp.household import HouseholdDefinition, load_household, main
from saarthi_mcp.repository import InMemoryRepository, PersonNotFoundError
from saarthi_mcp.server import _default_repo, build_server


@pytest.fixture
def household_data():
    # Novel identifiers make accidental dependence on built-in Ramesh fixtures visible.
    elder, family = "elder_" + uuid4().hex, "family_" + uuid4().hex
    return {
        "primary_person_id": elder,
        "people": [
            {"id": family, "name": "Family " + uuid4().hex, "role": "family", "aliases": ["caregiver"]},
            {"id": elder, "name": "Parent " + uuid4().hex, "role": "elder", "aliases": ["parent"],
             "medications": [{"name": "Saved routine " + uuid4().hex, "dose": "as entered",
                              "schedule": ["07:15"], "supply_count": 0}]},
        ],
        "relationships": [{"from_person": family, "to_person": elder, "relation": "child"}],
    }


def test_arbitrary_household_import_is_not_seed_data(household_data):
    definition = HouseholdDefinition.model_validate(household_data)
    repo = InMemoryRepository()
    repo.import_household(definition)
    elder = repo.primary_elder()
    assert elder.id == definition.primary_person_id
    assert repo.resolve_person("parent") == elder
    assert [m.model_dump() for m in repo.medications_for(elder.id)] == [m.model_dump() for m in definition.people[1].medications]
    assert repo.dose_logs(elder.id) == []
    assert repo.upcoming_appointments(elder.id) == []
    assert repo.recent_events(elder.id)[0].type == "household_setup"
    assert repo._relationships == [(definition.people[0].id, elder.id, "child")]
    with pytest.raises(PersonNotFoundError):
        repo.resolve_person("Ramesh")
    with pytest.raises(ValueError, match="empty store"):
        repo.import_household(definition)
    assert len(repo.recent_events(elder.id)) == 1


@pytest.mark.parametrize("change", [
    lambda d: d.update(primary_person_id="absent"),
    lambda d: d.update(primary_person_id=d["people"][0]["id"]),
    lambda d: d["people"].append(d["people"][0]),
    lambda d: d["people"][1].update(aliases=["caregiver"]),
    lambda d: d["people"][1].update(aliases=[d["people"][0]["id"]]),
    lambda d: d["people"][1].update(name=" "),
    lambda d: d["people"][1].update(unrecognized_option=True),
    lambda d: d["people"][1]["medications"][0].update(schedule=["25:00"]),
    lambda d: d["people"][1]["medications"][0].update(supply_count=-1),
    lambda d: d["people"][1]["medications"][0].update(unrecognized_option=True),
    lambda d: d["people"][1]["medications"].append(d["people"][1]["medications"][0]),
    lambda d: d["relationships"][0].update(to_person="missing"),
    lambda d: d["relationships"][0].update(to_person=d["relationships"][0]["from_person"]),
])
def test_invalid_or_ambiguous_setup_is_rejected(household_data, change):
    change(household_data)
    with pytest.raises(ValidationError):
        HouseholdDefinition.model_validate(household_data)


async def test_default_server_has_no_invented_household(monkeypatch):
    monkeypatch.setenv("SAARTHI_BACKEND", "memory")
    monkeypatch.setenv("SAARTHI_AGENTS", "off")
    monkeypatch.delenv("SAARTHI_HOUSEHOLD_FILE", raising=False)
    async with Client(build_server()) as client:
        with pytest.raises(ToolError, match="No household is configured"):
            await client.call_tool("get_household_summary", {})
        with pytest.raises(ToolError, match="Unknown person"):
            await client.call_tool("get_medication_schedule", {"person": "dad"})


async def test_file_supplies_actual_mcp_records(household_data, tmp_path):
    path = tmp_path / "private.json"
    path.write_text(json.dumps(household_data), encoding="utf-8")
    repo = _default_repo(Settings(host="127.0.0.1", port=8080, backend="memory", household_file=str(path)))
    async with Client(build_server(repo)) as client:
        result = await client.call_tool("get_medication_schedule", {"person": "parent"})
        assert result.data["person"]["id"] == household_data["primary_person_id"]
        assert result.data["medications"] == household_data["people"][1]["medications"]


def test_check_mode_does_not_open_database(household_data, tmp_path, monkeypatch, capsys):
    from saarthi_mcp.neo4j_repo import Neo4jRepository
    monkeypatch.setattr(Neo4jRepository, "from_settings", lambda _: pytest.fail("Unexpected database access"))
    path = tmp_path / "private.json"
    path.write_text(json.dumps(household_data), encoding="utf-8")
    main([str(path), "--check"])
    output = capsys.readouterr().out
    assert "No records written" in output
    assert household_data["people"][1]["name"] not in output


def test_schema_command_needs_no_household_or_connection(capsys):
    main(["--schema"])
    schema = json.loads(capsys.readouterr().out)
    assert {"people", "primary_person_id"} <= set(schema["required"])


def test_invalid_file_does_not_echo_personal_input(tmp_path):
    path = tmp_path / "private.json"
    path.write_text('{"private_value":"sensitive-content"}', encoding="utf-8")
    with pytest.raises(SystemExit) as error:
        main([str(path), "--check"])
    assert "sensitive-content" not in str(error.value)


def test_oversized_file_is_rejected(tmp_path):
    path = tmp_path / "private.json"
    path.write_bytes(b" " * 1_000_001)
    with pytest.raises(ValueError, match="1 MB"):
        load_household(path)


@pytest.mark.parametrize("uri,database", [
    ("neo4j+s://example.databases.neo4j.io", "neo4j"),
    ("bolt://127.0.0.1:7687", "neo4j"),
    ("bolt://example.com:17687", "neo4j"),
    ("bolt://127.0.0.1:17687", "household"),
    ("neo4j://127.0.0.1:17687", "neo4j"),
])
def test_sample_cli_rejects_non_test_targets_before_connecting(uri, database, monkeypatch):
    from saarthi_mcp import seed
    settings = Settings(host="127.0.0.1", port=8080, backend="neo4j",
                        neo4j=Neo4jSettings(uri, "neo4j", "test-only", database))
    monkeypatch.setattr(seed, "load_settings", lambda: settings)
    monkeypatch.setattr(seed.Neo4jRepository, "from_settings", lambda _: pytest.fail("Unexpected connection"))
    with pytest.raises(SystemExit, match="Refusing sample reset"):
        seed.main(["--reset-test-database"])


def test_sample_cli_requires_explicit_reset(monkeypatch):
    from saarthi_mcp import seed
    monkeypatch.setattr(seed, "load_settings", lambda: pytest.fail("Unexpected settings access"))
    with pytest.raises(SystemExit):
        seed.main([])
