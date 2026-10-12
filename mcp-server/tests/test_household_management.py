"""Real signed-token HTTP requests for reviewed family metadata updates."""
from uuid import uuid4
from unittest.mock import AsyncMock

import httpx
import pytest
from fastmcp import FastMCP
from fastmcp.server.auth.providers.jwt import RSAKeyPair

from saarthi_mcp.auth import CognitoSettings, HouseholdTokenVerifier
from saarthi_mcp.household_management import HouseholdManagement, attach_household_management
from saarthi_mcp.repository import InMemoryRepository


@pytest.fixture
async def api(monkeypatch):
    subjects = [str(uuid4()), str(uuid4())]
    settings = CognitoSettings('us-east-1', 'us-east-1_test', 'client123',
                               'https://notebook.example/mcp', frozenset(subjects))
    key = RSAKeyPair.generate()
    verifier = HouseholdTokenVerifier(settings)
    monkeypatch.setattr(verifier, '_get_verification_key', AsyncMock(return_value=key.public_key))
    def token(subject=subjects[0], **claims):
        return key.create_token(subject=subject, issuer=settings.issuer, audience=settings.resource_url,
                                scopes=[settings.scope], additional_claims={'token_use': 'access', 'client_id': settings.client_id, **claims})
    repo = InMemoryRepository()
    service = HouseholdManagement(repo, persistent=False)
    server = FastMCP('directory-test', auth=verifier)
    attach_household_management(server, service, verifier, settings.resource_url)
    app = server.http_app()
    # Custom browser routes do not create MCP sessions. Keeping MCP's task-group
    # lifespan outside a yield fixture also avoids crossing pytest task scopes.
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='https://notebook.example',
                                 headers={'Origin': 'https://notebook.example', 'Authorization': 'Bearer ' + token()}) as client:
        yield client, repo, service, token, subjects


async def test_review_changes_nothing_confirm_saves_and_retry_does_not_duplicate(api):
    client, repo, _, _, _ = api
    directory = (await client.post('/household/manage/status', json={})).json()
    member_id = str(uuid4())
    change = {'kind': 'add_person', 'id': member_id, 'name': 'New parent ' + uuid4().hex, 'role': 'elder'}
    reviewed = await client.post('/household/manage/review', json={'expected_revision': directory['revision'], 'change': change})
    assert reviewed.status_code == 200
    review = reviewed.json()
    assert review['change'] == change
    assert repo.household_directory()['people'] == []
    payload = {'review_token': review['review_token'], 'confirmed': True}
    saved = await client.post('/household/manage/confirm', json=payload)
    assert saved.status_code == 200
    assert saved.json()['directory']['primary_person_id'] == member_id
    before = repo.recent_events(member_id)
    retry = await client.post('/household/manage/confirm', json=payload)
    assert retry.json() == saved.json()
    assert repo.recent_events(member_id) == before
    assert len(before) == 1


@pytest.mark.parametrize('endpoint', ['status', 'review', 'confirm'])
@pytest.mark.parametrize('identity', ['none', 'foreign', 'wrong-audience', 'wrong-scope', 'id-token', 'expired'])
async def test_all_endpoints_reject_unauthorized_identity(api, endpoint, identity):
    client, repo, _, token, _ = api
    values = {'none': '', 'foreign': 'Bearer ' + token(subject=str(uuid4())),
              'wrong-audience': 'Bearer ' + token(aud='https://other.example/mcp'),
              'wrong-scope': 'Bearer ' + token(scope='openid'),
              'id-token': 'Bearer ' + token(token_use='id'), 'expired': 'Bearer ' + token(exp=1)}
    response = await client.post('/household/manage/' + endpoint, json={}, headers={'Authorization': values[identity]})
    assert response.status_code == 401
    assert response.headers['cache-control'] == 'no-store'
    assert repo.household_directory()['people'] == []


