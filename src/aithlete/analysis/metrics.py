"""Training-load and physiology metrics.

We FETCH CTL/ATL/TSB and curves from intervals.icu when available; this module
also computes them from the daily TSS series so we can (a) work offline and
(b) validate intervals' numbers. The model is the Banister impulse-response with
time constants tau_CTL = 42 and tau_ATL = 7. All TSS variants are normalized to
100 = 1 hour at threshold, so combined load is an additive sum.

Refs: Banister (1975); Coggan Performance Manager; intervals.icu fitness model;
Plews et al. (HRV baselines); Hopkins (smallest worthwhile change).
"""

from __future__ import annotations

import datetime as dt
import math

import numpy as np
import pandas as pd

from aithlete.models.common import Trend

TAU_CTL = 42
TAU_ATL = 7
SPORTS = ["swim", "bike", "run"]


def daily_tss(activities: pd.DataFrame) -> pd.DataFrame:
    """Continuous daily TSS series with columns combined/swim/bike/run (0-filled)."""
    if activities.empty:
        return pd.DataFrame(columns=["date", "combined", *SPORTS])
    g = activities.groupby(["date", "sport"])["tss"].sum().unstack(fill_value=0.0)
    # Combined = total fatigue across ALL sports (incl. strength/'other'), so
    # cross-training load is counted in CTL/ATL/ACWR/monotony.
    all_sports_total = g.sum(axis=1)
    for s in SPORTS:
        if s not in g.columns:
            g[s] = 0.0
    g = g[SPORTS]
    g["combined"] = all_sports_total
    idx = pd.date_range(min(activities["date"]), max(activities["date"]), freq="D").date
    g = g.reindex(idx, fill_value=0.0)
    g = g.reset_index(names="date")
    return g


def _impulse_response(series: pd.Series, tau: int) -> pd.Series:
    alpha = 1 - math.exp(-1 / tau)
    out = np.zeros(len(series))
    prev = 0.0
    for i, v in enumerate(series.to_numpy()):
        prev = prev + (v - prev) * alpha
        out[i] = prev
    return pd.Series(out, index=series.index)


def load_series(activities: pd.DataFrame) -> pd.DataFrame:
    """Per-series CTL/ATL/TSB. TSB uses yesterday's CTL-ATL (intervals 'Form')."""
    tss = daily_tss(activities)
    if tss.empty:
        return tss
    out = pd.DataFrame({"date": tss["date"]})
    for col in ["combined", *SPORTS]:
        ctl = _impulse_response(tss[col], TAU_CTL)
        atl = _impulse_response(tss[col], TAU_ATL)
        out[f"{col}_ctl"] = ctl.round(1)
        out[f"{col}_atl"] = atl.round(1)
        out[f"{col}_tsb"] = (ctl.shift(1) - atl.shift(1)).round(1)
    return out


def _trend(series: pd.Series, lookback: int = 14, eps: float = 1.0) -> Trend:
    s = series.dropna()
    if len(s) < lookback + 1:
        return Trend.UNKNOWN
    delta = s.iloc[-1] - s.iloc[-1 - lookback]
    if delta > eps:
        return Trend.UP
    if delta < -eps:
        return Trend.DOWN
    return Trend.FLAT


def latest_load(activities: pd.DataFrame) -> dict:
    ls = load_series(activities)
    if ls.empty:
        return {}
    last = ls.iloc[-1]
    result = {}
    for col in ["combined", *SPORTS]:
        result[col] = {
            "ctl": float(last[f"{col}_ctl"]),
            "atl": float(last[f"{col}_atl"]),
            "tsb": float(last[f"{col}_tsb"]) if not pd.isna(last[f"{col}_tsb"]) else None,
            "trend": _trend(ls[f"{col}_ctl"]).value,
        }
    return result


def fetched_combined_load(wellness: pd.DataFrame, as_of: dt.date | None = None) -> dict | None:
    """Canonical combined CTL/ATL/TSB from intervals' wellness 'ctl'/'atl' series
    when present. Preferred over the local recompute, which is cold-start seeded
    over a bounded window and biases CTL low. Returns None if unavailable.
    """
    if wellness is None or wellness.empty or "ctl" not in wellness.columns:
        return None
    df = wellness.copy()
    df = df[df["ctl"].notna()]
    if as_of is not None and "date" in df.columns:
        df = df[pd.to_datetime(df["date"]).dt.date <= as_of]
    if df.empty:
        return None
    df = df.sort_values("date")
    ctl = float(df["ctl"].iloc[-1])
    atl = float(df["atl"].iloc[-1]) if "atl" in df.columns and not pd.isna(df["atl"].iloc[-1]) else None
    tsb = round(ctl - atl, 1) if atl is not None else None
    return {
        "ctl": round(ctl, 1),
        "atl": round(atl, 1) if atl is not None else None,
        "tsb": tsb,
        "trend": _trend(df["ctl"]).value,
        "source": "intervals.icu (fetched)",
    }


def ctl_ramp_per_week(activities: pd.DataFrame, weeks: int = 4) -> float | None:
    """Mean weekly CTL change over the last ``weeks`` (for validating builds)."""
    ls = load_series(activities)
    if ls.empty or len(ls) < weeks * 7 + 1:
        return None
    end = ls["combined_ctl"].iloc[-1]
    start = ls["combined_ctl"].iloc[-1 - weeks * 7]
    return round((end - start) / weeks, 2)


