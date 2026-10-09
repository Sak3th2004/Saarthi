"""Offline checks for the deliberately read-only Neo4j diagnostic."""

import importlib.util
import json
import logging
from pathlib import Path
import ssl
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import MagicMock

from neo4j import READ_ACCESS
from neo4j.exceptions import AuthError, CertificateConfigurationError, Neo4jError, ServiceUnavailable
import pytest


spec = importlib.util.spec_from_file_location(
    "check_connection", Path(__file__).resolve().parents[2] / "scripts/check_connection.py"
)
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


@pytest.fixture
def settings():
    return {"uri": "neo4j+s://example.invalid", "username": "neo4j", "password": "private-value", "database": None}


@pytest.fixture
def connection(monkeypatch):
    factory = MagicMock()
    monkeypatch.setattr(probe.GraphDatabase, "driver", factory)
    driver = factory.return_value.__enter__.return_value
    session = driver.session.return_value.__enter__.return_value
    transaction = session.begin_transaction.return_value.__enter__.return_value
    transaction.run.return_value.single.return_value = {"ready": 1}
    return factory, driver, session, transaction


@pytest.mark.parametrize("database", [None, "selected-db"])
def test_only_constant_read_query_and_all_resources_closed(settings, connection, database):
    settings["database"] = database
    factory, driver, session, transaction = connection
    assert probe.check_connection(settings)["ok"] is True
    factory.assert_called_once_with(
        settings["uri"], auth=("neo4j", "private-value"), connection_timeout=5,
        connection_acquisition_timeout=5, max_transaction_retry_time=0,
    )
    driver.verify_connectivity.assert_called_once_with()
    expected = {"default_access_mode": READ_ACCESS}
    if database:
        expected["database"] = database
    driver.session.assert_called_once_with(**expected)
    session.begin_transaction.assert_called_once_with(timeout=5)
    transaction.run.assert_called_once_with("RETURN 1 AS ready")
    transaction.run.return_value.single.assert_called_once_with(strict=True)
    transaction.commit.assert_called_once_with()
    session.begin_transaction.return_value.__exit__.assert_called_once()
    driver.session.return_value.__exit__.assert_called_once()
    factory.return_value.__exit__.assert_called_once()


@pytest.mark.parametrize("uri", ["", "https://example.invalid", "neo4j+s://user:secret@example.invalid", "neo4j+s://example.invalid/path", "bolt://localhost:bad", "bolt://localhost:0", "neo4j+ssc://example.invalid", "neo4j+s://example.invalid?secret=value"])
def test_bad_configuration_rejected_before_connect(tmp_path, uri):
    with pytest.raises(ValueError):
        probe.configuration({"NEO4J_URI": uri, "NEO4J_PASSWORD": "private"}, tmp_path / "absent")


