"""Which screen is editing a reading's review.

Only a notice: a second screen is told the reading is already open and may take it over; the
first screen then learns it lost it. Saving is still guarded by the result revision, so this
never decides what is stored. Kept in memory: a restart simply forgets who had it open.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

# A screen renews every 20 s; one silent for this long (closed, crashed, asleep) no longer counts.
EXPIRY_SECONDS = 90.0


@dataclass
class _Holder:
    viewer_id: str
    since: float
    seen: float


@dataclass(frozen=True)
class Presence:
    held: bool
    other_since: float | None = None


_holders: dict[str, _Holder] = {}
_lock = threading.Lock()


def claim(job_id: str, viewer_id: str, take_over: bool) -> Presence:
    """Renew this screen's hold, take it over, or report the screen that has it."""
    now = time.time()
    with _lock:
        holder = _holders.get(job_id)
        if holder is not None and now - holder.seen > EXPIRY_SECONDS:
            holder = None
        if holder is None or holder.viewer_id == viewer_id or take_over:
            since = holder.since if holder is not None and holder.viewer_id == viewer_id else now
            _holders[job_id] = _Holder(viewer_id, since, now)
            return Presence(held=True)
        return Presence(held=False, other_since=holder.since)


def release(job_id: str, viewer_id: str) -> None:
    with _lock:
        holder = _holders.get(job_id)
        if holder is not None and holder.viewer_id == viewer_id:
            del _holders[job_id]
