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
    return results
