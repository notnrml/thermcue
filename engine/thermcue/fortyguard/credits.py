"""Credit ledger.

The hackathon key carries 2,000,000 credits over five weeks and the brief
requires spend to be logged per endpoint from day one. Credits are only deducted
by FortyGuard once a task reaches Completed, so the ledger records completions,
not submissions, and cache hits are recorded separately as avoided spend.
"""

from __future__ import annotations

import json
import threading
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path


class CreditLedger:
    """Append-only JSONL log plus an in-memory tally.

    Thread-safe because the FastAPI app and the agent loop both write to it.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._calls: dict[str, int] = defaultdict(int)
        self._cache_hits: dict[str, int] = defaultdict(int)
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        for line in self.path.read_text().splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            bucket = self._cache_hits if row.get("kind") == "cache_hit" else self._calls
            bucket[row.get("endpoint", "unknown")] += 1

    def _append(self, kind: str, endpoint: str, detail: dict | None = None) -> None:
        row = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "kind": kind,
            "endpoint": endpoint,
            **(detail or {}),
        }
        with self.path.open("a") as fh:
            fh.write(json.dumps(row, default=str) + "\n")

    def record_call(self, endpoint: str, activity_id: str | None = None) -> None:
        """One completed live call: FortyGuard has deducted credits for this."""
        with self._lock:
            self._calls[endpoint] += 1
            self._append("live_call", endpoint, {"activity_id": activity_id})

    def record_cache_hit(self, endpoint: str) -> None:
        with self._lock:
            self._cache_hits[endpoint] += 1
            self._append("cache_hit", endpoint)

    def summary(self) -> dict:
        with self._lock:
            live = dict(self._calls)
            cached = dict(self._cache_hits)
        return {
            "live_calls_by_endpoint": live,
            "cache_hits_by_endpoint": cached,
            "live_calls_total": sum(live.values()),
            "cache_hits_total": sum(cached.values()),
        }
