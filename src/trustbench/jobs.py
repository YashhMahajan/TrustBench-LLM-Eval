"""A queue of tasks and the workers that do them.

Two backends with the same interface:
  DbQueue     uses the database. It is the default. It needs nothing else.
  RedisQueue  uses Redis. Use it when many workers share a busy queue.
Choose Redis by setting TRUSTBENCH_REDIS_URL (and install the extra: pip install 'trustbench[redis]').

How a task is handled:
  - a worker takes (claims) a task. The task is invisible to other workers for a lease time
  - if the worker finishes, the task is done
  - if the handler fails, the task waits and is tried again later, up to max_attempts
  - if the worker dies, the lease ends and another worker takes the task
  - a task that fails too often is dead. Dead tasks are kept so that a person can look at them
Tasks run at least once. A handler must be safe to run twice.
"""

import datetime as dt
import json
import logging
import os
import signal
import threading
import uuid
from dataclasses import dataclass
from typing import Callable, Protocol

from sqlalchemy import func, select, update
from sqlalchemy.orm import sessionmaker

from .db import JobRow, utcnow

log = logging.getLogger("trustbench.worker")
LEASE_SECONDS = 120


@dataclass
class Job:
    id: str
    kind: str
    payload: dict
    workspace_id: str | None
    attempts: int
    max_attempts: int


def backoff_seconds(attempts: int) -> float:
    """Wait 5 s after the first failure, then 10 s, 20 s, ... at most 10 minutes."""
    return min(5 * 2 ** max(attempts - 1, 0), 600)


class JobQueue(Protocol):
    def enqueue(self, kind: str, payload: dict, workspace_id: str | None = None, run_after: dt.datetime | None = None,
                max_attempts: int = 3) -> str: ...
    def claim(self, worker_id: str, kinds: list[str] | None = None, lease_seconds: int = LEASE_SECONDS) -> Job | None: ...
    def complete(self, job_id: str) -> None: ...
    def fail(self, job_id: str, error: str, retry_in: float | None = None) -> str: ...      # Returns the new state.
    def counts(self) -> dict[str, int]: ...
    def dead(self, limit: int = 50) -> list[dict]: ...
    def retry_dead(self, job_id: str) -> bool: ...


class DbQueue:
    def __init__(self, factory: sessionmaker):
        self.factory = factory

    def enqueue(self, kind, payload, workspace_id=None, run_after=None, max_attempts=3) -> str:
        with self.factory.begin() as s:
            row = JobRow(kind=kind, payload=payload, workspace_id=workspace_id, max_attempts=max_attempts, run_after=run_after or utcnow())
            s.add(row)
            s.flush()
            return str(row.id)

    def claim(self, worker_id, kinds=None, lease_seconds=LEASE_SECONDS) -> Job | None:
        """Take the oldest task that is ready. Safe when several workers claim at the same time:
        the UPDATE succeeds for one worker only, because it checks the state it saw."""
        now = utcnow()
        for _ in range(5):
            with self.factory.begin() as s:
                ready = ((JobRow.state == "queued") & (JobRow.run_after <= now)) | ((JobRow.state == "running") & (JobRow.lease_expires_at < now))
                q = select(JobRow).where(ready).order_by(JobRow.id).limit(1)
                if kinds:
                    q = q.where(JobRow.kind.in_(kinds))
                row = s.scalars(q).first()
                if row is None:
                    return None
                seen_state, seen_attempts = row.state, row.attempts
                if seen_state == "running" and seen_attempts >= row.max_attempts:    # The worker died on its last try.
                    row.state, row.last_error, row.finished_at = "dead", (row.last_error or "") + " (worker lost on the last attempt)", now
                    continue
                won = s.execute(update(JobRow).where(JobRow.id == row.id, JobRow.state == seen_state, JobRow.attempts == seen_attempts)
                                .values(state="running", attempts=seen_attempts + 1, worker_id=worker_id,
                                        lease_expires_at=now + dt.timedelta(seconds=lease_seconds))).rowcount
                if won:
                    return Job(str(row.id), row.kind, row.payload, row.workspace_id, seen_attempts + 1, row.max_attempts)
        return None

    def complete(self, job_id) -> None:
        with self.factory.begin() as s:
            row = s.get(JobRow, int(job_id))
            if row:
                # The payload may hold encrypted trace text. A finished task does not need it any more.
                row.state, row.finished_at, row.lease_expires_at, row.payload = "done", utcnow(), None, {}

    def fail(self, job_id, error, retry_in=None) -> str:
        with self.factory.begin() as s:
            row = s.get(JobRow, int(job_id))
            if row is None:
                return "dead"
            row.last_error, row.lease_expires_at = error[:2000], None
            if row.attempts >= row.max_attempts:
                row.state, row.finished_at = "dead", utcnow()
            else:
                row.state = "queued"
                row.run_after = utcnow() + dt.timedelta(seconds=backoff_seconds(row.attempts) if retry_in is None else retry_in)
            return row.state

    def counts(self) -> dict[str, int]:
        with self.factory() as s:
            found = dict(s.execute(select(JobRow.state, func.count()).group_by(JobRow.state)).all())
        return {k: found.get(k, 0) for k in ("queued", "running", "done", "dead")}

    def dead(self, limit=50) -> list[dict]:
        with self.factory() as s:
            return [{"id": str(r.id), "kind": r.kind, "attempts": r.attempts, "error": r.last_error, "workspace_id": r.workspace_id}
                    for r in s.scalars(select(JobRow).where(JobRow.state == "dead").order_by(JobRow.id.desc()).limit(limit))]

    def purge_finished(self, older_than: dt.timedelta = dt.timedelta(days=7)) -> int:
        """Delete finished tasks (done) after some days, so the table does not grow without end."""
        from sqlalchemy import delete
        with self.factory.begin() as s:
            return s.execute(delete(JobRow).where(JobRow.state == "done", JobRow.finished_at < utcnow() - older_than)).rowcount

    def retry_dead(self, job_id) -> bool:
        with self.factory.begin() as s:
            row = s.get(JobRow, int(job_id))
            if row is None or row.state != "dead":
                return False
            row.state, row.attempts, row.run_after, row.finished_at = "queued", 0, utcnow(), None
            return True


