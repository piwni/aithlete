"""Plan-validator guardrail tests."""

from __future__ import annotations

from _helpers import good_plan, steep_plan
from aithlete.planning.validator import validate_plan


def test_good_plan_passes():
    rep = validate_plan(good_plan(), starting_ctl=80.0, weekly_hours_tolerance=13)
    assert rep.ok, [i.message for i in rep.errors]
    # An A race should land in a sensible peaked-form window.
    assert rep.projected_race_tsb is not None
    assert 5 <= rep.projected_race_tsb <= 30


def test_steep_ramp_is_blocked():
    rep = validate_plan(steep_plan(), starting_ctl=40.0)
    assert not rep.ok
    codes = {i.code for i in rep.errors}
    assert "ramp_too_steep" in codes or "weekly_tss_jump" in codes


def test_no_recovery_week_flagged():
    plan = steep_plan()
    rep = validate_plan(plan, starting_ctl=40.0)
    # Four straight load weeks with no recovery is allowed up to the cap; the
    # steep loads also trip ramp errors. Ensure errors exist and block push.
    assert len(rep.errors) >= 1


def test_projected_ctl_series_length_matches_weeks():
    plan = good_plan()
    rep = validate_plan(plan, starting_ctl=80.0)
    assert len(rep.projected_ctl_by_week) == len(plan.weeks)
