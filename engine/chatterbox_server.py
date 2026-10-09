"""Voice engine sidecar: Chatterbox Turbo / Turbo-Nano with sentence-level streaming.

Runs with Voicebox's venv interpreter (torch + chatterbox). It must NOT import service015.
Reference voices come from Voicebox profiles (VOICEBOX_URL); this process owns the single model copy.

Env:
  VOICE_ENGINE_DEVICE   "cpu" | "cuda" (string, default "cpu")
  CHATTERBOX_VARIANT    "turbo" (default) | "turbo-nano"
  VOICEBOX_URL          default http://127.0.0.1:17493
  VOICE_ENGINE_HOST/PORT  default 127.0.0.1 / 17494
  CHATTERBOX_SRC        dir containing the `chatterbox` package to prefer (GitHub master is required for Nano;
                        PyPI 0.1.7 has no `nano` option). Default: <repo>/deps/chatterbox-src/src if it exists.
  VOICE_ENGINE_FAIL_EXIT_S  seconds /health keeps reporting "error" before the process exits non-zero (default 10)
"""
from __future__ import annotations

import asyncio
import inspect
import io
import logging
import os
import sys
import tempfile
import threading
import uuid
import time
import wave
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

sys.path.insert(0, str(Path(__file__).resolve().parent))
from textsplit import split_for_streaming  # noqa: E402

log = logging.getLogger("voice-engine")

SAMPLE_RATE = 24000
ENGINE_NAME = "chatterbox-turbo"
CACHE_SIZE = 8
REQUESTS_KEPT = 256
PROFILE_ID_RE = r"^[A-Za-z0-9_-]{1,64}$"  # goes into a Voicebox URL path
MIN_REFERENCE_S = 5.0
WARM_TEXT = "Hello there."
GEN_KWARGS = dict(temperature=0.8, top_k=1000, top_p=0.95, repetition_penalty=1.2, norm_loudness=True)

DEVICE = os.environ.get("VOICE_ENGINE_DEVICE", "cpu")
VARIANT = os.environ.get("CHATTERBOX_VARIANT", "turbo")
VOICEBOX_URL = os.environ.get("VOICEBOX_URL", "http://127.0.0.1:17493").rstrip("/")
FAIL_EXIT_S = float(os.environ.get("VOICE_ENGINE_FAIL_EXIT_S", "10"))


class ProfileNotFound(Exception):
    pass


class VoiceboxUnavailable(Exception):
    pass


class ReferenceTooShort(Exception):
    pass


class CorruptReference(Exception):
    pass


# Error codes, in HTTP responses (before streaming) and in /requests/{id} (after it started).
HTTP_STATUS = {
    "profile_not_found": 404,
    "reference_too_short": 422,
    "corrupt_reference": 422,
    "voicebox_unavailable": 503,
    "synthesis_failed": 500,
}


def classify(exc: BaseException) -> tuple[str, str]:
    """Map an exception to a stable (code, detail). Never leaks tracebacks."""
    if isinstance(exc, ProfileNotFound):
        return "profile_not_found", f"Voicebox profile {exc} not found"
    if isinstance(exc, ReferenceTooShort):
        return "reference_too_short", f"profile {exc} has no sample longer than {MIN_REFERENCE_S:.0f} s"
    if isinstance(exc, CorruptReference):
        return "corrupt_reference", f"profile {exc} has no readable reference sample"
    if isinstance(exc, VoiceboxUnavailable):
        return "voicebox_unavailable", str(exc)
    return "synthesis_failed", f"{type(exc).__name__}: {exc}"


class State:
    """Shared engine state. `lock` serialises every model call; `executor` is the single worker thread."""

    def __init__(self) -> None:
        self.status = "loading"  # loading | ok | error
        self.error: str | None = None
        self.warm = False
        self.model = None
        self.lock = threading.Lock()
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="tts")
        self.conds: OrderedDict[str, object] = OrderedDict()  # profile_id -> Conditionals (LRU, CACHE_SIZE)
        self.prep_locks: dict[str, asyncio.Lock] = {}  # only while a prepare is in flight
        self.requests: OrderedDict[str, dict] = OrderedDict()  # request_id -> status (LRU, REQUESTS_KEPT)
        self.load_s: float | None = None
        self.nano_supported: bool | None = None


