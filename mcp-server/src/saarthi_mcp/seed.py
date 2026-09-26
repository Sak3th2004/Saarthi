"""Explicit sample setup for the disposable local test graph only.

    python -m saarthi_mcp.seed --reset-test-database

Real household setup uses ``python -m saarthi_mcp.household FILE --apply``.
"""

from __future__ import annotations

import argparse
from urllib.parse import urlparse

from saarthi_mcp.config import load_settings
from saarthi_mcp.neo4j_repo import Neo4jRepository, seed_neo4j


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reset-test-database", action="store_true",
                        help="Erase and seed only the isolated local test graph on port 17687")
    args = parser.parse_args(argv)
    if not args.reset_test_database:
        parser.error("Sample setup requires --reset-test-database; it is never used for real households.")
    settings = load_settings()
    if settings.backend != "neo4j" or settings.neo4j is None:
        raise SystemExit("Set SAARTHI_BACKEND=neo4j and the isolated test database settings.")
    target = urlparse(settings.neo4j.uri)
    if (target.scheme != "bolt" or target.hostname not in {"localhost", "127.0.0.1"}
            or target.port != 17687 or settings.neo4j.database != "neo4j"
            or target.username or target.password or target.path not in {"", "/"}
            or target.query or target.fragment):
        raise SystemExit("Refusing sample reset: only bolt://127.0.0.1:17687 (database neo4j) is allowed.")
    repo = Neo4jRepository.from_settings(settings.neo4j)
    try:
        seed_neo4j(repo, wipe=True)
        print("Reset the isolated test graph with sample data. Not a real household import.")
    finally:
        repo.close()


if __name__ == "__main__":
    main()
