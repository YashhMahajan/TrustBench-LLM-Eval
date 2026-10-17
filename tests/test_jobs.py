import datetime as dt
import threading

import pytest

from fake_redis import FakeRedis
from trustbench.db import init_db, make_engine, utcnow
from trustbench.jobs import DbQueue, RedisQueue, backoff_seconds, make_queue, run_worker


@pytest.fixture(params=["db", "redis"])
def q(request, factory):
    return DbQueue(factory) if request.param == "db" else RedisQueue(client=FakeRedis())


def test_backoff_grows_and_is_capped():
    assert [backoff_seconds(n) for n in (1, 2, 3, 4)] == [5, 10, 20, 40] and backoff_seconds(30) == 600


def test_jobs_come_out_in_order_and_only_once(q):
    ids = [q.enqueue("a", {"n": i}) for i in range(3)]
    got = [q.claim("w1") for _ in range(4)]
    assert [j.id for j in got[:3]] == ids and got[3] is None
    assert [j.payload["n"] for j in got[:3]] == [0, 1, 2] and all(j.attempts == 1 for j in got[:3])
    assert q.counts()["running"] == 3


def test_a_job_can_wait_for_its_time(q):
    q.enqueue("later", {}, run_after=utcnow() + dt.timedelta(hours=1))
    q.enqueue("now", {})
    assert q.claim("w").kind == "now" and q.claim("w") is None


def test_kinds_filter(q):
    q.enqueue("a", {})
    q.enqueue("b", {})
    assert q.claim("w", ["b"]).kind == "b" and q.claim("w", ["b"]) is None and q.claim("w", ["a"]).kind == "a"


def test_complete_removes_the_job_from_the_queue(q):
    jid = q.enqueue("a", {})
    job = q.claim("w")
    q.complete(job.id)
    assert q.claim("w") is None and jid == job.id and q.counts()["running"] == 0


def test_failure_retries_later_and_then_goes_to_the_dead_letter_list(q):
    q.enqueue("a", {"x": 1}, max_attempts=2)
    j1 = q.claim("w")
    assert q.fail(j1.id, "boom", retry_in=0) == "queued"
    j2 = q.claim("w")
    assert j2.id == j1.id and j2.attempts == 2
    assert q.fail(j2.id, "boom again") == "dead"
    assert q.claim("w") is None and q.counts()["dead"] == 1
    dead = q.dead()
    assert dead[0]["error"] == "boom again" and dead[0]["attempts"] == 2 and dead[0]["kind"] == "a"


def test_a_failed_job_waits_for_the_backoff_time(q):
    q.enqueue("a", {})
    j = q.claim("w")
    q.fail(j.id, "x")                       # Default backoff: 5 seconds.
    assert q.claim("w") is None


def test_dead_jobs_can_be_retried_once(q):
    q.enqueue("a", {}, max_attempts=1)
    j = q.claim("w")
    q.fail(j.id, "x")
    assert q.retry_dead(j.id) is True and q.retry_dead(j.id) is False and q.retry_dead("999") is False
    again = q.claim("w")
    assert again.id == j.id and again.attempts == 1


def test_a_lost_worker_loses_its_lease(q):
    q.enqueue("a", {}, max_attempts=3)
    q.claim("crashed", lease_seconds=-1)         # The lease is over at once, as if the worker had died.
    j = q.claim("healthy")
    assert j is not None and j.attempts == 2


def test_a_job_that_kills_its_worker_on_the_last_try_becomes_dead(q):
    q.enqueue("a", {}, max_attempts=1)
    q.claim("crashed", lease_seconds=-1)
    assert q.claim("healthy") is None and q.counts()["dead"] == 1


