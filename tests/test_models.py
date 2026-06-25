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
