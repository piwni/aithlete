"""Race auto-detection from activity history."""

from __future__ import annotations

import datetime as dt

import pandas as pd

from aithlete.analysis.races import detect_triathlon_results
from aithlete.models.profile import DistanceBucket


def _act(date, start, sport, dist_m, elapsed_s, name=None):
    return {
        "id": f"{date}-{sport}-{start}", "date": dt.date.fromisoformat(date),
        "start_time": f"{date}T{start}", "sport": sport, "type": sport,
        "name": name, "distance_m": dist_m, "elapsed_s": elapsed_s, "duration_s": elapsed_s,
        "tss": 0.0,
    }


def _quarter_day(date, name_prefix):
    # swim -> T1 -> bike -> T2 -> run, classic 1/4 IM distances.
    return [
        _act(date, "08:00:00", "openwaterswim", 950, 1100, f"{name_prefix} Swim"),
        _act(date, "08:20:00", "transition", 0, 120, f"{name_prefix} Multisport"),
        _act(date, "08:22:00", "bike", 45000, 4500, f"{name_prefix} Ride"),
        _act(date, "09:40:00", "transition", 0, 90, f"{name_prefix} Multisport"),
        _act(date, "09:42:00", "run", 10500, 3000, f"{name_prefix} Run"),
    ]


def test_detects_triathlon_and_classifies_quarter():
    df = pd.DataFrame(_quarter_day("2025-06-13", "Mietków"))
    results = detect_triathlon_results(df)
    assert len(results) == 1
    r = results[0]
    assert r.bucket is DistanceBucket.QUARTER          # 1/4, not 1/2 or full
    assert r.event_name == "Mietków"                   # discipline words stripped
    assert r.total_time_s == 1100 + 120 + 4500 + 90 + 3000
    assert r.t1_s == 120 and r.t2_s == 90
    assert r.swim_time_s == 1100 and r.run_time_s == 3000


def test_full_and_half_buckets_and_best_times():
    rows = []
    rows += _quarter_day("2024-06-08", "Spring")
    # A full IM day.
    rows += [
        _act("2025-09-07", "07:00:00", "openwaterswim", 3800, 4200, "Castle Triathlon Malbork Swim"),
        _act("2025-09-07", "08:10:00", "transition", 0, 300, "Castle Triathlon Malbork"),
        _act("2025-09-07", "08:15:00", "bike", 180000, 21600, "Castle Triathlon Malbork Ride"),
        _act("2025-09-07", "14:15:00", "transition", 0, 300, "Castle Triathlon Malbork"),
        _act("2025-09-07", "14:20:00", "run", 42200, 15600, "Castle Triathlon Malbork Run"),
    ]
    df = pd.DataFrame(rows)
    results = detect_triathlon_results(df)
    buckets = {r.bucket for r in results}
    assert DistanceBucket.QUARTER in buckets
    assert DistanceBucket.FULL in buckets
    full = next(r for r in results if r.bucket is DistanceBucket.FULL)
    assert full.event_name == "Castle Triathlon Malbork"


def test_brick_without_transition_is_not_a_race():
    # swim+bike+run on one day but NO transition activity -> training, not a race.
    rows = [
        _act("2025-04-01", "08:00:00", "swim", 2000, 2400, "Swim set"),
        _act("2025-04-01", "10:00:00", "bike", 60000, 7200, "Long ride"),
        _act("2025-04-01", "12:30:00", "run", 8000, 2400, "Brick run"),
    ]
    assert detect_triathlon_results(pd.DataFrame(rows)) == []


def test_empty_input():
    assert detect_triathlon_results(pd.DataFrame()) == []


def test_split_half_without_transition():
    """Bike+run at half-IM distances without transition (Gdynia 2023 pattern)."""
    rows = [
        _act("2023-08-06", "08:00:00", "bike", 88273, 10399, "Outdoor Cycling"),
        _act("2023-08-06", "12:00:00", "run", 20748, 7047, "Outdoor Running"),
    ]
    results = detect_triathlon_results(pd.DataFrame(rows))
    assert len(results) == 1
    r = results[0]
    assert r.bucket is DistanceBucket.HALF
    assert r.bike_time_s == 10399 and r.run_time_s == 7047
    assert r.swim_time_s is None


def test_monolithic_workout_half():
    """Single long Garmin Workout file (Syców 2025 pattern)."""
    rows = [_act("2025-07-13", "07:00:00", "workout", 0, 18163, "Morning Workout")]
    results = detect_triathlon_results(pd.DataFrame(rows))
    assert len(results) == 1
    assert results[0].bucket is DistanceBucket.HALF
    assert results[0].total_time_s == 18163


def test_merge_manual_races_adds_event_name():
    from aithlete.analysis.races import merge_manual_races

    auto = detect_triathlon_results(pd.DataFrame([
        _act("2023-08-06", "08:00:00", "bike", 88273, 10399, "Outdoor Cycling"),
        _act("2023-08-06", "12:00:00", "run", 20748, 7047, "Outdoor Running"),
    ]))
    merged = merge_manual_races(auto, {"races": [{
        "date": "2023-08-06", "event_name": "Ironman 70.3 Gdynia",
    }]})
    assert merged[0].event_name == "Ironman 70.3 Gdynia"
