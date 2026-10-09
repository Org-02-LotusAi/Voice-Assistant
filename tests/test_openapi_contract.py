import re
from datetime import datetime
from pathlib import Path

import pytest
import yaml
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator, FormatChecker
from openapi_spec_validator import validate
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

from conftest import H, FakeSupervisor, make_settings
from service015.api.app import create_app, package_version

SPEC_PATH = Path(__file__).resolve().parents[1] / "openapi.yaml"
SPEC = yaml.safe_load(SPEC_PATH.read_text(encoding="utf-8"))
METHODS = ("get", "post", "put", "patch", "delete")
OPS = [(p, m, op) for p, item in SPEC["paths"].items() for m, op in item.items() if m in METHODS]
LOWER = {"admin": "operator", "operator": "viewer"}
REGISTRY = Registry().with_resource("urn:spec", Resource.from_contents(SPEC, default_specification=DRAFT202012))


FORMATS = FormatChecker(formats=[])


@FORMATS.checks("date-time", raises=ValueError)
def _is_date_time(value):
    if not isinstance(value, str):
        return True
    return datetime.fromisoformat(value).tzinfo is not None  # RFC 3339 needs an offset


def _validator(schema):
    return Draft202012Validator(schema, registry=REGISTRY, format_checker=FORMATS)


def _abs_refs(node):
    if isinstance(node, dict):
        return {k: ("urn:spec" + v if k == "$ref" and v.startswith("#") else _abs_refs(v)) for k, v in node.items()}
    if isinstance(node, list):
        return [_abs_refs(v) for v in node]
    return node


def _response_schema(path, method, status):
    resp = SPEC["paths"][path][method]["responses"][str(status)]
    if "$ref" in resp:
        resp = SPEC["components"]["responses"][resp["$ref"].rsplit("/", 1)[1]]
    return _abs_refs(resp["content"]["application/json"]["schema"])


def assert_conforms(path, method, response):
    schema = _response_schema(path, method, response.status_code)
    errors = list(_validator(schema).iter_errors(response.json()))
    assert not errors, f"{method.upper()} {path} {response.status_code}: {[e.message for e in errors][:3]}"


def _api_routes(app):
    # FastAPI >=0.143 wraps included routers lazily in app.routes; walk the routers we included.
    return [r for router in app.state.routers for r in router.routes if isinstance(r, APIRoute)]


def _app_ops(app):
    return {(r.path, m.lower()) for r in _api_routes(app) for m in r.methods if m not in ("HEAD", "OPTIONS")}


def _route_role(app, path, method):
    route = next(r for r in _api_routes(app) if r.path == path and method.upper() in r.methods)
    roles = [d.call.required_role for d in route.dependant.dependencies if hasattr(d.call, "required_role")]
    roles += [d.call.required_role for d in _walk(route.dependant) if hasattr(d.call, "required_role")]
    return set(roles)


def _walk(dep):
    for d in dep.dependencies:
        yield d
        yield from _walk(d)


def test_spec_is_valid_openapi_31():
    validate(SPEC)
    assert SPEC["openapi"].startswith("3.1")
    assert SPEC["info"]["version"] == package_version()
    assert SPEC["servers"] and "bearerAuth" in SPEC["components"]["securitySchemes"]


def test_every_operation_declares_role():
    for path, method, op in OPS:
        assert op.get("x-required-role") in ("none", "viewer", "operator", "admin"), (path, method)


def test_routes_match_spec(db_file):
    app = create_app(make_settings(db_file))
    assert _app_ops(app) == {(p, m) for p, m, _ in OPS}


def test_app_dependency_roles_equal_spec(db_file):
    app = create_app(make_settings(db_file))
    for path, method, op in OPS:
        declared = op["x-required-role"]
        assert _route_role(app, path, method) == (set() if declared == "none" else {declared}), (path, method)


def test_error_body_is_spec_shaped_for_every_status(client):
    r = client.get("/templates")
    assert_conforms("/templates", "get", r)


def _concrete(path):
    return re.sub(r"\{identity_id\}", "ava-001", re.sub(r"\{template_id\}", "sales-representative", path))


BODIES = {
    ("/templates", "post"): None,  # filled from an existing template below
    ("/templates/{template_id}/instantiate", "post"): {"overrides": {"name": "Ava", "company": "Safi"}},
    ("/identities/{identity_id}", "patch"): {"overrides": {"role": "Lead"}},
    ("/identities/{identity_id}/phone/dial", "post"): {"to": "+919876543210"},
}