class RedisQueue:
    """The same queue in Redis. It uses only simple commands, and the atomic ZREM decides who wins a task.

    Keys: tb:seq (counter), tb:ready (sorted set: score = time to run), tb:running (sorted set: score = lease end),
    tb:job:ID (hash with the task), tb:dead (set).
    """

    def __init__(self, client=None, url: str | None = None, prefix: str = "tb"):
        if client is None:
            try:
                import redis
            except ImportError as e:
                raise RuntimeError("Redis support needs the redis package: pip install 'trustbench[redis]'") from e
            client = redis.Redis.from_url(url or os.environ["TRUSTBENCH_REDIS_URL"], decode_responses=True)
        self.r, self.p = client, prefix

    def _k(self, *parts) -> str:
        return ":".join([self.p, *map(str, parts)])

    @staticmethod
    def _ts(t: dt.datetime | None = None) -> float:
        return ((t or utcnow()) - dt.datetime(1970, 1, 1)).total_seconds()

    def enqueue(self, kind, payload, workspace_id=None, run_after=None, max_attempts=3) -> str:
        jid = str(self.r.incr(self._k("seq")))
        self.r.hset(self._k("job", jid), mapping={"kind": kind, "payload": json.dumps(payload), "workspace_id": workspace_id or "",
                                                 "attempts": 0, "max_attempts": max_attempts, "error": "", "state": "queued"})
        self.r.zadd(self._k("ready"), {jid: self._ts(run_after)})
        return jid

    def _load(self, jid: str) -> Job | None:
        h = self.r.hgetall(self._k("job", jid))
        if not h:
            return None
        return Job(jid, h["kind"], json.loads(h["payload"]), h["workspace_id"] or None, int(h["attempts"]), int(h["max_attempts"]))

    def claim(self, worker_id, kinds=None, lease_seconds=LEASE_SECONDS) -> Job | None:
        now = self._ts()
        # Tasks whose lease ended come back first. ZREM returns 1 for one worker only.
        for jid in self.r.zrangebyscore(self._k("running"), 0, now, start=0, num=10):
            if self.r.zrem(self._k("running"), jid) == 1:
                job = self._load(jid)
                if job and job.attempts >= job.max_attempts:
                    self.r.hset(self._k("job", jid), mapping={"state": "dead", "error": "worker lost on the last attempt"})
                    self.r.sadd(self._k("dead"), jid)
                else:
                    self.r.zadd(self._k("ready"), {jid: now})
        for jid in self.r.zrangebyscore(self._k("ready"), 0, now, start=0, num=20):
            job = self._load(jid)
            if job is None:
                self.r.zrem(self._k("ready"), jid)
                continue
            if kinds and job.kind not in kinds:
                continue
            if self.r.zrem(self._k("ready"), jid) != 1:     # Another worker was faster.
                continue
            attempts = self.r.hincrby(self._k("job", jid), "attempts", 1)
            self.r.hset(self._k("job", jid), mapping={"state": "running", "worker": worker_id})
            self.r.zadd(self._k("running"), {jid: now + lease_seconds})
            job.attempts = int(attempts)
            return job
        return None

    def complete(self, job_id) -> None:
        self.r.zrem(self._k("running"), job_id)
        self.r.hset(self._k("job", job_id), mapping={"state": "done", "payload": "{}"})

    def fail(self, job_id, error, retry_in=None) -> str:
        self.r.zrem(self._k("running"), job_id)
        job = self._load(job_id)
        if job is None:
            return "dead"
        self.r.hset(self._k("job", job_id), mapping={"error": error[:2000]})
        if job.attempts >= job.max_attempts:
            self.r.hset(self._k("job", job_id), mapping={"state": "dead"})
            self.r.sadd(self._k("dead"), job_id)
            return "dead"
        wait = backoff_seconds(job.attempts) if retry_in is None else retry_in
        self.r.hset(self._k("job", job_id), mapping={"state": "queued"})
        self.r.zadd(self._k("ready"), {job_id: self._ts() + wait})
        return "queued"

    def counts(self) -> dict[str, int]:
        out = {"queued": 0, "running": self.r.zcard(self._k("running")), "done": 0, "dead": self.r.scard(self._k("dead"))}
        out["queued"] = self.r.zcard(self._k("ready"))
        return out

    def dead(self, limit=50) -> list[dict]:
        rows = []
        for jid in sorted(self.r.smembers(self._k("dead")), key=int, reverse=True)[:limit]:
            h = self.r.hgetall(self._k("job", jid))
            rows.append({"id": jid, "kind": h.get("kind"), "attempts": int(h.get("attempts", 0)), "error": h.get("error"), "workspace_id": h.get("workspace_id") or None})
        return rows

    def retry_dead(self, job_id) -> bool:
        if not self.r.srem(self._k("dead"), job_id):
            return False
        self.r.hset(self._k("job", job_id), mapping={"state": "queued", "attempts": 0})
        self.r.zadd(self._k("ready"), {job_id: self._ts()})
        return True


