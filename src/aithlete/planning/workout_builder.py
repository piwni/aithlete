"""Typed intervals.icu workout-text builder.

The agent authors a plan as structured JSON; this module deterministically turns
each session into intervals.icu native workout text + an event payload for the
bulk-upsert endpoint. We never let the agent emit raw workout syntax — it is
generated and round-trip validated here.

Garmin-sync caveats (documented, enforced where possible):
- Structured SWIM workouts sync poorly to Garmin (distance/stroke steps, OWS).
  We emit them but warn.
- RUN targets must pick pace OR HR; mixing confuses some Garmin devices.
- intervals -> Garmin push requires a Garmin-linked intervals account with push
  enabled; we only push to intervals.
"""

from __future__ import annotations

import datetime as dt

from aithlete.models.common import Sport
from aithlete.models.plan import (
    EventCategory,
    PlannedSession,
    TargetType,
    TrainingPlan,
    WorkoutStep,
)

_SPORT_TO_TYPE = {Sport.SWIM: "Swim", Sport.BIKE: "Ride", Sport.RUN: "Run", Sport.OTHER: "Workout"}
_DEFAULT_TIME = {Sport.SWIM: dt.time(7, 0), Sport.BIKE: dt.time(9, 0),
                 Sport.RUN: dt.time(18, 0), Sport.OTHER: dt.time(12, 0)}


class WorkoutBuildError(ValueError):
    pass


def _fmt_duration(step: WorkoutStep) -> str:
    if step.distance_m is not None:
        if step.distance_m >= 1000:
            return f"{step.distance_m / 1000:g}km"
        return f"{int(step.distance_m)}m"
    if step.duration_s is not None:
        if step.duration_s % 60 == 0:
            return f"{step.duration_s // 60}m"
        return f"{step.duration_s}s"
    raise WorkoutBuildError("step needs duration_s or distance_m")


def _fmt_target(step: WorkoutStep) -> str:
    t = step.target
    if t.type in (TargetType.POWER, TargetType.HR):
        if t.low_pct is not None and t.high_pct is not None:
            return f"{round(t.low_pct * 100)}-{round(t.high_pct * 100)}%"
        if t.low_pct is not None:
            return f"{round(t.low_pct * 100)}%"
    if t.type is TargetType.PACE:
        if t.low_pct is not None and t.high_pct is not None:
            return f"{round(t.low_pct * 100)}-{round(t.high_pct * 100)}% pace"
        if t.low_pct is not None:
            return f"{round(t.low_pct * 100)}% pace"
    if t.type is TargetType.RPE and t.low_pct is not None:
        return f"RPE{round(t.low_pct * 10)}"
    return ""


def _step_line(step: WorkoutStep) -> str:
    dur = _fmt_duration(step)
    tgt = _fmt_target(step)
    label = f" {step.label}" if step.label else ""
    parts = [f"- {dur}"]
    if tgt:
        parts.append(tgt)
    line = " ".join(parts) + label
    return line.rstrip()


def build_workout_text(session: PlannedSession) -> str:
    """Render intervals.icu native workout text.

    Each top-level step and each repeat block is its own group; groups are
    separated by a blank line so repeat boundaries are unambiguous (intervals
    groups steps the same way).
    """
    groups: list[str] = []
    for step in session.structure:
        if step.is_repeat:
            block = [f"{step.reps}x"] + [_step_line(sub) for sub in step.substeps]
            groups.append("\n".join(block))
        else:
            groups.append(_step_line(step))
    return "\n\n".join(groups)


def parse_workout_text(text: str) -> list[dict]:
    """Lightweight round-trip parser used to VALIDATE generated text.

    The authoritative intervals.icu parser is client-side; this confirms our
    output is well-formed: ``Nx`` opens a repeat group, blank lines end it, and
    every other non-blank line is a ``- ...`` step.
    """
    steps: list[dict] = []
    pending_reps = 0
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            pending_reps = 0  # blank line closes any open repeat group
            continue
        if line.lower().endswith("x") and line[:-1].isdigit():
            pending_reps = int(line[:-1])
            steps.append({"type": "repeat", "reps": pending_reps, "substeps": 0})
            continue
        if not line.startswith("-"):
            raise WorkoutBuildError(f"malformed workout line: {raw!r}")
        if pending_reps and steps and steps[-1]["type"] == "repeat":
            steps[-1]["substeps"] += 1
        else:
            steps.append({"type": "step"})
    return steps


def session_to_event(session: PlannedSession, athlete_id: str = "athlete") -> dict:
    """Build an intervals.icu bulk-events payload entry for one session."""
    start_time = _DEFAULT_TIME[session.sport]
    start_local = dt.datetime.combine(session.date, start_time).isoformat()
    external_id = session.external_id or f"aithlete-{athlete_id}-{session.date.isoformat()}-{session.sport.value}"

    event: dict = {
        "category": session.category.value,
        "start_date_local": start_local,
        "name": session.name,
        "external_id": external_id,
    }

    if session.category is EventCategory.NOTE:
        event["description"] = session.description or session.name
        return event

    event["type"] = _SPORT_TO_TYPE[session.sport]

    if session.category is EventCategory.RACE:
        event["description"] = session.description or "Race day"
        if session.planned_duration_s:
            event["moving_time"] = session.planned_duration_s
        return event

    # WORKOUT
    text = build_workout_text(session)
    if text:
        parse_workout_text(text)  # raises if malformed
    description = text
    if session.description:
        description = f"{session.description}\n\n{text}" if text else session.description
    event["description"] = description
    if session.planned_duration_s:
        event["moving_time"] = session.planned_duration_s
    if session.planned_tss:
        event["icu_training_load"] = round(session.planned_tss)
    if session.is_brick:
        event["name"] = f"[Brick] {session.name}"
    return event


def plan_to_events(plan: TrainingPlan) -> list[dict]:
    return [session_to_event(s, plan.athlete_id) for s in plan.all_sessions()]


def garmin_sync_warnings(plan: TrainingPlan) -> list[str]:
    warnings: list[str] = []
    for s in plan.all_sessions():
        if s.category is not EventCategory.WORKOUT:
            continue
        if s.sport is Sport.SWIM and s.structure:
            warnings.append(f"{s.date}: structured swim '{s.name}' may not sync cleanly to Garmin")
        if s.sport is Sport.RUN:
            types = {st.target.type for st in s.structure if st.target.type is not TargetType.OPEN}
            if {TargetType.PACE, TargetType.HR}.issubset(types):
                warnings.append(f"{s.date}: run '{s.name}' mixes pace and HR targets; pick one for Garmin")
    return warnings