@pytest.mark.parametrize("path,method,op", OPS, ids=[f"{m.upper()} {p}" for p, m, _ in OPS])
def test_operation_rbac_and_response_conformance(client, path, method, op):
    required = op["x-required-role"]
    # seed state the operations need
    client.post("/templates/sales-representative/instantiate",
                json={"overrides": {"name": "Ava", "company": "Safi"}}, headers=H["operator"])
    url = _concrete(path)
    body = BODIES.get((path, method))
    if (path, method) == ("/templates", "post"):
        body = client.get("/templates/customer-support", headers=H["viewer"]).json()
        body = {k: v for k, v in body.items() if k not in ("version", "created_at")}
    kw = {"json": body} if body is not None else {}

    if required == "none":
        r = client.request(method, url, **kw)
        assert r.status_code in (200, 503)
        if r.headers["content-type"].startswith("application/json"):
            assert_conforms(path, method, r)
        return

    # no key and a bad key -> 401 with ErrorBody
    for hdr in ({}, {"Authorization": "Bearer nope"}):
        r = client.request(method, url, headers=hdr, **kw)
        assert r.status_code == 401
        assert_conforms(path, method, r)
    # one role below the declared minimum -> 403 (proves app role is not lower than spec)
    if required in LOWER:
        r = client.request(method, url, headers=H[LOWER[required]], **kw)
        assert r.status_code == 403
        assert_conforms(path, method, r)
    # exactly the declared role is accepted (proves app role is not higher than spec)
    r = client.request(method, url, headers=H[required], **kw)
    assert r.status_code not in (401, 403), r.text
    declared = {int(c) for c in op["responses"]}
    assert r.status_code in declared, (r.status_code, declared)
    assert_conforms(path, method, r)


def test_error_responses_conform(client):
    client.post("/templates/sales-representative/instantiate",
                json={"overrides": {"name": "Ava", "company": "Safi"}}, headers=H["operator"])
    cases = [
        ("/identities/zzz-001", "get", {}, 404),
        ("/templates/nope", "get", {}, 404),
        ("/templates/sales-representative/versions", "get", {}, 200),
        ("/templates/sales-representative/instantiate", "post", {"json": {"overrides": {"x": "y"}}}, 422),
        ("/templates/sales-representative/instantiate", "post", {"json": {"overrides": {"name": "A"}}}, 422),
        ("/identities/ava-001/launch", "post", {}, 503),
        ("/identities/ava-001/stop", "post", {}, 503),
    ]
    for path, method, kw, status in cases:
        r = client.request(method, path, headers=H["operator"], **kw)
        assert r.status_code == status, (path, r.text)
        generic = re.sub(r"/(zzz-001|ava-001)", "/{identity_id}", path)
        generic = generic.replace("/nope", "/{template_id}").replace("/sales-representative", "/{template_id}")
        assert_conforms(generic, method, r)


def _schema_errors(ref_or_schema, instance):
    schema = {"$ref": "urn:spec#/components/schemas/" + ref_or_schema} if isinstance(ref_or_schema, str) else ref_or_schema
    return list(_validator(schema).iter_errors(instance))


def test_every_operation_declares_500():
    for path, method, op in OPS:
        assert "500" in op["responses"], (path, method)


def test_packaged_spec_is_byte_identical_to_repo_root():
    packaged = Path(__file__).resolve().parents[1] / "src" / "service015" / "openapi.yaml"
    assert packaged.read_bytes() == SPEC_PATH.read_bytes()


def test_served_spec_is_the_contract(client):
    assert client.get("/openapi.yaml").content == SPEC_PATH.read_bytes()


def test_format_checker_rejects_bad_date_time():
    bad = {"identity_id": "a", "participant_identity": "a", "default_room": "a", "presence": "OFFLINE",
           "status_detail": None, "last_error": None, "launched_at": "yesterday", "heartbeat_at": None,
           "capabilities": {"audio_receive": True, "audio_publish": True, "barge_in": True, "video_publish": False}}
    assert _schema_errors("ParticipantStatus", bad)
    assert not _schema_errors("ParticipantStatus", {**bad, "launched_at": "2026-01-01T00:00:00+00:00"})


@pytest.mark.parametrize("name", ["InstantiateRequest", "PatchIdentityRequest", "TemplateCreate"])
def test_schema_examples_are_valid(name):
    examples = SPEC["components"]["schemas"][name]["examples"]
    assert examples
    for ex in examples:
        assert not _schema_errors(name, ex)


