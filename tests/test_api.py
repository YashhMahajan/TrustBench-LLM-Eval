import json

import pytest
from fastapi.testclient import TestClient

from trustbench import audit, crypto
from trustbench.api import MAX_BODY_BYTES, create_app
from trustbench.tenancy import create_api_key, create_workspace


@pytest.fixture
def world(factory):
    a, b = create_workspace(factory, "a"), create_workspace(factory, "b")
    keys = {}
    for ws, label in ((a, "a"), (b, "b")):
        for role in ("read", "write", "admin"):
            keys[f"{label}_{role}"] = create_api_key(factory, ws.id, role, role)[1]
    return TestClient(create_app(factory)), keys, a, b


def h(token):
    return {"Authorization": f"Bearer {token}"}


def post(client, token, traces):
    return client.post("/v1/traces", json={"traces": traces}, headers=h(token))


def test_health_needs_no_key(world):
    client, *_ = world
    assert client.get("/v1/health").json()["status"] == "ok"


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer nope"}, {"Authorization": "Bearer tb_" + "x" * 43}, {"X-API-Key": "bad"}])
def test_missing_or_wrong_keys_get_the_same_401(world, headers):
    client, *_ = world
    r = client.get("/v1/traces", headers=headers)
    assert r.status_code == 401 and r.json() == {"error": "A valid API key is needed."} and r.headers["www-authenticate"] == "Bearer"


def test_the_x_api_key_header_also_works(world):
    client, keys, *_ = world
    assert client.get("/v1/traces", headers={"X-API-Key": keys["a_read"]}).status_code == 200


def test_roles_are_enforced(world):
    client, keys, *_ = world
    t = [{"trace_id": "t1"}]
    assert post(client, keys["a_read"], t).status_code == 403
    assert post(client, keys["a_write"], t).status_code == 202
    assert client.delete("/v1/traces/t1", headers=h(keys["a_write"])).status_code == 403
    assert client.get("/v1/audit", headers=h(keys["a_write"])).status_code == 403
    assert client.put("/v1/settings", json={"retention_days": 5}, headers=h(keys["a_write"])).status_code == 403


def test_a_batch_is_accepted_in_part(world):
    client, keys, *_ = world
    r = post(client, keys["a_write"], [{"trace_id": "ok1", "model": "m"}, {"trace_id": "bad", "status": "maybe"},
                                       "not an object", {"trace_id": "ok1"}, {"trace_id": "ok2", "duration_ms": -5}])
    body = r.json()
    assert r.status_code == 202 and body["accepted"] == 1 and body["duplicates"] == 1
    assert [x["index"] for x in body["rejected"]] == [1, 2, 4] and "status" in body["rejected"][0]["reason"]


def test_bad_bodies(world):
    client, keys, *_ = world
    for body in (b"not json", b'{"nope": []}', b'{"traces": "x"}'):
        assert client.post("/v1/traces", content=body, headers=h(keys["a_write"])).status_code == 400
    assert post(client, keys["a_write"], [{"trace_id": str(i)} for i in range(501)]).status_code == 413
    big = client.post("/v1/traces", content=b"x" * (MAX_BODY_BYTES + 1), headers=h(keys["a_write"]))
    assert big.status_code == 413


def test_workspaces_are_isolated_through_the_api(world):
    client, keys, *_ = world
    post(client, keys["a_write"], [{"trace_id": "secret-a", "name": "bot"}])
    assert [t["trace_id"] for t in client.get("/v1/traces", headers=h(keys["a_read"])).json()["traces"]] == ["secret-a"]
    assert client.get("/v1/traces", headers=h(keys["b_read"])).json()["traces"] == []
    assert client.get("/v1/traces/secret-a", headers=h(keys["b_admin"])).status_code == 404
    assert client.delete("/v1/traces/secret-a", headers=h(keys["b_admin"])).status_code == 404
    assert client.get("/v1/traces/secret-a", headers=h(keys["a_read"])).status_code == 200