STATE = State()


# ---------------------------------------------------------------- model loading

def _prefer_chatterbox_src() -> str | None:
    src = os.environ.get("CHATTERBOX_SRC")
    if not src:
        cand = Path(__file__).resolve().parent.parent / "deps" / "chatterbox-src" / "src"
        src = str(cand) if cand.is_dir() else None
    if src:
        if not (Path(src) / "chatterbox" / "tts_turbo.py").is_file():
            raise RuntimeError(f"CHATTERBOX_SRC={src} does not contain chatterbox/tts_turbo.py")
        sys.path.insert(0, src)
    return src


def load_model() -> None:
    """Load the single model copy and run a warm-up generate. Raises on any problem (no silent fallback)."""
    if VARIANT not in ("turbo", "turbo-nano"):
        raise RuntimeError(f"CHATTERBOX_VARIANT must be 'turbo' or 'turbo-nano', got {VARIANT!r}")
    nano = VARIANT == "turbo-nano"
    src = _prefer_chatterbox_src()
    from chatterbox.tts_turbo import ChatterboxTurboTTS  # heavy import, deliberately lazy

    params = inspect.signature(ChatterboxTurboTTS.from_pretrained).parameters
    STATE.nano_supported = "nano" in params
    log.info("chatterbox from %s (src override: %s), nano option: %s", sys.modules["chatterbox.tts_turbo"].__file__, src, STATE.nano_supported)
    if nano and not STATE.nano_supported:
        raise RuntimeError(
            "CHATTERBOX_VARIANT=turbo-nano but the importable chatterbox package has no `nano` option "
            "(PyPI chatterbox-tts 0.1.7). Clone GitHub master and set CHATTERBOX_SRC."
        )
    t0 = time.perf_counter()
    kwargs = {"nano": nano} if STATE.nano_supported else {}
    model = ChatterboxTurboTTS.from_pretrained(DEVICE, **kwargs)  # device as STRING (torch.device breaks CPU load)
    STATE.load_s = time.perf_counter() - t0
    if model.sr != SAMPLE_RATE:
        raise RuntimeError(f"model sample rate {model.sr} != {SAMPLE_RATE}")
    STATE.model = model
    log.info("model loaded in %.1fs (variant=%s device=%s)", STATE.load_s, VARIANT, DEVICE)
    if model.conds is not None:  # warm-up generate with the checkpoint's built-in voice
        t1 = time.perf_counter()
        model.generate(WARM_TEXT, **GEN_KWARGS)
        STATE.warm = True
        log.info("warm-up generate done in %.1fs", time.perf_counter() - t1)
    else:
        log.warning("checkpoint has no built-in conditionals; warm-up generate happens on the first /warm")


def _load_in_background() -> None:
    try:
        load_model()
        STATE.status = "ok"
    except Exception as exc:  # noqa: BLE001 - recorded, logged; the process then exits non-zero (loud failure)
        STATE.status, STATE.error = "error", f"{type(exc).__name__}: {exc}"
        log.exception("engine startup failed; exiting in %.0fs", FAIL_EXIT_S)
        threading.Timer(FAIL_EXIT_S, lambda: os._exit(1)).start()  # /health reports "error" meanwhile


@asynccontextmanager
async def lifespan(app: FastAPI):
    threading.Thread(target=_load_in_background, name="model-load", daemon=True).start()
    yield
    STATE.executor.shutdown(wait=False, cancel_futures=True)


app = FastAPI(title="Service 015 voice engine", version="0.1.0", lifespan=lifespan)


# ---------------------------------------------------------------- voicebox access

def wav_duration(data: bytes) -> float:
    try:
        with wave.open(io.BytesIO(data)) as w:
            return w.getnframes() / float(w.getframerate())
    except (wave.Error, EOFError):
        # Not integer-PCM wav (e.g. float wav): soundfile ships with the chatterbox deps.
        import soundfile as sf

        info = sf.info(io.BytesIO(data))
        return info.frames / float(info.samplerate)


