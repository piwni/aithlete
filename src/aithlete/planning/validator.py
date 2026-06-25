"""Deterministic plan validator — the guardrail between the agent and `push`.

Loads knowledge/triathlon/rules.yaml and checks a TrainingPlan's *shape* without
any LLM: ramp-rate caps, recovery-week cadence, taper shape, race-day form (TSB)
target, intensity distribution, weekly hours vs tolerance, frequency minimums,
and hard sanity limits.

`errors` block a push; `warnings` annotate. This is what makes an LLM planner
safe to act on.
"""

from __future__ import annotations

import datetime as dt
import math
from enum import Enum

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field

from aithlete.knowledge import load_rules
from aithlete.models.plan import IntensityClass, Phase, TrainingPlan

TAU_CTL = 42
TAU_ATL = 7


class Severity(str, Enum):
    ERROR = "error"
    WARNING = "warning"


class Issue(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str
    severity: Severity
    message: str
    week_index: int | None = None


class ValidationReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ok: bool = True
    issues: list[Issue] = Field(default_factory=list)
    projected_ctl_by_week: list[float] = Field(default_factory=list)
    projected_race_tsb: float | None = None

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity is Severity.ERROR]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.severity is Severity.WARNING]


def _project_load(plan: TrainingPlan, starting_ctl: float) -> tuple[pd.Series, pd.Series]:
    """Daily CTL/ATL over the plan horizon, seeded from starting fitness."""
    sessions = plan.all_sessions()
    if not sessions:
        return pd.Series(dtype=float), pd.Series(dtype=float)
    start = plan.start_date
    end = max(s.date for s in sessions)
    idx = pd.date_range(start, end, freq="D").date
    daily = pd.Series(0.0, index=idx)
    for s in sessions:
        if s.date in daily.index:
            daily[s.date] += s.planned_tss

    alpha_c, alpha_a = 1 - math.exp(-1 / TAU_CTL), 1 - math.exp(-1 / TAU_ATL)
    ctl, atl = [], []
    pc = pa = float(starting_ctl)
    for v in daily.to_numpy():
        pc += (v - pc) * alpha_c
        pa += (v - pa) * alpha_a
        ctl.append(pc)
        atl.append(pa)
    return pd.Series(ctl, index=idx), pd.Series(atl, index=idx)


def validate_plan(
    plan: TrainingPlan,
    *,
    starting_ctl: float | None = None,
    weekly_hours_tolerance: float | None = None,
    training_age: str = "intermediate",
) -> ValidationReport:
    rules = load_rules()
    report = ValidationReport()

    starting_ctl = starting_ctl if starting_ctl is not None else (plan.starting_ctl or 0.0)
    ctl_series, atl_series = _project_load(plan, starting_ctl)

    # Per-week projected CTL (end of each plan week).
    weekly_ctl: list[float] = []
    for wk in plan.weeks:
        week_end = wk.start_date + dt.timedelta(days=6)
        upto = ctl_series[[d for d in ctl_series.index if d <= week_end]]
        weekly_ctl.append(round(float(upto.iloc[-1]), 1) if len(upto) else starting_ctl)
    report.projected_ctl_by_week = weekly_ctl

    _check_ramp(plan, weekly_ctl, starting_ctl, rules, report)
    _check_weekly_progression(plan, rules, report)
    _check_recovery_cadence(plan, rules, report)
    _check_intensity_distribution(plan, rules, report)
    _check_volume_tolerance(plan, weekly_hours_tolerance, rules, report)
    _check_frequency(plan, rules, report)
    _check_hard_limits(plan, rules, report)
    _check_taper(plan, ctl_series, atl_series, rules, report)

    report.ok = len(report.errors) == 0
    return report


def _add(report, code, severity, message, week_index=None):
    report.issues.append(Issue(code=code, severity=severity, message=message, week_index=week_index))


def _check_ramp(plan, weekly_ctl, starting_ctl, rules, report):
    cap = rules["ramp"]["ctl_per_week_max"]
    base_cap = rules["ramp"]["ctl_per_week_max_base"]
    prev = starting_ctl
    for wk, ctl_end in zip(plan.weeks, weekly_ctl, strict=False):
        ramp = ctl_end - prev
        limit = base_cap if wk.phase is Phase.BASE else cap
        if ramp > limit and not wk.is_recovery:
            _add(report, "ramp_too_steep", Severity.ERROR,
                 f"Week {wk.week_index} projected CTL ramp {ramp:.1f}/wk exceeds cap {limit} ({wk.phase.value})",
                 wk.week_index)
        prev = ctl_end


def _check_weekly_progression(plan, rules, report):
    tss_cap = rules["ramp"]["weekly_tss_increase_pct_max"]
    hours_cap = rules["ramp"]["weekly_hours_increase_pct_max"]
    prev_tss = prev_hours = None
    for wk in plan.weeks:
        tss, hours = wk.planned_tss, wk.planned_hours
        if prev_tss and prev_tss > 0 and not wk.is_recovery:
            inc = (tss - prev_tss) / prev_tss * 100
            if inc > tss_cap:
                _add(report, "weekly_tss_jump", Severity.ERROR,
                     f"Week {wk.week_index} TSS jumps {inc:.0f}% (cap {tss_cap}%)", wk.week_index)
        if prev_hours and prev_hours > 0 and not wk.is_recovery:
            inc = (hours - prev_hours) / prev_hours * 100
            if inc > hours_cap:
                _add(report, "weekly_hours_jump", Severity.WARNING,
                     f"Week {wk.week_index} hours jump {inc:.0f}% (cap {hours_cap}%)", wk.week_index)
        prev_tss, prev_hours = tss, hours


