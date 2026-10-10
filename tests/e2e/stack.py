"""Process and API plumbing for the harness: an isolated API process (which owns the LiveKit supervisor and the runtime worker it
spawns), an API client, engine start/stop through scripts/run_engine.ps1, and a fake LLM endpoint for the LLM-outage scenario."""

from __future__ import annotations

import asyncio
import http.server
import json
import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[2]
KEYS = {"admin": "e2e-admin-key", "operator": "e2e-operator-key", "viewer": "e2e-viewer-key"}


@dataclass
class Config:
    """Every timeout/threshold the harness uses. All are CLI flags of scripts/run_acceptance.py so the same harness runs
    unchanged on a GPU host (tighten them there)."""

    api_port: int = 18015
    livekit_url: str = "ws://localhost:7880"
    livekit_key: str = "devkey"
    livekit_secret: str = "secret"
    voicebox_url: str = "http://127.0.0.1:17493"
    engine_url: str = "http://127.0.0.1:17494"
    ava_voice: str = "6326829e-38ae-475f-aedb-5c293aa43e51"
    james_voice: str = "1ae6b96a-8410-4455-af19-7c58bcdfbd94"
    rms_threshold: float = 300.0
    launch_timeout_s: float = 240.0
    greeting_timeout_s: float = 240.0
    reply_start_timeout_s: float = 120.0
    reply_complete_timeout_s: float = 240.0
    post_interrupt_reply_timeout_s: float = 360.0  # CPU: the engine finishes the in-flight chunk and queued stale requests first
    settle_s: float = 2.0
    quiet_gap_s: float = 1.0  # barge-in: how long audio must stay quiet to count as "silent"
    barge_in_after_s: float = 1.5
    barge_in_limit_ms: float = 1000.0
    p50_limit_ms: float = 700.0
    p95_limit_ms: float = 1500.0
    engine_restore_timeout_s: float = 900.0
    llm_error_timeout_s: float = 120.0
    ten_turn_count: int = 10
    moonshine_arch: str = "small"
    engine_control: str = "auto"  # auto | powershell | script | compose (see engine_control_mode)


def python_exe() -> str:
    return sys.executable


def free_port() -> int:
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@dataclass
class ApiProcess:
    cfg: Config
    work_dir: Path
    extra_env: dict[str, str] = field(default_factory=dict)
    archive_dir: Path | None = None  # logs are copied here (evidence/raw, git-ignored) before each stop/restart
    proc: subprocess.Popen | None = None
    log_path: Path | None = None
    _log: Any = None

    @property
    def db_path(self) -> Path:
        return self.work_dir / "service015.db"

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.cfg.api_port}"

    @property
    def worker_pidfile(self) -> Path:
        return self.work_dir / "runtime-worker.pid"

    def start(self, tag: str) -> None:
        env = {k: v for k, v in os.environ.items() if not k.startswith(("SERVICE015_", "LLM_BASE_URL"))}
        env.update(
            SERVICE015_API_KEYS=",".join(f"{v}:{k}" for k, v in KEYS.items()),
            SERVICE015_DB_PATH=str(self.db_path),
            SERVICE015_HOST="127.0.0.1",
            SERVICE015_PORT=str(self.cfg.api_port),
            LIVEKIT_URL=self.cfg.livekit_url,
            LIVEKIT_API_KEY=self.cfg.livekit_key,
            LIVEKIT_API_SECRET=self.cfg.livekit_secret,
            VOICEBOX_URL=self.cfg.voicebox_url,
            VOICE_ENGINE_URL=self.cfg.engine_url,
            PYTHONUNBUFFERED="1",
        )
        env.update(self.extra_env)
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = self.work_dir / f"api-{tag}.log"
        self._log = open(self.log_path, "wb")
        flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        self.proc = subprocess.Popen(
            [python_exe(), "-c", "from service015.api.app import main; main()"],
            cwd=ROOT, env=env, stdout=self._log, stderr=subprocess.STDOUT, creationflags=flags,
            start_new_session=(os.name != "nt"),
        )

    async def wait_healthy(self, timeout: float = 90.0) -> None:
        deadline = time.monotonic() + timeout
        async with httpx.AsyncClient() as c:
            while time.monotonic() < deadline:
                if self.proc is not None and self.proc.poll() is not None:
                    raise RuntimeError(f"API process exited with {self.proc.returncode}; see {self.log_path}")
                try:
                    if (await c.get(self.base_url + "/health", timeout=3)).status_code == 200:
                        return
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(0.5)
        raise RuntimeError(f"API did not become healthy within {timeout:.0f}s; see {self.log_path}")

    def _worker_pid(self) -> int | None:
        try:
            return int(self.worker_pidfile.read_text().strip())
        except (OSError, ValueError):
            return None

    async def stop(self, graceful_timeout: float = 90.0) -> dict[str, Any]:
        """Stop the API (its lifespan stops the runtime worker). Graceful first (CTRL_BREAK/SIGTERM), then the whole tree."""
        out: dict[str, Any] = {"graceful": False}
        proc = self.proc
        worker_pid = self._worker_pid()
        if proc is not None and proc.poll() is None:
            try:
                proc.send_signal(signal.CTRL_BREAK_EVENT if os.name == "nt" else signal.SIGTERM)
                await asyncio.to_thread(proc.wait, graceful_timeout)
                out["graceful"] = True
            except subprocess.TimeoutExpired:
                out["forced"] = True
                kill_tree(proc.pid)
                await asyncio.to_thread(proc.wait, 30)
        out["returncode"] = proc.returncode if proc is not None else None
        if worker_pid is not None and pid_alive(worker_pid):  # the worker must die with the API
            out["worker_survived_api"] = True
            kill_tree(worker_pid)
        if self._log is not None:
            self._log.close()
            self._log = None
        if self.archive_dir is not None:
            import shutil

            self.archive_dir.mkdir(parents=True, exist_ok=True)
            stamp = time.strftime("%H%M%S")
            for src in (self.log_path, self.work_dir / "runtime-worker.log"):
                if src is not None and src.exists():
                    shutil.copy(src, self.archive_dir / f"{stamp}-{src.name}")
        self.proc = None
        return out


