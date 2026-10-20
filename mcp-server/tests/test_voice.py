import io
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
from unittest.mock import Mock

from botocore.credentials import Credentials
import pytest
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.testclient import TestClient

from saarthi_mcp.voice import VoiceLimit, VoiceService, attach_voice_routes


def service():
    session = Mock()
    session.get_credentials.return_value = Credentials("test-access", "test-secret", "session/token+=")
    session.client.return_value.synthesize_speech.side_effect = lambda **kw: {"AudioStream": io.BytesIO(b"audio")}
    return VoiceService(session=session, clock=lambda: 100), session


def test_scoped_short_lived_stream_signature_and_language_validation():
    voice, session = service()
    result = voice.start("actor", "en-IN")
    url = urlsplit(result["url"])
    query = parse_qs(url.query)
    assert url.scheme == "wss"
    assert url.hostname == "transcribestreaming.us-east-1.amazonaws.com"
    assert query["X-Amz-Expires"] == ["30"]
    assert query["X-Amz-Security-Token"] == ["session/token+="]
    assert query["sample-rate"] == ["16000"]
    assert "test-secret" not in result["url"]
    with pytest.raises(ValueError):
        voice.start("actor", "unsupported")


def test_voice_limits_and_actor_isolation():
    voice, _ = service()
    for _ in range(20):
        voice.start("actor", "en-IN")
    with pytest.raises(VoiceLimit):
        voice.start("actor", "en-IN")
    assert voice.start("other", "en-IN")["max_seconds"] == 60


def test_polly_plain_text_voice_and_private_cache():
    voice, session = service()
    assert voice.speak("one", "<speak>Hello</speak>") == b"audio"
    assert voice.speak("one", "<speak>Hello</speak>") == b"audio"
    assert session.client.return_value.synthesize_speech.call_count == 1
    voice.speak("two", "<speak>Hello</speak>")
    assert session.client.return_value.synthesize_speech.call_count == 2
    call = session.client.return_value.synthesize_speech.call_args.kwargs
    assert (call["Engine"], call["VoiceId"], call["TextType"]) == ("neural", "Kajal", "text")
    with pytest.raises(ValueError):
        voice.speak("one", "x" * 2001)


def routes(voice):
    collected = []
    class Server:
        def custom_route(self, path, methods):
            def decorate(handler):
                collected.append(Route(path, handler, methods=methods))
                return handler
            return decorate
    class Verifier:
        async def verify_token(self, token):
            return SimpleNamespace(claims={"sub": "approved-user"}) if token == "valid" else None
    attach_voice_routes(Server(), voice, Verifier(), "https://notebook.example/mcp")
    return TestClient(Starlette(routes=collected))


def test_routes_require_authorized_same_origin_and_do_not_leak_provider_errors():
    voice = Mock()
    client = routes(voice)
    headers = {"Authorization": "Bearer valid", "Origin": "https://notebook.example"}
    assert client.post("/household/voice/speak", json={"text": "hello"}).status_code == 401
    assert client.post("/household/voice/speak", headers={**headers, "Origin": "https://evil.example"}, json={"text": "hello"}).status_code == 403
    assert client.post("/household/voice/speak", headers=headers, json={"text": "hello", "voice": "override"}).status_code == 400
    voice.speak.assert_not_called()
    voice.speak.return_value = b"mp3-data"
    result = client.post("/household/voice/speak", headers=headers, json={"text": "hello"})
    assert result.status_code == 200 and result.content == b"mp3-data"
    assert result.headers["cache-control"] == "no-store"
    voice.speak.assert_called_once_with("approved-user", "hello")
    voice.start.side_effect = RuntimeError("private-credential-information")
    result = client.post("/household/voice/listen", headers=headers, json={"language": "en-IN"})
    assert result.status_code == 503 and "private-credential" not in result.text
    voice.start.side_effect = VoiceLimit()
    assert client.post("/household/voice/listen", headers=headers, json={"language": "en-IN"}).status_code == 429


def test_usage_window_expires_and_global_character_limit():
    voice, _ = service()
    now = [0]
    voice.clock = lambda: now[0]
    voice._claim("one", "speak", 100000)
    with pytest.raises(VoiceLimit):
        voice._claim("two", "speak", 1)
    now[0] = 86400
    voice._claim("two", "speak", 1)
