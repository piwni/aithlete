"""Model contract tests: the Tracked provenance primitive and profile logic."""

from __future__ import annotations

import datetime as dt

import pytest
from pydantic import ValidationError

from aithlete.models.common import Provenance, Tracked, estimate_only
from aithlete.models.profile import AthleteProfile, DistanceBucket, RaceResult


def test_tracked_invariant_value_requires_provenance():
    with pytest.raises(ValidationError):
        Tracked(value=None, provenance=Provenance.MEASURED)
    with pytest.raises(ValidationError):
        Tracked(value=42.0, provenance=Provenance.UNKNOWN)


def test_tracked_constructors():
    assert Tracked.unknown().provenance is Provenance.UNKNOWN
    assert not Tracked.unknown().known
    m = Tracked.measured(290.0, "lab test")
    assert m.known and m.provenance is Provenance.MEASURED


def test_estimate_only_demotes_unless_lab():
    device = Tracked.measured(58.0, "Garmin Firstbeat")
    assert estimate_only("vo2max", device).provenance is Provenance.ESTIMATED
    lab = Tracked.measured(60.0, "lab test")
    assert estimate_only("vo2max", lab).provenance is Provenance.MEASURED
    assert estimate_only("vo2max", Tracked.unknown()).provenance is Provenance.UNKNOWN


def test_best_times_ignores_noncomparable():
    p = AthleteProfile()
    p.results = [
        RaceResult(bucket=DistanceBucket.HALF, date=dt.date(2025, 6, 1), total_time_s=18000),
        RaceResult(bucket=DistanceBucket.HALF, date=dt.date(2025, 7, 1), total_time_s=1, dnf=True),
        RaceResult(bucket=DistanceBucket.HALF, date=dt.date(2025, 8, 1), total_time_s=17000),
    ]
    best = p.best_times()
    assert best[DistanceBucket.HALF].total_time_s == 17000


def test_enforce_estimate_only_on_profile():
    p = AthleteProfile()
    p.bike.vo2max = Tracked.measured(58.0, "Garmin")
    p.enforce_estimate_only()
    assert p.bike.vo2max.provenance is Provenance.ESTIMATED


def test_enforce_estimate_only_covers_threshold_proxies():
    """Regression (I5): LT/threshold/CSS/CP can't be 'measured' off a watch
    either — only an explicit lab source escapes."""
    p = AthleteProfile()
    p.bike.lthr_bpm = Tracked.measured(165.0, "Garmin")
    p.bike.cp_w = Tracked.measured(290.0, "intervals.icu")
    p.run.threshold_pace_s_per_km = Tracked.measured(240.0, "Garmin")
    p.run.lthr_bpm = Tracked.measured(170.0, "Garmin")
    p.swim.css_s_per_100m = Tracked.measured(95.0, "Form")
    p.enforce_estimate_only()
    assert p.bike.lthr_bpm.provenance is Provenance.ESTIMATED
    assert p.bike.cp_w.provenance is Provenance.ESTIMATED
    assert p.run.threshold_pace_s_per_km.provenance is Provenance.ESTIMATED
    assert p.run.lthr_bpm.provenance is Provenance.ESTIMATED
    assert p.swim.css_s_per_100m.provenance is Provenance.ESTIMATED

    # Lab-tested lactate threshold survives.
    lab = AthleteProfile()
    lab.bike.lthr_bpm = Tracked.measured(168.0, "lab test")
    lab.enforce_estimate_only()
    assert lab.bike.lthr_bpm.provenance is Provenance.MEASURED
