"""Check the release image over real HTTP using an empty, isolated in-memory notebook.

No real accounts, credentials, cloud calls or care records are used. The container
binds only to localhost and is stopped in finally. Pass a locally built image tag.
"""
import argparse
import json
import socket
import subprocess
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', default='saarthi-release:local')
    args = parser.parse_args()
    name = 'saarthi-release-check-' + uuid4().hex[:10]
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    environment = {
        'SAARTHI_BACKEND': 'memory', 'SAARTHI_AGENTS': 'off', 'SAARTHI_AUTH': 'cognito',
        'AWS_EC2_METADATA_DISABLED': 'true', 'AWS_REGION': 'us-east-1',
        'COGNITO_USER_POOL_ID': 'us-east-1_test', 'COGNITO_CLIENT_ID': 'testclient',
        'COGNITO_RESOURCE_URL': 'https://notebook.example/mcp',
        'COGNITO_LOGIN_DOMAIN': 'https://test.auth.us-east-1.amazoncognito.com',
        'COGNITO_ALLOWED_SUBJECTS': str(uuid4()), 'SAARTHI_GOOGLE_MODE': 'local',
        'SAARTHI_GOOGLE_CONFIG': '', 'SAARTHI_LOCAL_SETUP': '0',
    }
    command = ['docker', 'run', '--rm', '--detach', '--name', name, '-p', f'127.0.0.1:{port}:8080']
    for key, value in environment.items():
        command.extend(['-e', f'{key}={value}'])
    origin = f'http://127.0.0.1:{port}'

    def request(path, *, data=None, headers=None):
        req = Request(origin + path, data=data, headers=headers or {})
        try:
            with urlopen(req, timeout=5) as response:
                return response.status, response.read()
        except HTTPError as error:
            return error.code, error.read()

    try:
        subprocess.run([*command, args.image], check=True, capture_output=True, timeout=30)
        deadline = time.monotonic() + 60
        while True:
            try:
                status, body = request('/health')
                if status == 200: break
            except (URLError, TimeoutError, ConnectionError):
                pass
            if time.monotonic() >= deadline:
                logs = subprocess.run(['docker', 'logs', name], capture_output=True, text=True, timeout=10)
                raise RuntimeError('Isolated release image did not start:\n' + logs.stderr[-4000:])
            time.sleep(0.5)
        assert json.loads(body) == {'status': 'ok'}
        assert request('/')[0] == 200
        assert request('/auth/callback')[0] == 200
        status, config = request('/app-config.json')
        assert status == 200 and json.loads(config)['VITE_AUTH_MODE'] == 'cognito'
        assert request('/household/unknown')[0] == 404
        status, _ = request('/mcp', data=json.dumps({
            'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {
                'protocolVersion': '2025-11-25', 'capabilities': {},
                'clientInfo': {'name': 'release-check', 'version': '1'},
            },
        }).encode(), headers={'Content-Type': 'application/json', 'Accept': 'application/json, text/event-stream'})
        assert status == 401, f'Anonymous MCP must be rejected, got {status}'
        probe = ('import os; from pathlib import Path; import saarthi_agents, google_auth_oauthlib; '
                 'assert os.getuid() != 0; '
                 'assert not Path("/app/mcp-server/.env").exists(); '
                 'assert not Path("/app/dashboard/.env.local").exists(); '
                 'assert not Path("/app/.local-artifacts").exists()')
        subprocess.run(['docker', 'exec', name, 'python', '-c', probe], check=True, timeout=15)
        print(json.dumps({'health': 'passed', 'dashboard': 'passed', 'anonymous_mcp': 'rejected',
                          'runtime_dependencies': 'passed', 'non_root': True, 'real_data_changed': False}))
    finally:
        subprocess.run(['docker', 'stop', '--time', '5', name], capture_output=True, timeout=15)


if __name__ == '__main__':
    main()
