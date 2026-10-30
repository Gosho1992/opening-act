"""Run-level spending cap for paid search calls. Thread-safe; one Budget per agent run or comparison."""
from __future__ import annotations

import threading


class BudgetExceeded(Exception):
    """Raised before a paid call that would go over the cap. Completed work is kept."""


class Budget:
    def __init__(self, limit: int | None):
        self.limit = limit if limit is None or limit >= 0 else 0
        self.live = 0      # paid calls made (≈ credits for Tavily basic search)
        self.cached = 0    # calls answered from the cache
        self.exhausted = False
        self._lock = threading.Lock()

    def spend(self, n: int = 1) -> None:
        with self._lock:
            if self.limit is not None and self.live + n > self.limit:
                self.exhausted = True
                raise BudgetExceeded(f"Search budget of {self.limit} reached")
            self.live += n

    def hit(self) -> None:
        with self._lock:
            self.cached += 1

    def summary(self) -> dict:
        return {"live_searches": self.live, "cached_searches": self.cached, "limit": self.limit,
                "exhausted": self.exhausted}
