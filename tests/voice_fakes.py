"""In-process fakes for Voicebox and the voice engine sidecar (httpx.MockTransport, like the telephony tests)."""

from __future__ import annotations

import io
import json
import re
import wave

import httpx

from service015.voice.engine import VoiceEngineClient
from service015.voice.voicebox import VoiceboxClient


def make_wav(seconds: float = 0.2, sr: int = 24000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(b"\x00\x00" * int(sr * seconds))
    return buf.getvalue()


class FakeVoicebox:
    """Voicebox stand-in. Mirrors the real quirks: unknown profile -> 404 {"detail"}; samples of unknown -> 200 []."""

    def __init__(self) -> None:
        self.down = False
        self.fail_sample_upload = False
        self.profiles: dict[str, dict] = {
            "vp-ava": {"id": "vp-ava", "name": "Ava Warm", "language": "en", "voice_type": "cloned"},
            "vp-preset": {"id": "vp-preset", "name": "Kokoro Preset", "language": "en", "voice_type": "preset"},
        }
        self.samples: dict[str, list[dict]] = {}
        self.requests: list[tuple[str, str]] = []
        self._next = 1
        self.client = VoiceboxClient("http://voicebox.test", 5.0, httpx.MockTransport(self._handle))

    def _handle(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("refused", request=request)
        path, method = request.url.path, request.method
        self.requests.append((method, path))
        if path == "/health":
            return httpx.Response(200, json={"status": "healthy"})
        if path == "/profiles" and method == "GET":
            return httpx.Response(200, json=list(self.profiles.values()))
        if path == "/profiles" and method == "POST":
            body = json.loads(request.content)
            pid = f"vp-new-{self._next}"
            self._next += 1
            self.profiles[pid] = {"id": pid, "name": body["name"], "language": body["language"], "voice_type": body["voice_type"]}
            return httpx.Response(200, json=self.profiles[pid])
        m = re.fullmatch(r"/profiles/([^/]+)", path)
        if m and method == "GET":
            p = self.profiles.get(m.group(1))
            return httpx.Response(200, json=p) if p else httpx.Response(404, json={"detail": "Profile not found"})
        if m and method == "DELETE":
            return httpx.Response(200, json={}) if self.profiles.pop(m.group(1), None) else httpx.Response(404, json={"detail": "x"})
        m = re.fullmatch(r"/profiles/([^/]+)/samples", path)
        if m and method == "POST":
            if self.fail_sample_upload:
                return httpx.Response(400, json={"detail": "sample too short"})
            self.samples.setdefault(m.group(1), []).append({"len": len(request.content)})
            return httpx.Response(200, json={"id": "s1", "profile_id": m.group(1), "audio_path": "x", "reference_text": "t"})
        return httpx.Response(404, json={"detail": "no route"})


class FakeEngine:
    def __init__(self, voicebox: FakeVoicebox) -> None:
        self.down = False
        self.status = "ok"
        self.fail_with: tuple[int, dict] | None = None
        self.previews: list[tuple[str, str]] = []
        self._voicebox = voicebox
        self.client = VoiceEngineClient("http://engine.test", 5.0, httpx.MockTransport(self._handle))

    def _handle(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("refused", request=request)
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": self.status})
        body = json.loads(request.content)
        if self.fail_with:
            return httpx.Response(self.fail_with[0], json=self.fail_with[1])
        if body["profile_id"] not in self._voicebox.profiles:
            return httpx.Response(404, json={"code": "profile_not_found", "detail": "unknown"})
        if request.url.path == "/preview":
            self.previews.append((body["profile_id"], body["text"]))
            return httpx.Response(200, content=make_wav(), headers={"content-type": "audio/wav"})
        if request.url.path == "/synthesize/stream":
            return httpx.Response(200, content=b"\x01\x00" * 4800, headers={"content-type": "audio/pcm"})
        if request.url.path == "/warm":
            return httpx.Response(200, json={"warm": True})
        return httpx.Response(404, json={"code": "no_route", "detail": "x"})


class FakeVoiceStack:
    def __init__(self) -> None:
        self.voicebox = FakeVoicebox()
        self.engine = FakeEngine(self.voicebox)
