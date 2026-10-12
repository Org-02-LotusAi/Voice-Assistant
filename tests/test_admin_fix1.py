"""P2 fix round 1: owner hand-over rules, voice visibility at launch, Expert root anchoring, migration 008."""

import shutil
import subprocess
from importlib import resources

import pytest
from fastapi.testclient import TestClient

from service015 import accounts, templates_repo
from service015.db import connect, migrate
from service015.models import TemplateCreate
from tests.conftest import ADMIN_PW, H, USER_PW, FakeSupervisor, add_user, login_headers, make_app
from tests.test_admin import REPO, VOICE_AUTH, code, persona, template_body, upload

pytestmark = pytest.mark.usefixtures("fast_hash")


@pytest.fixture
def c(db_file, voice_stack):
    with TestClient(make_app(db_file, voice_stack, FakeSupervisor(), SERVICE015_ADMIN_PASSWORD=ADMIN_PW)) as client:
        assert client.post("/voices/vp-ava/authorizations", json=VOICE_AUTH, headers=H["admin"]).status_code == 201
        yield client


def test_identity_owner_must_be_an_active_user_account(c, db_file):
    add_user(db_file, "uma")
    viewer_id, off_id = add_user(db_file, "vic", "viewer"), add_user(db_file, "off")
    admin_id = accounts.find_user(connect(db_file), "admin")["user_id"]
    aid = persona(c, H["operator"])
    c.patch(f"/admin/users/{off_id}", headers=H["admin"], json={"disabled": True})
    for target in (viewer_id, off_id, admin_id):
        r = c.patch(f"/admin/identities/{aid}", headers=H["admin"], json={"owner_user_id": target})
        assert r.status_code == 422 and code(r) == "invalid_owner", r.text
    assert c.get("/admin/identities", headers=H["admin"]).json()[0]["owner_user_id"] is None  # nothing changed
    r = c.patch("/voices/vp-ava", headers=H["admin"], json={"owner_user_id": viewer_id})
    assert r.status_code == 422 and code(r) == "invalid_owner"


def test_handover_refuses_or_clears_a_voice_the_new_owner_cannot_use(c, db_file):
    add_user(db_file, "uma")
    wes_id = add_user(db_file, "wes")
    uma, wes = login_headers(c, "uma"), login_headers(c, "wes")
    private_voice = upload(c, uma).json()["voice_profile_id"]
    aid = persona(c, uma, **{"voice.profile_id": private_voice})
    r = c.patch(f"/admin/identities/{aid}", headers=H["admin"], json={"owner_user_id": wes_id})
    assert r.status_code == 409 and code(r) == "voice_not_accessible", r.text
    assert c.get(f"/identities/{aid}", headers=uma).json()["voice"]["profile_id"] == private_voice  # untouched
    assert c.get(f"/identities/{aid}", headers=wes).status_code == 404
    r = c.patch(f"/admin/identities/{aid}", headers=H["admin"], json={"owner_user_id": wes_id, "clear_voice": True})
    assert r.status_code == 200 and r.json()["owner_user_id"] == wes_id and r.json()["voice_profile_id"] is None
    r = c.post(f"/identities/{aid}/launch", headers=wes)
    assert r.status_code == 422 and code(r) == "voice_required"
    # a shared voice moves with the persona without any flag; a voice the new owner owns is fine too
    shared = persona(c, uma, name="Shared")
    assert c.patch(f"/admin/identities/{shared}", headers=H["admin"], json={"owner_user_id": wes_id}).status_code == 200
    mine = upload(c, wes).json()["voice_profile_id"]
    own = persona(c, wes, name="Own", **{"voice.profile_id": mine})
    assert c.patch(f"/admin/identities/{own}", headers=H["admin"], json={"owner_user_id": wes_id}).status_code == 200


