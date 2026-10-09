import re

import pytest
from pydantic import ValidationError

from service015.db import connect, migrate
from service015.errors import DomainError, NotFound
from service015.models import TemplateCreate
from service015.templates_repo import (
    create_template,
    get_template,
    list_templates,
    list_versions,
    seed_starter_templates,
)

STARTERS = {"generic-assistant", "sales-representative", "customer-support"}


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "t.db")
    migrate(c)
    return c


def _raw_rows(conn):
    return [tuple(r) for r in conn.execute("SELECT * FROM template_versions ORDER BY template_id, version")]


def test_three_starters_seeded_and_distinct(conn):
    assert sorted(seed_starter_templates(conn)) == sorted(STARTERS)
    summaries = list_templates(conn)
    assert {s.template_id for s in summaries} == STARTERS
    assert all(s.latest_version == 1 for s in summaries)
    bodies = [get_template(conn, t) for t in STARTERS]
    assert len({b.persona.instructions for b in bodies}) == 3
    assert len({b.persona.objective for b in bodies}) == 3
    assert len({b.persona.style for b in bodies}) == 3
    assert len({b.conversation.greeting for b in bodies}) == 3
    assert len({b.conversation.speaking_style for b in bodies}) == 3
    for b in bodies:
        assert b.voice.engine == "chatterbox-turbo"
        assert b.voice.default_voice_profile_id is None
        assert b.version == 1


def test_required_fields_per_starter(conn):
    seed_starter_templates(conn)
    assert get_template(conn, "generic-assistant").required_instance_fields == ["name"]
    assert get_template(conn, "sales-representative").required_instance_fields == ["name", "company"]
    assert get_template(conn, "customer-support").required_instance_fields == ["name", "company"]


def test_starter_text_is_speech_safe_and_placeholders_limited(conn):
    seed_starter_templates(conn)
    for tid in STARTERS:
        b = get_template(conn, tid)
        texts = [b.persona.objective, b.persona.style, b.persona.instructions, b.conversation.greeting, b.conversation.speaking_style]
        for t in texts:
            assert not re.search(r"^\s*([-*#]|\d+\.)\s", t, re.M), f"{tid}: list/markdown syntax"
            assert "**" not in t and "`" not in t
        used = set(re.findall(r"\{(\w+)\}", b.conversation.greeting + b.persona.instructions))
        assert used <= {"name", "company", "role"}
        assert "{name}" in b.conversation.greeting
        if "company" in b.required_instance_fields:
            assert "{company}" in b.conversation.greeting
        for t in (b.persona.objective, b.persona.style, b.conversation.speaking_style):
            assert "{" not in t


def test_seed_twice_is_noop(conn):
    seed_starter_templates(conn)
    before = _raw_rows(conn)
    assert seed_starter_templates(conn) == []
    assert _raw_rows(conn) == before


def test_seed_never_overwrites_modified_template(conn):
    seed_starter_templates(conn)
    v1 = get_template(conn, "sales-representative")
    create_template(conn, TemplateCreate(**{**v1.model_dump(exclude={"version", "created_at"}), "name": "Custom"}))
    assert seed_starter_templates(conn) == []
    assert get_template(conn, "sales-representative").name == "Custom"
    assert list_versions(conn, "sales-representative") == [1, 2]


def test_new_version_leaves_v1_byte_identical(conn):
    seed_starter_templates(conn)
    v1_before = conn.execute(
        "SELECT body_json, created_at FROM template_versions WHERE template_id='sales-representative' AND version=1"
    ).fetchone()
    base = get_template(conn, "sales-representative")
    payload = base.model_dump(exclude={"version", "created_at"})
    payload["persona"]["instructions"] = "Brand new instructions for the second version."
    v2 = create_template(conn, TemplateCreate(**payload))
    assert v2.version == 2
    assert list_versions(conn, "sales-representative") == [1, 2]
    v1_after = conn.execute(
        "SELECT body_json, created_at FROM template_versions WHERE template_id='sales-representative' AND version=1"
    ).fetchone()
    assert tuple(v1_after) == tuple(v1_before)
    assert get_template(conn, "sales-representative", 1) == base
    assert get_template(conn, "sales-representative").persona.instructions.startswith("Brand new")
    summary = {s.template_id: s for s in list_templates(conn)}["sales-representative"]
    assert summary.latest_version == 2
    # other templates untouched
    assert list_versions(conn, "customer-support") == [1]


def test_create_new_template_is_v1(conn):
    seed_starter_templates(conn)
    base = get_template(conn, "generic-assistant").model_dump(exclude={"version", "created_at"})
    base["template_id"] = "concierge"
    body = create_template(conn, TemplateCreate(**base))
    assert body.version == 1 and body.template_id == "concierge"
    assert {s.template_id for s in list_templates(conn)} == STARTERS | {"concierge"}


