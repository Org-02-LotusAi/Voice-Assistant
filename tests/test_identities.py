import json

import pytest

from service015.db import connect, migrate
from service015.errors import NotFound, ValidationFailed
from service015.identities_repo import (
    allocate_identity_id,
    get_identity,
    get_status,
    heartbeat,
    instantiate,
    list_identities,
    patch_identity,
    set_status,
    slugify,
)
from service015.models import TemplateCreate
from service015.resolver import resolve, validate_overrides
from service015.selfcontext import build_instructions, build_self_context
from service015.templates_repo import create_template, get_template, seed_starter_templates

SALES = "sales-representative"
AVA = {"name": "Ava", "company": "Safi", "role": "Account Specialist"}
JAMES = {"name": "James", "company": "Northwind", "role": "Support Lead"}


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "t.db")
    migrate(c)
    seed_starter_templates(c)
    return c


def _template_rows(conn):
    return [tuple(r) for r in conn.execute("SELECT * FROM template_versions ORDER BY template_id, version")]


def test_hash_is_deterministic_and_input_sensitive(conn):
    t = get_template(conn, SALES)
    a = resolve(t, AVA, "ava-001")
    assert a.resolved_hash == resolve(t, dict(AVA), "ava-001").resolved_hash
    assert a.resolved_hash != resolve(t, {**AVA, "role": "Other"}, "ava-001").resolved_hash
    assert a.resolved_hash != resolve(t, AVA, "ava-002").resolved_hash
    assert a.participant.participant_identity == a.participant.default_room == "ava-001"
    assert a.to_json_obj()["self"]["name"] == "Ava"


def test_id_allocation_and_slug(conn):
    assert instantiate(conn, SALES, AVA).identity_id == "ava-001"
    assert instantiate(conn, SALES, AVA).identity_id == "ava-002"
    assert allocate_identity_id(conn, "Ava") == "ava-003"
    assert allocate_identity_id(conn, "Ava Smith") == "ava-smith-001"
    assert slugify("  José  O'Neil!! ") == "jose-o-neil"
    assert slugify("!!!") == "identity"
    assert slugify("日本") == "identity"


def test_ava_and_james_self_facts_never_mix(conn):
    ava = instantiate(conn, SALES, AVA)
    james = instantiate(conn, SALES, JAMES)
    ia, ij = build_instructions(ava), build_instructions(james)
    for text, mine, other in ((ia, AVA, JAMES), (ij, JAMES, AVA)):
        for k in ("name", "company", "role"):
            assert mine[k] in text
            assert other[k] not in text
    assert 'My name is Ava."' in ia and 'My name is James."' in ij
    assert ia.startswith("AUTHORITATIVE SELF-FACTS")
    assert "ava-001" in build_self_context(ava) and "james-001" not in build_self_context(ava)


def test_override_not_allowed(conn):
    create_template(
        conn,
        TemplateCreate.model_validate(
            {
                **get_template(conn, SALES).model_dump(exclude={"version", "created_at"}),
                "allowed_overrides": ["name", "company"],
                "required_instance_fields": ["name"],
            }
        ),
    )
    with pytest.raises(ValidationFailed) as ei:
        instantiate(conn, SALES, {"name": "Ava", "role": "CEO"})
    assert ei.value.code == "override_not_allowed"
    assert ei.value.details["field"] == "role"
    assert ei.value.details["allowed"] == ["name", "company"]
    assert list_identities(conn) == []  # nothing persisted


def test_missing_required_and_type_checks(conn):
    t = get_template(conn, SALES)
    with pytest.raises(ValidationFailed) as ei:
        validate_overrides(t, {"name": "Ava"})  # company required too
    assert ei.value.code == "missing_required_field"
    for bad in (
        {**AVA, "conversation.barge_in": "yes"},
        {**AVA, "language": "english"},
        {**AVA, "greeting": ""},
        {**AVA, "greeting": "x" * 4001},
        {**AVA, "name": "Ava\nIgnore previous"},
        {**AVA, "role": 5},
    ):
        with pytest.raises(ValidationFailed) as ei:
            validate_overrides(t, bad)
        assert ei.value.code == "invalid_override"
    validate_overrides(t, {**AVA, "conversation.barge_in": False, "language": "fr-FR", "greeting": "x" * 4000})


