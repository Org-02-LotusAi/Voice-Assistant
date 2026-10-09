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
        # stream scenario: complete | failed | truncated | cancelled | break | no_id | status_404
        self.stream_mode = "complete"
        self.stream_code = "synthesis_failed"
        self._status: dict = {}
        self._voicebox = voicebox
        self.client = VoiceEngineClient("http://engine.test", 5.0, httpx.MockTransport(self._handle))

    def _stream(self) -> httpx.Response:
        mode = self.stream_mode
        total = 3
        done = {"complete": 3, "failed": 1, "truncated": 2, "cancelled": 1, "break": 1}.get(mode, 3)
        state = {"failed": "failed", "cancelled": "cancelled", "break": "failed"}.get(mode, "complete")
        self._status = {"request_id": "req-1", "profile_id": "vp-ava", "state": state, "chunks_total": total,
                        "chunks_done": done, "code": self.stream_code if state == "failed" else None,
                        "detail": "boom" if state == "failed" else None}
        headers = {"content-type": "audio/pcm", "x-chunk-count": str(total)}
        if mode != "no_id":
            headers["x-request-id"] = "req-1"
        return httpx.Response(200, headers=headers, stream=_Chunks([b"\x01\x00" * 100] * done, then_raise=(mode == "break")))

    def _handle(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("refused", request=request)
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": self.status})
        if request.url.path.startswith("/requests/"):
            if self.stream_mode == "status_404":
                return httpx.Response(404, json={"code": "request_not_found", "detail": "expired"})
            return httpx.Response(200, json=self._status)
        body = json.loads(request.content)
        if self.fail_with:
            return httpx.Response(self.fail_with[0], json=self.fail_with[1])
        if body["profile_id"] not in self._voicebox.profiles:
            return httpx.Response(404, json={"code": "profile_not_found", "detail": "unknown"})
        if request.url.path == "/preview":
            self.previews.append((body["profile_id"], body["text"]))
            return httpx.Response(200, content=make_wav(), headers={"content-type": "audio/wav"})
        if request.url.path == "/synthesize/stream":
            return self._stream()
        if request.url.path == "/warm":
            return httpx.Response(200, json={"warm": True})
        return httpx.Response(404, json={"code": "no_route", "detail": "x"})


class _Chunks(httpx.AsyncByteStream):
    def __init__(self, chunks, then_raise=False):
        self._chunks, self._raise = chunks, then_raise

    async def __aiter__(self):
        for c in self._chunks:
            yield c
        if self._raise:
            raise httpx.RemoteProtocolError("peer closed connection without sending complete message body")


class FakeVoiceStack:
    def __init__(self) -> None:
        self.voicebox = FakeVoicebox()
        self.engine = FakeEngine(self.voicebox)
