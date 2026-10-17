"""A rate limit that lives in the memory of one server process.

Each key gets a number of requests for each minute. Repeated bad keys from one address lock that address for a while.
These counters are not shared between processes. If you run several API processes, a limit is per process, so for a
strict global limit also set one in the proxy in front of the server.
"""

import threading
import time
from collections import deque
from typing import Callable


class RateLimiter:
    def __init__(self, limit: int, window: float = 60.0, clock: Callable[[], float] = time.monotonic):
        self.limit, self.window, self.clock = limit, window, clock
        self._hits: dict[str, deque] = {}
        self._lock = threading.Lock()

    def _trim(self, q: deque, now: float) -> None:
        while q and q[0] <= now - self.window:
            q.popleft()

    def count(self, key: str) -> int:
        with self._lock:
            q = self._hits.get(key)
            if not q:
                return 0
            self._trim(q, self.clock())
            return len(q)

    def hit(self, key: str) -> tuple[bool, float]:
        """Use one request. Returns (allowed, seconds to wait when it is not)."""
        if self.limit <= 0:
            return True, 0.0
        with self._lock:
            now = self.clock()
            q = self._hits.setdefault(key, deque())
            self._trim(q, now)
            if len(q) >= self.limit:
                return False, max(0.0, q[0] + self.window - now)
            q.append(now)
            if len(self._hits) > 20000:          # Do not grow without end when many keys come and go.
                for k in [k for k, v in self._hits.items() if not v or v[-1] <= now - self.window][:5000]:
                    del self._hits[k]
            return True, 0.0

    def record(self, key: str) -> None:
        """Count an event without checking the limit (a failed sign-in)."""
        with self._lock:
            self._hits.setdefault(key, deque()).append(self.clock())

    def blocked(self, key: str) -> float:
        """Seconds until this key may try again, or 0."""
        if self.limit <= 0:
            return 0.0
        with self._lock:
            q = self._hits.get(key)
            if not q:
                return 0.0
            now = self.clock()
            self._trim(q, now)
            return max(0.0, q[0] + self.window - now) if len(q) >= self.limit else 0.0
