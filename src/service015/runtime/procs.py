"""Runtime worker process lifecycle: spawn, pidfile, adoption, kill-on-parent-exit, tree kill.

- Windows: the worker is assigned to a Job Object with KILL_ON_JOB_CLOSE (ctypes, no dependency), so when the API process
  dies for any reason (crash, task kill) the OS kills the worker and all its job processes. A graceful stop sends CTRL_BREAK
  (the worker drains its jobs; job children ignore it, see runtime/worker.py) and falls back to `taskkill /T /F`.
- POSIX: the worker starts its own session (`start_new_session`) and is stopped with `killpg` (SIGTERM, then SIGKILL).
- A pidfile next to the database (`<pid>:<create_time>`) lets a restarted API adopt a worker that is still alive instead of
  spawning a duplicate. After a hard API exit the Job Object kills the worker but the pidfile stays behind, and pids get
  recycled, so adoption requires psutil to confirm that the pid is alive, its create_time matches the recorded one and its
  command line is the runtime worker. Anything else deletes the pidfile and is never touched (never bound, never killed).
  psutil is a dependency of livekit-agents.
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

log = logging.getLogger("service015.supervisor")

# Variables the worker (and its job processes) may inherit. Everything else, notably SERVICE015_API_KEYS and unrelated
# provider secrets, is withheld.
ENV_ALLOW_EXACT = frozenset(
    {"PATH", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "PATHEXT", "TEMP", "TMP", "TMPDIR", "HOME", "USERPROFILE",
     "APPDATA", "LOCALAPPDATA", "PROGRAMDATA", "PROGRAMFILES", "VIRTUAL_ENV", "LANG", "LC_ALL", "SSL_CERT_FILE",
     "REQUESTS_CA_BUNDLE", "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "HF_HOME", "XDG_CACHE_HOME", "USERNAME", "NUMBER_OF_PROCESSORS",
     "PROCESSOR_ARCHITECTURE", "VOICEBOX_URL", "SERVICE015_DB_PATH", "SERVICE015_RUNTIME_LOG"}
)
ENV_ALLOW_PREFIXES = ("LIVEKIT_", "LLM_", "VOICE_ENGINE_", "MOONSHINE_")


def allowed_env(source: dict[str, str]) -> dict[str, str]:
    """The allow-listed subset of `source` (case-insensitive names on Windows are preserved as given)."""
    return {
        k: v for k, v in source.items()
        if k.upper() in ENV_ALLOW_EXACT or k.upper().startswith(ENV_ALLOW_PREFIXES)
    }


# ---------------------------------------------------------------------------------------------------------------- liveness


def pid_alive(pid: int) -> bool:
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenProcess.restype = wintypes.HANDLE
        h = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        try:
            code = wintypes.DWORD()
            return bool(k32.GetExitCodeProcess(h, ctypes.byref(code))) and code.value == 259  # STILL_ACTIVE
        finally:
            k32.CloseHandle(h)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


WORKER_CMDLINE_MARKERS = ("service015.runtime.worker", "service015-runtime")
CREATE_TIME_TOLERANCE_S = 1.0


def read_pidfile(path: Path) -> tuple[int, float] | None:
    """(pid, create_time) from `<pid>:<create_time>`; None for a missing or malformed file."""
    try:
        pid_s, ct_s = path.read_text(encoding="ascii").strip().split(":", 1)
        return int(pid_s), float(ct_s)
    except (OSError, ValueError):
        return None


def write_pidfile(path: Path, pid: int) -> None:
    import psutil

    path.write_text(f"{pid}:{psutil.Process(pid).create_time()}", encoding="ascii")


def is_our_worker(pid: int, recorded_create_time: float) -> bool:
    """True only if `pid` is alive, was created at `recorded_create_time` and runs the service015 runtime worker."""
    import psutil

    try:
        proc = psutil.Process(pid)
        if abs(proc.create_time() - recorded_create_time) > CREATE_TIME_TOLERANCE_S:
            return False
        cmdline = " ".join(proc.cmdline())
        return proc.is_running() and any(m in cmdline for m in WORKER_CMDLINE_MARKERS)
    except (psutil.Error, OSError):
        return False


# ---------------------------------------------------------------------------------------------------------------- job object

_job_handle: Any = None


def _kill_on_close_job() -> Any:
    """The process-wide Job Object (created lazily); every handle to it closes when this process exits."""
    global _job_handle
    if _job_handle is not None:
        return _job_handle
    import ctypes
    from ctypes import wintypes

    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [(n, ctypes.c_uint64) for n in
                    ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                     "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

    class BASIC(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]

    class EXTENDED(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", BASIC), ("IoInfo", IO_COUNTERS), ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t)]

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateJobObjectW.restype = wintypes.HANDLE
    handle = k32.CreateJobObjectW(None, None)
    if not handle:
        raise OSError(ctypes.get_last_error(), "CreateJobObjectW failed")
    info = EXTENDED()
    info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not k32.SetInformationJobObject(handle, 9, ctypes.byref(info), ctypes.sizeof(info)):  # JobObjectExtendedLimitInformation
        err = ctypes.get_last_error()
        k32.CloseHandle(handle)
        raise OSError(err, "SetInformationJobObject failed")
    _job_handle = handle
    return handle


def bind_to_parent_lifetime(pid: int) -> bool:
    """Make the OS kill process `pid` (and its future children) when this process exits. True if the guarantee is in place."""
    if sys.platform != "win32":
        return True  # POSIX workers are session leaders killed by killpg on graceful exit; see WorkerProcess.stop
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.restype = wintypes.HANDLE
    h = k32.OpenProcess(0x0100 | 0x0001, False, pid)  # PROCESS_SET_QUOTA | PROCESS_TERMINATE
    if not h:
        log.warning("cannot open worker pid %s for the job object (error %s)", pid, ctypes.get_last_error())
        return False
    try:
        if not k32.AssignProcessToJobObject(_kill_on_close_job(), h):
            log.warning("AssignProcessToJobObject failed for pid %s (error %s)", pid, ctypes.get_last_error())
            return False
        return True
    finally:
        k32.CloseHandle(h)


# ---------------------------------------------------------------------------------------------------------------- worker


class WorkerProcess:
    """The `service015-runtime` worker (module form: python -m service015.runtime.worker) this supervisor owns or adopted."""

    READY_MARKER = "registered worker"
    STOP_TIMEOUT_S = 15.0

    def __init__(self, env: dict[str, str], log_path: Path, pid_path: Path) -> None:
        self._env = env
        self._log_path = log_path
        self._pid_path = pid_path
        self._proc: subprocess.Popen | None = None
        self._adopted: int | None = None
        self._log_file: Any = None
        self.job_bound = False
        self.had_pidfile = False  # a pidfile existed at adoption time: this API managed a worker before

    # -- state ----------------------------------------------------------------------------------------------------------

    @property
    def pid(self) -> int | None:
        if self._proc is not None:
            return self._proc.pid
        return self._adopted

    def alive(self) -> bool:
        if self._proc is not None:
            return self._proc.poll() is None
        return self._adopted is not None and pid_alive(self._adopted)

    def log_text(self) -> str:
        try:
            return self._log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""

    def registered(self) -> bool:
        return self.READY_MARKER in self.log_text()

    # -- lifecycle ------------------------------------------------------------------------------------------------------

    def adopt_existing(self) -> bool:
        """Adopt the live worker recorded in the pidfile (previous API run); delete a stale or foreign pidfile."""
        rec = read_pidfile(self._pid_path)
        self.had_pidfile = self._pid_path.exists()
        if rec is None:
            self._pid_path.unlink(missing_ok=True)
            return False
        pid, created = rec
        if not is_our_worker(pid, created):
            log.info("pidfile %s is stale (pid %s is dead, recycled or not a runtime worker); ignoring it", self._pid_path, pid)
            self._pid_path.unlink(missing_ok=True)
            return False
        self._adopted = pid
        self.job_bound = bind_to_parent_lifetime(pid)
        log.info("adopted running runtime worker pid=%s", pid)
        return True

    def spawn(self) -> None:
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        self._log_file = open(self._log_path, "wb")  # truncated per spawn: the ready marker must come from THIS process
        kwargs: dict[str, Any] = {}
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True
        self._proc = subprocess.Popen(
            [sys.executable, "-m", "service015.runtime.worker"],
            env=self._env, stdout=self._log_file, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, **kwargs,
        )
        self._adopted = None
        self.job_bound = bind_to_parent_lifetime(self._proc.pid)
        write_pidfile(self._pid_path, self._proc.pid)
        log.info("spawned runtime worker pid=%s job_bound=%s log=%s", self._proc.pid, self.job_bound, self._log_path)

    def stop(self) -> None:
        """Ask the worker to drain its jobs (CTRL_BREAK / SIGTERM); kill the whole tree if it does not exit in time."""
        pid = self.pid
        try:
            if pid is not None and self.alive():
                self._signal_graceful(pid)
                if not self._wait_dead(pid, self.STOP_TIMEOUT_S):
                    self._kill_tree(pid)
        finally:
            if self._log_file is not None:
                self._log_file.close()
                self._log_file = None
            self._proc, self._adopted = None, None
            self._pid_path.unlink(missing_ok=True)

    def _signal_graceful(self, pid: int) -> None:
        try:
            if sys.platform == "win32":
                if self._proc is not None:
                    self._proc.send_signal(signal.CTRL_BREAK_EVENT)
                else:
                    os.kill(pid, signal.CTRL_BREAK_EVENT)
            else:
                os.killpg(pid, signal.SIGTERM)
        except OSError as exc:
            log.warning("graceful stop signal to worker pid=%s failed: %s", pid, exc)

    def _wait_dead(self, pid: int, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not (self._proc.poll() is None if self._proc is not None else pid_alive(pid)):
                return True
            time.sleep(0.2)
        return False

    def _kill_tree(self, pid: int) -> None:
        log.warning("runtime worker pid=%s did not drain in time; killing its process tree", pid)
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, check=False)
        else:
            try:
                os.killpg(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        self._wait_dead(pid, 10.0)