def make_queue(factory: sessionmaker) -> JobQueue:
    url = os.environ.get("TRUSTBENCH_REDIS_URL")
    return RedisQueue(url=url) if url else DbQueue(factory)


Handler = Callable[[dict, Job], None]


def run_worker(queue: JobQueue, handlers: dict[str, Handler], worker_id: str | None = None, concurrency: int = 2,
               poll_interval: float = 1.0, stop: threading.Event | None = None, max_jobs: int | None = None,
               idle_exit: bool = False, periodic: list[tuple[float, Callable[[], None]]] | None = None) -> int:
    """Work until stopped. Returns the number of tasks that were handled.

    max_jobs     stop after this many tasks (for tests and one-off runs)
    idle_exit    stop when the queue is empty
    periodic     (seconds, function) pairs that run now and then, such as the purge of old traces
    """
    stop = stop or threading.Event()
    worker_id = worker_id or f"{os.uname().nodename}-{os.getpid()}-{uuid.uuid4().hex[:6]}"
    handled, lock = [0], threading.Lock()

    def loop(n: int) -> None:
        wid = f"{worker_id}/{n}"
        while not stop.is_set():
            with lock:
                if max_jobs is not None and handled[0] >= max_jobs:
                    return
            try:
                job = queue.claim(wid, list(handlers))
            except Exception:       # The database or Redis may be down for a moment. The worker waits and tries again.
                log.exception("could not take a task")
                stop.wait(min(poll_interval * 5, 30))
                continue
            if job is None:
                if idle_exit:
                    return
                stop.wait(poll_interval)
                continue
            try:
                handlers[job.kind](job.payload, job)
            except Exception as e:      # Any error of a handler is recorded. The worker goes on.
                try:
                    state = queue.fail(job.id, f"{type(e).__name__}: {e}")
                    log.warning("job %s (%s) failed on attempt %d: %s -> %s", job.id, job.kind, job.attempts, e, state)
                except Exception:
                    log.exception("could not record the failure of job %s. Its lease will end and it will run again.", job.id)
            else:
                try:
                    queue.complete(job.id)
                except Exception:
                    log.exception("could not mark job %s as done. Its lease will end and it will run again.", job.id)
            with lock:
                handled[0] += 1

    def ticker(every: float, fn: Callable[[], None]) -> None:
        while not stop.is_set():
            try:
                fn()
            except Exception:
                log.exception("periodic task failed")
            stop.wait(every)

    threads = [threading.Thread(target=loop, args=(i,), daemon=True) for i in range(concurrency)]
    threads += [threading.Thread(target=ticker, args=p, daemon=True) for p in (periodic or [])]
    for t in threads:
        t.start()
    for t in threads[:concurrency]:
        t.join()
    stop.set()
    return handled[0]


def install_signal_handlers(stop: threading.Event) -> None:
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
