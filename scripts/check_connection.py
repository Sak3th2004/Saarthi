"""Check Neo4j connectivity without reading household records or changing the graph.

Run: python scripts/check_connection.py [--json]
Uses NEO4J_URI, NEO4J_USERNAME (default neo4j), NEO4J_PASSWORD, and optional
NEO4J_DATABASE from the environment, falling back to mcp-server/.env. An unset
database selects the authenticated user's home database. Nothing is rewritten.
This checks connectivity only; it does not establish application readiness,
authorization, backup status, or protection against future Aura inactivity.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path
import ssl
from typing import Mapping
from urllib.parse import urlsplit

from dotenv import dotenv_values
from neo4j import GraphDatabase, READ_ACCESS
from neo4j.exceptions import (
    AuthError, CertificateConfigurationError, ConfigurationError, Neo4jError,
    ServiceUnavailable, SessionExpired,
)


MESSAGES = {
    "ready": "Neo4j accepted a read-only connection check.",
    "configuration": "Check NEO4J_URI, NEO4J_USERNAME, NEO4J_PASSWORD and optional NEO4J_DATABASE locally.",
    "authentication": "Neo4j rejected authentication or permission to connect. Check credentials locally.",
    "tls": "TLS verification failed. Check the URI, certificate trust and system clock; keep verification enabled.",
    "network": "Neo4j could not be reached. Check its Running status, network access and connection URI.",
    "database": "Neo4j could not use the selected database. Check its name and availability.",
    "unexpected": "The connection check failed. No exception details or credentials were printed.",
}


def configuration(environ: Mapping[str, str], env_file: Path) -> dict[str, str | None]:
    # Interpolation is deliberately disabled: this probe never expands another
    # environment variable into credentials or silently modifies process settings.
    values = {**dotenv_values(env_file, interpolate=False), **environ}
    uri = (values.get("NEO4J_URI") or "").strip()
    username = values.get("NEO4J_USERNAME", "neo4j")
    password = values.get("NEO4J_PASSWORD")
    database = (values.get("NEO4J_DATABASE") or "").strip() or None
    parts = urlsplit(uri)
    if (
        parts.scheme not in {"neo4j", "neo4j+s", "bolt", "bolt+s"}
        or not parts.hostname
        or parts.username is not None
        or parts.password is not None
        or parts.path not in {"", "/"}
        or parts.query or parts.fragment
        or not username or not username.strip()
        or not password or not password.strip()
    ):
        raise ValueError("Invalid Neo4j configuration")
    # Validate a malformed/out-of-range port before handing it to the driver.
    if parts.port == 0:
        raise ValueError("Invalid Neo4j port")
    return {"uri": uri, "username": username, "password": password, "database": database}


def classify_error(error: Exception) -> str:
    chain: list[BaseException] = []
    current: BaseException | None = error
    while current is not None and all(current is not item for item in chain):
        chain.append(current)
        current = current.__cause__ or current.__context__
    if any(isinstance(item, (ssl.SSLError, CertificateConfigurationError)) for item in chain):
        return "tls"
    for item in chain:
        if isinstance(item, AuthError):
            return "authentication"
        if isinstance(item, Neo4jError):
            code = item.code or ""
            if code.startswith("Neo.ClientError.Security."):
                return "authentication"
            if ".Database." in code or ".DatabaseNotFound" in code:
                return "database"
        if isinstance(item, ConfigurationError):
            return "configuration"
    if any(isinstance(item, (ServiceUnavailable, SessionExpired, OSError)) for item in chain):
        return "network"
    return "unexpected"


def check_connection(settings: Mapping[str, str | None]) -> dict[str, str | bool]:
    # This standalone diagnostic emits only its fixed result messages. Driver
    # routing logs can include exception detail even when we catch the exception.
    previous_logging_disable = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        with GraphDatabase.driver(
            settings["uri"],
            auth=(settings["username"], settings["password"]),
            connection_timeout=5,
            connection_acquisition_timeout=5,
            max_transaction_retry_time=0,
        ) as driver:
            driver.verify_connectivity()
            session_options = {"default_access_mode": READ_ACCESS}
            if settings["database"] is not None:
                session_options["database"] = settings["database"]
            with driver.session(**session_options) as session:
                with session.begin_transaction(timeout=5) as transaction:
                    # Fixed literal query: no records, labels, schema, or user input.
                    record = transaction.run("RETURN 1 AS ready").single(strict=True)
                    if record is None or record["ready"] != 1:
                        raise RuntimeError("Unexpected connection-check result")
                    transaction.commit()
        status = "ready"
    except Exception as error:
        status = classify_error(error)
    finally:
        logging.disable(previous_logging_disable)
    return {"ok": status == "ready", "status": status, "message": MESSAGES[status]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--json", action="store_true", help="Print a sanitized JSON result")
    args = parser.parse_args(argv)
    try:
        settings = configuration(os.environ, Path(__file__).resolve().parents[1] / "mcp-server/.env")
    except Exception:
        result = {"ok": False, "status": "configuration", "message": MESSAGES["configuration"]}
    else:
        result = check_connection(settings)
    print(json.dumps(result) if args.json else result["message"])
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
