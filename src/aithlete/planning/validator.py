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


def _daily_plan_tss(plan: TrainingPlan) -> pd.Series:
    """Continuous daily planned-TSS series over the plan horizon (0-filled)."""
    sessions = plan.all_sessions()
    if not sessions:
        return pd.Series(dtype=float)
    start = plan.start_date
    end = max(s.date for s in sessions)
    idx = pd.date_range(start, end, freq="D").date
    daily = pd.Series(0.0, index=idx)
    for s in sessions:
        if s.date in daily.index:
            daily[s.date] += s.planned_tss
    return daily


def _project_load(plan: TrainingPlan, starting_ctl: float) -> tuple[pd.Series, pd.Series]:
    """Daily CTL/ATL over the plan horizon, seeded from starting fitness."""
    daily = _daily_plan_tss(plan)
    if daily.empty:
        return pd.Series(dtype=float), pd.Series(dtype=float)
    idx = daily.index
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
    _check_recovery_cadence(plan, rules, report, training_age)
    _check_projected_acwr(plan, rules, report)
    _check_intensity_distribution(plan, rules, report)
    _check_volume_tolerance(plan, weekly_hours_tolerance, rules, report)
    _check_frequency(plan, rules, report)
    _check_hard_limits(plan, rules, report)
    _check_taper(plan, ctl_series, atl_series, rules, report)

    report.ok = len(report.errors) == 0
    return report


def _add(report, code, severity, message, week_index=None):
    report.issues.append(Issue(code=code, severity=severity, message=message, week_index=week_index))


def _is_effective_recovery(week, prev_week, red_lo: float) -> bool:
    """Recovery is DERIVED from the actual load drop, never trusted from the
    agent-supplied flag — otherwise a week mislabeled ``is_recovery=True`` could
    raise load and skip the ramp/cadence guardrails."""
    if prev_week is None or prev_week.planned_tss <= 0:
        return week.planned_tss == 0
    drop = (prev_week.planned_tss - week.planned_tss) / prev_week.planned_tss * 100
    return drop >= red_lo


def _check_ramp(plan, weekly_ctl, starting_ctl, rules, report):
    # No is_recovery exemption: a genuine recovery week is a *downward* move and
    # won't trip an upward cap, so always applying the cap is both safe and the
    # fix for the bypass.
    cap = rules["ramp"]["ctl_per_week_max"]
    base_cap = rules["ramp"]["ctl_per_week_max_base"]
    prev = starting_ctl
    for wk, ctl_end in zip(plan.weeks, weekly_ctl, strict=False):
        ramp = ctl_end - prev
        limit = base_cap if wk.phase is Phase.BASE else cap
        if ramp > limit:
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
        # Caps apply regardless of the recovery flag (an increase is an increase).
        if prev_tss and prev_tss > 0:
            inc = (tss - prev_tss) / prev_tss * 100
            if inc > tss_cap:
                _add(report, "weekly_tss_jump", Severity.ERROR,
                     f"Week {wk.week_index} TSS jumps {inc:.0f}% (cap {tss_cap}%)", wk.week_index)
        if prev_hours and prev_hours > 0:
            inc = (hours - prev_hours) / prev_hours * 100
            if inc > hours_cap:
                _add(report, "weekly_hours_jump", Severity.WARNING,
                     f"Week {wk.week_index} hours jump {inc:.0f}% (cap {hours_cap}%)", wk.week_index)
        prev_tss, prev_hours = tss, hours