async def test_review_cannot_be_confirmed_by_another_approved_caregiver(api):
    client, repo, _, token, subjects = api
    directory = (await client.post('/household/manage/status', json={})).json()
    review = (await client.post('/household/manage/review', json={'expected_revision': directory['revision'],
        'change': {'kind': 'add_person', 'id': str(uuid4()), 'name': 'Parent', 'role': 'elder'}})).json()
    response = await client.post('/household/manage/confirm', json={'review_token': review['review_token'], 'confirmed': True},
                                 headers={'Authorization': 'Bearer ' + token(subject=subjects[1])})
    assert response.status_code == 409
    assert repo.household_directory()['people'] == []


async def test_expired_and_stale_reviews_cannot_write(api):
    client, repo, service, _, _ = api
    directory = (await client.post('/household/manage/status', json={})).json()
    reviews = []
    for index in range(3):
        reviews.append((await client.post('/household/manage/review', json={'expected_revision': directory['revision'],
            'change': {'kind': 'add_person', 'id': str(uuid4()), 'name': f'Person {index}', 'role': 'elder'}})).json())
    service.reviews[reviews[0]['review_token']]['expires'] = 0
    for index, expected in [(0, 409), (1, 200), (2, 409)]:
        response = await client.post('/household/manage/confirm', json={'review_token': reviews[index]['review_token'], 'confirmed': True})
        assert response.status_code == expected
    assert len(repo.household_directory()['people']) == 1


async def test_origin_size_format_and_explicit_confirmation_boundaries(api):
    client, _, _, _, _ = api
    assert (await client.post('/household/manage/status', json={}, headers={'Origin': 'https://attacker.example'})).status_code == 403
    assert (await client.post('/household/manage/status', content='{}', headers={'Content-Type': 'text/plain'})).status_code == 415
    assert (await client.post('/household/manage/status', content='x' * 16385, headers={'Content-Type': 'application/json'})).status_code == 413
    for confirmed in [False, 1, 'true', None]:
        assert (await client.post('/household/manage/confirm', json={'review_token': 'unused', 'confirmed': confirmed})).status_code == 400


async def test_database_failure_is_sanitized(api, monkeypatch):
    client, repo, _, _, _ = api
    def fail():
        raise RuntimeError('private connection credentials')
    monkeypatch.setattr(repo, 'household_directory', fail)
    result = await client.post('/household/manage/status', json={})
    assert result.status_code == 503
    assert 'private' not in result.text


async def test_review_endpoint_cannot_modify_contacts_or_care_records(api):
    client, repo, _, _, _ = api
    directory = (await client.post('/household/manage/status', json={})).json()
    for added in [{'email': 'unknown@example.com'}, {'medications': []}, {'primary': True}]:
        response = await client.post('/household/manage/review', json={'expected_revision': directory['revision'],
            'change': {'kind': 'add_person', 'id': str(uuid4()), 'name': 'Parent', 'role': 'elder', **added}})
        assert response.status_code == 400
    assert repo.household_directory()['people'] == []


async def test_set_person_email_review_confirm_saves_family_email(api):
    client, repo, _, _, _ = api
    elder_id, family_id = str(uuid4()), str(uuid4())
    directory = (await client.post('/household/manage/status', json={})).json()
    for change in [
        {'kind': 'add_person', 'id': elder_id, 'name': 'Parent', 'role': 'elder'},
        {'kind': 'add_person', 'id': family_id, 'name': 'Saketh', 'role': 'family'},
    ]:
        reviewed = (await client.post('/household/manage/review', json={
            'expected_revision': directory['revision'], 'change': change})).json()
        directory = (await client.post('/household/manage/confirm', json={
            'review_token': reviewed['review_token'], 'confirmed': True})).json()['directory']
    email_change = {'kind': 'set_person_email', 'person_id': family_id, 'email': 'saketh@example.test'}
    reviewed = await client.post('/household/manage/review', json={
        'expected_revision': directory['revision'], 'change': email_change})
    assert reviewed.status_code == 200
    assert reviewed.json()['change'] == email_change
    assert repo.resolve_person(family_id).email is None
    saved = await client.post('/household/manage/confirm', json={
        'review_token': reviewed.json()['review_token'], 'confirmed': True})
    assert saved.status_code == 200
    people = {p['id']: p for p in saved.json()['directory']['people']}
    assert people[family_id]['email'] == 'saketh@example.test'
    assert repo.resolve_person(family_id).email == 'saketh@example.test'
    elder_email = await client.post('/household/manage/review', json={
        'expected_revision': saved.json()['directory']['revision'],
        'change': {'kind': 'set_person_email', 'person_id': elder_id, 'email': 'elder@example.test'}})
    assert elder_email.status_code == 400


