import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient

from trustbench.api import create_app
from trustbench.sdk import TrustBench
from trustbench.tenancy import create_api_key, create_workspace
from trustbench.traces import get_trace, list_traces


@pytest.fixture
def env(factory):
    ws = create_workspace(factory, "acme")
    _, write = create_api_key(factory, ws.id, "sdk", "write")
    server = TestClient(create_app(factory))
    return factory, ws, write, server


def sdk(env, **kw):
    factory, ws, write, server = env
    kw.setdefault("flush_interval", 3600)       # Tests flush by hand.
    return TrustBench(api_key=write, base_url="http://testserver", client=server, **kw)


def test_a_trace_reaches_the_server_with_spans_in_a_tree(env):
    factory, ws, *_ = env
    tb = sdk(env)
    with tb.trace("support-bot", model="gpt-4o-mini", tags={"plan": "pro"}) as t:
        t.input = "How long do refunds take?"
        with t.span("search", kind="retrieval", hits=3):
            with t.span("rerank", kind="other"):
                pass
        t.output = "30 days."
        t.set(input_tokens=12, output_tokens=4)
    tb.flush()
    assert tb.stats.sent == 1 and tb.stats.dropped == 0
    row, spans = get_trace(factory, ws.id, t.trace_id)
    assert (row.name, row.model, row.status, row.input_tokens, row.tags) == ("support-bot", "gpt-4o-mini", "ok", 12, {"plan": "pro"})
    assert row.duration_ms >= 0 and row.input_chars == 25
    by_name = {s.name: s for s in spans}
    assert by_name["rerank"].parent_id == by_name["search"].span_id and by_name["search"].parent_id is None
    assert by_name["search"].attrs == {"hits": 3} and by_name["rerank"].offset_ms >= by_name["search"].offset_ms


def test_your_own_errors_still_happen_and_are_recorded(env):
    factory, ws, *_ = env
    tb = sdk(env)
    with pytest.raises(ZeroDivisionError):
        with tb.trace("job") as t:
            with t.span("calc"):
                1 / 0
    tb.flush()
    row, spans = get_trace(factory, ws.id, t.trace_id)
    assert (row.status, row.error_type, spans[0].status) == ("error", "ZeroDivisionError", "error")


def test_personal_data_is_removed_before_it_leaves_the_process(env):
    seen = []

    def spy(request: httpx.Request):
        seen.append(request.content.decode())
        return httpx.Response(202, json={"accepted": 1, "duplicates": 0, "rejected": []})
    factory, ws, write, _ = env
    tb = TrustBench(api_key=write, base_url="http://x", client=httpx.Client(transport=httpx.MockTransport(spy)), flush_interval=3600)
    tb.log("bot", input="mail ana@gmail.com, card 4111 1111 1111 1111", output="ok", tags={"user": "ana@gmail.com"})
    tb.flush()
    assert seen and "gmail" not in seen[0] and "4111" not in seen[0] and "[EMAIL]" in seen[0]
    raw = TrustBench(api_key=write, base_url="http://x", client=httpx.Client(transport=httpx.MockTransport(spy)), flush_interval=3600, redact=False)
    raw.log("bot", input="ana@gmail.com")
    raw.flush()
    assert "ana@gmail.com" in seen[-1]


def test_no_key_means_no_tracing_and_no_errors(monkeypatch):
    monkeypatch.delenv("TRUSTBENCH_API_KEY", raising=False)
    tb = TrustBench()
    assert not tb.enabled
    with tb.trace("x") as t:
        t.input = "hello"
    assert tb.log("y") and tb.stats.sent == 0
    tb.feedback("x", 1)
    tb.flush()
    tb.close()


def test_a_dead_server_never_breaks_or_blocks_the_app(monkeypatch):
    def down(request):
        raise httpx.ConnectError("no route")
    tb = TrustBench(api_key="tb_x", base_url="http://x", client=httpx.Client(transport=httpx.MockTransport(down)), flush_interval=3600, retries=2)
    monkeypatch.setattr("trustbench.sdk.time.sleep", lambda s: None)      # Do not wait during the retries in the test.
    with tb.trace("x") as t:
        t.output = "fine"
    tb.flush()
    assert tb.stats.dropped == 1 and tb.stats.sent == 0


