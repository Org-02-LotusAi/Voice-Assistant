"""Host resource sampler (psutil): system CPU / RAM / swap plus per-component CPU and RSS, so a slow or failed scenario can be
tied to measured saturation instead of "the CPU is slow". Samples go to <evidence>/resources.jsonl; each scenario gets a
min/avg/max summary of its own time window in its result JSON."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

import psutil

GROUPS = (  # (group, substring of the command line); first match wins, order matters
    ("engine", "chatterbox_server.py"),
    ("voicebox", "backend.main"),
    ("api", "service015.api.app"),
    ("runtime_worker", "service015.runtime"),
    ("runtime_jobs", "multiprocessing.spawn"),  # LiveKit job processes are spawned children of the worker
    ("harness", "run_acceptance"),
)


def _group(proc: psutil.Process, cmdline: str) -> str | None:
    for name, needle in GROUPS:
        if needle in cmdline:
            return name
    return None


class ResourceMonitor:
    def __init__(self, out_path: Path, interval_s: float = 2.0) -> None:
        self._out, self._interval = out_path, interval_s
        self._procs: dict[int, psutil.Process] = {}
        self._samples: list[dict[str, Any]] = []
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._run, name="resource-monitor", daemon=True)

    def start(self) -> None:
        self._out.parent.mkdir(parents=True, exist_ok=True)
        psutil.cpu_percent(None)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=10)

    def _sample(self) -> dict[str, Any]:
        vm, sw = psutil.virtual_memory(), psutil.swap_memory()
        groups: dict[str, dict[str, float]] = {}
        seen: set[int] = set()
        for p in psutil.process_iter(["pid", "cmdline"]):
            cmd = " ".join(p.info["cmdline"] or [])
            g = _group(p, cmd)
            if g is None:
                continue
            seen.add(p.pid)
            proc = self._procs.setdefault(p.pid, p)
            try:
                cpu, rss = proc.cpu_percent(None), proc.memory_info().rss
            except psutil.Error:
                continue
            d = groups.setdefault(g, {"cpu_pct": 0.0, "rss_mb": 0.0, "procs": 0})
            d["cpu_pct"] += cpu
            d["rss_mb"] += rss / 2**20
            d["procs"] += 1
        for pid in list(self._procs):
            if pid not in seen:
                del self._procs[pid]
        return {
            "t": time.monotonic(), "wall": time.strftime("%H:%M:%S"), "cpu_pct": psutil.cpu_percent(None),
            "mem_used_pct": vm.percent, "mem_available_mb": round(vm.available / 2**20), "swap_used_mb": round(sw.used / 2**20),
            "groups": {k: {kk: round(vv, 1) for kk, vv in v.items()} for k, v in groups.items()},
        }

    def _run(self) -> None:
        with open(self._out, "a", encoding="utf-8") as f:
            while not self._stop.is_set():
                s = self._sample()
                with self._lock:
                    self._samples.append(s)
                f.write(json.dumps(s) + "\n")
                f.flush()
                self._stop.wait(self._interval)

    def summary(self, t0: float, t1: float) -> dict[str, Any]:
        with self._lock:
            window = [s for s in self._samples if t0 <= s["t"] <= t1]
        if not window:
            return {"samples": 0}

        def stat(vals: list[float]) -> dict[str, float]:
            return {"min": round(min(vals), 1), "avg": round(sum(vals) / len(vals), 1), "max": round(max(vals), 1)}

        out: dict[str, Any] = {
            "samples": len(window), "cpu_pct": stat([s["cpu_pct"] for s in window]),
            "mem_available_mb": stat([s["mem_available_mb"] for s in window]), "swap_used_mb": stat([s["swap_used_mb"] for s in window]),
        }
        names = {g for s in window for g in s["groups"]}
        out["groups"] = {
            g: {
                "cpu_pct": stat([s["groups"].get(g, {}).get("cpu_pct", 0.0) for s in window]),
                "rss_mb": stat([s["groups"].get(g, {}).get("rss_mb", 0.0) for s in window]),
            }
            for g in sorted(names)
        }
        return out