def test_template_create_example_is_accepted_by_server(client):
    ex = SPEC["components"]["schemas"]["TemplateCreate"]["examples"][0]
    r = client.post("/templates", json=ex, headers=H["admin"])
    assert r.status_code == 201, r.text
    assert_conforms("/templates", "post", r)
    ex = SPEC["components"]["schemas"]["InstantiateRequest"]["examples"][0]
    r = client.post("/templates/sales-representative/instantiate", json=ex, headers=H["operator"])
    assert r.status_code == 201, r.text
    ex = SPEC["components"]["schemas"]["PatchIdentityRequest"]["examples"][0]
    r = client.patch("/identities/ava-001", json=ex, headers=H["operator"])
    assert r.status_code == 200, r.text


def test_request_bodies_exercised_by_tests_match_spec(client):
    """Every request body we send in the conformance test is valid against the spec request schema."""
    for (path, method), body in BODIES.items():
        if body is None:
            continue
        schema = _abs_refs(SPEC["paths"][path][method]["requestBody"]["content"]["application/json"]["schema"])
        assert not list(_validator(schema).iter_errors(body)), (path, method)


def test_unknown_override_key_is_spec_valid_and_rejected_by_server(client):
    body = {"overrides": {"name": "Ava", "company": "Safi", "template_id": "x"}}
    assert not _schema_errors("InstantiateRequest", body)
    r = client.post("/templates/sales-representative/instantiate", json=body, headers=H["operator"])
    assert r.status_code == 422 and r.json()["error"]["code"] == "override_not_allowed"
    assert_conforms("/templates/{template_id}/instantiate", "post", r)


VALUE_CASES = [
    ("greeting", ""),
    ("greeting", "   "),
    ("greeting", "x" * 4001),
    ("conversation.barge_in", "yes"),
    ("persona.style", True),
    ("language", "english"),
    ("name", "N" * 81),
    ("company", "A" * 121),
    ("company", "multi" + chr(10) + "line"),
]


@pytest.mark.parametrize("key,value", VALUE_CASES)
def test_override_value_rules_agree_between_spec_and_server(client, key, value):
    ov = {"name": "Ava", "company": "Safi", key: value}
    assert _schema_errors("InstantiateRequest", {"overrides": ov}), "spec should reject"
    r = client.post("/templates/sales-representative/instantiate", json={"overrides": ov}, headers=H["operator"])
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_override", r.text
    assert_conforms("/templates/{template_id}/instantiate", "post", r)


def test_valid_override_values_accepted_by_both(client):
    ov = {"name": "Ava", "company": "Safi", "language": "en-GB", "greeting": "Hi {name}", "conversation.barge_in": False}
    assert not _schema_errors("InstantiateRequest", {"overrides": ov})
    assert client.post("/templates/sales-representative/instantiate", json={"overrides": ov},
                       headers=H["operator"]).status_code == 201


@pytest.fixture
def fake_client(db_file):
    with TestClient(create_app(make_settings(db_file), FakeSupervisor())) as c:
        c.post("/templates/sales-representative/instantiate",
               json={"overrides": {"name": "Ava", "company": "Safi"}}, headers=H["operator"])
        yield c


@pytest.mark.parametrize("path,method", [("/identities/{identity_id}/launch", "post"),
                                         ("/identities/{identity_id}/stop", "post"),
                                         ("/identities/{identity_id}/status", "get")])
def test_supervisor_backed_200_bodies_conform(fake_client, path, method):
    r = fake_client.request(method, _concrete(path), headers=H["operator"])
    assert r.status_code == 200
    assert_conforms(path, method, r)


def test_unauthorized_has_www_authenticate_header(client):
    r = client.get("/templates")
    assert r.status_code == 401 and r.headers["www-authenticate"] == "Bearer"


def test_500_matches_error_body(db_file):
    app = create_app(make_settings(db_file), FakeSupervisor(explode=True))
    with TestClient(app, raise_server_exceptions=False) as c:
        c.post("/templates/sales-representative/instantiate",
               json={"overrides": {"name": "Ava", "company": "Safi"}}, headers=H["operator"])
        r = c.post("/identities/ava-001/launch", headers=H["operator"])
        assert r.status_code == 500 and r.json()["error"]["code"] == "internal_error"
        assert_conforms("/identities/{identity_id}/launch", "post", r)
        assert "boom" not in r.text
