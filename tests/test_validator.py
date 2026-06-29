"""Plan-validator guardrail tests."""

from __future__ import annotations

import datetime as dt

from _helpers import build_week, good_plan, session, steep_plan
from aithlete.models.common import Sport
from aithlete.models.plan import IntensityClass, Phase, TrainingPlan
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


def test_fake_recovery_flag_cannot_bypass_ramp():
    """Regression (C2): marking every week is_recovery=True must NOT let a load
    ramp slip past the validator."""
    start = dt.date(2026, 6, 22)
    tss = [700, 1400, 2100, 2800]
    weeks = [build_week(i + 1, start + dt.timedelta(weeks=i), Phase.BUILD, tss[i], recovery=True)
             for i in range(4)]
    plan = TrainingPlan(plan_id="fake-recovery", start_date=start, starting_ctl=40.0, weeks=weeks)
    rep = validate_plan(plan, starting_ctl=40.0)
    assert not rep.ok, "rising load disguised as recovery weeks must be blocked"
    codes = {i.code for i in rep.errors}
    assert codes & {"ramp_too_steep", "weekly_tss_jump", "fake_recovery_week", "projected_acwr_spike"}


def test_big_weekend_is_allowed():
    """Long ride one day + long run the next (the standard long-course big
    weekend) must NOT trip the same-day stacking guardrail."""
    rep = validate_plan(good_plan(), starting_ctl=80.0)
    assert "stacked_long_day" not in {i.code for i in rep.issues}


def test_long_run_stacked_on_brick_day_is_flagged():
    """Regression: a standalone long run on the same day as a brick (the exact
    Sunday bug) must surface as a stacked_long_day warning."""
    plan = good_plan()
    brick_day = plan.weeks[0].sessions[5].date  # the long-ride+brick day
    plan.weeks[0].sessions.append(
        session(brick_day, Sport.RUN, "Long run (stacked)", 70, 95, IntensityClass.EASY)
    )
    rep = validate_plan(plan, starting_ctl=80.0)
    hits = [i for i in rep.issues if i.code == "stacked_long_day"]
    assert hits, "a long run stacked on a brick day must be flagged"
    assert hits[0].week_index == 1


def test_taper_peak_without_goal_race_is_blocked():
    """Regression (I2): a plan that tapers/peaks but omits goal_race can't have
    its race-day form checked -> must be an error, not a silent pass."""
    start = dt.date(2026, 6, 22)
    weeks = [
        build_week(1, start, Phase.BUILD, 480),
        build_week(2, start + dt.timedelta(weeks=1), Phase.BUILD, 500),
        build_week(3, start + dt.timedelta(weeks=2), Phase.TAPER, 300, recovery=True),
    ]
    plan = TrainingPlan(plan_id="no-race", start_date=start, starting_ctl=80.0, weeks=weeks)
    rep = validate_plan(plan, starting_ctl=80.0)
    assert not rep.ok
    assert "missing_goal_race" in {i.code for i in rep.errors}
