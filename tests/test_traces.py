import datetime as dt

import pytest

from trustbench import crypto
from trustbench.dataprotect import update_policy
from trustbench.db import TraceSpan
from trustbench.tenancy import create_workspace
from trustbench.traces import (
    MAX_BATCH, IngestResult, SpanIn, TraceIn, count_traces, delete_trace, get_trace, ingest, is_sampled, list_traces,
    purge_expired, reveal,
)


@pytest.fixture
def ws(factory):
    return create_workspace(factory, "acme")


@pytest.fixture
def key(monkeypatch):
    monkeypatch.setenv("TRUSTBENCH_SECRET_KEY", crypto.generate_key())


def trace(**kw):
    kw.setdefault("name", "support-bot")
    return TraceIn(**kw)


def test_by_default_only_measurements_are_stored(factory, ws):
    t = trace(trace_id="t1", input="my email is ana@gmail.com", output="ok", model="gpt-4o-mini", input_tokens=1000, output_tokens=500,
              duration_ms=820.0, tags={"plan": "pro"})
    r = ingest(factory, ws.id, [t])
    assert (r.accepted, r.duplicates, r.rejected) == (1, 0, [])
    row, _ = get_trace(factory, ws.id, "t1")
    assert row.input_enc is None and row.output_enc is None and row.context_enc is None      # No text.
    assert row.input_chars == 25 and row.output_chars == 2 and row.redactions == {"EMAIL": 1}
    assert row.duration_ms == 820.0 and row.tags == {"plan": "pro"} and row.cost_usd == pytest.approx((1000 * 0.15 + 500 * 0.60) / 1e6)
    assert row.input_hash and row.status == "ok"


def test_content_is_stored_redacted_and_encrypted_when_the_workspace_asks(factory, ws, key):
    update_policy(factory, ws.id, store_content=True)
    ingest(factory, ws.id, [trace(trace_id="t1", input="mail ana@gmail.com please", output="Done.", context="Refunds take 30 days.")])
    row, _ = get_trace(factory, ws.id, "t1")
    assert "gmail" not in row.input_enc and "Done" not in row.output_enc
    assert reveal(row) == {"input": "mail [EMAIL] please", "output": "Done.", "context": "Refunds take 30 days."}


def test_content_without_a_key_is_rejected_and_nothing_is_stored(factory, ws, key, monkeypatch):
    update_policy(factory, ws.id, store_content=True)
    monkeypatch.delenv("TRUSTBENCH_SECRET_KEY")
    r = ingest(factory, ws.id, [trace(trace_id="t1", input="x"), trace(trace_id="t2")])
    assert r.accepted == 1 and r.rejected[0][0] == 0 and "TRUSTBENCH_SECRET_KEY" in r.rejected[0][1]
    assert count_traces(factory, ws.id) == 1                # t2 has no text, so it needs no key.


def test_sending_a_trace_twice_stores_it_once(factory, ws):
    t = trace(trace_id="t1")
    assert ingest(factory, ws.id, [t]).accepted == 1
    r = ingest(factory, ws.id, [t, trace(trace_id="t2"), trace(trace_id="t2")])
    assert (r.accepted, r.duplicates) == (1, 2) and count_traces(factory, ws.id) == 2


def test_workspaces_cannot_see_each_other(factory, ws):
    other = create_workspace(factory, "other")
    ingest(factory, ws.id, [trace(trace_id="same")])
    ingest(factory, other.id, [trace(trace_id="same")])        # The same id in another workspace is a different trace.
    assert count_traces(factory, ws.id) == count_traces(factory, other.id) == 1
    assert get_trace(factory, other.id, "same")[0].workspace_id == other.id
    only_a = ingest(factory, ws.id, [trace(trace_id="only-a")])
    assert get_trace(factory, other.id, "only-a") is None and only_a.accepted == 1
    assert [t.trace_id for t in list_traces(factory, other.id)] == ["same"]
    assert not delete_trace(factory, other.id, "only-a") and get_trace(factory, ws.id, "only-a")


def test_spans_form_a_tree_and_their_text_is_redacted(factory, ws):
    t = trace(trace_id="t1", spans=[SpanIn(span_id="a", kind="retrieval", name="search", duration_ms=40, attrs={"query": "refund for ana@gmail.com", "hits": 3}),
                                    SpanIn(span_id="b", parent_id="a", kind="tool", name="lookup", offset_ms=40, duration_ms=5, status="error")])
    ingest(factory, ws.id, [t])
    _, spans = get_trace(factory, ws.id, "t1")
    assert [(s.span_id, s.parent_id, s.kind) for s in spans] == [("a", None, "retrieval"), ("b", "a", "tool")]
    assert spans[0].attrs == {"query": "refund for [EMAIL]", "hits": 3}


