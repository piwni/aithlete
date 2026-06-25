"""Load-math tests, including property checks against the Banister model."""

from __future__ import annotations

import math

import pandas as pd

from aithlete.analysis import metrics


def test_daily_tss_is_additive_across_sports(synth_frames):
    acts, _, _ = synth_frames
    tss = metrics.daily_tss(acts)
    # combined must equal the sum of the three sports, every day.
    recomputed = tss[["swim", "bike", "run"]].sum(axis=1)
    assert (tss["combined"] - recomputed).abs().max() < 1e-9


def test_combined_load_includes_other_sports():
    """Regression (M1): cross-training ('other', e.g. strength) must count toward
    combined load, otherwise CTL/ATL/ACWR under-report real fatigue."""
    import datetime as dt
    acts = pd.DataFrame({
        "date": [dt.date(2026, 1, 1), dt.date(2026, 1, 1)],
        "sport": ["run", "other"],
        "tss": [50.0, 30.0],
        "duration_s": [1800, 1800],
        "intensity_class": ["easy", "easy"],
    })
    tss = metrics.daily_tss(acts)
    row = tss.iloc[0]
    assert row["run"] == 50.0
    assert row["combined"] == 80.0  # run + other


def test_ctl_atl_converge_to_constant_load():
    # A constant daily TSS should drive CTL/ATL toward that value.
    days = pd.date_range("2026-01-01", periods=400, freq="D").date
    acts = pd.DataFrame({
        "date": days, "sport": "bike", "tss": 100.0, "duration_s": 3600,
        "intensity_class": "easy",
    })
    ls = metrics.load_series(acts)
    assert abs(ls["combined_ctl"].iloc[-1] - 100.0) < 1.0
    assert abs(ls["combined_atl"].iloc[-1] - 100.0) < 0.5


def test_impulse_response_alpha():
    # First step from 0 with load L gives L * (1 - e^{-1/tau}).
    s = pd.Series([100.0])
    out = metrics._impulse_response(s, metrics.TAU_CTL)
    assert math.isclose(out.iloc[0], 100.0 * (1 - math.exp(-1 / 42)), rel_tol=1e-6)


def test_hrv_baselines_present(synth_frames):
    _, well, _ = synth_frames
    b = metrics.hrv_rhr_baselines(well)
    assert b["hrv_baseline_60d_ms"] is not None
    assert b["hrv_swc_ms"] is not None and b["hrv_swc_ms"] > 0
    assert b["resting_hr_baseline_bpm"] is not None


def test_intensity_distribution_sums_to_one(synth_frames):
    acts, _, _ = synth_frames
    dist = metrics.intensity_distribution(acts)
    # Shares are rounded to 3 dp for display, so allow small rounding slack.
    assert abs(sum(dist.values()) - 1.0) < 5e-3
    assert dist["easy"] > dist["hard"]  # sane endurance distribution
