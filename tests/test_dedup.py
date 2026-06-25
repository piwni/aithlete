"""Source-of-truth / dedup tests."""

from __future__ import annotations

from aithlete.connectors.dedup import dedup_activities, merge_wellness


def _act(start, sport_type, dur, source="intervals"):
    return {"start_date_local": start, "type": sport_type, "moving_time": dur, "_source": source}


def test_dedup_collapses_same_session_from_two_sources():
    acts = [
        _act("2026-06-01T09:00:00", "Ride", 3600, "intervals"),
        _act("2026-06-01T09:05:00", "Ride", 3700, "open_wearables"),  # same ride, slight diff
    ]
    out = dedup_activities(acts)
    assert len(out) == 1
    assert out[0]["_source"] == "intervals"  # higher priority kept


def test_dedup_keeps_distinct_sessions():
    acts = [
        _act("2026-06-01T09:00:00", "Ride", 3600),
        _act("2026-06-01T18:00:00", "Run", 1800),   # different sport + time
        _act("2026-06-01T09:00:00", "Swim", 2000),  # same time, different sport
    ]
    assert len(dedup_activities(acts)) == 3


def test_merge_wellness_prefers_open_wearables_recovery():
    iv = [{"id": "2026-06-01", "hrv": 50, "restingHR": 55, "sleepSecs": 7 * 3600, "weight": 72}]
    ow = [{"date": "2026-06-01", "hrv_rmssd_ms": 80, "resting_hr_bpm": 48, "sleep_hours": 8.0}]
    merged = merge_wellness(iv, ow)
    assert len(merged) == 1
    row = merged[0]
    assert row["hrv_rmssd_ms"] == 80         # OW wins for recovery
    assert row["resting_hr_bpm"] == 48
    assert row["weight_kg"] == 72            # intervals weight retained
    assert row["source"] == "open_wearables"
