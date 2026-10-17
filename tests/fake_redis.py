"""A small in-memory stand-in for the Redis commands that RedisQueue uses.

It is NOT Redis. It copies the behaviour of these commands: INCR, HSET, HGETALL, HINCRBY, ZADD, ZREM, ZRANGEBYSCORE,
ZCARD, SADD, SREM, SMEMBERS, SCARD. Every call takes a lock, like the single thread of a Redis server.
"""

import threading


class FakeRedis:
    def __init__(self):
        self._lock = threading.RLock()
        self.kv, self.hashes, self.zsets, self.sets = {}, {}, {}, {}

    def incr(self, key):
        with self._lock:
            self.kv[key] = self.kv.get(key, 0) + 1
            return self.kv[key]

    def hset(self, key, mapping=None, **kw):
        with self._lock:
            self.hashes.setdefault(key, {}).update({k: str(v) for k, v in (mapping or kw).items()})

    def hgetall(self, key):
        with self._lock:
            return dict(self.hashes.get(key, {}))

    def hincrby(self, key, field, n=1):
        with self._lock:
            h = self.hashes.setdefault(key, {})
            h[field] = str(int(h.get(field, 0)) + n)
            return int(h[field])

    def zadd(self, key, mapping):
        with self._lock:
            self.zsets.setdefault(key, {}).update({k: float(v) for k, v in mapping.items()})

    def zrem(self, key, member):
        with self._lock:
            return 1 if self.zsets.get(key, {}).pop(member, None) is not None else 0

    def zrangebyscore(self, key, lo, hi, start=0, num=None):
        with self._lock:
            items = sorted(((s, m) for m, s in self.zsets.get(key, {}).items() if lo <= s <= hi))
            members = [m for _, m in items][start:]
            return members[:num] if num is not None else members

    def zcard(self, key):
        with self._lock:
            return len(self.zsets.get(key, {}))

    def sadd(self, key, member):
        with self._lock:
            self.sets.setdefault(key, set()).add(member)

    def srem(self, key, member):
        with self._lock:
            s = self.sets.get(key, set())
            had = member in s
            s.discard(member)
            return 1 if had else 0

    def smembers(self, key):
        with self._lock:
            return set(self.sets.get(key, set()))

    def scard(self, key):
        with self._lock:
            return len(self.sets.get(key, set()))
