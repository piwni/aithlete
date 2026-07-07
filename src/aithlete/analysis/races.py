"""Race detection from activity history.

A triathlon is reconstructed from the activity stream without any manual tagging:
a recorded ``Transition`` activity essentially only occurs in a multisport race,
so the swim+bike+run activities sharing that day are grouped into one race. The
finish is the summed elapsed time (swim -> T1 -> bike -> T2 -> run); the format
is classified to the nearest Ironman-fraction bucket (1/8, 1/4, 1/2, full) by
distance. The result feeds ``AthleteProfile.results`` -> ``best_times``.

Detection keys on a ``transition`` activity to avoid false positives from ordinary
big swim+bike+run training days. Duathlons/aquabikes (missing a leg) are skipped.
"""

from __future__ import annotations

import datetime as dt
import re
from collections import Counter

import pandas as pd

from aithlete.knowledge import load_rules
from aithlete.models.profile import DistanceBucket, RaceResult

SWIM_SPORTS = {"swim", "openwaterswim"}
BIKE_SPORTS = {"bike", "virtualride"}
RUN_SPORTS = {"run"}
WORKOUT_SPORTS = {"workout"}

# Half-IM bike/run distance bands (meters) for split-leg detection when Garmin
# logs bike+run without a transition marker (common at big races pre-2024).
_HALF_BIKE_M = (85_000, 92_000)
_HALF_RUN_M = (20_000, 21_500)
# Monolithic Garmin "Workout" finish-time window for a 70.3 (no per-leg export).
_HALF_WORKOUT_S = (270 * 60, 420 * 60)

# Tokens stripped from activity names to recover the event name (e.g.
# "Castle Triathlon Malbork Run" -> "Castle Triathlon Malbork").
_DISCIPLINE_WORDS = re.compile(
    r"\b(run|ride|bike|cycling|swim|t1|t2|transition|multisport|leg|część)\b",
    re.IGNORECASE,
)


def _clean_event_name(names: list[str | None]) -> str | None:
    cleaned: list[str] = []
    for n in names:
        if not n:
            continue
        head = re.split(r"\s*[-–:|]\s*", str(n))[0]      # drop "... - Run"
        head = _DISCIPLINE_WORDS.sub("", head)
        head = re.sub(r"\s{2,}", " ", head).strip(" -–:|")
        if head and head.lower() != "multisport":
            cleaned.append(head)
    if not cleaned:
        return None
    return Counter(cleaned).most_common(1)[0][0]


def _classify(swim_m: float, bike_m: float, run_m: float, distances: dict) -> str:
    """Nearest bucket by relative distance error. Bike/run dominate; swim is
    down-weighted because open-water GPS distances are noisy."""
    best_key, best_score = "full", None
    for key, d in distances.items():
        sc, bc, rc = d["swim_m"], d["bike_km"] * 1000, d["run_km"] * 1000
        score = abs(bike_m - bc) / bc + abs(run_m - rc) / rc + 0.5 * abs(swim_m - sc) / sc
        if best_score is None or score < best_score:
            best_key, best_score = key, score
    return best_key


def _as_date(value) -> dt.date:
    if isinstance(value, dt.date) and not isinstance(value, dt.datetime):
        return value
    if isinstance(value, dt.datetime):
        return value.date()
    return dt.date.fromisoformat(str(value)[:10])


def _classify_bike_run(bike_m: float, run_m: float, distances: dict) -> str:
    """Classify from bike+run only (swim missing / not logged)."""
    best_key, best_score = "full", None
    for key, d in distances.items():
        bc, rc = d["bike_km"] * 1000, d["run_km"] * 1000
        score = abs(bike_m - bc) / bc + abs(run_m - rc) / rc
        if best_score is None or score < best_score:
            best_key, best_score = key, score
    return best_key


def _detect_split_half_days(
    df: pd.DataFrame, existing: set[dt.date], distances: dict,
) -> list[RaceResult]:
    """Bike+run on one day at ~half-IM distances, no transition activity.

    Catches races where the watch exported separate ride/run files but no swim
    or transition (e.g. Ironman 70.3 Gdynia 2023-08-06 in this athlete's history).
    """
    results: list[RaceResult] = []
    for day in sorted(df["date"].unique()):
        day = _as_date(day)
        if day in existing:
            continue
        g = df[df["date"] == day].sort_values("start_time")
        if (g["sport"] == "transition").any():
            continue
        bike, run = g[g["sport"].isin(BIKE_SPORTS)], g[g["sport"].isin(RUN_SPORTS)]
        if bike.empty or run.empty:
            continue
        bike_m, run_m = float(bike["distance_m"].sum()), float(run["distance_m"].sum())
        if not (_HALF_BIKE_M[0] <= bike_m <= _HALF_BIKE_M[1]
                and _HALF_RUN_M[0] <= run_m <= _HALF_RUN_M[1]):
            continue
        if _classify_bike_run(bike_m, run_m, distances) != "1/2":
            continue
        bike_t, run_t = float(bike["elapsed_s"].sum()), float(run["elapsed_s"].sum())
        results.append(RaceResult(
            bucket=DistanceBucket.HALF,
            date=day,
            total_time_s=round(bike_t + run_t) or None,
            bike_time_s=round(bike_t) or None,
            run_time_s=round(run_t) or None,
            event_name=_clean_event_name(list(g["name"])),
        ))
        existing.add(day)
    return results