async def fetch_reference(profile_id: str) -> tuple[bytes, float]:
    """Return (wav bytes, duration s) of the longest sample > 5 s of a Voicebox profile."""
    best: tuple[float, bytes] | None = None
    try:
        async with httpx.AsyncClient(base_url=VOICEBOX_URL, timeout=httpx.Timeout(30.0)) as client:
            p = await client.get(f"/profiles/{profile_id}")  # /samples answers 200 [] for unknown ids
            if p.status_code in (404, 422):
                raise ProfileNotFound(profile_id)
            p.raise_for_status()
            r = await client.get(f"/profiles/{profile_id}/samples")
            if r.status_code in (404, 422):
                raise ProfileNotFound(profile_id)
            r.raise_for_status()
            samples = r.json()
            readable = 0
            for sample in samples:
                a = await client.get(f"/samples/{sample['id']}")
                a.raise_for_status()
                try:
                    dur = wav_duration(a.content)
                except Exception as exc:  # noqa: BLE001 - unreadable sample is skipped, reported if none is usable
                    log.warning("profile %s sample %s unreadable: %s: %s", profile_id, sample.get("id"), type(exc).__name__, exc)
                    continue
                readable += 1
                if dur > MIN_REFERENCE_S and (best is None or dur > best[0]):
                    best = (dur, a.content)
    except httpx.TransportError as exc:
        raise VoiceboxUnavailable(f"{VOICEBOX_URL}: {type(exc).__name__}: {exc}") from exc
    except httpx.HTTPStatusError as exc:
        raise VoiceboxUnavailable(f"{VOICEBOX_URL}: HTTP {exc.response.status_code}") from exc
    if best is None:
        if samples and readable == 0:
            raise CorruptReference(profile_id)
        raise ReferenceTooShort(profile_id)
    return best[1], best[0]


# ---------------------------------------------------------------- synthesis (worker thread)

def _prepare_blocking(profile_id: str, wav_bytes: bytes) -> dict:
    with STATE.lock:
        t0 = time.perf_counter()
        fd, path = tempfile.mkstemp(suffix=".wav")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(wav_bytes)
            STATE.model.prepare_conditionals(path)
        finally:
            os.unlink(path)
        STATE.conds[profile_id] = STATE.model.conds
        STATE.conds.move_to_end(profile_id)
        while len(STATE.conds) > CACHE_SIZE:
            evicted, _ = STATE.conds.popitem(last=False)
            log.info("evicted conditionals for profile %s", evicted)
        cond_s = time.perf_counter() - t0
        t1 = time.perf_counter()
        STATE.model.generate(WARM_TEXT, **GEN_KWARGS)
        STATE.warm = True
        return {"conditionals_s": round(cond_s, 3), "tiny_generate_s": round(time.perf_counter() - t1, 3)}