def _check_recovery_cadence(plan, rules, report, training_age="intermediate"):
    # Cadence cap is training-age specific (novice 2:1, others 3:1), bounded by
    # the absolute max_consecutive_load_weeks.
    by_age = rules["loading_pattern"].get("by_training_age", {})
    age_load_weeks = by_age.get(training_age, {}).get("load_weeks")
    abs_max = rules["loading_pattern"]["max_consecutive_load_weeks"]
    max_run = min(age_load_weeks, abs_max) if age_load_weeks else abs_max
    red_lo = rules["recovery_week"]["volume_reduction_pct"][0]
    weeks = plan.weeks
    run = 0
    for i, wk in enumerate(weeks):
        prev = weeks[i - 1] if i > 0 else None
        derived_recovery = _is_effective_recovery(wk, prev, red_lo)
        if derived_recovery or wk.phase in (Phase.TAPER, Phase.TRANSITION):
            run = 0
        else:
            run += 1
            if run > max_run:
                _add(report, "no_recovery_week", Severity.ERROR,
                     f"{run} consecutive load weeks by week {wk.week_index} (max {max_run} before a recovery week)",
                     wk.week_index)
        # A week claimed as recovery that doesn't actually reduce load is an ERROR
        # (this is the bypass vector — a fake recovery week hiding a load spike).
        if wk.is_recovery and not derived_recovery:
            _add(report, "fake_recovery_week", Severity.ERROR,
                 f"Week {wk.week_index} is marked is_recovery but does not reduce load >= {red_lo}%",
                 wk.week_index)


def _check_projected_acwr(plan, rules, report):
    """Acute:Chronic Workload Ratio on the PLANNED load. Flags load spikes the
    weekly-TSS cap can miss (e.g. a huge single session)."""
    daily = _daily_plan_tss(plan)
    if daily.empty:
        return
    hard_cap = rules["ramp"].get("acwr_hard_cap", 1.5)
    s = daily.reset_index(drop=True)
    cum = s.cumsum()
    for i in range(len(s)):
        if i < 27:
            continue  # need ~4 weeks of chronic load to be meaningful
        acute = cum.iloc[i] - (cum.iloc[i - 7] if i >= 7 else 0.0)
        chronic = (cum.iloc[i] - (cum.iloc[i - 28] if i >= 28 else 0.0)) / 4.0
        if chronic <= 0:
            continue
        acwr = acute / chronic
        if acwr > hard_cap:
            week_index = i // 7 + 1
            _add(report, "projected_acwr_spike", Severity.ERROR,
                 f"Projected ACWR {acwr:.2f} exceeds hard cap {hard_cap} around day {i + 1}",
                 week_index)
            return  # one report is enough


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
    peaks = any(wk.phase in (Phase.TAPER, Phase.PEAK) for wk in plan.weeks)
    if plan.goal_race is None:
        # A plan that tapers/peaks but declares no race can't have its race-day
        # form checked — that's a guardrail bypass. Require the race.
        if peaks:
            _add(report, "missing_goal_race", Severity.ERROR,
                 "Plan has a taper/peak phase but no goal_race; declare the race so race-day form can be validated")
        return
    if len(ctl_series) == 0:
        return
    race = plan.goal_race
    _check_taper_duration(plan, race, rules, report)
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


def _check_taper_duration(plan, race, rules, report):
    """Taper length (consecutive taper weeks before the race) within the
    distance-specific window from rules.yaml. Advisory (warning)."""
    window = rules["taper"]["duration_days_by_distance"].get(race.bucket.value)
    if not window:
        return
    taper_weeks = sum(1 for wk in plan.weeks if wk.phase is Phase.TAPER)
    if taper_weeks == 0:
        # Only flag missing taper for A/B races (C races may be raced through).
        if race.priority.upper() in ("A", "B"):
            _add(report, "no_taper", Severity.WARNING,
                 f"No taper phase before priority {race.priority} {race.bucket.value} race")
        return
    taper_days = taper_weeks * 7
    lo, hi = window
    # Allow a week of slack on each side (weeks are coarse-grained).
    if not (lo - 7 <= taper_days <= hi + 7):
        _add(report, "taper_duration_out_of_range", Severity.WARNING,
             f"Taper ~{taper_days}d ({taper_weeks}wk) outside {window}d window for {race.bucket.value}")