def test_limits_and_validation():
    with pytest.raises(ValueError, match="at most 200 spans"):
        TraceIn(spans=[SpanIn(name="s")] * 201)
    with pytest.raises(ValueError, match="at most 20 tags"):
        TraceIn(tags={str(i): i for i in range(21)})
    with pytest.raises(ValueError, match="feedback"):
        TraceIn(feedback=5)
    with pytest.raises(ValueError):
        TraceIn(status="maybe")
    with pytest.raises(ValueError):
        TraceIn(unknown_field=1)
    with pytest.raises(ValueError):
        TraceIn(duration_ms=-1)


def test_a_batch_has_a_size_limit(factory, ws):
    with pytest.raises(ValueError, match=str(MAX_BATCH)):
        ingest(factory, ws.id, [trace()] * (MAX_BATCH + 1))
    with pytest.raises(ValueError, match="Unknown workspace"):
        ingest(factory, "nope", [trace()])


def test_retention_and_purge(factory, ws):
    update_policy(factory, ws.id, retention_days=7)
    old = dt.datetime(2024, 1, 1)
    ingest(factory, ws.id, [trace(trace_id="old", started_at=old, spans=[SpanIn(name="s")]), trace(trace_id="new")])
    row, _ = get_trace(factory, ws.id, "old")
    assert row.expires_at == old + dt.timedelta(days=7)
    assert purge_expired(factory) == 1
    assert get_trace(factory, ws.id, "old") is None and get_trace(factory, ws.id, "new")
    from sqlalchemy import select
    with factory() as s:
        assert s.scalars(select(TraceSpan)).all() == []     # The spans went with the trace.
    assert purge_expired(factory) == 0


def test_time_zones_are_normalized_to_utc(factory, ws):
    aware = dt.datetime(2025, 6, 1, 12, 0, tzinfo=dt.timezone(dt.timedelta(hours=2)))
    ingest(factory, ws.id, [trace(trace_id="t1", started_at=aware)])
    assert get_trace(factory, ws.id, "t1")[0].started_at == dt.datetime(2025, 6, 1, 10, 0)


def test_listing_filters_and_order(factory, ws):
    base = dt.datetime(2025, 1, 1)
    ingest(factory, ws.id, [trace(trace_id=f"t{i}", started_at=base + dt.timedelta(hours=i), status="error" if i % 2 else "ok",
                                  model="m1" if i < 3 else "m2", name="a" if i < 4 else "b") for i in range(6)])
    assert [t.trace_id for t in list_traces(factory, ws.id)] == ["t5", "t4", "t3", "t2", "t1", "t0"]
    assert [t.trace_id for t in list_traces(factory, ws.id, status="error")] == ["t5", "t3", "t1"]
    assert [t.trace_id for t in list_traces(factory, ws.id, model="m1")] == ["t2", "t1", "t0"]
    assert [t.trace_id for t in list_traces(factory, ws.id, name="b")] == ["t5", "t4"]
    assert [t.trace_id for t in list_traces(factory, ws.id, since=base + dt.timedelta(hours=2), until=base + dt.timedelta(hours=4))] == ["t3", "t2"]
    assert [t.trace_id for t in list_traces(factory, ws.id, limit=2, offset=1)] == ["t4", "t3"]


def test_sampling_is_stable_and_follows_the_rate():
    assert is_sampled("w", "t", 0.5) == is_sampled("w", "t", 0.5)
    assert not is_sampled("w", "t", 0.0) and is_sampled("w", "t", 1.0)
    hits = sum(is_sampled("w", f"trace-{i}", 0.1) for i in range(5000))
    assert 400 < hits < 600


def test_sampled_traces_are_reported_and_marked_pending(factory, ws):
    update_policy(factory, ws.id, sample_rate=1.0)
    r = ingest(factory, ws.id, [trace(trace_id="t1")])
    assert len(r.sampled) == 1 and get_trace(factory, ws.id, "t1")[0].eval_state == "pending"
    update_policy(factory, ws.id, sample_rate=0.0)
    r2 = ingest(factory, ws.id, [trace(trace_id="t2")])
    assert r2.sampled == [] and get_trace(factory, ws.id, "t2")[0].eval_state == "not_sampled"


def test_long_text_is_cut_and_marked(factory, ws):
    update_policy(factory, ws.id, max_payload_chars=100)
    ingest(factory, ws.id, [trace(trace_id="t1", input="x" * 500)])
    row, _ = get_trace(factory, ws.id, "t1")
    assert row.truncated and row.input_chars == 500
