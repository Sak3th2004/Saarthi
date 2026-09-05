"""Seed the Neo4j graph with the demo household.

    python -m saarthi_mcp.seed          # wipes + seeds the DB in SAARTHI_BACKEND=neo4j

Destructive (wipes first). Point it at your dev/demo database, not production data.
"""

from __future__ import annotations

from saarthi_mcp.config import load_settings
from saarthi_mcp.neo4j_repo import Neo4jRepository, seed_neo4j


def main() -> None:
    settings = load_settings()
    if settings.backend != "neo4j" or settings.neo4j is None:
        raise SystemExit("Set SAARTHI_BACKEND=neo4j and NEO4J_* in .env before seeding.")
    repo = Neo4jRepository.from_settings(settings.neo4j)
    try:
        seed_neo4j(repo, wipe=True)
        elder = repo.primary_elder()
        meds = repo.medications_for(elder.id)
        print(f"Seeded {elder.name}: {len(meds)} medications, dose history, appointments, events.")
    finally:
        repo.close()


if __name__ == "__main__":
    main()
