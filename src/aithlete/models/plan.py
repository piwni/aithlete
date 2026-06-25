"""Training plan models — authored by the agent, checked by the validator,
turned into intervals.icu events by the workout builder.
"""

from __future__ import annotations

import datetime as dt
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from aithlete import SCHEMA_VERSION
from aithlete.models.common import Sport
from aithlete.models.profile import DistanceBucket


class Phase(str, Enum):
    BASE = "base"
    BUILD = "build"
    PEAK = "peak"
    TAPER = "taper"
    TRANSITION = "transition"


class IntensityClass(str, Enum):
    EASY = "easy"
    MODERATE = "moderate"
    HARD = "hard"


class EventCategory(str, Enum):
    WORKOUT = "WORKOUT"
    RACE = "RACE"
    NOTE = "NOTE"


class TargetType(str, Enum):
    POWER = "power"   # % FTP or watts
    PACE = "pace"     # sec/km or sec/100m
    HR = "hr"         # % LTHR or bpm
    RPE = "rpe"
    OPEN = "open"


class StepKind(str, Enum):
    WARMUP = "warmup"
    STEADY = "steady"
    INTERVAL = "interval"
    RECOVERY = "recovery"
    COOLDOWN = "cooldown"


class Target(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: TargetType = TargetType.OPEN
    # Percent-of-anchor targets (e.g. 0.95 = 95% FTP). Absolute values optional.
    low_pct: float | None = None
    high_pct: float | None = None
    low_abs: float | None = None
    high_abs: float | None = None


class WorkoutStep(BaseModel):
    """A single step, or — if ``reps`` > 1 and ``substeps`` set — a repeat block."""

    model_config = ConfigDict(extra="forbid")
    kind: StepKind = StepKind.STEADY
    duration_s: int | None = None
    distance_m: float | None = None
    target: Target = Field(default_factory=Target)
    label: str | None = None
    reps: int = 1
    substeps: list[WorkoutStep] = Field(default_factory=list)

    @property
    def is_repeat(self) -> bool:
        return self.reps > 1 and bool(self.substeps)


class PlannedSession(BaseModel):
    model_config = ConfigDict(extra="forbid")
    date: dt.date
    sport: Sport
    name: str
    category: EventCategory = EventCategory.WORKOUT
    intensity_class: IntensityClass = IntensityClass.EASY
    planned_tss: float = 0.0
    planned_duration_s: int = 0
    is_brick: bool = False
    target_type: TargetType = TargetType.OPEN
    structure: list[WorkoutStep] = Field(default_factory=list)
    description: str | None = None
    # Stable idempotency key for intervals.icu upsert.
    external_id: str | None = None


class PlanWeek(BaseModel):
    model_config = ConfigDict(extra="forbid")
    week_index: int
    start_date: dt.date
    phase: Phase
    is_recovery: bool = False
    sessions: list[PlannedSession] = Field(default_factory=list)
    # Optional projection the agent fills; the validator recomputes from sessions.
    projected_ctl_end: float | None = None

    @property
    def planned_tss(self) -> float:
        return sum(s.planned_tss for s in self.sessions)

    @property
    def planned_hours(self) -> float:
        return sum(s.planned_duration_s for s in self.sessions) / 3600.0

    def intensity_distribution(self) -> dict[IntensityClass, float]:
        """Share of planned *time* in each intensity class (0..1)."""
        total = sum(s.planned_duration_s for s in self.sessions)
        if total <= 0:
            return {c: 0.0 for c in IntensityClass}
        out = {c: 0.0 for c in IntensityClass}
        for s in self.sessions:
            out[s.intensity_class] += s.planned_duration_s / total
        return out

    def sessions_by_sport(self) -> dict[Sport, int]:
        counts: dict[Sport, int] = {}
        for s in self.sessions:
            if s.category is EventCategory.WORKOUT:
                counts[s.sport] = counts.get(s.sport, 0) + 1
        return counts


class GoalRace(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    date: dt.date
    bucket: DistanceBucket
    priority: str = "A"  # A | B | C


class PlanInputs(BaseModel):
    """Provenance of a plan so it is explainable even if not reproducible."""

    model_config = ConfigDict(extra="forbid")
    context_digest_sha256: str | None = None
    knowledge_version: int | None = None
    profile_generated_at: dt.datetime | None = None
    rules_sha256: str | None = None


class TrainingPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: int = SCHEMA_VERSION
    athlete_id: str = "athlete"
    plan_id: str
    generated_at: dt.datetime = Field(default_factory=lambda: dt.datetime.now(dt.UTC))
    start_date: dt.date
    goal_race: GoalRace | None = None
    starting_ctl: float | None = None
    weeks: list[PlanWeek] = Field(default_factory=list)
    inputs: PlanInputs = Field(default_factory=PlanInputs)
    rationale: str | None = None

    def all_sessions(self) -> list[PlannedSession]:
        return [s for w in self.weeks for s in w.sessions]