def test_file_configuration_does_not_mutate_environment_or_file(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    original = "NEO4J_URI=bolt://localhost:7687\nNEO4J_PASSWORD=file-private\nNEO4J_DATABASE=file-db\n"
    env_file.write_text(original)
    environ = {"NEO4J_PASSWORD": "env-private", "NEO4J_DATABASE": ""}
    result = probe.configuration(environ, env_file)
    assert result == {"uri": "bolt://localhost:7687", "username": "neo4j", "password": "env-private", "database": None}
    assert environ == {"NEO4J_PASSWORD": "env-private", "NEO4J_DATABASE": ""}
    assert env_file.read_text() == original


@pytest.mark.parametrize("overrides", [{"NEO4J_PASSWORD": ""}, {"NEO4J_PASSWORD": "   "}, {"NEO4J_USERNAME": ""}])
def test_missing_credentials_rejected(tmp_path, overrides):
    with pytest.raises(ValueError):
        probe.configuration({"NEO4J_URI": "bolt://localhost", "NEO4J_PASSWORD": "private", **overrides}, tmp_path / "absent")


@pytest.mark.parametrize("error, status", [
    (AuthError("private-value"), "authentication"),
    (ServiceUnavailable("private-value"), "network"),
    (ssl.SSLError("private-value"), "tls"),
    (CertificateConfigurationError("private-value"), "tls"),
    (RuntimeError("private-value"), "unexpected"),
])
def test_error_output_redacted_and_driver_closed(settings, connection, error, status):
    factory, driver, _, _ = connection
    driver.verify_connectivity.side_effect = error
    result = probe.check_connection(settings)
    assert result["status"] == status
    assert result["ok"] is False
    assert "private-value" not in json.dumps(result)
    driver.session.assert_not_called()
    factory.return_value.__exit__.assert_called_once()


def test_query_failure_closes_every_resource(settings, connection):
    factory, driver, session, transaction = connection
    transaction.run.side_effect = RuntimeError("secret-query-error")
    result = probe.check_connection(settings)
    assert result["status"] == "unexpected"
    assert "secret-query-error" not in json.dumps(result)
    transaction.commit.assert_not_called()
    session.begin_transaction.return_value.__exit__.assert_called_once()
    driver.session.return_value.__exit__.assert_called_once()
    factory.return_value.__exit__.assert_called_once()


def test_driver_logs_are_suppressed_and_logging_state_restored(settings, connection, caplog):
    _, driver, _, _ = connection
    previous = logging.root.manager.disable
    def fail():
        logging.getLogger("neo4j.io").error("private-value")
        raise ServiceUnavailable("private-value")
    driver.verify_connectivity.side_effect = fail
    assert probe.check_connection(settings)["ok"] is False
    assert "private-value" not in caplog.text
    assert logging.root.manager.disable == previous


def test_wrapped_tls_error_classified_without_message_inspection():
    error = ServiceUnavailable("private-value")
    error.__cause__ = ssl.SSLCertVerificationError("private-value")
    assert probe.classify_error(error) == "tls"


def test_database_error_classification():
    error = Neo4jError._hydrate_neo4j(code="Neo.ClientError.Database.DatabaseNotFound", message="private-value")
    assert probe.classify_error(error) == "database"


@pytest.mark.parametrize("ok", [False, True])
def test_cli_exit_code_and_sanitized_json(monkeypatch, capsys, settings, ok):
    monkeypatch.setattr(probe, "configuration", lambda *_: settings)
    status = "ready" if ok else "network"
    monkeypatch.setattr(probe, "check_connection", lambda _: {"ok": ok, "status": status, "message": probe.MESSAGES[status]})
    assert probe.main(["--json"]) == (0 if ok else 1)
    result = json.loads(capsys.readouterr().out)
    assert result["ok"] == ok
    assert "private-value" not in json.dumps(result)


def test_cli_configuration_errors_are_redacted(monkeypatch, capsys):
    def invalid(*_):
        raise ValueError("private-value")
    monkeypatch.setattr(probe, "configuration", invalid)
    assert probe.main(["--json"]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "configuration"
    assert "private-value" not in json.dumps(result)


def test_wrapped_dns_failure_is_distinct_and_redacted(settings, connection):
    _, driver, _, _ = connection
    error = ServiceUnavailable("private-value")
    error.__cause__ = socket.gaierror("private-value")
    driver.verify_connectivity.side_effect = error
    result = probe.check_connection(settings)
    assert result["status"] == "dns" and result["ok"] is False
    assert "private-value" not in json.dumps(result)


@pytest.mark.parametrize("url", [
    "http://example.com/mcp", "https://user:private@example.com/mcp",
    "https://example.com/mcp?token=private", "https://example.com/mcp#private",
    "https://example.com/", "file:///mcp", "https://example.com:bad/mcp",
    "https://example.com:0/mcp", "http://localhost/mcp\n",
])
def test_mcp_rejects_unsafe_url_before_network(monkeypatch, url):
    connect = MagicMock()
    monkeypatch.setattr(probe.http.client, "HTTPSConnection", connect)
    monkeypatch.setattr(probe.http.client, "HTTPConnection", connect)
    result = probe.check_mcp_boundary(url)
    assert result["status"] == "configuration" and not result["ok"]
    assert "private" not in json.dumps(result)
    connect.assert_not_called()


@pytest.mark.parametrize("code,challenge,expected", [
    (401, 'Bearer realm="mcp"', True), (401, "Basic", False),
    (401, "", False), (200, "", False), (500, "", False), (302, "", False),
])
def test_mcp_boundary_over_real_http(code, challenge, expected):
    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            received.append((self.path, dict(self.headers), json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
            self.send_response(code)
            if challenge:
                self.send_header("WWW-Authenticate", challenge)
            if code == 302:
                self.send_header("Location", "/private-redirect")
            self.end_headers()

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = probe.check_mcp_boundary(f"http://127.0.0.1:{server.server_port}/mcp")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    assert result["ok"] is expected and result["http_status"] == code
    assert len(received) == 1  # Redirects are never followed.
    path, headers, request = received[0]
    assert path == "/mcp" and "Authorization" not in headers and "Cookie" not in headers
    assert request["method"] == "initialize" and request["params"]["capabilities"] == {}


@pytest.mark.parametrize("database_ok,mcp_ok", [(True, False), (False, True), (False, False), (True, True)])
def test_combined_readiness_requires_both_checks(monkeypatch, capsys, settings, database_ok, mcp_ok):
    monkeypatch.setattr(probe, "configuration", lambda *_: settings)
    monkeypatch.setattr(probe, "check_connection", lambda _: {"ok": database_ok, "message": "Database check."})
    monkeypatch.setattr(probe, "check_mcp_boundary", lambda _: {"ok": mcp_ok, "message": "MCP check."})
    assert probe.main(["--json", "--mcp-url", "http://localhost:5173/mcp"]) == (0 if database_ok and mcp_ok else 1)
    result = json.loads(capsys.readouterr().out)
    assert result["database"]["ok"] is database_ok
    assert result["mcp"]["ok"] is mcp_ok
