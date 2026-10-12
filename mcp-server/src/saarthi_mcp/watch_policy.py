"""Explicit Watch check-in policy — never invents medical meaning from silence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo
import os
import re

from saarthi_mcp.timeutil import ensure_aware


_HHMM = re.compile(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]")


@dataclass(frozen=True)
class WatchPolicy:
    """Caregiver-configured observation window for check-in concerns."""

    timezone: str = "Asia/Calcutta"
    window_start: str = "08:00"
    window_end: str = "21:00"
    grace_hours: int = 2
    low_supply_threshold: int = 10

    @classmethod
    def from_env(cls) -> WatchPolicy:
        tz = os.getenv("SAARTHI_WATCH_TIMEZONE", "Asia/Calcutta").strip() or "Asia/Calcutta"
        start = os.getenv("SAARTHI_WATCH_WINDOW_START", "08:00").strip() or "08:00"
        end = os.getenv("SAARTHI_WATCH_WINDOW_END", "21:00").strip() or "21:00"
        try:
            ZoneInfo(tz)
        except Exception as exc:
            raise ValueError("SAARTHI_WATCH_TIMEZONE must be a valid IANA timezone.") from exc
        if not _HHMM.fullmatch(start) or not _HHMM.fullmatch(end):
            raise ValueError("Watch window times must use HH:MM on a 24-hour clock.")
        grace = int(os.getenv("SAARTHI_WATCH_GRACE_HOURS", "2"))
        if not 0 <= grace <= 24:
            raise ValueError("SAARTHI_WATCH_GRACE_HOURS must be between 0 and 24.")
        threshold = int(os.getenv("SAARTHI_LOW_SUPPLY_THRESHOLD", "10"))
        if not 0 <= threshold <= 9999:
            raise ValueError("SAARTHI_LOW_SUPPLY_THRESHOLD must be between 0 and 9999.")
        return cls(timezone=tz, window_start=start, window_end=end,
                   grace_hours=grace, low_supply_threshold=threshold)

    def zone(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    def _parse_clock(self, value: str) -> time:
        hour, minute = value.split(":")
        return time(hour=int(hour), minute=int(minute))

    def local_now(self, instant: datetime | None = None) -> datetime:
        current = ensure_aware(instant or datetime.now(timezone.utc))
        return current.astimezone(self.zone())

    def in_active_window(self, instant: datetime | None = None) -> bool:
        local = self.local_now(instant)
        start = self._parse_clock(self.window_start)
        end = self._parse_clock(self.window_end)
        clock = local.timetz().replace(tzinfo=None)
        if start <= end:
            return start <= clock <= end
        return clock >= start or clock <= end

    def activity_stale(self, last_activity: datetime | None, *, instant: datetime | None = None) -> bool:
        """True when no saved activity is newer than grace after window start (local day)."""
        local = self.local_now(instant)
        if not self.in_active_window(instant):
            return False
        start = self._parse_clock(self.window_start)
        window_open = datetime.combine(local.date(), start, tzinfo=self.zone())
        if local < window_open + timedelta(hours=self.grace_hours):
            return False
        if last_activity is None:
            return True
        last_local = ensure_aware(last_activity).astimezone(self.zone())
        return last_local < window_open
