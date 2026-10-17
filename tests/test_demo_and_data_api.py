import pytest
from fastapi.testclient import TestClient

from trustbench import crypto
from trustbench.api import create_app
from trustbench.demo import DemoProvider, PROFILES, demo_cases, make_demo
from trustbench.engine import RunOptions, run_eval
from trustbench.datasets import import_dataset
from trustbench.gate import evaluate_gate
from trustbench.tenancy import TenancyError, create_api_key, create_workspace


@pytest.fixture(scope="module")
def demo_env():
    from trustbench.db import init_db, make_engine
    factory = init_db(make_engine("sqlite://"))
    result = make_demo(factory, "demo", scale=0.5)
    return factory, result, TestClient(create_app(factory))


def hdr(key):
    return {"Authorization": f"Bearer {key}"}


def test_the_demo_suite_is_clean_and_every_case_has_an_answer_shape(factory):
    from trustbench import quality
    cases = demo_cases()
    assert len(cases) > 100 and quality.check(cases) == []
    assert any(c.metadata.get("source_case") for c in cases)


def test_the_demo_models_have_different_strengths(factory):
    cases = demo_cases()
    with factory() as s:
        v = import_dataset(s, "d", cases)
    provider = DemoProvider(cases)
    rates = {}
    from trustbench.rundata import load_run
    from trustbench.scorecard import scorecard
    for model in ("atlas-1", "nova-small", "nova-large"):
        rid = run_eval(factory, v.id, [f"demo:{model}"], RunOptions(backoff=0, concurrency=8), provider_factory=lambda n: provider)
        rates[model] = scorecard(load_run(factory, rid).records).overall.rate
    assert rates["nova-small"] < rates["atlas-1"] < rates["nova-large"] and rates["nova-large"] > .85


def test_demo_data_is_made_once_and_is_repeatable(demo_env, factory):
    f, result, _ = demo_env
    with pytest.raises(TenancyError, match="exists already"):
        make_demo(f, "demo", scale=0.1)
    again = make_demo(factory, "demo", scale=0.1)
    from trustbench.rundata import load_run
    a, b = load_run(f, result.runs["main"]), load_run(factory, again.runs["main"])
    assert [(r.case_id, r.passed) for r in a.records if r.repetition == 0][:30] == [(r.case_id, r.passed) for r in b.records if r.repetition == 0][:30]


def test_me_and_runs(demo_env):
    _, r, client = demo_env
    me = client.get("/v1/me", headers=hdr(r.api_key)).json()
    assert me["workspace"]["name"] == "demo" and me["role"] == "admin"
    runs = client.get("/v1/runs", headers=hdr(r.api_key)).json()["runs"]
    by_name = {x["name"]: x for x in runs}
    assert set(by_name) == {"main", "release-candidate", "model bake-off"} and by_name["main"]["baseline"] == "main"
    assert set(by_name["model bake-off"]["models"]) == {"demo:atlas-1", "demo:nova-small", "demo:nova-large"}
    assert by_name["main"]["models"]["demo:atlas-1"]["rate"] > by_name["release-candidate"]["models"]["demo:atlas-2"]["rate"]
    assert all(x["cost_usd"] > 0 for x in runs)


def test_run_detail_cases_and_case_detail(demo_env):
    _, r, client = demo_env
    run = r.runs["candidate"]
    d = client.get(f"/v1/runs/{run}", headers=hdr(r.api_key)).json()
    assert d["status"] == "complete" and "demo:atlas-2" in d["models"] and d["models"]["demo:atlas-2"]["by_task"]
    cases = client.get(f"/v1/runs/{run}/cases?result=fail&limit=5", headers=hdr(r.api_key)).json()
    assert cases["total"] > 5 and len(cases["cases"]) == 5 and all(c["result"] == "fail" and c["labels"] for c in cases["cases"])
    cid = cases["cases"][0]["case_id"]
    one = client.get(f"/v1/runs/{run}/cases/{cid}", headers=hdr(r.api_key)).json()
    assert one["case_id"] == cid and one["executions"][0]["results"] and "rag" in one["executions"][0]
    assert client.get(f"/v1/runs/{run}/cases/nope", headers=hdr(r.api_key)).status_code == 404
    assert client.get(f"/v1/runs/{run}/cases?result=weird", headers=hdr(r.api_key)).status_code == 422
    q = client.get(f"/v1/runs/{run}/cases?q={cid}", headers=hdr(r.api_key)).json()
    assert any(c["case_id"] == cid for c in q["cases"])


def test_analysis_endpoints(demo_env):
    _, r, client = demo_env
    run = r.runs["candidate"]
    h = hdr(r.api_key)
    assert client.get(f"/v1/runs/{run}/failures", headers=h).json()["failed_cases"] > 0
    rob = client.get(f"/v1/runs/{run}/robustness", headers=h).json()
    assert rob["pairs"] > 20 and rob["break_rate"]["n"] > 0 and rob["by_transform"]
    cal = client.get(f"/v1/runs/{run}/calibration", headers=h).json()
    assert cal["n"] > 0 and 0 <= cal["brier"] <= 1 and cal["bins"]
    slices = client.get(f"/v1/runs/{run}/slices", headers=h).json()["slices"]
    assert any(s["weak"] for s in slices) and all(s["n"] > 0 for s in slices)