def weekly_summary(activities: pd.DataFrame, weeks: int = 4) -> list[dict]:
    """Per-ISO-week TSS, hours, and per-sport TSS for the last ``weeks`` weeks."""
    if activities.empty:
        return []
    df = activities.copy()
    df["dt"] = pd.to_datetime(df["date"])
    df["week"] = df["dt"].dt.to_period("W-SUN").dt.start_time
    rows = []
    for week, part in df.groupby("week"):
        rows.append({
            "week_start": week.date().isoformat(),
            "tss": round(float(part["tss"].sum()), 1),
            "hours": round(float(part["duration_s"].sum()) / 3600, 2),
            "by_sport_tss": {s: round(float(part[part.sport == s]["tss"].sum()), 1) for s in SPORTS},
        })
    return rows[-weeks:]


def hrv_rhr_baselines(wellness: pd.DataFrame, as_of: dt.date | None = None) -> dict:
    """HRV 7/60-day baselines + SWC, RHR baseline, sleep average, with trends."""
    if wellness.empty:
        return {}
    df = wellness.copy()
    df = df.sort_values("date")
    as_of = as_of or max(df["date"])
    last60 = df[df["date"] > as_of - dt.timedelta(days=60)]
    last7 = df[df["date"] > as_of - dt.timedelta(days=7)]

    hrv60 = last60["hrv_rmssd_ms"].dropna()
    hrv7 = last7["hrv_rmssd_ms"].dropna()
    swc = None
    if len(hrv60) >= 14 and hrv60.mean() > 0:
        cv = hrv60.std(ddof=1) / hrv60.mean()
        swc = round(0.5 * cv * hrv60.mean(), 2)  # 0.5 * within-person CV, in ms

    rhr60 = last60["resting_hr_bpm"].dropna()

    return {
        "hrv_baseline_7d_ms": round(float(hrv7.mean()), 1) if len(hrv7) else None,
        "hrv_baseline_60d_ms": round(float(hrv60.mean()), 1) if len(hrv60) else None,
        "hrv_swc_ms": swc,
        "hrv_latest_ms": float(df["hrv_rmssd_ms"].dropna().iloc[-1]) if df["hrv_rmssd_ms"].notna().any() else None,
        "hrv_trend": _trend(df.set_index("date")["hrv_rmssd_ms"].dropna().reset_index(drop=True), lookback=14, eps=2.0).value,
        "resting_hr_baseline_bpm": round(float(rhr60.mean()), 1) if len(rhr60) else None,
        "resting_hr_latest_bpm": float(df["resting_hr_bpm"].dropna().iloc[-1]) if df["resting_hr_bpm"].notna().any() else None,
        "resting_hr_trend": _trend(df.set_index("date")["resting_hr_bpm"].dropna().reset_index(drop=True), lookback=14, eps=1.5).value,
        "sleep_avg_hours": round(float(last7["sleep_hours"].dropna().mean()), 2) if last7["sleep_hours"].notna().any() else None,
        "weight_kg": float(df["weight_kg"].dropna().iloc[-1]) if "weight_kg" in df and df["weight_kg"].notna().any() else None,
    }


def durability_by_sport(activities: pd.DataFrame, weeks: int = 8) -> dict:
    """Mean recent aerobic decoupling % and efficiency factor per sport."""
    if activities.empty:
        return {}
    as_of = max(activities["date"])
    recent = activities[activities["date"] > as_of - dt.timedelta(weeks=weeks)]
    out = {}
    for s in SPORTS:
        part = recent[recent.sport == s]
        dec = part["decoupling_pct"].dropna() if "decoupling_pct" in part else pd.Series(dtype=float)
        ef = part["efficiency_factor"].dropna() if "efficiency_factor" in part else pd.Series(dtype=float)
        out[s] = {
            "decoupling_pct": round(float(dec.mean()), 2) if len(dec) else None,
            "efficiency_factor": round(float(ef.mean()), 3) if len(ef) else None,
        }
    return out


def intensity_distribution(activities: pd.DataFrame, weeks: int = 6) -> dict:
    """Share of training TIME in each intensity class over recent weeks."""
    if activities.empty or "intensity_class" not in activities:
        return {}
    as_of = max(activities["date"])
    recent = activities[activities["date"] > as_of - dt.timedelta(weeks=weeks)].copy()
    total = recent["duration_s"].sum()
    if total <= 0:
        return {}
    out = {"easy": 0.0, "moderate": 0.0, "hard": 0.0}
    for cls, part in recent.groupby("intensity_class"):
        if cls in out:
            out[cls] = round(float(part["duration_s"].sum()) / total, 3)
    return out


def sessions_per_week_by_sport(activities: pd.DataFrame, weeks: int = 6) -> dict:
    if activities.empty:
        return {}
    as_of = max(activities["date"])
    recent = activities[activities["date"] > as_of - dt.timedelta(weeks=weeks)]
    return {
        s: round(len(recent[recent.sport == s]) / weeks, 2) for s in SPORTS
    }
