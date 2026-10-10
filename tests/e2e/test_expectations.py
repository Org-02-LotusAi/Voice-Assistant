"""Unit tests for the harness's matching rules (no services needed)."""

from tests.e2e.expectations import INTERRUPT_KEYWORDS, TURN_KEYWORDS, keyword_hits, mentions


def test_name_matches_whole_word_and_aliases():
    assert mentions("My name is Ava.", "Ava")
    assert mentions("my name is eva", "Ava")
    assert not mentions("I am available to help", "Ava")  # never a substring match


def test_company_alias_and_foreign_company():
    assert mentions("I work for Saafi", "Safi")
    assert mentions("I work for North Wind.", "Northwind")
    assert not mentions("I work for Safi", "Northwind")


def test_role_needs_every_word_plural_tolerant():
    assert mentions("I'm an account specialist here", "Account Specialist")
    assert mentions("I'm a solutions consultant", "Solutions Consultant")
    assert mentions("one of the Account Specialists", "Account Specialist")
    assert not mentions("I'm a specialist", "Account Specialist")


def test_keyword_hits_are_word_prefixes():
    assert keyword_hits("We offer pricing tiers", INTERRUPT_KEYWORDS) == ["pric", "tier"]
    assert keyword_hits("nothing relevant", TURN_KEYWORDS["turn_09"]) == []
    assert keyword_hits("onboarding takes a week", TURN_KEYWORDS["turn_03"])


def test_engine_control_dispatch():
    import pytest

    from tests.e2e.stack import engine_command, engine_control_mode

    assert engine_control_mode("auto", {}, "win32") == "powershell"
    assert engine_control_mode("auto", {}, "linux") == "script"
    assert engine_control_mode("auto", {"ENGINE_CONTROL": "compose"}, "linux") == "compose"
    assert engine_control_mode("script", {"ENGINE_CONTROL": "compose"}, "win32") == "script"  # the flag wins
    with pytest.raises(ValueError):
        engine_control_mode("auto", {"ENGINE_CONTROL": "bogus"}, "linux")
    ps = engine_command("stop", "powershell")
    assert ps[0] == "powershell" and ps[-2:] == ["-Action", "stop"] and ps[-3].endswith("run_engine.ps1")
    sh = engine_command("start", "script")
    assert sh[0] == "bash" and sh[1].endswith("run_engine.sh") and sh[2] == "start"
    assert engine_command("stop", "compose", {}) == ["docker", "compose", "stop", "engine"]
    assert engine_command("start", "compose", {"ENGINE_COMPOSE_SERVICE": "voice"}) == ["docker", "compose", "start", "voice"]
    with pytest.raises(ValueError):
        engine_command("restart", "script")


def test_strict_match_has_no_aliases():
    from tests.e2e.expectations import mentions_strict

    assert mentions_strict("I work for Safi.", "Safi")
    assert not mentions_strict("I work for Saffy.", "Safi")
    assert mentions("I work for Saffy.", "Safi")  # the documented alias still passes the aliased check


def test_documented_stt_mishearings_come_from_the_probe_evidence(tmp_path):
    import json

    from tests.e2e.expectations import PROBE_PATH, documented_stt_mishearings

    real = documented_stt_mishearings()
    assert "safi" in real  # the invented brand the probe records as mis-heard (Safiq / Saffee)
    assert "ava" not in real  # heard correctly in every probe run: a miss on "Ava" is a real FAIL, not a caveat
    assert PROBE_PATH.exists()
    probe = tmp_path / "p.json"
    probe.write_text(json.dumps([{"fact": "Zed", "fact_heard": False}, {"fact": "Ava", "fact_heard": True}]), encoding="utf-8")
    assert documented_stt_mishearings(probe) == {"zed"}
    assert documented_stt_mishearings(tmp_path / "missing.json") == set()  # nothing excused without evidence
