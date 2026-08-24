"""Disk cache for FortyGuard responses.

The judging requirement is a public demo that works with zero setup. That means
the app must render correctly with the network removed, so every FortyGuard
response is written to disk the first time it is fetched and served from there
afterwards. A cache read is not a silent success: it carries its own freshness
flag up to the API surface, which the UI renders as the Live/Cached badge.

The cache key is the endpoint plus a canonical hash of the request payload, so
two logically identical requests hit the same entry regardless of dict ordering.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slug(value: str) -> str:
    return _SLUG_RE.sub("-", value.lower()).strip("-")


def canonical_key(endpoint: str, payload: dict[str, Any]) -> str:
    """Stable cache key for one request.

    ``sort_keys`` makes the hash independent of dict insertion order, and
    ``separators`` removes whitespace so formatting cannot change the key.
    """
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    digest = hashlib.sha256(blob.encode("utf-8")).hexdigest()[:20]
    return f"{_slug(endpoint)}__{digest}"


@dataclass(slots=True, frozen=True)
class CacheEntry:
    """A stored response plus the provenance needed to be honest about it."""

    key: str
    endpoint: str
    payload: dict[str, Any]
    result: Any
    fetched_at: datetime
    activity_id: str | None

    @property
    def age_seconds(self) -> float:
        return (datetime.now(timezone.utc) - self.fetched_at).total_seconds()


class DiskCache:
    """A flat JSON-file cache. No eviction: the corpus is small and bounded by
    the scenario, and losing an entry during judging is worse than disk use."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def path_for(self, key: str) -> Path:
        return self.root / f"{key}.json"

    def get(self, endpoint: str, payload: dict[str, Any]) -> CacheEntry | None:
        key = canonical_key(endpoint, payload)
        path = self.path_for(key)
        if not path.exists():
            return None
        try:
            raw = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            # A corrupt entry is a cache miss, never a crash: the caller can
            # still go live. It is surfaced by leaving the bad file in place
            # for inspection rather than deleting evidence.
            return None
        return CacheEntry(
            key=key,
            endpoint=raw.get("endpoint", endpoint),
            payload=raw.get("payload", payload),
            result=raw.get("result"),
            fetched_at=datetime.fromisoformat(raw["fetched_at"]),
            activity_id=raw.get("activity_id"),
        )

    def put(
        self,
        endpoint: str,
        payload: dict[str, Any],
        result: Any,
        activity_id: str | None = None,
    ) -> CacheEntry:
        key = canonical_key(endpoint, payload)
        entry = CacheEntry(
            key=key,
            endpoint=endpoint,
            payload=payload,
            result=result,
            fetched_at=datetime.now(timezone.utc),
            activity_id=activity_id,
        )
        tmp = self.path_for(key).with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(
                {
                    "endpoint": endpoint,
                    "payload": payload,
                    "result": result,
                    "fetched_at": entry.fetched_at.isoformat(),
                    "activity_id": activity_id,
                },
                indent=2,
                default=str,
            )
        )
        # Atomic replace: a half-written cache file during judging would be a
        # corrupt entry on every subsequent read.
        tmp.replace(self.path_for(key))
        return entry

    def keys(self) -> list[str]:
        return sorted(p.stem for p in self.root.glob("*.json"))