def test_launch_checks_voice_visibility_for_the_personas_owner(c, db_file):
    add_user(db_file, "uma")
    wes_id = add_user(db_file, "wes")
    uma = login_headers(c, "uma")
    pid = upload(c, uma).json()["voice_profile_id"]
    aid = persona(c, uma, **{"voice.profile_id": pid})
    assert c.post(f"/identities/{aid}/launch", headers=uma).status_code == 200
    # the voice is reassigned away from the persona's owner: launch is refused (owner rule, also for a global operator)
    assert c.patch(f"/voices/{pid}", headers=H["admin"], json={"owner_user_id": wes_id}).status_code == 200
    for who in (uma, H["operator"]):
        r = c.post(f"/identities/{aid}/launch", headers=who)
        assert r.status_code == 422 and code(r) == "voice_profile_not_found", r.text
    # a machine-created (unowned) persona stays unrestricted
    mid = persona(c, H["operator"], name="Machine", **{"voice.profile_id": pid})
    assert c.post(f"/identities/{mid}/launch", headers=H["operator"]).status_code == 200


def test_user_patch_rejects_null_fields(c, db_file):
    uid = add_user(db_file, "uma")
    for body in ({"disabled": None}, {"role": None}, {"temporary_password": None}):
        r = c.patch(f"/admin/users/{uid}", headers=H["admin"], json=body)
        assert r.status_code == 422 and code(r) == "invalid_request"


def test_reingest_and_experience_are_anchored_to_the_repo_not_the_cwd(c, monkeypatch, tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)  # not a git checkout and unrelated to the service
    r = c.post("/expert/reingest", headers=H["admin"])
    assert r.status_code == 200, r.text
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True, text=True).stdout.strip()
    assert r.json()["sha"] == head and r.json()["ingested"]
    r = c.post("/expert/experience", headers=H["admin"],
               json={"title": "Anchored", "body": "Resolved from the repo root.", "source": "t"})
    assert r.status_code == 201 and r.json()["sha"] == head


def test_expert_root_must_be_a_git_checkout(db_file, voice_stack, tmp_path):
    with TestClient(make_app(db_file, voice_stack, FakeSupervisor(), SERVICE015_REPO_ROOT=str(tmp_path))) as client:
        r = client.post("/expert/reingest", headers=H["admin"])
        assert r.status_code == 422 and code(r) == "provenance_unresolved"


def test_migration_namespaces_unbound_private_templates(tmp_path):
    old = tmp_path / "m"
    old.mkdir()
    for f in resources.files("service015").joinpath("migrations").iterdir():
        if f.name.endswith(".sql") and f.name < "008":
            shutil.copy(str(f), old / f.name)
    conn = connect(tmp_path / "legacy.db")
    migrate(conn, old)
    uid = accounts.create_user(conn, "uma", USER_PW, "user")["user_id"]
    legacy = TemplateCreate.model_validate(template_body("legacy"))
    templates_repo.create_template(conn, legacy, owner_user_id=uid, shared=False)
    templates_repo.create_template(conn, legacy, owner_user_id=uid, shared=False)  # v2
    templates_repo.create_template(conn, TemplateCreate.model_validate(template_body("bound")), owner_user_id=uid, shared=False)
    conn.execute(
        "INSERT INTO identities (identity_id, template_id, template_version, overrides_json, resolved_json, resolved_hash, "
        "participant_identity, default_room, created_at, updated_at) VALUES ('b-001','bound',1,'{}','{}','h','b-001','b-001','n','n')")
    conn.commit()
    assert migrate(conn) == ["008_private_template_namespace.sql"]
    ids = {r["template_id"] for r in conn.execute("SELECT template_id FROM templates")}
    assert "uma.legacy" in ids and "legacy" not in ids and "bound" in ids  # a family with identities keeps its id
    assert templates_repo.list_versions(conn, "uma.legacy") == [1, 2]
    assert templates_repo.get_template(conn, "uma.legacy").template_id == "uma.legacy"
    with pytest.raises(Exception, match="immutable"):  # the immutability triggers were restored
        conn.execute("UPDATE template_versions SET body_json = '{}' WHERE template_id = 'uma.legacy'")
