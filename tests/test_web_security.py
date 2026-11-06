"""Protections at the edge of the API. Many of these are mistakes that code written fast tends to contain."""

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from trustbench.api import create_app
from trustbench.tenancy import create_api_key, create_workspace
from trustbench.web_security import client_ip, parse_trusted


@pytest.fixture
def world(factory):
    ws = create_workspace(factory, "a")
    keys = {role: create_api_key(factory, ws.id, role, role)[1] for role in ("read", "write", "admin")}
    return factory, keys


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def routes(app):
    return [r for r in app.routes if isinstance(r, APIRoute) and r.path.startswith("/v1/")]


def fill(path):
    return path.replace("{trace_id}", "t").replace("{run_id}", "r").replace("{case_id}", "c").replace("{name}", "n") \
        .replace("{job_id}", "1").replace("{proposal_id}", "1").replace("{decision_id}", "1")


# ---- Which routes need which key ----------------------------------------------------------------

PUBLIC = {"/v1/health"}
READ_ONLY_POSTS = {"/v1/gate/preview"}          # A preview saves nothing, so a read key may use it.


def test_every_route_needs_a_key_except_the_health_check(world):
    """A new route that forgets its auth dependency fails here. This is the commonest hole in code written fast."""
    factory, _ = world
    app = create_app(factory, rate_limit=0, bad_key_limit=10_000)
    client = TestClient(app, raise_server_exceptions=False)
    found = routes(app)
    assert len(found) >= 30                                     # The test really looks at the whole API.
    assert {r.path for r in app.routes if not r.path.startswith('/v1/')} <= {'/openapi.json', '/docs', '/docs/oauth2-redirect'}
    for route in found:
        for method in route.methods - {"HEAD", "OPTIONS"}:
            r = client.request(method, fill(route.path))
            if route.path in PUBLIC:
                assert r.status_code == 200, (method, route.path)
            else:
                assert r.status_code == 401, f"{method} {route.path} answered {r.status_code} without a key"


def test_a_read_key_cannot_change_anything(world):
    factory, keys = world
    app = create_app(factory, rate_limit=0)
    client = TestClient(app, raise_server_exceptions=False)
    for route in routes(app):
        for method in route.methods - {"GET", "HEAD", "OPTIONS"}:
            if route.path in READ_ONLY_POSTS:
                continue
            r = client.request(method, fill(route.path), headers=auth(keys["read"]), json={})
            assert r.status_code == 403, f"{method} {route.path} let a read key through ({r.status_code})"


def test_a_write_key_cannot_do_admin_work(world):
    factory, keys = world
    app = create_app(factory, rate_limit=0)
    client = TestClient(app, raise_server_exceptions=False)
    for method, path in [("PUT", "/v1/settings"), ("GET", "/v1/audit"), ("DELETE", "/v1/traces/t"), ("POST", "/v1/traces/t/promote")]:
        assert client.request(method, path, headers=auth(keys["write"]), json={}).status_code == 403, (method, path)


# ---- Size of the body ---------------------------------------------------------------------------

def test_a_body_without_a_length_is_stopped_while_it_arrives(world):
    factory, keys = world
    client = TestClient(create_app(factory, rate_limit=0, max_body_bytes=1000))

    def chunks():                                               # No Content-Length: we do not know the size at the start.
        for _ in range(50):
            yield b"x" * 100

    r = client.post("/v1/traces", content=chunks(), headers=auth(keys["write"]))
    assert r.status_code == 413 and "larger than" in r.json()["error"]


def test_every_route_has_the_limit_not_only_the_trace_route(world):
    factory, keys = world
    client = TestClient(create_app(factory, rate_limit=0, max_body_bytes=1000))
    r = client.put("/v1/settings", content=b"x" * 5000, headers=auth(keys["admin"]))
    assert r.status_code == 413
    r = client.post("/v1/gate/preview", content=b"x" * 5000, headers={**auth(keys["read"]), "content-length": "5000"})
    assert r.status_code == 413


def test_a_body_under_the_limit_still_works(world):
    factory, keys = world
    client = TestClient(create_app(factory, rate_limit=0, max_body_bytes=1000))
    assert client.post("/v1/traces", json={"traces": []}, headers=auth(keys["write"])).status_code == 202


# ---- Headers ------------------------------------------------------------------------------------