def test_a_full_queue_drops_new_traces_and_counts_them():
    tb = TrustBench(api_key="tb_x", base_url="http://x", max_queue=2, flush_interval=3600,
                    client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(202, json={"accepted": 2, "duplicates": 0, "rejected": []}))))
    for _ in range(5):
        tb.log("x")
    assert tb.stats.dropped == 3


def test_a_refused_key_switches_tracing_off(env):
    factory, ws, write, server = env
    tb = TrustBench(api_key="tb_" + "z" * 43, base_url="http://testserver", client=server, flush_interval=3600)
    tb.log("x")
    tb.flush()
    assert not tb.enabled and tb.stats.dropped == 1
    tb.log("y")
    assert tb.stats.dropped == 1                # The SDK queues nothing more.


def test_invalid_traces_are_counted_as_rejected(env):
    tb = sdk(env)
    tb.log("x", tags={f"k{i}": i for i in range(30)})
    tb.flush()
    assert tb.stats.rejected == 1 and tb.stats.sent == 0


def test_large_batches_are_split_on_413():
    sizes = []

    def handler(request):
        import json
        n = len(json.loads(request.content)["traces"])
        sizes.append(n)
        return httpx.Response(413, json={}) if n > 2 else httpx.Response(202, json={"accepted": n, "duplicates": 0, "rejected": []})
    tb = TrustBench(api_key="tb_x", base_url="http://x", client=httpx.Client(transport=httpx.MockTransport(handler)), flush_interval=3600)
    for _ in range(8):
        tb.log("x")
    tb.flush()
    assert tb.stats.sent == 8 and sizes[0] == 8 and max(sizes[1:]) <= 2 + 2


def test_the_decorator_records_time_and_errors_but_not_arguments(env):
    factory, ws, *_ = env
    tb = sdk(env)

    @tb.observe("answer", model="m1")
    def answer(question):
        return "42"

    @tb.observe("boom")
    def boom():
        raise ValueError("x")

    @tb.observe("later", capture_input=True, capture_output=True)
    async def later(x):
        return f"got {x}"

    assert answer("secret question") == "42"
    with pytest.raises(ValueError):
        boom()
    assert asyncio.run(later(7)) == "got 7"
    tb.flush()
    rows = {r.name: r for r in list_traces(factory, ws.id)}
    assert rows["answer"].input_chars is None and rows["answer"].model == "m1"        # Nothing about the arguments.
    assert rows["boom"].status == "error" and rows["later"].input_chars is not None and rows["later"].output_chars == 5


def test_feedback_is_sent_after_its_trace(env):
    factory, ws, *_ = env
    tb = sdk(env)
    tid = tb.log("bot", output="a")
    tb.feedback(tid, -1)
    tb.flush()
    assert get_trace(factory, ws.id, tid)[0].feedback == -1


def test_close_sends_what_is_left(env):
    factory, ws, *_ = env
    tb = sdk(env)
    tid = tb.log("bot")
    tb.close()
    assert get_trace(factory, ws.id, tid) is not None


def test_unknown_trace_field_is_an_error_in_your_code_not_silent():
    tb = TrustBench(api_key="tb_x", client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(202, json={}))), flush_interval=3600)
    with pytest.raises(TypeError, match="Unknown trace field"):
        with tb.trace("x") as t:
            t.set(colour="red")


def test_the_background_thread_sends_without_a_manual_flush(env):
    factory, ws, write, server = env
    tb = TrustBench(api_key=write, base_url="http://testserver", client=server, flush_interval=0.05)
    tid = tb.log("bot")
    import time
    for _ in range(100):
        if get_trace(factory, ws.id, tid):
            break
        time.sleep(0.05)
    tb.close()
    assert get_trace(factory, ws.id, tid) is not None