def test_many_workers_never_take_the_same_job(tmp_path):
    factory = init_db(make_engine(f"sqlite:///{tmp_path}/q.db"))
    for queue in (DbQueue(factory), RedisQueue(client=FakeRedis())):
        for i in range(60):
            queue.enqueue("a", {"n": i})
        taken, lock = [], threading.Lock()

        def work(name):
            while (job := queue.claim(name)) is not None:
                with lock:
                    taken.append(job.id)
                queue.complete(job.id)

        threads = [threading.Thread(target=work, args=(f"w{i}",)) for i in range(6)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        assert len(taken) == 60 and len(set(taken)) == 60, type(queue).__name__


def test_worker_runs_handlers_retries_and_survives_errors(q):
    seen = []
    q.enqueue("ok", {"n": 1})
    q.enqueue("flaky", {}, max_attempts=2)
    q.enqueue("broken", {}, max_attempts=1)
    q.enqueue("unknown_kind", {})              # No handler: it stays in the queue for another worker.

    def flaky(payload, job):
        seen.append(job.attempts)
        if job.attempts == 1:
            raise RuntimeError("first time fails")

    handlers = {"ok": lambda p, j: seen.append(("ok", p["n"])), "flaky": flaky, "broken": lambda p, j: 1 / 0}
    n = run_worker(q, handlers, concurrency=1, idle_exit=True, poll_interval=0.01)
    # The flaky job waits 5 seconds after its first failure, so one pass handles ok, flaky(1st try) and broken.
    assert n == 3 and ("ok", 1) in seen and seen.count(1) == 1
    dead = q.dead()
    assert [d["kind"] for d in dead] == ["broken"] and "ZeroDivisionError" in dead[0]["error"]
    # The failed flaky job waits for its retry and the job without a handler is untouched.
    assert q.counts()["queued"] == 2


def test_worker_stops_after_max_jobs_and_on_the_stop_event(q):
    for _ in range(5):
        q.enqueue("a", {})
    assert run_worker(q, {"a": lambda p, j: None}, concurrency=1, max_jobs=3, poll_interval=0.01) == 3
    stop = threading.Event()
    threading.Timer(0.1, stop.set).start()
    run_worker(q, {"a": lambda p, j: None}, concurrency=2, stop=stop, poll_interval=0.01)
    assert q.counts()["queued"] == 0


def test_periodic_tasks_run(q):
    ticks = []
    stop = threading.Event()
    threading.Timer(0.25, stop.set).start()
    run_worker(q, {}, concurrency=1, stop=stop, poll_interval=0.01, periodic=[(0.05, lambda: ticks.append(1))])
    assert len(ticks) >= 3


def test_make_queue_picks_the_backend(factory, monkeypatch):
    monkeypatch.delenv("TRUSTBENCH_REDIS_URL", raising=False)
    assert isinstance(make_queue(factory), DbQueue)
    monkeypatch.setenv("TRUSTBENCH_REDIS_URL", "redis://localhost:1/0")
    try:
        make_queue(factory)
    except RuntimeError as e:
        assert "pip install" in str(e)         # The redis package is an optional extra.


def test_a_database_error_does_not_kill_a_worker(factory):
    """The first calls to claim, complete and fail raise errors, as if the database were down for a moment."""
    real = DbQueue(factory)
    real.enqueue("a", {"n": 1})
    real.enqueue("b", {"n": 2}, max_attempts=1)
    broken = {"claim": 2, "complete": 1, "fail": 1}

    class Flaky:
        def __getattr__(self, name):
            fn = getattr(real, name)
            def call(*a, **k):
                if broken.get(name, 0) > 0:
                    broken[name] -= 1
                    raise ConnectionError("database is away")
                return fn(*a, **k)
            return call

    done = []
    handlers = {"a": lambda p, j: done.append("a"), "b": lambda p, j: 1 / 0}
    stop = threading.Event()
    threading.Timer(0.5, stop.set).start()
    run_worker(Flaky(), handlers, concurrency=1, stop=stop, poll_interval=0.01)
    assert done == ["a"]                              # The worker lived through two failed claims and handled the job.
    # Recording the result failed for both jobs, so they stay "running" until the lease ends. Then another run takes them again.
    assert real.counts() == {"queued": 0, "running": 2, "done": 0, "dead": 0}