async def test_add_medication_review_confirm_saves_fact_and_rejects_bad_payloads(api):
    from saarthi_mcp.directory import CAREGIVER_MEDICATION_NOTICE

    client, repo, _, _, _ = api
    elder_id = str(uuid4())
    directory = (await client.post('/household/manage/status', json={})).json()
    reviewed = (await client.post('/household/manage/review', json={
        'expected_revision': directory['revision'],
        'change': {'kind': 'add_person', 'id': elder_id, 'name': 'Parent', 'role': 'elder'},
    })).json()
    directory = (await client.post('/household/manage/confirm', json={
        'review_token': reviewed['review_token'], 'confirmed': True})).json()['directory']
    assert repo.medications_for(elder_id) == []

    med_change = {
        'kind': 'add_medication',
        'person_id': elder_id,
        'name': 'OwnerSuppliedMed',
        'dose': '5mg',
        'schedule': ['08:00', '20:00'],
    }
    preview = await client.post('/household/manage/review', json={
        'expected_revision': directory['revision'], 'change': med_change})
    assert preview.status_code == 200
    body = preview.json()
    assert body['change'] == med_change
    assert body['notice'] == CAREGIVER_MEDICATION_NOTICE
    assert 'medical advice' in body['notice'].lower()
    assert repo.medications_for(elder_id) == []

    saved = await client.post('/household/manage/confirm', json={
        'review_token': body['review_token'], 'confirmed': True})
    assert saved.status_code == 200
    result = saved.json()
    assert result['status'] == 'saved'
    assert result['notice'] == CAREGIVER_MEDICATION_NOTICE
    assert result['medication'] == {
        'person_id': elder_id, 'name': 'OwnerSuppliedMed', 'dose': '5mg', 'schedule': ['08:00', '20:00'],
    }
    assert result['directory']['revision'] == directory['revision']
    meds = repo.medications_for(elder_id)
    assert len(meds) == 1 and meds[0].name == 'OwnerSuppliedMed' and meds[0].schedule == ['08:00', '20:00']
    audit = repo.recent_events(elder_id)[0]
    assert audit.type == 'household_setup' and 'add_medication' in audit.detail

    retry = await client.post('/household/manage/confirm', json={
        'review_token': body['review_token'], 'confirmed': True})
    assert retry.json() == result
    assert len(repo.medications_for(elder_id)) == 1

    duplicate = await client.post('/household/manage/review', json={
        'expected_revision': result['directory']['revision'],
        'change': {**med_change, 'name': 'ownersuppliedmed', 'dose': '10mg'}})
    assert duplicate.status_code == 400
    assert len(repo.medications_for(elder_id)) == 1

    for bad in [
        {**med_change, 'supply_count': 3},
        {**med_change, 'schedule': ['25:99']},
        {**med_change, 'schedule': '08:00'},
        {'kind': 'add_medication', 'person_id': elder_id, 'name': 'X', 'dose': '1mg'},
        {'kind': 'add_medication', 'person_id': str(uuid4()), 'name': 'X', 'dose': '1mg', 'schedule': []},
    ]:
        response = await client.post('/household/manage/review', json={
            'expected_revision': result['directory']['revision'], 'change': bad})
        assert response.status_code == 400
    assert len(repo.medications_for(elder_id)) == 1


async def test_management_routes_absent_without_cognito(monkeypatch):
    from saarthi_mcp.server import build_server
    monkeypatch.setenv('SAARTHI_AUTH', 'local')
    monkeypatch.setenv('SAARTHI_HOST', '127.0.0.1')
    monkeypatch.setenv('SAARTHI_AGENTS', 'off')
    server = build_server(InMemoryRepository())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.http_app()), base_url='http://localhost') as client:
        assert (await client.post('/household/manage/status', json={})).status_code == 404
