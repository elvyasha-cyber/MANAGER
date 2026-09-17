from __future__ import annotations

import time


class RateLimiter:
    """At most N requests per 60 seconds for one client_id."""

    def __init__(self, max_per_minute: int) -> None:
        self.max_per_minute = max(1, max_per_minute)
        self._hits: dict[str, list[float]] = {}

    def allow(self, client_id: str) -> bool:
        now = time.monotonic()
        window_start = now - 60
        recent = [stamp for stamp in self._hits.get(client_id, []) if stamp > window_start]
        if len(recent) >= self.max_per_minute:
            self._hits[client_id] = recent
            return False
        recent.append(now)
        self._hits[client_id] = recent
        return True
