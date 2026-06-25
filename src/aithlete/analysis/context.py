"""Build the bounded context digest the agent reads.

Compresses the heavy raw data into a small, provenance-tagged summary. Every
value carries a unit + date range + sample count. Missing data is listed
explicitly so the agent knows what it doesn't know.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd

from aithlete.analysis import metrics
from aithlete.analysis.loaders import data_as_of
from aithlete.models.common import Provenance, Sport, Tracked, Trend
from aithlete.models.context_digest import (
    ContextDigest,
    DigestValue,
    LoadSummary,
    PRRow,
    UpcomingRace,
)
from aithlete.models.profile import AthleteProfile
from aithlete.models.readiness import ReadinessFlag, ReadinessReport


def _dv(t: Tracked, unit: str, *, date_from=None, date_to=None, samples=0, trend=Trend.UNKNOWN) -> DigestValue:
    return DigestValue(
        value=float(t.value) if t.known else None,
        unit=unit,
        provenance=t.provenance,
        source=t.source,
        date_from=date_from,
        date_to=date_to,
        sample_count=samples,
        trend=trend,
    )


def _load_summary(load: dict, key: str, weekly: list[dict], sport: str | None, as_of: dt.date) -> LoadSummary:
    d = load.get(key, {})
    prov = Provenance.ESTIMATED if d else Provenance.UNKNOWN
    src = "computed (Banister)"

    def mk(field):
        val = d.get(field)
        return DigestValue(value=val, unit="TSS/day" if field != "tsb" else "TSB",
                           provenance=prov if val is not None else Provenance.UNKNOWN,
                           source=src if val is not None else None,
                           date_to=as_of, trend=Trend(d["trend"]) if d.get("trend") else Trend.UNKNOWN)

    if sport:
        tss_hist = [w["by_sport_tss"].get(sport, 0.0) for w in weekly]
        hours_hist = []  # per-sport hours omitted to keep digest bounded
    else:
        tss_hist = [w["tss"] for w in weekly]
        hours_hist = [w["hours"] for w in weekly]

    return LoadSummary(
        ctl=mk("ctl"), atl=mk("atl"), tsb=mk("tsb"),
        weekly_tss_last_4=tss_hist,
        weekly_hours_last_4=hours_hist,
    )


def build_digest(
    profile: AthleteProfile,
    activities: pd.DataFrame,
    wellness: pd.DataFrame,
    *,
    readiness: ReadinessReport | None = None,
    calendar: list[dict] | None = None,
    athlete_id: str = "athlete",
) -> ContextDigest:
    as_of = data_as_of(activities, wellness)
    window_from = (min(activities["date"]) if not activities.empty else as_of)
    digest = ContextDigest(athlete_id=athlete_id, window_from=window_from, window_to=as_of)

    # Physiology anchors.
    digest.ftp_w = _dv(profile.bike.ftp_w, "W", date_to=as_of)
    digest.run_threshold_pace_s_per_km = _dv(profile.run.threshold_pace_s_per_km, "s/km", date_to=as_of)
    digest.css_s_per_100m = _dv(profile.swim.css_s_per_100m, "s/100m", date_to=as_of)
    digest.vo2max_bike = _dv(profile.bike.vo2max, "ml/kg/min", date_to=as_of)
    digest.hrv_baseline_60d_ms = _dv(profile.health.hrv_baseline_60d_ms, "ms",
                                     date_from=as_of - dt.timedelta(days=60), date_to=as_of,
                                     trend=profile.health.hrv_trend)
    digest.resting_hr_bpm = _dv(profile.health.resting_hr_baseline_bpm, "bpm",
                                date_to=as_of, trend=profile.health.resting_hr_trend)

    # Load.
    load = metrics.latest_load(activities)
    weekly = metrics.weekly_summary(activities, weeks=4)
    digest.load_combined = _load_summary(load, "combined", weekly, None, as_of)
    digest.load_by_sport = {
        Sport(s): _load_summary(load, s, weekly, s, as_of) for s in metrics.SPORTS
    }

    # Durability + frequency.
    dur = metrics.durability_by_sport(activities)
    for s, vals in dur.items():
        if vals.get("decoupling_pct") is not None:
            digest.decoupling_pct_by_sport[Sport(s)] = DigestValue(
                value=vals["decoupling_pct"], unit="%", provenance=Provenance.ESTIMATED,
                source="computed Pw:Hr/Pa:Hr", date_to=as_of,
            )
    spw = metrics.sessions_per_week_by_sport(activities)
    digest.sessions_per_week_by_sport = {Sport(s): v for s, v in spw.items()}

    # PRs.
    for bucket, bt in profile.best_times().items():
        digest.personal_records.append(PRRow(
            bucket=bucket.value, total_time_s=bt.total_time_s, date=bt.date,
        ))

    # Upcoming races from calendar.
    for ev in calendar or []:
        rdate = dt.date.fromisoformat(ev["date"])
        digest.upcoming_races.append(UpcomingRace(
            name=ev.get("name", "race"), date=rdate, bucket=ev.get("bucket", "1/4"),
            priority=ev.get("priority", "A"), weeks_out=max(0, (rdate - as_of).days // 7),
        ))

    # Readiness.
    if readiness is not None:
        digest.readiness_flag = readiness.overall_flag
        digest.readiness_notes = [f"{r.rule}: {r.message}" for r in readiness.results
                                  if r.flag is not ReadinessFlag.GREEN]

    # Honesty: what's missing.
    digest.missing = _collect_missing(profile, activities, wellness)
    return digest


def _collect_missing(profile: AthleteProfile, activities: pd.DataFrame, wellness: pd.DataFrame) -> list[str]:
    missing: list[str] = []
    if not profile.bike.ftp_w.known:
        missing.append("bike FTP unknown")
    if not profile.run.threshold_pace_s_per_km.known:
        missing.append("run threshold pace unknown")
    if not profile.swim.css_s_per_100m.known:
        missing.append("swim CSS unknown")
    if not profile.health.hrv_baseline_60d_ms.known:
        missing.append("HRV baseline unknown (no wearable recovery data)")
    if wellness.empty:
        missing.append("no wellness/recovery data")
    if not profile.results:
        missing.append("no race results on file")
    return missing
