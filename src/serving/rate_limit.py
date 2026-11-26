"""Per-client request rate limit (sliding window), in process.

The API runs one gunicorn worker per container (see `docker/Dockerfile.api`), so an in-process
counter is exact for that container. With several replicas the effective limit is per replica: a
ceiling against floods and runaway clients, not an exact quota. Behind a proxy the client address
is the proxy's; this project has no load balancer in front of the API, and the frontend server
counts as one client.
"""

from __future__ import annotations

import threading
import time
from collections import deque

PRUNE_ABOVE = 10_000  # distinct clients tracked before idle ones are dropped


class RateLimiter:
    def __init__(self, per_minute: int, window_s: float = 60.0) -> None:
        self.limit = per_minute
        self.window = window_s
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def allow(self, client: str, now: float | None = None) -> tuple[bool, int]:
        """(allowed, seconds to wait before retrying when refused)."""
        stamp = time.monotonic() if now is None else now
        with self._lock:
            if len(self._hits) > PRUNE_ABOVE:
                self._prune(stamp)
            hits = self._hits.setdefault(client, deque())
            while hits and stamp - hits[0] >= self.window:
                hits.popleft()
            if len(hits) >= self.limit:
                return False, max(1, int(self.window - (stamp - hits[0])) + 1)
            hits.append(stamp)
            return True, 0

    def _prune(self, stamp: float) -> None:
        for client in [c for c, h in self._hits.items() if not h or stamp - h[-1] >= self.window]:
            del self._hits[client]
