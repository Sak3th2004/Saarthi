"""Timezone helpers shared by every repository backend."""

from __future__ import annotations

from datetime import datetime, timezone


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def ensure_aware(dt: datetime) -> datetime:
    """Treat a naive datetime as UTC so all comparisons are safe."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt
