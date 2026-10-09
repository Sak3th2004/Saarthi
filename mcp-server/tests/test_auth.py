"""Signed tokens and actual MCP HTTP authorization; no Cognito account required."""

from dataclasses import replace
import time
from uuid import uuid4
from unittest.mock import AsyncMock

import httpx
import pytest
from fastmcp import FastMCP
from fastmcp.server.auth.providers.jwt import RSAKeyPair

from saarthi_mcp.auth import CognitoSettings, HouseholdTokenVerifier
from saarthi_mcp.config import load_settings


@pytest.fixture
def identity(monkeypatch):
    subject = str(uuid4())
    settings = CognitoSettings("us-east-1", "us-east-1_testpool", "client123", "https://notebook.example/mcp", frozenset({subject}))
    key = RSAKeyPair.generate()
    verifier = HouseholdTokenVerifier(settings)
    monkeypatch.setattr(verifier, "_get_verification_key", AsyncMock(return_value=key.public_key))
    def token(**claims):
        return key.create_token(subject=subject, issuer=settings.issuer, audience=settings.resource_url,
                                scopes=[settings.scope], additional_claims={"token_use": "access", "client_id": settings.client_id, **claims})
    return settings, verifier, token


async def test_correct_signed_household_access_token_is_accepted(identity):
    settings, verifier, token = identity
    result = await verifier.verify_token(token())
    assert result.subject in settings.allowed_subjects
    assert result.client_id == settings.client_id


@pytest.mark.parametrize("claims", [
    {"token_use": "id"}, {"token_use": None}, {"client_id": "anotherapp"},
    {"sub": str(uuid4())}, {"sub": ""}, {"sub": None},
    {"exp": None}, {"exp": "2099999999"}, {"exp": True}, {"exp": 1},
    {"iat": None}, {"iat": True}, {"iat": 9999999999}, {"nbf": 9999999999},
    {"scope": "openid"}, {"iss": "https://other.example"}, {"aud": "https://other.example/mcp"},
])
async def test_invalid_token_claims_are_denied(identity, claims):
    _, verifier, token = identity
    assert await verifier.verify_token(token(**claims)) is None


async def test_bad_signature_malformed_token_and_key_failure_fail_closed(identity, monkeypatch):
    settings, verifier, _ = identity
    stranger = RSAKeyPair.generate().create_token(issuer=settings.issuer, audience=settings.resource_url)
    assert await verifier.verify_token(stranger) is None
    assert await verifier.verify_token("not.a.jwt") is None
    monkeypatch.setattr(verifier, "_get_verification_key", AsyncMock(side_effect=RuntimeError("private network error")))
    assert await verifier.verify_token(stranger) is None


async def test_http_rejects_anonymous_calls_even_with_an_existing_session(identity):
    _, verifier, token = identity
    server = FastMCP("protected", auth=verifier)
    calls = []
    @server.tool
    def saved_fact():
        calls.append(True)
        return {"fact": "private household record"}
    app = server.http_app()
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://notebook.example") as client:
            headers = {"accept": "application/json, text/event-stream", "content-type": "application/json"}
            init = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                "protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "auth-check", "version": "1"}}}
            assert (await client.post("/mcp", json=init, headers=headers)).status_code == 401
            authorized = {**headers, "authorization": "Bearer " + token()}
            first = await client.post("/mcp", json=init, headers=authorized)
            assert first.status_code == 200
            session = first.headers.get("mcp-session-id")
            if session:
                headers["mcp-session-id"] = session
                authorized["mcp-session-id"] = session
            await client.post("/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"}, headers=authorized)
            for method, params in [("tools/list", {}), ("tools/call", {"name": "saved_fact", "arguments": {}})]:
                payload = {"jsonrpc": "2.0", "id": 2, "method": method, "params": params}
                assert (await client.post("/mcp", json=payload, headers=headers)).status_code == 401
                bad = {**headers, "authorization": "Bearer " + token(sub=str(uuid4()))}
                assert (await client.post("/mcp", json=payload, headers=bad)).status_code == 401
            assert calls == []
            success = await client.post("/mcp", json={"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "saved_fact", "arguments": {}}}, headers=authorized)
            assert success.status_code == 200 and "private household record" in success.text
            assert calls == [True]


@pytest.mark.parametrize("change", [
    {"pool_id": "us-west-2_wrong"}, {"client_id": ""}, {"resource_url": "http://public.example/mcp"},
    {"resource_url": "https://user:password@example.com/mcp"}, {"resource_url": "https://example.com/mcp?secret=x"},
    {"allowed_subjects": frozenset()}, {"allowed_subjects": frozenset({"not-a-subject-id"})},
])
def test_incomplete_or_unsafe_settings_rejected(identity, change):
    with pytest.raises(ValueError):
        replace(identity[0], **change)


def test_public_binding_without_auth_is_rejected_before_database_access(monkeypatch):
    monkeypatch.setenv("SAARTHI_AUTH", "local")
    monkeypatch.setenv("SAARTHI_HOST", "0.0.0.0")
    monkeypatch.setenv("SAARTHI_BACKEND", "neo4j")
    with pytest.raises(ValueError, match="Public binding requires"):
        load_settings()


@pytest.mark.parametrize("local_feature", ["SAARTHI_LOCAL_SETUP", "SAARTHI_GOOGLE_CONFIG"])
def test_cognito_cannot_expose_local_admin_routes(monkeypatch, identity, local_feature):
    settings = identity[0]
    for key, value in {"SAARTHI_AUTH": "cognito", "AWS_REGION": settings.region,
                       "COGNITO_USER_POOL_ID": settings.pool_id, "COGNITO_CLIENT_ID": settings.client_id,
                       "COGNITO_RESOURCE_URL": settings.resource_url, "COGNITO_ALLOWED_SUBJECTS": next(iter(settings.allowed_subjects)),
                       "SAARTHI_LOCAL_SETUP": "0", "SAARTHI_GOOGLE_CONFIG": ""}.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv(local_feature, "1")
    with pytest.raises(ValueError, match="local administrator or local Calendar"):
        load_settings()
