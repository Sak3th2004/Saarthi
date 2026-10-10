"""Authenticated, reviewed changes to one deployment's household directory.

These browser endpoints deliberately are not agent tools. Every request verifies
the existing Cognito household membership; a review is also bound to its author.
"""
from __future__ import annotations

import copy
import json
import re
import secrets
import threading
import time
from urllib.parse import urlsplit

from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse

from saarthi_mcp.directory import DirectoryConflict, directory_revision, preview_change, validate_change


class ManagementError(ValueError):
    def __init__(self, status: int, message: str):
        self.status = status
        super().__init__(message)


class HouseholdManagement:
    def __init__(self, repository, *, persistent: bool):
        self.repository = repository
        self.persistence = 'persistent' if persistent else 'session'
        self.reviews = {}
        self.lock = threading.Lock()

    def _directory(self, directory):
        return {**directory, 'revision': directory_revision(directory), 'persistence': self.persistence}

    def status(self, payload, subject):
        if payload != {}:
            raise ManagementError(400, 'Invalid directory request.')
        return self._directory(self.repository.household_directory())

    def review(self, payload, subject):
        if (not isinstance(payload, dict) or set(payload) != {'expected_revision', 'change'}
                or not isinstance(payload['expected_revision'], str)
                or not re.fullmatch('[a-f0-9]{64}', payload['expected_revision'])):
            raise ManagementError(400, 'Invalid review request.')
        change = validate_change(payload['change'])
        with self.lock:
            directory = self.repository.household_directory()
            if directory_revision(directory) != payload['expected_revision']:
                raise ManagementError(409, 'Family details changed. Refresh and review again.')
            preview_change(directory, change)
            now = time.monotonic()
            self.reviews = {key: value for key, value in self.reviews.items() if value['expires'] > now}
            if len(self.reviews) >= 100 or sum(r['subject'] == subject for r in self.reviews.values()) >= 20:
                raise ManagementError(429, 'Too many pending reviews. Try again later.')
            token = secrets.token_urlsafe(32)
            self.reviews[token] = {'subject': subject, 'change': change, 'revision': payload['expected_revision'],
                                   'expires': now + 600, 'result': None}
            return {'review_token': token, 'change': copy.deepcopy(change),
                    'expires_in_seconds': 600, 'persistence': self.persistence}

    def confirm(self, payload, subject):
        if (not isinstance(payload, dict) or set(payload) != {'review_token', 'confirmed'}
                or payload['confirmed'] is not True or not isinstance(payload['review_token'], str)):
            raise ManagementError(400, 'Review and explicitly confirm the change.')
        with self.lock:
            review = self.reviews.get(payload['review_token'])
            if review is None or review['subject'] != subject or review['expires'] <= time.monotonic():
                raise ManagementError(409, 'Review unavailable. Refresh family details and review again.')
            if review['result'] is not None:
                return copy.deepcopy(review['result'])
            directory = self.repository.apply_household_change(review['change'], review['revision'], actor=subject)
            review['result'] = {'status': 'saved', 'directory': self._directory(directory)}
            return copy.deepcopy(review['result'])


def attach_household_management(server, service, verifier, resource_url):
    resource = urlsplit(resource_url)
    allowed_origin = f'{resource.scheme}://{resource.netloc}'

    async def handle(request, action):
        headers = {'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer'}
        def response(body, status):
            return JSONResponse(body, status_code=status, headers=headers)
        authorization = request.headers.getlist('authorization')
        if len(authorization) != 1 or not authorization[0].startswith('Bearer ') or len(authorization[0]) > 16384:
            return response({'error': 'Sign-in required.'}, 401)
        try:
            identity = await verifier.verify_token(authorization[0][7:])
        except Exception:
            identity = None
        if identity is None:
            return response({'error': 'Sign-in required.'}, 401)
        # Explicit Bearer tokens are never read from cookies; reject other origins.
        if (request.headers.get('origin') != allowed_origin
                or request.headers.get('sec-fetch-site') == 'cross-site'):
            return response({'error': 'Open the household dashboard to continue.'}, 403)
        if request.headers.get('content-type', '').split(';')[0].strip() != 'application/json':
            return response({'error': 'Use a JSON request.'}, 415)
        data = bytearray()
        async for chunk in request.stream():
            if len(data) + len(chunk) > 16384:
                return response({'error': 'Request too large.'}, 413)
            data.extend(chunk)
        try:
            payload = json.loads(data)
            result = await run_in_threadpool(action, payload, identity.claims['sub'])
            return response(result, 200)
        except ManagementError as error:
            return response({'error': str(error)}, error.status)
        except DirectoryConflict:
            return response({'error': 'Family details changed. Refresh and review again.'}, 409)
        except (ValueError, TypeError, KeyError):
            return response({'error': 'Check the family details and review again.'}, 400)
        except Exception:
            return response({'error': 'Result not confirmed. Refresh family details before trying again.'}, 503)

    @server.custom_route('/household/manage/status', methods=['POST'])
    async def status(request):
        return await handle(request, service.status)

    @server.custom_route('/household/manage/review', methods=['POST'])
    async def review(request):
        return await handle(request, service.review)

    @server.custom_route('/household/manage/confirm', methods=['POST'])
    async def confirm(request):
        return await handle(request, service.confirm)
