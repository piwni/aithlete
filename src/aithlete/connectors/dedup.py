"""Source-of-truth + dedup matrix.

The same ride can arrive from multiple places (intervals.icu and Open Wearables
both ingest Garmin/Strava), and recovery data can come from either. To avoid
double-counted load and conflicting metrics we declare an explicit canonical
source per concern and dedup activities by (start_time +/- tolerance, sport,
duration +/- tolerance).

Source-of-truth matrix
----------------------
- Activities, streams, load (CTL/ATL/TSB), power/pace curves, eFTP -> intervals.icu
- Recovery, sleep, HRV, RHR (Oura/Whoop)                          -> Open Wearables
"""

from __future__ import annotations

import datetime as dt

# Canonical source per concern (documented + used by callers).
SOURCE_OF_TRUTH = {
    "activities": "intervals",
    "load": "intervals",
    "curves": "intervals",
    "eftp": "intervals",
    "recovery": "open_wearables",
    "sleep": "open_wearables",
    "hrv": "open_wearables",
    "resting_hr": "open_wearables",
}

# Dedup tolerances.
START_TOLERANCE_S = 20 * 60   # activities within 20 min of each other...
DURATION_TOLERANCE_FRAC = 0.10  # ...and within 10% duration are the same session.


def _sport_of(activity: dict) -> str:
    return {"Swim": "swim", "Ride": "bike", "Run": "run"}.get(
        activity.get("type", ""), activity.get("type", "other").lower()
    )


def _start_of(activity: dict) -> dt.datetime:
    return dt.datetime.fromisoformat(activity["start_date_local"])


def _duration_of(activity: dict) -> float:
    return float(activity.get("moving_time") or activity.get("elapsed_time") or 0)


def dedup_activities(activities: list[dict], priority: list[str] | None = None) -> list[dict]:
    """Collapse duplicate activities. ``priority`` is a list of source names
    (high to low); the kept copy is the one from the highest-priority source.

    Each activity may carry a ``_source`` key; defaults to "intervals".
    """
    priority = priority or ["intervals", "open_wearables", "strava"]

    def rank(act: dict) -> int:
        src = act.get("_source", "intervals")
        return priority.index(src) if src in priority else len(priority)

    # Skip events with no parseable start time rather than crashing.
    activities = [a for a in activities if a.get("start_date_local")]

    kept: list[dict] = []
    for act in sorted(activities, key=rank):
        s, d, sport = _start_of(act), _duration_of(act), _sport_of(act)
        dup = False
        for k in kept:
            if _sport_of(k) != sport:
                continue
            if abs((_start_of(k) - s).total_seconds()) > START_TOLERANCE_S:
                continue
            dk = _duration_of(k)
            denom = max(dk, d, 1.0)
            if abs(dk - d) / denom <= DURATION_TOLERANCE_FRAC:
                dup = True
                break
        if not dup:
            kept.append(act)
    kept.sort(key=_start_of)
    return kept


def merge_wellness(intervals_rows: list[dict], ow_rows: list[dict]) -> list[dict]:
    """Merge daily wellness, preferring Open Wearables for recovery fields
    (its source-of-truth) and intervals for anything else.

    Returns rows: {date, hrv_rmssd_ms, resting_hr_bpm, sleep_hours, weight_kg, source}.
    """
    by_date: dict[str, dict] = {}

    # Start from intervals wellness (broad coverage), normalized.
    for r in intervals_rows:
        day = r.get("id") or r.get("date")
        by_date[day] = {
            "date": day,
            "hrv_rmssd_ms": r.get("hrv"),
            "resting_hr_bpm": r.get("restingHR"),
            "sleep_hours": round(r["sleepSecs"] / 3600, 2) if r.get("sleepSecs") else None,
            "weight_kg": r.get("weight"),
            # intervals' canonical fitness series (carries full history). Captured
            # so analysis can prefer it over the cold-start local recompute.
            "ctl": r.get("ctl"),
            "atl": r.get("atl"),
            "source": "intervals",
        }

    # Overlay Open Wearables recovery fields (canonical).
    for r in ow_rows:
        day = r["date"]
        row = by_date.setdefault(day, {"date": day, "weight_kg": None})
        if r.get("hrv_rmssd_ms") is not None:
            row["hrv_rmssd_ms"] = r["hrv_rmssd_ms"]
        if r.get("resting_hr_bpm") is not None:
            row["resting_hr_bpm"] = r["resting_hr_bpm"]
        if r.get("sleep_hours") is not None:
            row["sleep_hours"] = r["sleep_hours"]
        row["source"] = "open_wearables"

    return [by_date[d] for d in sorted(by_date)]
