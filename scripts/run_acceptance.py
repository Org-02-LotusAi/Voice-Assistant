"""End-to-end voice acceptance run (Task 10): real audio, real LiveKit rooms, evidence under evidence/<UTC timestamp>/.

  uv run python scripts/run_acceptance.py [--only self_knowledge,isolation,...] [--allow-latency-fail] [flags]

What it does: creates an isolated temp database, authorizes the two seeded voices in it, starts its OWN API process on
--api-port (which spawns the LiveKit runtime worker), creates Ava and James from the Sales Representative template, then drives a
scripted human (pre-rendered wavs in assets/test_audio, never synthesized by the system under test) through the scenarios in
tests/e2e/scenarios.py. Everything it started is stopped at the end; leftovers are reported.

Scenarios (in run order): self_knowledge (TEST 4/5), isolation (TEST 6), ten_turn (TEST 9), latency (TEST 11), barge_in (TEST 10),
default_room (TEST 8), restart (TEST 13), failure (SOW 27 - stops and restarts the voice engine, so it always runs last).
`--only` runs a subset (scenarios that need a conversation launch Ava themselves).

Exit code 1 when any scenario FAILs. The SOW latency ceiling (P50 <= 700 ms, P95 <= 1500 ms T0->T4) cannot be met on a CPU-only
host (TTS first audio takes 15-30 s). With --allow-latency-fail a missed ceiling is reported as FAIL-ON-THIS-HOST (with the
measured bottleneck in latency.md) and does not fail the run; every other latency check (>= 10 turns, all intervals captured) must
still pass. Do not pass the flag on the GPU reference host.

Engine stop/start for the failure scenario follows --engine-control (powershell | script | compose; default from $ENGINE_CONTROL, else powershell on Windows and script elsewhere; compose uses $ENGINE_COMPOSE_SERVICE, default "engine").
All timeouts and thresholds are flags (see --help); the defaults are generous for CPU. Requires: LiveKit, Voicebox, the voice
engine and LLM_API_KEY (env or .env) to be up already.
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import subprocess
import sys
import tempfile
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402
from livekit import api as lk_api  # noqa: E402

from service015.config import Settings  # noqa: E402
from tests.e2e import scenarios  # noqa: E402
from tests.e2e.driver import Ctx, ScenarioResult  # noqa: E402
from tests.e2e.stack import ApiProcess, Config, engine_health, kill_tree, pid_alive  # noqa: F401  # noqa: E402

LATENCY_CHECK_PREFIX = "P50 T0->T4"


def parse_args() -> tuple[argparse.Namespace, Config]:
    cfg = Config()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", default="", help=f"comma-separated subset of: {', '.join(scenarios.ORDER)}")
    ap.add_argument("--allow-latency-fail", action="store_true", help="report a missed SOW latency ceiling as FAIL-ON-THIS-HOST without failing the run (CPU hosts)")
    ap.add_argument("--evidence-root", default=str(ROOT / "evidence"))
    for f in dataclasses.fields(Config):
        default = getattr(cfg, f.name)
        flag = "--" + f.name.replace("_", "-")
        if isinstance(default, str):
            ap.add_argument(flag, default=default)
        elif isinstance(default, int):
            ap.add_argument(flag, type=int, default=default)
        else:
            ap.add_argument(flag, type=float, default=default)
    args = ap.parse_args()
    cfg = Config(**{f.name: getattr(args, f.name) for f in dataclasses.fields(Config)})
    return args, cfg


async def preflight(cfg: Config) -> None:
    s = Settings(_env_file=ROOT / ".env")
    problems = []
    if not s.llm_api_key:
        problems.append("LLM_API_KEY is not set (env or .env)")
    eng = engine_health(cfg.engine_url)
    if not eng or eng.get("status") != "ok":
        problems.append(f"voice engine not ready at {cfg.engine_url}: {eng}")
    try:
        if httpx.get(cfg.voicebox_url + "/profiles", timeout=5).status_code != 200:
            problems.append("Voicebox /profiles did not answer 200")
    except httpx.HTTPError as exc:
        problems.append(f"Voicebox unreachable: {type(exc).__name__}")
    try:
        httpx.get(cfg.livekit_url.replace("ws://", "http://").replace("wss://", "https://"), timeout=5)
    except httpx.HTTPError as exc:
        problems.append(f"LiveKit unreachable: {type(exc).__name__}")
    if problems:
        raise SystemExit("preflight failed:\n  " + "\n  ".join(problems))


async def reset_rooms(cfg: Config) -> None:
    """Stale rooms from earlier runs would carry stale participants; start from empty rooms."""
    lk = lk_api.LiveKitAPI(cfg.livekit_url, cfg.livekit_key, cfg.livekit_secret)
    try:
        for room in ("ava-001", "james-001"):
            try:
                await lk.room.delete_room(lk_api.DeleteRoomRequest(room=room))
            except lk_api.TwirpError as exc:
                if exc.code != "not_found":  # no such room is the state we want
                    raise
    finally:
        await lk.aclose()


def leftovers(work: Path) -> list[str]:
    ps = (
        "Get-CimInstance Win32_Process | Where-Object { $_.Name -notlike 'powershell*' -and $_.CommandLine -and ($_.CommandLine -like '*service015.runtime*' "
        f"-or $_.CommandLine -like '*service015.api.app*' -or $_.CommandLine -like '*{work.name}*') }} | "
        "ForEach-Object { \"$($_.ProcessId) $($_.Name)\" }"
    )
    ps = ps.replace("}}", "}")
    out = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True).stdout
    return [line for line in out.splitlines() if line.strip()]


def summary_markdown(ts: str, results: list[ScenarioResult], cfg: Config, extra: dict) -> str:
    lines = [
        f"# Acceptance run {ts}", "",
        f"Host: {extra['host']}. Engine: {extra['engine']}. LLM: {extra['llm']}.", "",
        "| # | Scenario | SOW | Status | Duration | Failed checks |", "|---|---|---|---|---|---|",
    ]
    for i, r in enumerate(results, 1):
        failed = [c.name for c in r.checks if not c.ok]
        if r.error:
            failed.append(f"ERROR: {r.error.splitlines()[-1][:120]}")
        lines.append(f"| {i} | {r.name} | {r.test_ref} | **{r.status}** | {r.duration_s:.0f} s | {'; '.join(failed) or '-'} |")
    lines += ["", "## Checks", ""]
    for r in results:
        lines.append(f"### {r.name} ({r.status})")
        for c in r.checks:
            lines.append(f"- {'PASS' if c.ok else 'FAIL'}: {c.name}" + (f" -- {c.detail}" if c.detail else ""))
        if r.error:
            lines.append(f"- ERROR: `{r.error.splitlines()[-1]}`")
        lines.append("")
    lines += [
        "## Notes", "",
        "- Timeouts/thresholds used: " + ", ".join(f"{k}={v}" for k, v in dataclasses.asdict(cfg).items() if k.endswith(("_s", "_ms", "_threshold", "_count"))) + ".",
        "- FAIL-ON-THIS-HOST means the SOW latency ceiling was missed on this CPU-only host; the measured bottleneck is in latency.md.",
        f"- Leftover processes after teardown: {extra['leftovers'] or 'none'}.",
        f"- Engine after the run: {extra['engine_after']}.", "",
    ]
    return "\n".join(lines)


async def main_async(args: argparse.Namespace, cfg: Config) -> int:
    selected = [s.strip() for s in args.only.split(",") if s.strip()] or scenarios.ORDER
    unknown = [s for s in selected if s not in scenarios.SCENARIOS]
    if unknown:
        raise SystemExit(f"unknown scenario(s): {', '.join(unknown)}")
    selected = [s for s in scenarios.ORDER if s in selected]
    await preflight(cfg)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    evidence = Path(args.evidence_root) / ts
    raw = Path(args.evidence_root) / "raw" / ts
    evidence.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="s015-acceptance-"))
    print(f"evidence: {evidence}\nwork dir (isolated DB): {work}")
    await reset_rooms(cfg)
    auth = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "authorize_seed_voices.py"), "--db", str(work / "service015.db"), "--voicebox-url", cfg.voicebox_url],
        capture_output=True, text=True, cwd=ROOT,
    )
    print(auth.stdout.strip())
    if auth.returncode != 0:
        raise SystemExit(f"authorize_seed_voices failed: {auth.stderr.strip()}")

    api = ApiProcess(cfg, work, archive_dir=raw / "logs")
    ctx = Ctx(cfg, api, evidence, raw)
    results: list[ScenarioResult] = []
    engine_info = engine_health(cfg.engine_url, 30) or {}
    settings = Settings(_env_file=ROOT / ".env")
    try:
        api.start("main")
        await api.wait_healthy()
        for name in selected:
            res = ScenarioResult(name, "")
            t0 = time.monotonic()
            print(f"\n=== {name} ===", flush=True)
            try:
                res = await scenarios.SCENARIOS[name](ctx)
            except Exception as exc:  # a crashed scenario is a FAIL with the traceback as evidence, never a silent skip
                res.error = f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}"
                res.test_ref = res.test_ref or "-"
            res.duration_s = time.monotonic() - t0
            res.finalize()
            if res.name == "latency" and res.status == "FAIL" and not res.error and args.allow_latency_fail:
                failing = [c.name for c in res.checks if not c.ok]
                if failing and all(n.startswith(LATENCY_CHECK_PREFIX) for n in failing):
                    res.status = "FAIL-ON-THIS-HOST"
            results.append(res)
            ctx.write_json(f"{len(results)}-{res.name}.json", dataclasses.asdict(res))
            print(f"{res.name}: {res.status} ({res.duration_s:.0f}s)", flush=True)
            for c in res.checks:
                if not c.ok:
                    print(f"   FAIL {c.name}: {c.detail[:200]}")
            if res.error:
                print(res.error.splitlines()[-1] if res.error else "")
            if name != "failure":
                # keep the engine honest between scenarios: a dead engine would poison every later result
                if not engine_health(cfg.engine_url, 30):
                    print("WARNING: voice engine is not answering (30 s) after", name)
    finally:
        try:
            await scenarios.end_all_sessions(ctx)
        except Exception as exc:
            print(f"teardown: end_all_sessions failed: {type(exc).__name__}: {exc}")
        await ctx.cleanup()
        stop = await api.stop()
        print("API stop:", stop)
        await asyncio.sleep(2)
        left = leftovers(work)
        # reported, not killed: the match is by command line and could include a process someone else started
        extra = {
            "host": f"{sys.platform}, python {sys.version.split()[0]}", "llm": settings.llm_model,
            "engine": f"{engine_info.get('variant')} on {engine_info.get('device')}",
            "leftovers": left, "engine_after": engine_health(cfg.engine_url, 30),
        }
        (evidence / "summary.md").write_text(summary_markdown(ts, results, cfg, extra), encoding="utf-8")
    print("\n" + "=" * 70)
    print(f"{'SCENARIO':<22}{'SOW':<12}{'STATUS':<20}")
    for r in results:
        print(f"{r.name:<22}{r.test_ref:<12}{r.status:<20}")
    print(f"leftover processes: {left or 'none'}")
    bad = [r for r in results if r.status == "FAIL"]
    return 1 if bad else 0


def main() -> None:
    args, cfg = parse_args()
    sys.exit(asyncio.run(main_async(args, cfg)))


if __name__ == "__main__":
    main()