def test_trace_detail_has_spans_and_no_content_by_default(world):
    client, keys, *_ = world
    post(client, keys["a_write"], [{"trace_id": "t1", "input": "hello", "spans": [{"span_id": "s1", "name": "search", "kind": "retrieval"}]}])
    d = client.get("/v1/traces/t1", headers=h(keys["a_read"])).json()
    assert d["spans"][0]["kind"] == "retrieval" and d["has_content"] is False and "content" not in d and d["input_chars"] == 5


def test_content_needs_an_admin_key_and_is_logged(world, factory, monkeypatch):
    client, keys, a, _ = world
    monkeypatch.setenv("TRUSTBENCH_SECRET_KEY", crypto.generate_key())
    assert client.put("/v1/settings", json={"store_content": True}, headers=h(keys["a_admin"])).json()["store_content"] is True
    post(client, keys["a_write"], [{"trace_id": "t1", "input": "mail ana@gmail.com", "output": "sent"}])
    assert client.get("/v1/traces/t1?reveal=true", headers=h(keys["a_write"])).status_code == 403
    assert "content" not in client.get("/v1/traces/t1", headers=h(keys["a_read"])).json()
    d = client.get("/v1/traces/t1?reveal=true", headers=h(keys["a_admin"])).json()
    assert d["content"] == {"input": "mail [EMAIL]", "output": "sent", "context": None}
    log = client.get("/v1/audit", headers=h(keys["a_admin"])).json()["entries"]
    assert [e["action"] for e in log] == ["trace.reveal", "settings.update"] and log[0]["target"] == "t1"
    assert audit.entries(factory, world[3].id) == []                     # The other workspace sees none of it.


def test_revealing_without_the_encryption_key_is_a_clear_error(world, monkeypatch):
    client, keys, *_ = world
    monkeypatch.setenv("TRUSTBENCH_SECRET_KEY", crypto.generate_key())
    client.put("/v1/settings", json={"store_content": True}, headers=h(keys["a_admin"]))
    post(client, keys["a_write"], [{"trace_id": "t1", "input": "x"}])
    monkeypatch.delenv("TRUSTBENCH_SECRET_KEY")
    r = client.get("/v1/traces/t1?reveal=true", headers=h(keys["a_admin"]))
    assert r.status_code == 409 and "TRUSTBENCH_SECRET_KEY" in r.json()["error"]


def test_delete_and_settings(world):
    client, keys, *_ = world
    post(client, keys["a_write"], [{"trace_id": "t1"}])
    assert client.delete("/v1/traces/t1", headers=h(keys["a_admin"])).status_code == 204
    assert client.get("/v1/traces/t1", headers=h(keys["a_read"])).status_code == 404
    assert client.get("/v1/settings", headers=h(keys["a_read"])).json()["store_content"] is False
    bad = client.put("/v1/settings", json={"sample_rate": 3}, headers=h(keys["a_admin"]))
    assert bad.status_code == 422 and "sample_rate" in bad.json()["error"]
    assert client.put("/v1/settings", json={"colour": "red"}, headers=h(keys["a_admin"])).status_code == 422
    assert client.put("/v1/settings", json=[1], headers=h(keys["a_admin"])).status_code == 400
    assert client.put("/v1/settings", json={"store_content": True}, headers=h(keys["a_admin"])).status_code == 422   # No encryption key set.


def test_listing_filters_and_limits(world):
    client, keys, *_ = world
    post(client, keys["a_write"], [{"trace_id": f"t{i}", "status": "error" if i % 2 else "ok", "model": "m"} for i in range(6)])
    get = lambda q: client.get(f"/v1/traces?{q}", headers=h(keys["a_read"]))
    assert len(get("status=error").json()["traces"]) == 3 and len(get("limit=2").json()["traces"]) == 2
    assert get("status=weird").status_code == 422 and get("limit=0").status_code == 422
    assert len(get("model=m&offset=4").json()["traces"]) == 2