def test_api_answers_carry_security_headers(world):
    factory, keys = world
    client = TestClient(create_app(factory, rate_limit=0))
    r = client.get("/v1/traces", headers=auth(keys["read"]))
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["cache-control"] == "no-store"            # An answer holds private data. No cache may keep it.
    assert "default-src 'none'" in r.headers["content-security-policy"]
    assert "strict-transport-security" not in r.headers        # Only when someone tells us that the client used https.
    r = client.get("/v1/health", headers={"x-forwarded-proto": "https"})
    assert "max-age" in r.headers["strict-transport-security"]


def test_the_docs_page_is_not_broken_by_the_policy(world):
    factory, _ = world
    r = TestClient(create_app(factory)).get("/docs")
    assert r.status_code == 200 and "content-security-policy" not in r.headers and r.headers["x-content-type-options"] == "nosniff"


# ---- Address of the client ----------------------------------------------------------------------

NETS = parse_trusted("10.0.0.0/8, 192.168.1.5")


def test_the_forwarded_header_is_ignored_when_the_connection_is_not_from_a_trusted_proxy():
    assert client_ip("203.0.113.9", "1.2.3.4", NETS) == "203.0.113.9"      # A client wrote this header himself.
    assert client_ip("203.0.113.9", "1.2.3.4", []) == "203.0.113.9"
    assert client_ip(None, None, NETS) == "unknown"


def test_the_client_is_the_first_address_from_the_right_that_is_not_a_proxy():
    assert client_ip("10.0.0.2", "198.51.100.7", NETS) == "198.51.100.7"
    assert client_ip("10.0.0.2", "6.6.6.6, 198.51.100.7, 10.0.0.9", NETS) == "198.51.100.7"   # 6.6.6.6 is a claim by the client.
    assert client_ip("10.0.0.2", "10.0.0.7, 10.0.0.9", NETS) == "10.0.0.7"
    assert client_ip("192.168.1.5", "garbage", NETS) == "garbage"
    assert client_ip("10.0.0.2", None, NETS) == "10.0.0.2"


def test_trusted_proxy_setting_is_checked():
    assert parse_trusted(None) == [] and parse_trusted("") == []
    with pytest.raises(ValueError):
        parse_trusted("not an address")


def test_users_behind_one_proxy_do_not_lock_each_other_out(world):
    factory, keys = world
    app = create_app(factory, rate_limit=0, bad_key_limit=3, trusted_proxies="10.0.0.0/8")
    client = TestClient(app, client=("10.0.0.2", 5000))
    for _ in range(4):
        client.get("/v1/traces", headers={**auth("wrong"), "x-forwarded-for": "198.51.100.1"})
    assert client.get("/v1/traces", headers={**auth("wrong"), "x-forwarded-for": "198.51.100.1"}).status_code == 429
    assert client.get("/v1/traces", headers={**auth(keys["read"]), "x-forwarded-for": "198.51.100.2"}).status_code == 200


def test_a_forged_header_cannot_dodge_the_lockout(world):
    factory, _ = world
    client = TestClient(create_app(factory, rate_limit=0, bad_key_limit=3))
    codes = [client.get("/v1/traces", headers={**auth("wrong"), "x-forwarded-for": f"9.9.9.{i}"}).status_code for i in range(8)]
    assert 429 in codes


# ---- Errors do not leak -------------------------------------------------------------------------

def test_an_unexpected_error_gives_no_details_to_the_caller(world, caplog):
    factory, keys = world
    app = create_app(factory, rate_limit=0)

    @app.get("/v1/boom")
    def boom():
        raise RuntimeError("password=hunter2 at /srv/secret/path.py")

    r = TestClient(app, raise_server_exceptions=False).get("/v1/boom")
    assert r.status_code == 500 and "hunter2" not in r.text and "/srv" not in r.text
    assert "hunter2" in caplog.text                              # The operator still gets the facts, in the log.


def test_the_gate_preview_does_not_show_internal_errors(world, monkeypatch):
    factory, keys = world
    from trustbench import api_data
    monkeypatch.setattr(api_data, "evaluate_gate", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db password is hunter2")))
    client = TestClient(create_app(factory, rate_limit=0), raise_server_exceptions=False)
    r = client.post("/v1/gate/preview", json={"run": "x"}, headers=auth(keys["read"]))
    assert "hunter2" not in r.text