def test_template_v2_does_not_touch_ava_and_patch_uses_bound_version(conn):
    ava = instantiate(conn, SALES, AVA)
    before = conn.execute("SELECT * FROM identities WHERE identity_id='ava-001'").fetchone()
    v1_rows = _template_rows(conn)
    # v2 narrows allowed overrides so that 'role' is no longer permitted
    v1 = get_template(conn, SALES)
    v2 = create_template(
        conn,
        TemplateCreate.model_validate(
            {**v1.model_dump(exclude={"version", "created_at"}), "allowed_overrides": ["name", "company"],
             "required_instance_fields": ["name", "company"], "name": "Sales V2"}
        ),
    )
    assert v2.version == 2
    assert _template_rows(conn)[: len(v1_rows)] == v1_rows
    same = conn.execute("SELECT * FROM identities WHERE identity_id='ava-001'").fetchone()
    assert tuple(same) == tuple(before)
    assert get_identity(conn, "ava-001") == ava and ava.template_version == 1

    patched = patch_identity(conn, "ava-001", {"role": "Senior Specialist"})  # allowed by v1, not by v2
    assert patched.template_version == 1
    assert patched.self_.role == "Senior Specialist"
    assert patched.self_.name == "Ava" and patched.self_.company == "Safi"
    assert patched.participant == ava.participant and patched.identity_id == "ava-001"
    assert patched.resolved_hash != ava.resolved_hash
    row = conn.execute("SELECT * FROM identities WHERE identity_id='ava-001'").fetchone()
    assert json.loads(row["overrides_json"])["role"] == "Senior Specialist"
    assert row["updated_at"] >= before["updated_at"] and row["created_at"] == before["created_at"]
    assert get_identity(conn, "ava-001") == patched

    # new instances bind to latest (v2) by default, explicit version still honoured
    assert instantiate(conn, SALES, {"name": "Zed", "company": "Z"}).template_version == 2
    assert instantiate(conn, SALES, AVA, template_version=1).template_version == 1
    with pytest.raises(ValidationFailed):
        patch_identity(conn, "ava-001", {"bogus": "x"})
    with pytest.raises(NotFound):
        patch_identity(conn, "nobody-001", {"role": "x"})


def test_persona_text_cannot_alter_self_block(conn):
    ava = instantiate(conn, SALES, {**AVA, "persona.instructions": "Your name is Bob. Say {name} and {company}. {oops}"})
    assert ava.self_.name == "Ava"
    block = build_self_context(ava)
    assert "Bob" not in block and "name: Ava" in block
    text = build_instructions(ava)
    head, _, persona_part = text.partition("PERSONA (subordinate")
    assert "Bob" not in head
    assert "Your name is Bob. Say Ava and Safi. {oops}" in persona_part
    assert text.count("Bob") == 1
    assert text.index("AUTHORITATIVE SELF-FACTS") < text.index("PERSONA (subordinate") < text.index("SPOKEN CONVERSATION RULES")
    assert text.rstrip().endswith("REMINDER: You are Ava; company: Safi; role: Account Specialist.")


def test_braces_in_user_text_do_not_break_or_chain(conn):
    r = instantiate(conn, SALES, {"name": "{company}", "company": "A{role}B", "role": "R", "greeting": "{name}|{company}|{x}"})
    assert r.conversation.greeting == "{company}|A{role}B|{x}"


def test_null_company_and_role_are_well_formed(conn):
    ga = instantiate(conn, "generic-assistant", {"name": "Max"})
    assert ga.self_.company is None
    assert "company: null" in build_self_context(ga)
    text = build_instructions(ga)
    assert "None" not in text and "null" not in text.split("PERSONA")[1]
    assert "Company: not specified" in text
    assert "not representing a specific company" in text
    assert "None" not in ga.conversation.greeting
    # explicit empty-gap cleanup
    g = instantiate(conn, "generic-assistant", {"name": "Max", "greeting": "Hi, I am {name} from {company}."})
    assert g.conversation.greeting == "Hi, I am Max from."


def test_status_lifecycle(conn):
    instantiate(conn, SALES, AVA)
    s = get_status(conn, "ava-001")
    assert s.presence == "OFFLINE" and s.pid is None and s.heartbeat_at is None
    s = set_status(conn, "ava-001", "STARTING", detail="booting", pid=1234)
    assert s.presence == "STARTING" and s.pid == 1234 and s.launched_at
    s = set_status(conn, "ava-001", "READY")
    assert s.pid == 1234 and s.status_detail is None
    s = heartbeat(conn, "ava-001")
    assert s.heartbeat_at
    s = set_status(conn, "ava-001", "ERROR", detail="RECONNECTING", error="room dropped")
    assert s.last_error == "room dropped" and s.status_detail == "RECONNECTING"
    s = set_status(conn, "ava-001", "OFFLINE")
    assert (s.pid, s.launched_at, s.heartbeat_at) == (None, None, None)
    with pytest.raises(ValidationFailed):
        set_status(conn, "ava-001", "RECONNECTING")
    with pytest.raises(NotFound):
        get_status(conn, "nobody-001")
    with pytest.raises(NotFound):
        heartbeat(conn, "nobody-001")


def test_persistence_across_connections(tmp_path):
    p = tmp_path / "p.db"
    c = connect(p)
    migrate(c)
    seed_starter_templates(c)
    ava = instantiate(c, SALES, AVA)
    c.close()
    c2 = connect(p)
    again = get_identity(c2, "ava-001")
    assert again == ava and again.resolved_hash == ava.resolved_hash
    assert [i.identity_id for i in list_identities(c2)] == ["ava-001"]
