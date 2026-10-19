"""Serve the built dashboard and public sign-in settings from the API's own origin."""
from pathlib import Path
from urllib.parse import urlsplit

from starlette.responses import FileResponse, JSONResponse


def attach_web_host(server, settings, directory: str, *, login_domain: str):
    root = Path(directory).resolve()
    if not (root / 'index.html').is_file():
        raise ValueError('Build the dashboard before enabling hosted web routes.')
    if settings.cognito is None:
        raise ValueError('Hosted dashboard requires Cognito authentication.')
    resource = urlsplit(settings.cognito.resource_url)
    origin = f'{resource.scheme}://{resource.netloc}'
    import re
    if not re.fullmatch(r'https://[a-z0-9-]+\.auth\.[a-z0-9-]+\.amazoncognito\.com', login_domain):
        raise ValueError('Set COGNITO_LOGIN_DOMAIN to the managed sign-in HTTPS domain.')
    headers = {'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
               'Referrer-Policy': 'no-referrer', 'X-Frame-Options': 'DENY'}

    @server.custom_route('/health', methods=['GET'])
    async def health(request):
        # Liveness only. Cloud/database checks must not run for each load-balancer probe.
        return JSONResponse({'status': 'ok'}, headers=headers)

    @server.custom_route('/app-config.json', methods=['GET'])
    async def app_config(request):
        return JSONResponse({
            'VITE_AUTH_MODE': 'cognito', 'VITE_COGNITO_DOMAIN': login_domain,
            'VITE_COGNITO_CLIENT_ID': settings.cognito.client_id,
            'VITE_COGNITO_RESOURCE_URL': settings.cognito.resource_url,
            'VITE_COGNITO_REDIRECT_URI': origin + '/auth/callback',
        }, headers=headers)

    @server.custom_route('/', methods=['GET'])
    @server.custom_route('/auth/callback', methods=['GET'])
    async def index(request):
        return FileResponse(root / 'index.html', headers=headers)

    @server.custom_route('/assets/{asset:path}', methods=['GET'])
    async def asset(request):
        assets = root / 'assets'
        path = (assets / request.path_params['asset']).resolve()
        if not path.is_relative_to(assets) or not path.is_file():
            return JSONResponse({'error': 'Not found'}, status_code=404, headers=headers)
        return FileResponse(path, headers={**headers, 'Cache-Control': 'public, max-age=31536000, immutable'})
