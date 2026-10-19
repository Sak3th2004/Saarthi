"""Hosted OAuth boundaries and encrypted per-subject storage; no live credentials."""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from urllib.parse import urlsplit

import httpx
import pytest
from botocore.exceptions import ClientError
from fastmcp import FastMCP

from saarthi_mcp.calendar_tokens import TokenStoreError
from saarthi_mcp.cloud_calendar_tokens import SecretsManagerTokenStore
from saarthi_mcp.google_calendar import CalendarConfig, CalendarConnection, attach_authenticated_calendar_routes, configured_calendar


def aws_error(code):
    return ClientError({'Error': {'Code': code, 'Message': 'private-provider-detail'}}, 'test')


def test_cloud_tokens_survive_new_store_and_are_subject_scoped():
    saved = {}
    client = Mock()
    def put(**kw): saved[kw['SecretId']] = kw['SecretString']
    client.put_secret_value.side_effect = put
    client.get_secret_value.side_effect = lambda **kw: {'SecretString': saved[kw['SecretId']]}
    store = SecretsManagerTokenStore('saarthi/reviewer/calendar', 'us-east-1', client=client)
    store.save({'account': 'one@example.test'}, subject='person-one')
    store.save({'account': 'two@example.test'}, subject='person-two')
    restarted = SecretsManagerTokenStore('saarthi/reviewer/calendar', 'us-east-1', client=client)
    assert restarted.load(subject='person-one')['account'] == 'one@example.test'
    assert restarted.load(subject='person-two')['account'] == 'two@example.test'
    assert all('person-' not in key for key in saved)


def test_initial_secret_creation_and_idempotent_version():
    client = Mock()
    client.put_secret_value.side_effect = aws_error('ResourceNotFoundException')
    store = SecretsManagerTokenStore('saarthi/reviewer/calendar', 'us-east-1', client=client)
    store.save({'token': 'test-only'}, subject='caregiver')
    put = client.put_secret_value.call_args.kwargs
    created = client.create_secret.call_args.kwargs
    assert created['Name'] == put['SecretId']
    assert created['ClientRequestToken'] == put['ClientRequestToken']
    assert json.loads(created['SecretString']) == {'token': 'test-only'}


def test_cloud_store_missing_denied_and_invalid_payload_fail_closed():
    client = Mock()
    store = SecretsManagerTokenStore('saarthi/reviewer/calendar', 'us-east-1', client=client)
    client.get_secret_value.side_effect = aws_error('ResourceNotFoundException')
    assert store.load(subject='caregiver') is None
    client.get_secret_value.side_effect = aws_error('AccessDeniedException')
    with pytest.raises(TokenStoreError, match='read securely') as error: store.load(subject='caregiver')
    assert 'private-provider-detail' not in str(error.value)
    with pytest.raises(TokenStoreError): store.save({'token': 'x'}, subject=None)
    with pytest.raises(TokenStoreError): store.save({'token': 'x' * 65536}, subject='caregiver')
    client.put_secret_value.assert_not_called()


@pytest.mark.parametrize('url', ['http://public.example/oauth/google/callback',
    'https://user:password@public.example/oauth/google/callback', 'https://public.example/wrong'])
def test_hosted_callback_rejects_insecure_or_ambiguous_addresses(url):
    with pytest.raises(ValueError):
        CalendarConfig('test.apps.googleusercontent.com', 'test-secret', 'owner@example.test', url, hosted=True)


def test_hosted_configuration_requires_auth_and_same_origin(monkeypatch):
    monkeypatch.setenv('SAARTHI_GOOGLE_MODE', 'hosted')
    settings = SimpleNamespace(cognito=None)
    with pytest.raises(ValueError, match='Cognito'): configured_calendar(settings)
    settings.cognito = SimpleNamespace(resource_url='https://notebook.example/mcp')
    monkeypatch.setenv('GOOGLE_REDIRECT_URI', 'https://wrong.example/oauth/google/callback')
    with pytest.raises(ValueError, match='origin'): configured_calendar(settings, authenticated=True)


async def test_hosted_handoff_requires_auth_sets_secure_cookie_and_rejects_wrong_host():
    config = CalendarConfig('test.apps.googleusercontent.com', 'test-secret', 'owner@example.test',
                            'https://notebook.example/oauth/google/callback', hosted=True)
    connection = CalendarConnection(config, Mock())
    verifier = Mock(verify_token=AsyncMock(return_value=SimpleNamespace(claims={'sub': 'reviewer'})))
    server = FastMCP('hosted-calendar-test')
    attach_authenticated_calendar_routes(server, connection, verifier, 'https://notebook.example/mcp')
    transport = httpx.ASGITransport(app=server.http_app(), client=('203.0.113.10', 12345))
    async with httpx.AsyncClient(transport=transport, base_url='https://notebook.example') as client:
        assert (await client.post('/household/calendar/connect', json={})).status_code == 401
        headers = {'authorization': 'Bearer test', 'origin': 'https://notebook.example'}
        result = await client.post('/household/calendar/connect', json={}, headers=headers)
        assert result.status_code == 200
        handoff = result.json()['handoff_url']
        assert handoff.startswith('https://notebook.example/oauth/google/household-connect?h=')
        assert (await client.get(handoff, headers={'host': 'wrong.example'})).status_code == 403
        redirect = await client.get(handoff)
        assert redirect.status_code == 303
        assert 'Secure' in redirect.headers['set-cookie'] and 'HttpOnly' in redirect.headers['set-cookie']
        assert (await client.get(handoff)).status_code == 400
        callback = await client.get('/oauth/google/callback?state=wrong&code=private-code')
        assert callback.status_code == 400 and 'private-code' not in callback.text
        connection.store.save.assert_not_called()