def test_invalid_allowed_override_key_rejected(conn):
    base = {
        "template_id": "bad",
        "name": "Bad",
        "description": "d",
        "persona": {"objective": "o", "style": "s", "instructions": "i"},
        "conversation": {"greeting": "g", "speaking_style": "s"},
    }
    with pytest.raises(ValidationError, match="unknown keys"):
        TemplateCreate(**base, allowed_overrides=["name", "persona.secret"])
    with pytest.raises(ValidationError, match="must also be listed"):
        TemplateCreate(**base, allowed_overrides=["name"], required_instance_fields=["company"])
    with pytest.raises(ValidationError):
        TemplateCreate(**{**base, "template_id": "Bad Id"})
    assert TemplateCreate(**base, allowed_overrides=["name"], required_instance_fields=["name"])


def test_get_and_versions_not_found(conn):
    seed_starter_templates(conn)
    with pytest.raises(NotFound) as e:
        get_template(conn, "nope")
    assert e.value.status == 404 and e.value.code == "template_not_found"
    with pytest.raises(NotFound):
        get_template(conn, "sales-representative", 9)
    with pytest.raises(NotFound):
        list_versions(conn, "nope")
    assert isinstance(e.value, DomainError)


def _valid_payload(**over):
    base = {
        "template_id": "custom",
        "name": "Custom",
        "description": "d",
        "default_self_facts": {"role": "Helper", "company": None},
        "persona": {"objective": "o", "style": "s", "instructions": "You work for {company}."},
        "conversation": {"greeting": "Hi, this is {name}.", "speaking_style": "calm"},
        "required_instance_fields": ["name", "company"],
        "allowed_overrides": ["name", "company"],
    }
    base.update(over)
    return base


def test_placeholder_without_required_or_default_rejected():
    TemplateCreate.model_validate(_valid_payload())  # baseline is valid
    with pytest.raises(ValidationError, match="company"):
        TemplateCreate.model_validate(_valid_payload(required_instance_fields=["name"]))
    with pytest.raises(ValidationError, match="name"):
        TemplateCreate.model_validate(_valid_payload(required_instance_fields=["company"]))


def test_placeholder_covered_by_default_is_accepted():
    t = TemplateCreate.model_validate(
        _valid_payload(required_instance_fields=["name"], default_self_facts={"company": "Acme"})
    )
    assert t.default_self_facts.company == "Acme"


def test_role_placeholder_checked_in_greeting():
    p = _valid_payload()
    p["conversation"]["greeting"] = "Hi, I am {name}, your {role}."
    p["default_self_facts"] = {"role": None, "company": "Acme"}
    with pytest.raises(ValidationError, match="role"):
        TemplateCreate.model_validate(p)


def test_starters_carry_voice_behavior_rules(conn):
    seed_starter_templates(conn)
    for tid in STARTERS:
        text = get_template(conn, tid).persona.instructions.lower()
        assert "interrupted" in text and "repeat" in text and "ai voice assistant" in text


def test_concurrent_seeders_produce_exactly_one_v1(tmp_path):
    import threading

    path = tmp_path / "race.db"
    c0 = connect(path)
    migrate(c0)
    barrier = threading.Barrier(2)
    errors = []

    def run():
        try:
            c = connect(path)
            barrier.wait()
            seed_starter_templates(c)
        except Exception as exc:  # surfaced via assertion below
            errors.append(exc)

    threads = [threading.Thread(target=run) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    rows = c0.execute("SELECT template_id, version FROM template_versions ORDER BY template_id, version").fetchall()
    assert sorted((r["template_id"], r["version"]) for r in rows) == sorted((t, 1) for t in STARTERS)


def test_seed_recheck_inside_transaction_skips_stale_absent(tmp_path):
    """A second connection that passed any outside 'absent' check must not write v2."""
    path = tmp_path / "stale.db"
    a, b = connect(path), connect(path)
    migrate(a)
    assert len(seed_starter_templates(a)) == 3
    assert seed_starter_templates(b) == []
    assert list_versions(a, "generic-assistant") == [1]


def test_corrupt_body_raises_domain_error(conn):
    seed_starter_templates(conn)
    conn.execute("DROP TRIGGER IF EXISTS template_versions_no_update")
    triggers = [r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='trigger'")]
    for name in triggers:
        conn.execute(f'DROP TRIGGER "{name}"')
    conn.execute("UPDATE template_versions SET body_json = '{\"template_id\": 1}' WHERE template_id = 'generic-assistant'")
    conn.commit()
    with pytest.raises(DomainError) as ei:
        get_template(conn, "generic-assistant")
    assert ei.value.code == "corrupt_template" and ei.value.status == 500
    with pytest.raises(DomainError) as ei2:
        list_templates(conn)
    assert ei2.value.code == "corrupt_template"