def _generate_blocking(profile_id: str, text: str) -> bytes:
    """Generate one chunk. Returns s16le mono 24 kHz PCM bytes."""
    import numpy as np

    with STATE.lock:
        conds = STATE.conds.get(profile_id)
        if conds is None:
            raise ProfileNotFound(profile_id)  # evicted since prepare; caller re-prepares
        STATE.conds.move_to_end(profile_id)
        STATE.model.conds = conds
        wav = STATE.model.generate(text, **GEN_KWARGS)
    arr = wav.squeeze(0).detach().cpu().numpy()
    return (np.clip(arr, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()


async def ensure_profile(profile_id: str) -> dict | None:
    """Make sure conditionals for profile_id are cached. Returns timings when it had to prepare them."""
    if profile_id in STATE.conds:
        return None
    plock = STATE.prep_locks.setdefault(profile_id, asyncio.Lock())
    try:
        async with plock:
            if profile_id in STATE.conds:
                return None
            t0 = time.perf_counter()
            wav_bytes, dur = await fetch_reference(profile_id)
            fetch_s = time.perf_counter() - t0
            timings = await asyncio.get_running_loop().run_in_executor(STATE.executor, _prepare_blocking, profile_id, wav_bytes)
            timings.update(fetch_reference_s=round(fetch_s, 3), reference_s=round(dur, 2))
            return timings
    finally:
        if not plock.locked() and STATE.prep_locks.get(profile_id) is plock:
            del STATE.prep_locks[profile_id]  # bound the dict: entries live only while a prepare is in flight


# ---------------------------------------------------------------- HTTP

class WarmRequest(BaseModel):
    profile_id: str = Field(pattern=PROFILE_ID_RE)


class SynthRequest(BaseModel):
    profile_id: str = Field(pattern=PROFILE_ID_RE)
    text: str = Field(min_length=1, max_length=4000)


def _error(status: int, code: str, detail: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "detail": detail})


def _require_ready() -> None:
    if STATE.status == "loading":
        raise _error(503, "engine_loading", "model is still loading")
    if STATE.status == "error":
        raise _error(503, "engine_error", STATE.error or "engine failed to start")


async def _ensure_or_http(profile_id: str) -> dict | None:
    try:
        return await ensure_profile(profile_id)
    except (ProfileNotFound, ReferenceTooShort, CorruptReference, VoiceboxUnavailable) as exc:
        code, detail = classify(exc)
        log.warning("request rejected code=%s profile=%s", code, profile_id)
        raise _error(HTTP_STATUS[code], code, detail) from None
    except Exception as exc:  # noqa: BLE001 - coded 500, never a bare traceback
        code, detail = classify(exc)
        log.exception("prepare failed code=%s profile=%s", code, profile_id)
        raise _error(500, code, detail) from None


@app.exception_handler(RequestValidationError)
async def _validation_exc(_: Request, exc: RequestValidationError):
    first = exc.errors()[0]
    return JSONResponse(status_code=422, content={"code": "invalid_request", "detail": f"{first['msg']}: {'.'.join(str(x) for x in first['loc'])}"})


@app.exception_handler(HTTPException)
async def _http_exc(_: Request, exc: HTTPException):
    detail = exc.detail if isinstance(exc.detail, dict) else {"code": "error", "detail": str(exc.detail)}
    return JSONResponse(status_code=exc.status_code, content=detail)


@app.get("/health")
async def health():
    return {
        "status": STATE.status,
        "engine": ENGINE_NAME,
        "variant": VARIANT,
        "device": DEVICE,
        "sample_rate": SAMPLE_RATE,
        "warm": STATE.warm,
        "cached_profiles": list(STATE.conds.keys()),
        "nano_supported": STATE.nano_supported,
        "load_seconds": None if STATE.load_s is None else round(STATE.load_s, 1),
        "error": STATE.error,
    }


@app.post("/warm")
async def warm(req: WarmRequest):
    _require_ready()
    t0 = time.perf_counter()
    timings = await _ensure_or_http(req.profile_id)
    cached = timings is None
    if cached:
        if req.profile_id in STATE.conds:
            STATE.conds.move_to_end(req.profile_id)
        timings = {}
    timings.update(already_cached=cached, total_s=round(time.perf_counter() - t0, 3))
    log.info("warm profile=%s %s", req.profile_id, timings)
    return {"profile_id": req.profile_id, "status": "warm", "timings": timings}


def _new_request(profile_id: str, total: int) -> dict:
    rec = {"request_id": uuid.uuid4().hex, "profile_id": profile_id, "state": "streaming", "chunks_total": total,
           "chunks_done": 0, "code": None, "detail": None}
    STATE.requests[rec["request_id"]] = rec
    while len(STATE.requests) > REQUESTS_KEPT:
        STATE.requests.popitem(last=False)
    return rec


async def _stream_chunks(request: Request, rec: dict, chunks: list[str], t_start: float):
    """Yield PCM per chunk. Sets rec.state to complete | cancelled | failed. On failure it logs the code and RE-RAISES,
    which aborts the HTTP response without the terminating chunk (clients see an incomplete read)."""
    loop = asyncio.get_running_loop()
    profile_id = rec["profile_id"]
    audio_s, first_s = 0.0, None
    try:
        for i, chunk in enumerate(chunks):
            if await request.is_disconnected():
                log.info("client disconnected: skipping %d remaining chunk(s) (barge-in)", len(chunks) - i)
                rec["state"] = "cancelled"
                return
            try:
                pcm = await loop.run_in_executor(STATE.executor, _generate_blocking, profile_id, chunk)
            except ProfileNotFound:  # conditionals evicted by other requests mid-stream: rebuild once
                await ensure_profile(profile_id)
                pcm = await loop.run_in_executor(STATE.executor, _generate_blocking, profile_id, chunk)
            rec["chunks_done"] += 1
            audio_s += len(pcm) / 2 / SAMPLE_RATE
            if first_s is None:
                first_s = time.perf_counter() - t_start
            yield pcm
        rec["state"] = "complete"
    except asyncio.CancelledError:  # Starlette cancelled us because the client went away
        rec["state"] = "cancelled"
        raise
    except Exception as exc:  # noqa: BLE001 - classified, logged with code, then re-raised to abort the response
        rec["state"] = "failed"
        rec["code"], rec["detail"] = classify(exc)
        log.error("stream failed request=%s profile=%s code=%s detail=%s", rec["request_id"], profile_id, rec["code"], rec["detail"])
        raise
    finally:
        wall = time.perf_counter() - t_start
        log.info(
            "synth request=%s profile=%s state=%s chunks=%d/%d chars=%d first_chunk_s=%s audio_s=%.2f wall_s=%.2f rtf=%s",
            rec["request_id"], profile_id, rec["state"], rec["chunks_done"], len(chunks), sum(map(len, chunks)),
            None if first_s is None else round(first_s, 2), audio_s, wall,
            None if audio_s <= 0 else round(wall / audio_s, 2),
        )


@app.post("/synthesize/stream")
async def synthesize_stream(req: SynthRequest, request: Request):
    _require_ready()
    t_start = time.perf_counter()
    chunks = split_for_streaming(req.text)
    if not chunks:
        raise _error(422, "empty_text", "text has no speakable content")
    await _ensure_or_http(req.profile_id)
    rec = _new_request(req.profile_id, len(chunks))
    return StreamingResponse(
        _stream_chunks(request, rec, chunks, t_start),
        media_type="audio/pcm",
        headers={"X-Sample-Rate": str(SAMPLE_RATE), "X-Chunk-Count": str(len(chunks)),
                 "X-Request-Id": rec["request_id"], "Cache-Control": "no-store"},
    )


@app.get("/requests/{request_id}")
async def request_status(request_id: str):
    rec = STATE.requests.get(request_id)
    if rec is None:
        raise _error(404, "request_not_found", f"unknown or expired request {request_id}")
    return rec


@app.post("/preview")
async def preview(req: SynthRequest, request: Request):
    _require_ready()
    t_start = time.perf_counter()
    chunks = split_for_streaming(req.text)
    if not chunks:
        raise _error(422, "empty_text", "text has no speakable content")
    await _ensure_or_http(req.profile_id)
    rec = _new_request(req.profile_id, len(chunks))
    try:
        pcm = b"".join([c async for c in _stream_chunks(request, rec, chunks, t_start)])
    except Exception:  # noqa: BLE001 - _stream_chunks already classified and logged it
        raise _error(HTTP_STATUS[rec["code"]], rec["code"], rec["detail"]) from None
    if rec["state"] != "complete":
        raise _error(499, "client_disconnected", "client went away during preview")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(pcm)
    return Response(buf.getvalue(), media_type="audio/wav",
                    headers={"X-Sample-Rate": str(SAMPLE_RATE), "X-Request-Id": rec["request_id"]})


def main() -> None:
    import uvicorn

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    uvicorn.run(
        app,
        host=os.environ.get("VOICE_ENGINE_HOST", "127.0.0.1"),
        port=int(os.environ.get("VOICE_ENGINE_PORT", "17494")),
        log_level="info",
    )


if __name__ == "__main__":
    main()
