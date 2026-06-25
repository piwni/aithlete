"""Plan-construction helpers for validator/builder tests."""

from __future__ import annotations

import datetime as dt

from aithlete.models.common import Sport
from aithlete.models.plan import (
    DistanceBucket,
    GoalRace,
    IntensityClass,
    Phase,
    PlannedSession,
    PlanWeek,
    Target,
    TargetType,
    TrainingPlan,
    WorkoutStep,
)
from aithlete.models.plan import StepKind as SK


def session(d, sport, name, tss, minutes, iclass, brick=False, structure=None):
    return PlannedSession(
        date=d, sport=sport, name=name, planned_tss=tss,
        planned_duration_s=minutes * 60, intensity_class=iclass, is_brick=brick,
        structure=structure or [],
    )


def vo2_bike_structure():
    return [
        WorkoutStep(kind=SK.WARMUP, duration_s=600, target=Target(type=TargetType.POWER, low_pct=0.55)),
        WorkoutStep(reps=5, substeps=[
            WorkoutStep(kind=SK.INTERVAL, duration_s=180, target=Target(type=TargetType.POWER, low_pct=1.10, high_pct=1.15)),
            WorkoutStep(kind=SK.RECOVERY, duration_s=180, target=Target(type=TargetType.POWER, low_pct=0.50)),
        ]),
        WorkoutStep(kind=SK.COOLDOWN, duration_s=600, target=Target(type=TargetType.POWER, low_pct=0.55)),
    ]


def build_week(idx, start, phase, week_tss, recovery=False):
    return PlanWeek(
        week_index=idx, start_date=start, phase=phase, is_recovery=recovery,
        sessions=[
            session(start, Sport.RUN, "Easy run", week_tss * 0.18, 50, IntensityClass.EASY),
            session(start + dt.timedelta(days=1), Sport.SWIM, "Swim tech", week_tss * 0.12, 55, IntensityClass.EASY),
            session(start + dt.timedelta(days=2), Sport.BIKE, "Bike VO2", week_tss * 0.20, 70, IntensityClass.HARD, structure=vo2_bike_structure()),
            session(start + dt.timedelta(days=3), Sport.RUN, "Threshold run", week_tss * 0.16, 55, IntensityClass.HARD),
            session(start + dt.timedelta(days=4), Sport.SWIM, "Swim endurance", week_tss * 0.10, 50, IntensityClass.EASY),
            session(start + dt.timedelta(days=5), Sport.BIKE, "Long ride + brick", week_tss * 0.16, 180, IntensityClass.EASY, brick=True),
            session(start + dt.timedelta(days=6), Sport.RUN, "Long run", week_tss * 0.13, 95, IntensityClass.EASY),
        ],
    )


def good_plan(start=dt.date(2026, 6, 22), starting_ctl=80.0):
    tss = [480, 520, 560, 330]
    weeks = [build_week(i + 1, start + dt.timedelta(weeks=i), Phase.BUILD, tss[i], recovery=(i == 3))
             for i in range(4)]
    return TrainingPlan(
        plan_id="good", start_date=start, starting_ctl=starting_ctl, weeks=weeks,
        goal_race=GoalRace(name="A 70.3", date=start + dt.timedelta(weeks=4),
                           bucket=DistanceBucket.HALF, priority="A"),
    )


def steep_plan(start=dt.date(2026, 6, 22), starting_ctl=40.0):
    # Huge week-over-week jumps from a low base -> ramp + TSS-jump errors.
    tss = [700, 1100, 1500, 1900]
    weeks = [build_week(i + 1, start + dt.timedelta(weeks=i), Phase.BUILD, tss[i]) for i in range(4)]
    return TrainingPlan(plan_id="steep", start_date=start, starting_ctl=starting_ctl, weeks=weeks)