def pid_alive(pid: int) -> bool:
    if os.name == "nt":
        res = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True)
        return str(pid) in res.stdout
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def kill_tree(pid: int) -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
    else:
        try:
            os.killpg(os.getpgid(pid), signal.SIGKILL)
        except OSError:
            pass


class ApiClient:
    def __init__(self, base_url: str, role: str = "operator") -> None:
        self._base = base_url
        self._c = httpx.AsyncClient(base_url=base_url, headers={"Authorization": f"Bearer {KEYS[role]}"}, timeout=180)
        self._admin = {"Authorization": f"Bearer {KEYS['admin']}"}

    async def request(self, method: str, path: str, **kw: Any) -> httpx.Response:
        return await self._c.request(method, path, **kw)

    async def json(self, method: str, path: str, expect: int = 200, **kw: Any) -> Any:
        r = await self.request(method, path, **kw)
        if r.status_code != expect:
            raise AssertionError(f"{method} {path} -> {r.status_code} (expected {expect}): {r.text[:500]}")
        return r.json()

    async def aclose(self) -> None:
        await self._c.aclose()


# -- engine control -----------------------------------------------------------------------------------------------------------


ENGINE_CONTROL_MODES = ("powershell", "script", "compose")


def engine_control_mode(configured: str = "auto", env: dict[str, str] | None = None, platform: str | None = None) -> str:
    """Flag/env/platform precedence: explicit --engine-control, then $ENGINE_CONTROL, then powershell on Windows, script elsewhere."""
    env = os.environ if env is None else env
    mode = configured if configured != "auto" else env.get("ENGINE_CONTROL", "")
    if not mode:
        mode = "powershell" if (platform or sys.platform).startswith("win") else "script"
    if mode not in ENGINE_CONTROL_MODES:
        raise ValueError(f"unknown ENGINE_CONTROL {mode!r}; expected one of {', '.join(ENGINE_CONTROL_MODES)}")
    return mode


def engine_command(action: str, mode: str, env: dict[str, str] | None = None) -> list[str]:
    """argv that stops/starts the voice engine: run_engine.ps1 (powershell), run_engine.sh (script) or docker compose."""
    if action not in ("start", "stop"):
        raise ValueError(f"unknown engine action {action!r}")
    env = os.environ if env is None else env
    if mode == "powershell":
        return ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ROOT / "scripts" / "run_engine.ps1"), "-Action", action]
    if mode == "script":
        return ["bash", str(ROOT / "scripts" / "run_engine.sh"), action]
    if mode == "compose":
        return ["docker", "compose", action, env.get("ENGINE_COMPOSE_SERVICE", "engine")]
    raise ValueError(f"unknown engine control mode {mode!r}")


def run_engine_script(action: str, mode: str = "auto") -> str:
    argv = engine_command(action, engine_control_mode(mode))
    res = subprocess.run(argv, capture_output=True, text=True, cwd=ROOT, timeout=300)
    out = (res.stdout + res.stderr).strip()
    if res.returncode != 0 and mode != "compose":
        raise RuntimeError(f"{' '.join(argv)} exited {res.returncode}: {out[:300]}")
    return out


def engine_health(url: str, timeout: float = 3.0) -> dict[str, Any] | None:
    """None when the engine does not answer in `timeout` s (it answers slowly while a synthesis is running on CPU)."""
    try:
        return httpx.get(url + "/health", timeout=timeout).json()
    except (httpx.HTTPError, ValueError):
        return None


# -- fake LLM: GET /models answers (so the launch preflight passes) but every completion drops the connection ---------------------


class _BrokenLlm(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a: Any) -> None:
        pass

    def do_GET(self) -> None:
        body = json.dumps({"object": "list", "data": [{"id": "fake", "object": "model"}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        self.connection.close()  # abrupt: client sees a connection error, never a completion


class BrokenLlmServer:
    def __init__(self) -> None:
        self.port = free_port()
        self._srv = http.server.ThreadingHTTPServer(("127.0.0.1", self.port), _BrokenLlm)
        self._t = threading.Thread(target=self._srv.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1"

    def start(self) -> None:
        self._t.start()

    def stop(self) -> None:
        self._srv.shutdown()
        self._srv.server_close()
