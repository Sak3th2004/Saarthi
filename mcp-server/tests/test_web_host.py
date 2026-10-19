from types import SimpleNamespace

import httpx
import pytest
from fastmcp import FastMCP

from saarthi_mcp.web_host import attach_web_host


async def test_hosted_assets_and_public_configuration_never_replace_api_routes(tmp_path):
    (tmp_path / 'assets').mkdir()
    (tmp_path / 'index.html').write_text('<html>Notebook</html>')
    (tmp_path / 'assets' / 'app.js').write_text('/* built app */')
    settings = SimpleNamespace(cognito=SimpleNamespace(
        resource_url='https://notebook.example/mcp', client_id='publicclient'),
        private_secret='must-not-leak')
    app = FastMCP('web-test')
    attach_web_host(app, settings, str(tmp_path), login_domain='https://test.auth.us-east-1.amazoncognito.com')
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app.http_app()), base_url='https://notebook.example') as client:
        assert (await client.get('/')).status_code == 200
        assert (await client.get('/auth/callback')).status_code == 200
        assert (await client.get('/assets/app.js')).status_code == 200
        assert (await client.get('/health')).json() == {'status': 'ok'}
        config = await client.get('/app-config.json')
        assert config.json()['VITE_COGNITO_REDIRECT_URI'] == 'https://notebook.example/auth/callback'
        assert len(config.json()) == 5 and 'must-not-leak' not in config.text
        assert config.headers['cache-control'] == 'no-store'
        assert (await client.get('/household/unknown')).status_code == 404
        assert (await client.get('/assets/%2e%2e%2findex.html')).status_code == 404


def test_hosted_web_requires_authentication_and_built_assets(tmp_path):
    with pytest.raises(ValueError, match='Build'): attach_web_host(FastMCP('test'), None, str(tmp_path), login_domain='')
    (tmp_path / 'index.html').write_text('built')
    with pytest.raises(ValueError, match='Cognito'):
        attach_web_host(FastMCP('test'), SimpleNamespace(cognito=None), str(tmp_path), login_domain='')
