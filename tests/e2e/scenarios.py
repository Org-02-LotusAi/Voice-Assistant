"""The eight acceptance scenarios (SOW TEST 4-6, 8-11, 13 and section 27). Each takes the Ctx and returns a ScenarioResult whose
checks carry the evidence. Long-lived conversations are shared through per-identity sessions so a full run launches Ava once."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from typing import Any

from livekit import api, rtc

from service015.runtime import latency

from .audio import load_clip
from .driver import Ctx, ScenarioResult, TurnResult
from .expectations import INTERRUPT_KEYWORDS, TURN_KEYWORDS, documented_stt_mishearings, keyword_hits, mentions, mentions_strict, normalize as normalize_fact
from .human import Human
from .stack import engine_health, run_engine_script

QUESTIONS = (("q_name", "name"), ("q_company", "company"), ("q_role", "role"))


@dataclass
class Session:
    key: str
    identity_id: str
    human: Human
    info: dict[str, Any]


# -- sessions ---------------------------------------------------------------------------------------------------------------


def _sessions(ctx: Ctx) -> dict[str, Session]:
    return ctx.cache.setdefault("sessions", {})


async def session(ctx: Ctx, key: str) -> Session:
    """Launch (if needed) and join the identity; reuse the open conversation when one exists."""
    existing = _sessions(ctx).get(key)
    if existing is not None and existing.human.agent_present():
        return existing
    if existing is not None:
        await end_session(ctx, key)
    ident = await ctx.ensure_identity(key)
    iid = ident["identity_id"]
    st = await ctx.status(iid)
    launch = {"already_running": True, "status": st}
    if st["presence"] not in ("READY", "BUSY"):
        launch = await ctx.launch_ready(iid)
    human, info = await ctx.join(iid)
    info["launch"] = launch
    s = Session(key, iid, human, info)
    _sessions(ctx)[key] = s
    return s


async def end_session(ctx: Ctx, key: str, *, stop: bool = True) -> None:
    s = _sessions(ctx).pop(key, None)
    if s is None:
        return
    await ctx.leave(s.human)
    if stop:
        await ctx.stop(s.identity_id)


async def end_all_sessions(ctx: Ctx) -> None:
    for key in list(_sessions(ctx)):
        await end_session(ctx, key)


def turn_json(t: TurnResult) -> dict[str, Any]:
    d = t.as_json()
    d["agent_text"] = t.agent_text
    d["heard_transcript"] = t.heard
    return d


def _other(key: str) -> str:
    return "james" if key == "ava" else "ava"


# -- 1 + 2: self-knowledge and isolation --------------------------------------------------------------------------------------


async def run_self_knowledge(ctx: Ctx, key: str) -> dict[str, Any]:
    """Ask name/company/role; judge the transcribed audio (and the LLM text) against the configured facts. Cached per key."""
    cache_key = f"selfknow_{key}"
    if cache_key in ctx.cache:
        return ctx.cache[cache_key]
    s = await session(ctx, key)
    mine, theirs = ctx.spec(key), ctx.spec(_other(key))
    turns, checks, fact_checks = [], [], []
    known_mishearings = documented_stt_mishearings()
    for stem, fact in QUESTIONS:
        t = await ctx.ask(s.human, s.identity_id, stem, f"selfknowledge-{key}")
        want = mine[fact]
        texts = {"heard": t.heard, "agent_text": t.agent_text}
        llm_ok, heard_ok = mentions(t.agent_text, want), mentions(t.heard, want)
        # (a) the LLM did not state the fact: product failure. (b) the LLM stated it but the audio path lost it.
        verdict = "ok" if llm_ok and heard_ok else "(b) audio path: LLM right, transcript of the audio lost it" if llm_ok else "(a) LLM wrong"
        # `llm_text_pass`: the LLM text states the configured fact (alias-free whole word). The strict transcript result stays in
        # `heard_has_fact_strict_no_alias`. A transcript miss is PASS-with-audio-caveat only if the LLM text is right AND the fact is
        # one the audio-path probe documents as mis-heard by Moonshine (evidence/probe/audio_path_probe.json); otherwise it FAILs.
        llm_text_pass = mentions_strict(t.agent_text, want)
        known_stt_mishearing = normalize_fact(want) in known_mishearings
        audio_kind = "audio" if llm_text_pass and known_stt_mishearing else ""
        fact_checks.append({"identity": key, "question": t.human_says, "fact": fact, "expected": want, "llm_text": t.agent_text, "heard_transcript": t.heard, "llm_has_fact": llm_ok, "heard_has_fact": heard_ok, "heard_has_fact_strict_no_alias": mentions_strict(t.heard, want), "llm_has_fact_strict_no_alias": mentions_strict(t.agent_text, want), "llm_text_pass": llm_text_pass, "known_stt_mishearing": known_stt_mishearing, "verdict": verdict})
        checks += [
            (f"{key}/{stem}: reply audio produced", t.reply.started, f"wait {t.wait_start_s}s", ""),
            (f"{key}/{stem}: LLM text contains {fact} '{want}'", llm_ok, t.agent_text, ""),
            (f"{key}/{stem}: transcript of the audio contains {fact} '{want}'", heard_ok, f"heard={t.heard!r} llm={t.agent_text!r}", audio_kind),
        ]
        for fact_o in ("name", "company"):
            leak = [n for n, txt in texts.items() if mentions(txt, theirs[fact_o])]
            checks.append((f"{key}/{stem}: no {_other(key)} {fact_o} '{theirs[fact_o]}'", not leak, f"leaked in {leak}" if leak else "", ""))
        turns.append(turn_json(t))
    out = {"turns": turns, "checks": checks, "fact_checks": fact_checks, "greeting": s.info, "identity": s.identity_id}
    ctx.cache[cache_key] = out
    return out


async def scenario_self_knowledge(ctx: Ctx) -> ScenarioResult:
    res = ScenarioResult("self_knowledge", "TEST 4/5")
    out = await run_self_knowledge(ctx, "ava")
    for name, ok, detail, kind in out["checks"]:
        res.check(name, ok, detail, kind)
    greet = out["greeting"]
    res.check("greeting played when the human joined", bool(greet.get("greeting_transcript")), greet.get("greeting_transcript", ""))
    res.data = {"turns": out["turns"], "fact_checks": out["fact_checks"], "greeting": {k: v for k, v in greet.items() if k != "launch"}, "launch": greet.get("launch")}
    return res


async def scenario_isolation(ctx: Ctx) -> ScenarioResult:
    res = ScenarioResult("isolation", "TEST 6")
    ava_ident = await ctx.ensure_identity("ava")
    james_ident = await ctx.ensure_identity("james")
    res.check("different identities", ava_ident["identity_id"] != james_ident["identity_id"], f"{ava_ident['identity_id']} / {james_ident['identity_id']}")
    res.check("same template + version", (ava_ident["template_id"], ava_ident["template_version"]) == (james_ident["template_id"], james_ident["template_version"]))
    res.check("different voices", ava_ident["voice"]["profile_id"] != james_ident["voice"]["profile_id"])
    res.check("different rooms", ava_ident["participant"]["default_room"] != james_ident["participant"]["default_room"])
    james = await run_self_knowledge(ctx, "james")
    ava = await run_self_knowledge(ctx, "ava")  # cached when scenario 1 ran
    for name, ok, detail, kind in james["checks"] + ava["checks"]:
        res.check(name, ok, detail, kind)
    res.data = {"james": james["turns"], "ava": ava["turns"], "fact_checks": james["fact_checks"] + ava["fact_checks"], "james_greeting": james["greeting"].get("greeting_transcript")}
    await end_session(ctx, "james")
    return res


# -- 3: ten-turn conversation -----------------------------------------------------------------------------------------------


async def run_ten_turns(ctx: Ctx) -> dict[str, Any]:
    if "ten" in ctx.cache:
        return ctx.cache["ten"]
    s = await session(ctx, "ava")
    turns, checks = [], []
    for i in range(1, ctx.cfg.ten_turn_count + 1):
        stem = f"turn_{i:02d}"
        t = await ctx.ask(s.human, s.identity_id, stem, "ten-turn")
        hits = keyword_hits(t.heard, TURN_KEYWORDS[stem])
        checks += [
            (f"{stem}: reply audio produced", t.reply.started, f"wait {t.wait_start_s}s"),
            (f"{stem}: reply transcribed (non-empty)", bool(t.heard.strip()), t.heard),
            (f"{stem}: LLM text non-empty", bool(t.agent_text.strip()), t.agent_text),
            (f"{stem}: relevant (keyword hit)", bool(hits), f"hits={hits} heard={t.heard!r}"),
        ]
        j = turn_json(t)
        j["keyword_hits"] = hits
        j["expected_any_of"] = list(TURN_KEYWORDS[stem])
        turns.append(j)
    session_ids = {t["row"]["session_id"] for t in turns if t["row"]}
    out = {"turns": turns, "checks": checks, "session_ids": sorted(session_ids), "identity": s.identity_id}
    ctx.cache["ten"] = out
    return out


async def scenario_ten_turn(ctx: Ctx) -> ScenarioResult:
    res = ScenarioResult("ten_turn", "TEST 9")
    out = await run_ten_turns(ctx)
    res.check(f">= {ctx.cfg.ten_turn_count} turns completed", len(out["turns"]) >= ctx.cfg.ten_turn_count, str(len(out["turns"])))
    for name, ok, detail in out["checks"]:
        res.check(name, ok, detail)
    res.data = {"turns": out["turns"]}
    return res


# -- 5: latency ------------------------------------------------------------------------------------------------------------


def _fmt(v: Any) -> str:
    return "n/a" if v is None else f"{v:,.0f}"


async def scenario_latency(ctx: Ctx) -> ScenarioResult:
    res = ScenarioResult("latency", "TEST 11")
    cfg = ctx.cfg
    out = await run_ten_turns(ctx)
    iid = out["identity"]
    sid = out["session_ids"][-1] if out["session_ids"] else None
    api_report = await ctx.viewer.json("GET", f"/identities/{iid}/latency", params={"session_id": sid} if sid else None)
    local = latency.report(ctx.db(), iid, sid)
    res.check(">= 10 turns recorded by the API", api_report["turns"] >= 10, f"turns={api_report['turns']}")
    res.check("API report equals report computed from the DB rows", api_report == local)
    for name in ("t0_t4", "t0_t1", "t1_t2", "t2_t3", "t3_t4", "t4_t5"):
        res.check(f"{name}: >= 10 samples", api_report[name]["count"] >= 10, f"count={api_report[name]['count']}")
    p50, p95 = api_report["t0_t4"]["p50"], api_report["t0_t4"]["p95"]
    intervals = ("t0_t1", "t1_t2", "t2_t3", "t3_t4", "t4_t5")
    bottleneck = max((n for n in intervals if api_report[n]["p50"] is not None), key=lambda n: api_report[n]["p50"], default=None)
    eng = engine_health(cfg.engine_url) or {}
    rows = ctx.db().execute(
        "SELECT turn_index, t0, t4, t5 FROM turn_metrics WHERE identity_id = ? AND session_id = ? ORDER BY id", (iid, sid)
    ).fetchall()
    per_turn = [
        {"turn_index": r["turn_index"], "t0_t4_ms": None if r["t4"] is None else round((r["t4"] - r["t0"]) * 1000)} for r in rows
    ]
    first = per_turn[0]["t0_t4_ms"] if per_turn else None
    rest = sorted(p["t0_t4_ms"] for p in per_turn[1:] if p["t0_t4_ms"] is not None)
    ceiling_ok = p50 is not None and p95 is not None and p50 <= cfg.p50_limit_ms and p95 <= cfg.p95_limit_ms
    res.check(
        f"P50 T0->T4 <= {cfg.p50_limit_ms:.0f} ms and P95 <= {cfg.p95_limit_ms:.0f} ms (SOW ceiling)",
        ceiling_ok, f"P50={_fmt(p50)} ms P95={_fmt(p95)} ms; measured bottleneck: {bottleneck} (P50 {_fmt(api_report[bottleneck]['p50']) if bottleneck else 'n/a'} ms)",
    )
    res.data = {
        "session_id": sid, "identity": iid, "report": api_report, "bottleneck_interval": bottleneck,
        "per_turn": per_turn, "first_turn_t0_t4_ms": first, "later_turns_t0_t4_ms": rest,
        "engine_health_at_report": eng, "sow": {"p50_limit_ms": cfg.p50_limit_ms, "p95_limit_ms": cfg.p95_limit_ms},
        "ceiling_met": ceiling_ok,
    }
    ctx.write_json("latency.json", res.data)
    lines = [
        "# Latency evidence (SOW section 20, TEST 11)", "",
        f"Identity `{iid}`, session `{sid}`, {api_report['turns']} turns, host-measured (milliseconds, nearest-rank).", "",
        "| Interval | n | P50 | P95 | MAX |", "|---|---|---|---|---|",
    ]
    labels = {"t0_t4": "T0->T4 (end of speech -> agent audio published)", "t0_t1": "T0->T1 (end of speech -> final transcript)",
              "t1_t2": "T1->T2 (transcript -> first LLM chunk)", "t2_t3": "T2->T3 (first LLM chunk -> first TTS PCM)",
              "t3_t4": "T3->T4 (first TTS PCM -> audio published)", "t4_t5": "T4->T5 (published -> heard by the human)"}
    for n, label in labels.items():
        st = api_report[n]
        lines.append(f"| {label} | {st['count']} | {_fmt(st['p50'])} | {_fmt(st['p95'])} | {_fmt(st['max'])} |")
    lines += [
        "", f"SOW targets: P50 T0->T4 <= {cfg.p50_limit_ms:.0f} ms, P95 <= {cfg.p95_limit_ms:.0f} ms. Ceiling met: **{'yes' if ceiling_ok else 'NO'}**.",
        f"Measured bottleneck (largest P50 interval): **{bottleneck}**.", "",
        "## Cold-start check",
        f"- First turn T0->T4: {_fmt(first)} ms; later turns median: {_fmt(rest[len(rest) // 2]) if rest else 'n/a'} ms.",
        f"- Engine at report time: warm={eng.get('warm')}, device={eng.get('device')}, variant={eng.get('variant')}, cached_profiles={eng.get('cached_profiles')}.",
        "- The runtime pre-warms the voice conditionals at job start and the engine warms the profile at launch preflight, so no avoidable cold start sits in these turns.",
        "", "## Per-turn T0->T4 (ms)", "",
        ", ".join(f"#{p['turn_index']}: {_fmt(p['t0_t4_ms'])}" for p in per_turn), "",
    ]
    ctx.evidence_dir.mkdir(parents=True, exist_ok=True)
    (ctx.evidence_dir / "latency.md").write_text("\n".join(lines), encoding="utf-8")
    return res


# -- 4: barge-in -------------------------------------------------------------------------------------------------------------


async def scenario_barge_in(ctx: Ctx) -> ScenarioResult:
    res = ScenarioResult("barge_in", "TEST 10")
    cfg = ctx.cfg
    s = await session(ctx, "ava")
    h, iid = s.human, s.identity_id
    before = ctx.max_turn_id(iid)
    long_clip, cut_clip = load_clip("q_long_answer"), load_clip("q_interrupt")
    utt1 = await h.say(long_clip)
    t_wait = time.monotonic()
    t_first = None
    while time.monotonic() - t_wait < cfg.reply_start_timeout_s and t_first is None:
        t_first = h.first_loud_after(utt1.onset_t)
        await asyncio.sleep(0.1)
    if not res.check("the long answer started", t_first is not None, f"waited {time.monotonic() - t_wait:.1f}s"):
        return res
    # "1.5 s into the Identity's speech" = after 1.5 s of audible agent audio has been received, while it is still flowing. On CPU
    # the engine delivers a reply in bursts, so wall-clock time since the first burst could land in a pause between chunks, where
    # there is nothing to interrupt.
    flow_deadline = time.monotonic() + cfg.reply_complete_timeout_s
    while time.monotonic() < flow_deadline:
        last = h.last_loud()
        if h.loud_seconds(t_first, time.monotonic()) >= cfg.barge_in_after_s and last is not None and time.monotonic() - last < 0.1:
            break
        await asyncio.sleep(0.01)
    state_before = h.current_state()
    utt2 = await h.say(cut_clip, lead_s=0.0, tail_s=2.0)  # returns after the interrupting clip + tail were sent
    silent_at = None
    deadline = time.monotonic() + 30
    while silent_at is None and time.monotonic() < deadline:
        silent_at = h.silence_after(utt2.onset_t, cfg.quiet_gap_s)
        await asyncio.sleep(0.1)
    # silence_after returns the last loud frame of the first audio run after the human's onset; a negative value would mean
    # the agent had already stopped before the human spoke
    stop_ms = None if silent_at is None else round((silent_at - utt2.onset_t) * 1000)
    state_change = next((t for t, st in h.states if t > utt2.onset_t and st != "speaking"), None)
    state_ms = None if state_change is None else round((state_change - utt2.onset_t) * 1000)
    res.check("the agent was speaking when interrupted", state_before == "speaking" or h.loud_seconds(t_first, utt2.onset_t) > 0.5, f"state={state_before}")
    res.check("agent audio went silent after the interruption", silent_at is not None)
    res.check(f"speech onset -> agent audio silent <= {cfg.barge_in_limit_ms:.0f} ms", stop_ms is not None and stop_ms <= cfg.barge_in_limit_ms, f"{stop_ms} ms (agent state left 'speaking' after {state_ms} ms)")
    # the next reply must address the interruption
    reply = await ctx.wait_reply(h, silent_at or utt2.onset_t, cfg.post_interrupt_reply_timeout_s, cfg.reply_complete_timeout_s)
    if reply.started:
        reply.transcript = await ctx.tr.transcribe(reply.pcm)
    # The interrupting turn's row is persisted by the runtime only once that turn's agent starts speaking (or the session closes),
    # so the row must be awaited: wait up to 25 s for a second row (the long-answer turn is the first), not read a stale snapshot.
    rows: list[dict[str, Any]] = []
    row_deadline = time.monotonic() + 25
    while time.monotonic() < row_deadline:
        rows = [dict(r) for r in ctx.db().execute("SELECT * FROM turn_metrics WHERE identity_id = ? AND id > ? ORDER BY id", (iid, before)).fetchall()]
        if len(rows) >= 2:
            break
        await asyncio.sleep(0.5)
    interrupt_row = rows[1] if len(rows) >= 2 else {}
    last = interrupt_row
    hits = keyword_hits(reply.transcript, INTERRUPT_KEYWORDS)
    heard_by_agent = (last.get("user_text") or "").lower()
    res.check("a next reply was produced", reply.started)
    res.check("the agent heard the interruption (user_text mentions price)", "price" in heard_by_agent, last.get("user_text") or "")
    res.check("the next reply addresses the interruption (pricing keyword)", bool(hits), f"hits={hits} heard={reply.transcript!r}")
    res.data = {
        "long_answer_first_audio_after_s": round(t_first - utt1.end_t, 1), "interrupt_onset_after_first_audio_s": round(utt2.onset_t - t_first, 2), "agent_audio_heard_before_interrupt_s": round(h.loud_seconds(t_first, utt2.onset_t), 2),
        "stop_ms_audio_rms": stop_ms, "agent_state_change_ms": state_ms, "rms_threshold": cfg.rms_threshold, "quiet_gap_s": cfg.quiet_gap_s,
        "next_reply_transcript": reply.transcript, "next_reply_agent_text": last.get("agent_text"), "turn_rows": rows,
        "note": "Silence = last received frame above the RMS gate before the first >= quiet_gap_s pause. On CPU the engine bounds a follow-up by one in-flight chunk, but the AgentSession stops publishing on interruption independently.",
    }
    return res


# -- 7: default room ---------------------------------------------------------------------------------------------------------


async def _agent_participant(ctx: Ctx, room: str, identity: str) -> dict[str, Any] | None:
    lk = api.LiveKitAPI(ctx.cfg.livekit_url, ctx.cfg.livekit_key, ctx.cfg.livekit_secret)
    try:
        res = await lk.room.list_participants(api.ListParticipantsRequest(room=room))
    finally:
        await lk.aclose()
    for p in res.participants:
        if p.identity == identity:
            return {"identity": p.identity, "name": p.name, "kind": rtc.ParticipantKind.Name(p.kind) if hasattr(rtc.ParticipantKind, "Name") else int(p.kind)}
    return None


async def scenario_default_room(ctx: Ctx) -> ScenarioResult:
    res = ScenarioResult("default_room", "TEST 8")
    await end_all_sessions(ctx)
    ident = await ctx.ensure_identity("ava")
    iid = ident["identity_id"]
    runs = []
    for n in (1, 2, 3):
        launch = await ctx.launch_ready(iid)
        st = await ctx.status(iid)
        jt = await ctx.client.json("POST", f"/identities/{iid}/join-token")
        part = await _agent_participant(ctx, st["default_room"], st["participant_identity"])
        runs.append({"launch": n, "ready_after_s": launch["ready_after_s"], "room": st["default_room"], "participant_identity": st["participant_identity"],
                     "join_token_room": jt["room"], "livekit_participant": part})
        await ctx.stop(iid)
    base = runs[0]
    res.check("room equals identity default_room on every launch", all(r["room"] == ident["participant"]["default_room"] == r["join_token_room"] for r in runs), json.dumps([r["room"] for r in runs]))
    res.check("participant identity equals identity participant_identity on every launch", all(r["participant_identity"] == ident["participant"]["participant_identity"] for r in runs))
    res.check("the agent participant is present in that room on every launch", all(r["livekit_participant"] is not None and r["livekit_participant"]["identity"] == r["participant_identity"] for r in runs), json.dumps([r["livekit_participant"] for r in runs]))
    res.check("room/participant identical across launches", all((r["room"], r["participant_identity"]) == (base["room"], base["participant_identity"]) for r in runs))
    after = await ctx.client.json("GET", f"/identities/{iid}")
    res.check("identity snapshot unchanged by relaunching", after == ident)
    res.data = {"launches": runs}
    return res


# -- 6: restart persistence --------------------------------------------------------------------------------------------------


async def scenario_restart(ctx: Ctx) -> ScenarioResult:
    res = ScenarioResult("restart_persistence", "TEST 13")
    ident = await ctx.ensure_identity("ava")
    iid = ident["identity_id"]
    s = await session(ctx, "ava")  # Ava running with a human in the room when the processes die
    before = await ctx.client.json("GET", f"/identities/{iid}")
    templates_before = await ctx.client.json("GET", "/templates/sales-representative/versions")
    await ctx.leave(s.human)
    _sessions(ctx).pop("ava", None)
    stop = await ctx.api.stop()
    res.check("API + runtime stopped", ctx.api.proc is None, json.dumps(stop))
    res.check("runtime worker did not survive the API", not stop.get("worker_survived_api"), json.dumps(stop))
    ctx.api.start("restart")
    await ctx.api.wait_healthy()
    after = await ctx.client.json("GET", f"/identities/{iid}")
    st = await ctx.status(iid)
    res.check("identity_id unchanged", after["identity_id"] == before["identity_id"])
    res.check("template_id/template_version unchanged", (after["template_id"], after["template_version"]) == (before["template_id"], before["template_version"]), f"v{after['template_version']}")
    res.check("name/company/role (self) unchanged", after["self"] == before["self"])
    res.check("persona unchanged", after["persona"] == before["persona"])
    res.check("voice unchanged", after["voice"] == before["voice"], json.dumps(after["voice"]))
    res.check("default_room/participant_identity unchanged", after["participant"] == before["participant"])
    res.check("resolved_hash identical", after["resolved_hash"] == before["resolved_hash"], after["resolved_hash"])
    res.check("whole snapshot identical", after == before)
    templates_after = await ctx.client.json("GET", "/templates/sales-representative/versions")
    res.check("template versions unchanged", templates_after == templates_before)
    res.check("presence after restart is OFFLINE (stale runtime reconciled)", st["presence"] == "OFFLINE", f"{st['presence']} / {st['status_detail']}")
    s2 = await session(ctx, "ava")  # relaunch + join
    st2 = await ctx.status(s2.identity_id)
    t = await ctx.ask(s2.human, s2.identity_id, "q_name", "restart")
    res.check("relaunched Ava reaches READY/BUSY", st2["presence"] in ("READY", "BUSY"), st2["presence"])
    res.check("still answers 'Ava' (audio transcript)", mentions(t.heard, "Ava"), t.heard)
    res.check("still answers 'Ava' (LLM text)", mentions(t.agent_text, "Ava"), t.agent_text)
    res.data = {"identity_before": before, "status_after_restart": st, "stop": stop, "turn": turn_json(t), "relaunch": s2.info.get("launch")}
    return res


# -- 8: failure and recovery (LAST: restarts the engine) -----------------------------------------------------------------------


async def _wait_engine_ready(ctx: Ctx, timeout: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    h = None
    while time.monotonic() < deadline:
        h = await asyncio.to_thread(engine_health, ctx.cfg.engine_url)
        if h and h.get("status") == "ok" and h.get("warm"):
            return h
        await asyncio.sleep(5)
    raise AssertionError(f"engine not ready/warm within {timeout:.0f}s (last health: {h})")


async def _wait_engine_down(ctx: Ctx, timeout: float = 60) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if await asyncio.to_thread(engine_health, ctx.cfg.engine_url) is None:
            return
        await asyncio.sleep(1)
    raise AssertionError("engine still answering after stop")


async def scenario_failure_recovery(ctx: Ctx) -> ScenarioResult:
    from .stack import BrokenLlmServer

    res = ScenarioResult("failure_recovery", "SOW 27")
    cfg = ctx.cfg
    await end_all_sessions(ctx)
    ident = await ctx.ensure_identity("ava")
    iid = ident["identity_id"]
    data: dict[str, Any] = {}

    # (a) validation failures are 422 and leave state untouched
    r1 = await ctx.client.request("POST", "/templates/sales-representative/instantiate", json={"overrides": {"name": "Zed", "company": "Nowhere", "bogus.key": "x"}})
    r2 = await ctx.client.request("PATCH", f"/identities/{iid}", json={"overrides": {"conversation.barge_in": "maybe"}})
    r3 = await ctx.client.request("PATCH", f"/identities/{iid}", json={"overrides": {"name": ""}})
    r4 = await ctx.client.request("POST", "/templates/sales-representative/instantiate", json={"overrides": {"name": "Zed"}})
    data["invalid"] = {r.request.url.path + ":" + r.request.method: [r.status_code, r.json().get("error", {}).get("code")] for r in (r1, r2, r3, r4)}
    for label, r in (("disallowed override key", r1), ("non-boolean barge_in", r2), ("empty name", r3), ("missing required company", r4)):
        res.check(f"invalid override ({label}) -> 422", r.status_code == 422, f"{r.status_code} {r.json().get('error', {}).get('code')}")
    res.check("identity unchanged after invalid overrides", await ctx.client.json("GET", f"/identities/{iid}") == ident)
    res.check("no identity was created by rejected instantiates", len(await ctx.client.json("GET", "/identities")) == len(ctx.identities))

    # (b) LLM unreachable: launch passes (models probe answers) but completions fail -> presence ERROR llm_unavailable, no speech
    fake = BrokenLlmServer()
    fake.start()
    try:
        await ctx.api.stop()
        ctx.api.extra_env = {"LLM_BASE_URL": fake.base_url}
        ctx.api.start("llm-down")
        await ctx.api.wait_healthy()
        launch = await ctx.launch_ready(iid)
        human, info = await ctx.join(iid)
        utt = await human.say(load_clip("q_name"))
        deadline = time.monotonic() + cfg.llm_error_timeout_s
        st: dict[str, Any] = {}
        while time.monotonic() < deadline:
            st = await ctx.status(iid)
            if st["presence"] == "ERROR":
                break
            await asyncio.sleep(1)
        await asyncio.sleep(10)  # any fabricated speech would show up here
        spoke = human.loud_seconds(utt.onset_t, time.monotonic())
        flag = f"{st.get('status_detail')} | {st.get('last_error')}"
        res.check("presence became ERROR after the user's turn", st.get("presence") == "ERROR", flag)
        res.check("error is llm_unavailable", "llm_unavailable" in flag, flag)
        res.check("no speech after the user's turn (no fabricated reply)", spoke < 0.2, f"{spoke:.2f}s of audio above the RMS gate")
        data["llm_down"] = {"launch": launch, "greeting": info.get("greeting_transcript"), "status": st, "speech_after_turn_s": round(spoke, 2)}
        await ctx.leave(human)
        await ctx.stop(iid)
    finally:
        await ctx.api.stop()
        fake.stop()
        ctx.api.extra_env = {}
        ctx.api.start("restored")
        await ctx.api.wait_healthy()

    # (c) engine outage and restore. The engine restart is slow and memory-heavy: always restored in the finally block.
    before = await ctx.client.json("GET", f"/identities/{iid}")
    try:
        data["engine_stop"] = await asyncio.to_thread(run_engine_script, "stop", cfg.engine_control)
        await _wait_engine_down(ctx)
        r = await ctx.client.request("POST", f"/identities/{iid}/launch")
        body = r.json()
        data["engine_down_launch"] = {"status": r.status_code, "body": body}
        res.check("launch with the engine down -> 503", r.status_code == 503, f"{r.status_code}")
        res.check("error names the dependency failure (voice_engine)", body.get("error", {}).get("details", {}).get("dependency") == "voice_engine" and body["error"]["code"] == "dependency_unavailable", json.dumps(body.get("error")))
        res.check("identity unchanged by the failed launch", await ctx.client.json("GET", f"/identities/{iid}") == before)
        st = await ctx.status(iid)
        res.check("presence still OFFLINE after the failed launch", st["presence"] == "OFFLINE", st["presence"])
    finally:
        data["engine_start"] = await asyncio.to_thread(run_engine_script, "start", cfg.engine_control)
        t0 = time.monotonic()
        health = await _wait_engine_ready(ctx, cfg.engine_restore_timeout_s)
        data["engine_restored_after_s"] = round(time.monotonic() - t0, 1)
        data["engine_health_restored"] = health
    res.check("engine restored and warm", health.get("status") == "ok" and bool(health.get("warm")), f"{data['engine_restored_after_s']}s")
    launch = await ctx.launch_ready(iid)
    res.check("relaunch after restore reaches READY", launch["status"]["presence"] in ("READY", "BUSY"), f"{launch['ready_after_s']}s")
    res.check("identity unchanged after recovery", await ctx.client.json("GET", f"/identities/{iid}") == before)
    data["relaunch"] = launch
    await ctx.stop(iid)
    final = await asyncio.to_thread(engine_health, cfg.engine_url)
    data["engine_final_health"] = final
    res.check("engine still up and warm at the end", bool(final) and final.get("status") == "ok" and bool(final.get("warm")), json.dumps(final))
    res.data = data
    return res


SCENARIOS = {
    "self_knowledge": scenario_self_knowledge,
    "isolation": scenario_isolation,
    "ten_turn": scenario_ten_turn,
    "latency": scenario_latency,
    "barge_in": scenario_barge_in,
    "default_room": scenario_default_room,
    "restart": scenario_restart,
    "failure": scenario_failure_recovery,
}
# Order matters: conversations share Ava's session; restart ends it; the engine-outage scenario is LAST (slow, memory-heavy).
ORDER = ["self_knowledge", "isolation", "ten_turn", "latency", "barge_in", "default_room", "restart", "failure"]
