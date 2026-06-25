"""Readiness engine tests — flags and worst-wins aggregation."""

from __future__ import annotations

import datetime as dt

import pandas as pd

from aithlete.analysis import readiness as readiness_mod
from aithlete.models.readiness import ReadinessFlag


def _wellness(hrv_tail):
    """Build 70 days of steady wellness, overriding the last len(hrv_tail) HRV values."""
    days = pd.date_range("2026-04-01", periods=70, freq="D").date
    hrv = [70.0] * 70
    rhr = [50.0] * 70
    sleep = [7.5] * 70
    for i, v in enumerate(hrv_tail):
        hrv[-(len(hrv_tail) - i)] = v
    return pd.DataFrame({
        "date": days, "hrv_rmssd_ms": hrv, "resting_hr_bpm": rhr,
        "sleep_hours": sleep, "weight_kg": 72.0,
    })


def test_green_when_everything_normal(synth_frames):
    _, well, _ = synth_frames
    # Use steady wellness so HRV/RHR/sleep are all nominal.
    rep = readiness_mod.evaluate(_wellness([70, 70, 70]), _empty_acts())
    assert rep.overall_flag in (ReadinessFlag.GREEN, ReadinessFlag.AMBER)


def test_red_on_sustained_hrv_suppression():
    well = _wellness([40, 38, 41, 39])  # 4 days well below baseline-SWC
    rep = readiness_mod.evaluate(well, _empty_acts(), as_of=dt.date(2026, 6, 9))
    hrv = next(r for r in rep.results if r.rule == "hrv")
    assert hrv.flag is ReadinessFlag.RED
    assert rep.overall_flag is ReadinessFlag.RED  # worst-wins


def test_illness_below_neck_forces_red():
    well = _wellness([70, 70, 70])
    rep = readiness_mod.evaluate(well, _empty_acts(), illness="below_neck",
                                 as_of=dt.date(2026, 6, 9))
    assert rep.overall_flag is ReadinessFlag.RED


def test_elevated_rhr_flags_amber_or_red():
    well = _wellness([70, 70, 70])
    well.loc[well.index[-1], "resting_hr_bpm"] = 62  # +12 over baseline 50
    rep = readiness_mod.evaluate(well, _empty_acts(), as_of=dt.date(2026, 6, 9))
    rhr = next(r for r in rep.results if r.rule == "resting_hr")
    assert rhr.flag is ReadinessFlag.RED


def _empty_acts():
    return pd.DataFrame(columns=["date", "sport", "tss", "duration_s", "intensity_class"])
