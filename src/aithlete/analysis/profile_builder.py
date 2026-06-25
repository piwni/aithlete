"""Build a baseline AthleteProfile from fetched settings + computed metrics.

This produces an objective, provenance-tagged starting profile. The agent then
refines the interpretive fields (strengths/limiters, training age, notes) via the
triathlete-profile skill. Numbers come from Python; judgment comes from the agent.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd

from aithlete.analysis import metrics
from aithlete.models.common import Sport, Tracked, Trend
from aithlete.models.profile import (
    AthleteProfile,
    BikePhysiology,
    Durability,
    HealthBlock,
    LoadState,
    RunPhysiology,
    SwimPhysiology,
    TrainingLoad,
)

INTERVALS = "intervals.icu sport settings"


def _trend(v: str | None) -> Trend:
    try:
        return Trend(v) if v else Trend.UNKNOWN
    except ValueError:
        return Trend.UNKNOWN


def _load_state(d: dict | None, as_of: dt.date) -> LoadState:
    if not d:
        return LoadState()
    return LoadState(
        ctl=Tracked.estimated(d["ctl"], "computed (Banister tau42)", as_of) if d.get("ctl") is not None else Tracked.unknown(),
        atl=Tracked.estimated(d["atl"], "computed (Banister tau7)", as_of) if d.get("atl") is not None else Tracked.unknown(),
        tsb=Tracked.estimated(d["tsb"], "computed (CTL-ATL)", as_of) if d.get("tsb") is not None else Tracked.unknown(),
        as_of=as_of,
        trend=_trend(d.get("trend")),
    )


def build_profile(
    settings: dict,
    activities: pd.DataFrame,
    wellness: pd.DataFrame,
    as_of: dt.date,
    athlete_id: str = "athlete",
) -> AthleteProfile:
    p = AthleteProfile(athlete_id=athlete_id)

    bike = settings.get("bike", {})
    run = settings.get("run", {})
    swim = settings.get("swim", {})
    baselines = metrics.hrv_rhr_baselines(wellness, as_of)
    weight = baselines.get("weight_kg") or settings.get("weight_kg")

    if weight:
        p.anthropometrics.weight_kg = Tracked.estimated(float(weight), "intervals.icu wellness", as_of)

    # Bike physiology.
    ftp = bike.get("ftp_w")
    p.bike = BikePhysiology(
        ftp_w=Tracked.estimated(ftp, INTERVALS, as_of) if ftp else Tracked.unknown(),
        ftp_w_per_kg=Tracked.estimated(round(ftp / weight, 2), "computed", as_of) if ftp and weight else Tracked.unknown(),
        eftp_w=Tracked.estimated(bike["eftp_w"], "intervals.icu eFTP", as_of) if bike.get("eftp_w") else Tracked.unknown(),
        cp_w=Tracked.estimated(bike["cp_w"], "computed CP (Monod-Scherrer)", as_of) if bike.get("cp_w") else Tracked.unknown(),
        w_prime_kj=Tracked.estimated(bike["w_prime_kj"], "computed W' (Skiba)", as_of) if bike.get("w_prime_kj") else Tracked.unknown(),
        vo2max=Tracked.estimated(settings.get("vo2max_bike"), "Firstbeat estimate", as_of) if settings.get("vo2max_bike") else Tracked.unknown(),
        lthr_bpm=Tracked.estimated(bike["lthr_bpm"], INTERVALS, as_of) if bike.get("lthr_bpm") else Tracked.unknown(),
        max_hr_bpm=Tracked.estimated(bike["max_hr_bpm"], INTERVALS, as_of) if bike.get("max_hr_bpm") else Tracked.unknown(),
    )

    # Run physiology.
    p.run = RunPhysiology(
        threshold_pace_s_per_km=Tracked.estimated(run["threshold_pace_s_per_km"], INTERVALS, as_of) if run.get("threshold_pace_s_per_km") else Tracked.unknown(),
        vo2max=Tracked.estimated(run["vo2max"], "Firstbeat estimate", as_of) if run.get("vo2max") else Tracked.unknown(),
        lthr_bpm=Tracked.estimated(run["lthr_bpm"], INTERVALS, as_of) if run.get("lthr_bpm") else Tracked.unknown(),
        max_hr_bpm=Tracked.estimated(run["max_hr_bpm"], INTERVALS, as_of) if run.get("max_hr_bpm") else Tracked.unknown(),
    )

    # Swim physiology.
    p.swim = SwimPhysiology(
        css_s_per_100m=Tracked.estimated(swim["css_s_per_100m"], INTERVALS, as_of) if swim.get("css_s_per_100m") else Tracked.unknown(),
    )

    # Load.
    load = metrics.latest_load(activities)
    p.load = TrainingLoad(
        combined=_load_state(load.get("combined"), as_of),
        swim=_load_state(load.get("swim"), as_of),
        bike=_load_state(load.get("bike"), as_of),
        run=_load_state(load.get("run"), as_of),
    )

    # Durability.
    dur = metrics.durability_by_sport(activities)
    for s, vals in dur.items():
        sport = Sport(s)
        p.durability[sport] = Durability(
            decoupling_pct=Tracked.estimated(vals["decoupling_pct"], "computed Pw:Hr/Pa:Hr", as_of) if vals.get("decoupling_pct") is not None else Tracked.unknown(),
            efficiency_factor=Tracked.estimated(vals["efficiency_factor"], "computed", as_of) if vals.get("efficiency_factor") is not None else Tracked.unknown(),
        )

    # Health.
    p.health = HealthBlock(
        hrv_rmssd_ms=Tracked.estimated(baselines["hrv_latest_ms"], "wearable rMSSD", as_of) if baselines.get("hrv_latest_ms") else Tracked.unknown(),
        hrv_baseline_7d_ms=Tracked.estimated(baselines["hrv_baseline_7d_ms"], "computed 7d mean", as_of) if baselines.get("hrv_baseline_7d_ms") else Tracked.unknown(),
        hrv_baseline_60d_ms=Tracked.estimated(baselines["hrv_baseline_60d_ms"], "computed 60d mean", as_of) if baselines.get("hrv_baseline_60d_ms") else Tracked.unknown(),
        hrv_swc_ms=Tracked.estimated(baselines["hrv_swc_ms"], "computed 0.5*CV", as_of) if baselines.get("hrv_swc_ms") else Tracked.unknown(),
        hrv_trend=_trend(baselines.get("hrv_trend")),
        resting_hr_bpm=Tracked.estimated(baselines["resting_hr_latest_bpm"], "wearable RHR", as_of) if baselines.get("resting_hr_latest_bpm") else Tracked.unknown(),
        resting_hr_baseline_bpm=Tracked.estimated(baselines["resting_hr_baseline_bpm"], "computed 30d mean", as_of) if baselines.get("resting_hr_baseline_bpm") else Tracked.unknown(),
        resting_hr_trend=_trend(baselines.get("resting_hr_trend")),
        sleep_avg_hours=Tracked.estimated(baselines["sleep_avg_hours"], "wearable sleep", as_of) if baselines.get("sleep_avg_hours") else Tracked.unknown(),
    )

    return p.enforce_estimate_only()
