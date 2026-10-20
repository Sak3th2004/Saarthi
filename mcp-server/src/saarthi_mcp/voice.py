"""Authenticated, bounded AWS voice access. Audio is never stored by Saarthi."""
from collections import deque
from hashlib import sha256
import json
from threading import Lock
from time import monotonic
from urllib.parse import urlencode, urlsplit

import boto3
from botocore.auth import SigV4QueryAuth
from botocore.awsrequest import AWSRequest
from botocore.config import Config


class VoiceLimit(ValueError):
    pass


class VoiceService:
    def __init__(self, region="us-east-1", *, session=None, clock=monotonic):
        self.region = region
        self.session = session or boto3.Session(region_name=region)
        self.clock = clock
        self.lock = Lock()
        self.usage = deque()
        self.cache = {}

    def _claim(self, actor, kind, amount=1):
        now = self.clock()
        with self.lock:
            while self.usage and now - self.usage[0][0] >= 86400:
                self.usage.popleft()
            matching = [u for u in self.usage if u[2] == kind]
            hourly = [u for u in matching if u[1] == actor and now - u[0] < 3600]
            if kind == "listen":
                exceeded = len(matching) >= 100 or len(hourly) >= 20
            else:
                exceeded = sum(u[3] for u in matching) + amount > 100000 or len(hourly) >= 100
            if exceeded:
                raise VoiceLimit("Voice usage limit reached. Please use typed questions for now.")
            self.usage.append((now, actor, kind, amount))

    def start(self, actor, language):
        if language not in {"en-IN", "en-US", "en-GB"}:
            raise ValueError("Unsupported recognition language.")
        self._claim(actor, "listen")
        credentials = self.session.get_credentials().get_frozen_credentials()
        endpoint = f"https://transcribestreaming.{self.region}.amazonaws.com:8443/stream-transcription-websocket"
        query = urlencode({
            "language-code": language, "media-encoding": "pcm", "sample-rate": "16000",
            "enable-partial-results-stabilization": "true", "partial-results-stability": "medium",
        })
        request = AWSRequest(method="GET", url=endpoint + "?" + query)
        SigV4QueryAuth(credentials, "transcribe", self.region, expires=30).add_auth(request)
        # Scoped capability URL only; never expose the secret signing key.
        return {"url": request.url.replace("https://", "wss://", 1),
                "expires_in": 30, "max_seconds": 60, "sample_rate": 16000}

    def speak(self, actor, text):
        if not isinstance(text, str) or not text.strip() or len(text) > 2000:
            raise ValueError("Speech text must contain 1 to 2000 characters.")
        key = (actor, sha256(text.encode()).hexdigest())
        with self.lock:
            self.cache = {k: v for k, v in self.cache.items() if self.clock() - v[0] < 300}
            cached = self.cache.get(key)
        if cached:
            return cached[1]
        self._claim(actor, "speak", len(text))
        polly = self.session.client("polly", config=Config(
            connect_timeout=5, read_timeout=20, retries={"total_max_attempts": 1},
        ))
        result = polly.synthesize_speech(
            Engine="neural", VoiceId="Kajal", LanguageCode="en-IN",
            OutputFormat="mp3", TextType="text", Text=text, SampleRate="24000",
        )
        stream = result["AudioStream"]
        try:
            audio = stream.read(1024 * 1024 + 1)
        finally:
            stream.close()
        if not audio or len(audio) > 1024 * 1024:
            raise ValueError("Unexpected speech response.")
        with self.lock:
            if len(self.cache) >= 100:
                self.cache.pop(next(iter(self.cache)))
            self.cache[key] = (self.clock(), audio)
        return audio


def attach_voice_routes(server, service, verifier, resource_url):
    from starlette.concurrency import run_in_threadpool
    from starlette.responses import JSONResponse, Response
    resource = urlsplit(resource_url)
    origin = f"{resource.scheme}://{resource.netloc}"
    headers = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer", "X-Content-Type-Options": "nosniff"}

    async def handle(request, kind):
        auth = request.headers.getlist("authorization")
        if len(auth) != 1 or not auth[0].startswith("Bearer ") or len(auth[0]) > 16384:
            return JSONResponse({"error": "Sign-in required."}, status_code=401, headers=headers)
        try:
            identity = await verifier.verify_token(auth[0][7:])
        except Exception:
            identity = None
        if identity is None:
            return JSONResponse({"error": "Sign-in required."}, status_code=401, headers=headers)
        if request.headers.get("origin") != origin or request.headers.get("sec-fetch-site") == "cross-site":
            return JSONResponse({"error": "Open the household dashboard."}, status_code=403, headers=headers)
        if request.headers.get("content-type", "").split(";")[0].strip() != "application/json":
            return JSONResponse({"error": "JSON required."}, status_code=415, headers=headers)
        data = bytearray()
        async for chunk in request.stream():
            data.extend(chunk)
            if len(data) > 10000:
                return JSONResponse({"error": "Request too large."}, status_code=413, headers=headers)
        try:
            payload = json.loads(data)
            field = "language" if kind == "listen" else "text"
            if not isinstance(payload, dict) or set(payload) != {field}:
                raise ValueError()
            actor = identity.claims["sub"]
            result = await run_in_threadpool(
                service.start if kind == "listen" else service.speak, actor, payload[field],
            )
            return JSONResponse(result, headers=headers) if kind == "listen" else Response(
                result, media_type="audio/mpeg", headers=headers,
            )
        except VoiceLimit:
            return JSONResponse({"error": "Voice usage limit reached. Use typed questions for now."}, status_code=429, headers=headers)
        except (ValueError, TypeError, KeyError):
            return JSONResponse({"error": "Invalid voice request."}, status_code=400, headers=headers)
        except Exception:
            return JSONResponse({"error": "AWS voice is unavailable. Try again shortly or use typed questions."}, status_code=503, headers=headers)

    @server.custom_route("/household/voice/listen", methods=["POST"])
    async def listen(request):
        return await handle(request, "listen")

    @server.custom_route("/household/voice/speak", methods=["POST"])
    async def speak(request):
        return await handle(request, "speak")