def _detect_monolithic_workout_days(
    df: pd.DataFrame, existing: set[dt.date], distances: dict,
) -> list[RaceResult]:
    """Single long ``workout`` activity spanning ~half-IM finish time.

    Catches races exported as one Garmin file (e.g. Syców 2025-07-13). Leg splits
    are filled from ``manual_races.json`` when available.
    """
    results: list[RaceResult] = []
    for day in sorted(df["date"].unique()):
        day = _as_date(day)
        if day in existing:
            continue
        g = df[df["date"] == day]
        if (g["sport"] == "transition").any():
            continue
        legs = g[g["sport"].isin(SWIM_SPORTS | BIKE_SPORTS | RUN_SPORTS)]
        if not legs.empty:
            continue  # per-leg files present — other detectors handle it
        workouts = g[g["sport"].isin(WORKOUT_SPORTS)]
        if len(workouts) != 1:
            continue
        elapsed = float(workouts.iloc[0]["elapsed_s"])
        if not (_HALF_WORKOUT_S[0] <= elapsed <= _HALF_WORKOUT_S[1]):
            continue
        results.append(RaceResult(
            bucket=DistanceBucket.HALF,
            date=day,
            total_time_s=round(elapsed) or None,
            event_name=_clean_event_name([workouts.iloc[0]["name"]]),
        ))
        existing.add(day)
    return results


def merge_manual_races(
    auto: list[RaceResult], manual: dict | list | None,
) -> list[RaceResult]:
    """Overlay ``manual_races.json`` entries onto auto-detected results (by date)."""
    specs = manual.get("races", manual) if isinstance(manual, dict) else (manual or [])
    if not specs:
        return auto
    by_date = {r.date: r for r in auto}
    for spec in specs:
        if not spec:
            continue
        day = _as_date(spec["date"])
        bucket = spec.get("bucket")
        patch = {k: v for k, v in spec.items()
                 if k not in ("date", "bucket") and v is not None}
        if bucket is not None:
            patch["bucket"] = DistanceBucket(bucket)
        if day in by_date:
            by_date[day] = by_date[day].model_copy(update=patch)
        else:
            b = patch.pop("bucket", DistanceBucket(spec.get("bucket", "1/2")))
            by_date[day] = RaceResult(date=day, bucket=b, **patch)
    return sorted(by_date.values(), key=lambda r: r.date)


def detect_triathlon_results(activities: pd.DataFrame) -> list[RaceResult]:
    """Reconstruct finished triathlons from the activity history."""
    if activities is None or activities.empty or "sport" not in activities.columns:
        return []

    df = activities.copy()
    for col, default in (("distance_m", 0.0), ("elapsed_s", 0.0), ("name", None),
                         ("start_time", "")):
        if col not in df.columns:
            df[col] = default

    race_days = sorted(df.loc[df["sport"] == "transition", "date"].unique())
    distances = load_rules()["distances"]

    results: list[RaceResult] = []
    for day in race_days:
        g = df[df["date"] == day].sort_values("start_time")
        swim, bike, run = (g[g["sport"].isin(s)] for s in (SWIM_SPORTS, BIKE_SPORTS, RUN_SPORTS))
        if swim.empty or bike.empty or run.empty:
            continue  # not a full swim+bike+run triathlon

        swim_m, bike_m, run_m = (float(x["distance_m"].sum()) for x in (swim, bike, run))
        swim_t, bike_t, run_t = (float(x["elapsed_s"].sum()) for x in (swim, bike, run))
        trans = g[g["sport"] == "transition"].sort_values("start_time")
        t_list = [float(x) for x in trans["elapsed_s"].tolist()]
        total = swim_t + bike_t + run_t + sum(t_list)

        results.append(RaceResult(
            bucket=DistanceBucket(_classify(swim_m, bike_m, run_m, distances)),
            date=_as_date(day),
            total_time_s=round(total) or None,
            swim_time_s=round(swim_t) or None,
            bike_time_s=round(bike_t) or None,
            run_time_s=round(run_t) or None,
            t1_s=round(t_list[0]) if len(t_list) >= 1 else None,
            t2_s=round(t_list[1]) if len(t_list) >= 2 else None,
            event_name=_clean_event_name(list(g["name"])),
        ))

    seen = {r.date for r in results}
    results.extend(_detect_split_half_days(df, seen, distances))
    results.extend(_detect_monolithic_workout_days(df, seen, distances))
    return sorted(results, key=lambda r: r.date)
