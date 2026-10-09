import copy
import re
from pathlib import Path

import pytest
import yaml
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator
from openapi_spec_validator import validate
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

from conftest import H, make_settings
from service015.api.app import create_app, package_version

SPEC_PATH = Path(__file__).resolve().parents[1] / "openapi.yaml"
SPEC = yaml.safe_load(SPEC_PATH.read_text(encoding="utf-8"))
METHODS = ("get", "post", "put", "patch", "delete")
OPS = [(p, m, op) for p, item in SPEC["paths"].items() for m, op in item.items() if m in METHODS]
LOWER = {"admin": "operator", "operator": "viewer"}
REGISTRY = Registry().with_resource("urn:spec", Resource.from_contents(SPEC, default_specification=DRAFT202012))


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
    errors = list(Draft202012Validator(schema, registry=REGISTRY).iter_errors(response.json()))
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