def _check_recovery_cadence(plan, rules, report):
    max_run = rules["loading_pattern"]["max_consecutive_load_weeks"]
    run = 0
    for wk in plan.weeks:
        if wk.is_recovery or wk.phase in (Phase.TAPER, Phase.TRANSITION):
            run = 0
        else:
            run += 1
            if run > max_run:
                _add(report, "no_recovery_week", Severity.ERROR,
                     f"{run} consecutive load weeks by week {wk.week_index} (max {max_run} before a recovery week)",
                     wk.week_index)
    # Recovery weeks should actually reduce volume.
    red_lo = rules["recovery_week"]["volume_reduction_pct"][0]
    weeks = plan.weeks
    for i, wk in enumerate(weeks):
        if wk.is_recovery and i > 0:
            prev = weeks[i - 1]
            if prev.planned_tss > 0:
                drop = (prev.planned_tss - wk.planned_tss) / prev.planned_tss * 100
                if drop < red_lo:
                    _add(report, "weak_recovery_week", Severity.WARNING,
                         f"Recovery week {wk.week_index} only drops load {drop:.0f}% (target >= {red_lo}%)",
                         wk.week_index)


def _check_intensity_distribution(plan, rules, report):
    min_easy = rules["intensity_distribution"]["min_easy_pct"] / 100.0
    for wk in plan.weeks:
        if wk.phase is Phase.PEAK or wk.planned_hours == 0:
            continue
        dist = wk.intensity_distribution()
        if dist[IntensityClass.EASY] < min_easy:
            _add(report, "too_much_intensity", Severity.WARNING,
                 f"Week {wk.week_index} easy share {dist[IntensityClass.EASY]*100:.0f}% below {min_easy*100:.0f}% min",
                 wk.week_index)


def _check_volume_tolerance(plan, tolerance, rules, report):
    hard_max = rules["hard_limits"]["max_weekly_hours"]
    for wk in plan.weeks:
        if wk.planned_hours > hard_max:
            _add(report, "weekly_hours_over_hard_limit", Severity.ERROR,
                 f"Week {wk.week_index} {wk.planned_hours:.1f}h exceeds hard limit {hard_max}h", wk.week_index)
        elif tolerance and wk.planned_hours > tolerance * 1.15:
            _add(report, "over_volume_tolerance", Severity.WARNING,
                 f"Week {wk.week_index} {wk.planned_hours:.1f}h exceeds athlete tolerance {tolerance:.1f}h +15%",
                 wk.week_index)


def _check_frequency(plan, rules, report):
    freq_min = rules["frequency_min_per_week"]
    for wk in plan.weeks:
        if wk.phase in (Phase.TAPER, Phase.TRANSITION) or wk.is_recovery:
            continue
        counts = {s.value: c for s, c in wk.sessions_by_sport().items()}
        for sport, mn in freq_min.items():
            if counts.get(sport, 0) < mn:
                _add(report, "low_frequency", Severity.WARNING,
                     f"Week {wk.week_index}: {counts.get(sport, 0)} {sport} sessions (< min {mn})", wk.week_index)


def _check_hard_limits(plan, rules, report):
    max_session_h = rules["hard_limits"]["max_single_session_hours"]
    brick_required = rules["long_session"]["brick_required_in_build"]
    for wk in plan.weeks:
        for s in wk.sessions:
            if s.planned_duration_s / 3600 > max_session_h:
                _add(report, "session_too_long", Severity.ERROR,
                     f"Week {wk.week_index} '{s.name}' {s.planned_duration_s/3600:.1f}h exceeds {max_session_h}h",
                     wk.week_index)
        if (brick_required and wk.phase is Phase.BUILD and not wk.is_recovery
                and not any(s.is_brick for s in wk.sessions)):
            _add(report, "missing_brick", Severity.WARNING,
                 f"Build week {wk.week_index} has no brick session", wk.week_index)


def _check_taper(plan, ctl_series, atl_series, rules, report):
    if plan.goal_race is None or len(ctl_series) == 0:
        return
    race = plan.goal_race
    priority = race.priority.upper()
    target = rules["taper"]["target_tsb_by_priority"].get(priority)
    # Race-day TSB = yesterday's CTL - ATL (intervals 'Form').
    race_day = race.date
    days = [d for d in ctl_series.index if d < race_day]
    if not days:
        return
    last = days[-1]
    tsb = float(ctl_series[last] - atl_series[last])
    report.projected_race_tsb = round(tsb, 1)
    if target and not (target[0] <= tsb <= target[1]):
        sev = Severity.ERROR if priority == "A" else Severity.WARNING
        _add(report, "race_tsb_out_of_range", sev,
             f"Projected race-day TSB {tsb:.1f} outside target {target} for priority {priority} race")