def test_compare_gate_and_decisions(demo_env):
    _, r, client = demo_env
    h = hdr(r.api_key)
    cmp = client.get("/v1/compare", params={"candidate": r.runs["candidate"], "baseline": "main"}, headers=h).json()
    assert cmp["comparison"]["overall"]["verdict"] in ("meaningful_regression", "regression_below_threshold")
    assert cmp["comparison"]["by_task"]["grounded_qa"]["diff"] < 0 and cmp["worse_slices"] and cmp["operations"]["candidate"]["calls"] > 0
    decisions = client.get("/v1/decisions", headers=h).json()["decisions"]
    by_run = {d["run_id"]: d for d in decisions}
    assert by_run[r.runs["candidate"]]["status"] == "fail" and by_run[r.runs["candidate"]]["allowed"] is False
    assert any(c["status"] == "fail" for c in by_run[r.runs["candidate"]]["checks"])
    prev = client.post("/v1/gate/preview", json={"run": r.runs["candidate"], "baseline": "main", "policy": {"min_cases": 10, "critical_failures_allowed": None}}, headers=h)
    assert prev.status_code == 200 and prev.json()["decision"]["status"] in ("pass", "fail", "inconclusive", "incomplete")
    assert client.post("/v1/gate/preview", json={"nothing": 1}, headers=h).status_code == 400
    assert client.post("/v1/gate/preview", json={"run": "x"}, headers=h).status_code == 404
    assert client.post("/v1/gate/preview", json={"run": r.runs["main"], "policy": {"bogus": 1}}, headers=h).status_code == 422
    assert client.get("/v1/baselines", headers=h).json()["baselines"][0]["name"] == "main"


def test_frontier_datasets_and_proposals(demo_env):
    _, r, client = demo_env
    h = hdr(r.api_key)
    front = client.get("/v1/frontier", params={"runs": r.runs["bake-off"], "min_quality": 0.5}, headers=h).json()["candidates"]
    assert len(front) == 3 and any(c["on_frontier"] for c in front) and all(c["cost_per_success"] is not None for c in front)
    ds = client.get("/v1/datasets", headers=h).json()["datasets"]
    assert ds[0]["name"] == "demo-suite" and ds[0]["runs"] == 3 and "grounded_qa" in ds[0]["tasks"]
    detail = client.get(f"/v1/datasets/{ds[0]['id']}", headers=h).json()
    assert detail["cases"] == ds[0]["cases"] and detail["issues"] == [] and detail["preview"]
    props = client.get("/v1/proposals?status=proposed", headers=h).json()["proposals"]
    assert props and all(p["status"] == "proposed" for p in props)
    pid = props[0]["id"]
    assert client.post(f"/v1/proposals/{pid}/approve", json={}, headers=h).json() == {"id": pid, "status": "approved"}
    assert client.post(f"/v1/proposals/{props[1]['id']}/reject", json={"note": "no"}, headers=h).json()["status"] == "rejected"
    assert client.post("/v1/proposals/99999/approve", json={}, headers=h).status_code == 404


def test_production_endpoints_see_the_demo_traffic_and_the_change(demo_env):
    _, r, client = demo_env
    h = hdr(r.api_key)
    mon = client.get("/v1/monitor?days=7", headers=h).json()
    assert mon["traces"] > 60 and mon["measured"]["error_rate"]["n"] == mon["traces"] and mon["estimated"] and mon["unmeasured"]
    drift = client.get("/v1/drift?current_days=5&reference_days=30", headers=h).json()
    assert any(f["drifted"] for f in drift["findings"])
    assert client.get("/v1/flagged", headers=h).status_code == 200


def test_another_workspace_sees_none_of_it(demo_env):
    f, r, client = demo_env
    other = create_workspace(f, "outsider")
    _, key = create_api_key(f, other.id, "k", "admin")
    h = hdr(key)
    assert client.get("/v1/runs", headers=h).json()["runs"] == []
    assert client.get("/v1/datasets", headers=h).json()["datasets"] == []
    assert client.get("/v1/proposals", headers=h).json()["proposals"] == []
    assert client.get("/v1/decisions", headers=h).json()["decisions"] == []
    assert client.get("/v1/baselines", headers=h).json()["baselines"] == []
    for path in (f"/v1/runs/{r.runs['main']}", f"/v1/runs/{r.runs['main']}/cases", f"/v1/runs/{r.runs['main']}/failures", f"/v1/runs/{r.runs['main']}/slices"):
        assert client.get(path, headers=h).status_code == 404, path
    assert client.get("/v1/compare", params={"candidate": r.runs["main"], "baseline": r.runs["candidate"]}, headers=h).status_code == 404
    assert client.get("/v1/frontier", params={"runs": r.runs["main"]}, headers=h).status_code == 404
    assert client.post("/v1/proposals/1/approve", json={}, headers=h).status_code == 404
    assert client.get("/v1/monitor", headers=h).json()["traces"] == 0


def test_the_cli_run_belongs_to_the_default_workspace_and_the_default_key_sees_it(tmp_path, monkeypatch):
    from typer.testing import CliRunner
    from trustbench.cli import app
    from trustbench.db import init_db, make_engine
    url = f"sqlite:///{tmp_path}/t.db"
    monkeypatch.setenv("TRUSTBENCH_DATABASE_URL", url)
    CliRunner().invoke(app, ["run", "--model", "mock:smoke"])
    f = init_db(make_engine(url))
    from trustbench.tenancy import get_workspace
    ws = get_workspace(f, "default")
    _, key = create_api_key(f, ws.id, "k", "read")
    client = TestClient(create_app(f))
    runs = client.get("/v1/runs", headers=hdr(key)).json()["runs"]
    assert len(runs) == 1 and runs[0]["models"]["mock:smoke"]["n"] == 5
