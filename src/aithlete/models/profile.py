"""Triathlete profile — the athlete's current physiological + competitive state.

Authored/updated by the agent from the context digest; consumed by the planner.
Every metric is a ``Tracked`` value so confidence travels with the number.
"""

from __future__ import annotations

import datetime as dt
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from aithlete import SCHEMA_VERSION
from aithlete.models.common import Sport, Tracked, Trend, estimate_only


class Sex(str, Enum):
    MALE = "male"
    FEMALE = "female"
    OTHER = "other"


class TrainingAge(str, Enum):
    NOVICE = "novice"
    INTERMEDIATE = "intermediate"
    ADVANCED = "advanced"


class DistanceBucket(str, Enum):
    SPRINT = "sprint"     # ~1/8
    OLYMPIC = "olympic"   # ~1/4
    HALF = "half"         # 70.3, ~1/2
    FULL = "full"         # Ironman


class Anthropometrics(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sex: Sex = Sex.OTHER
    birthdate: dt.date | None = None
    weight_kg: Tracked[float] = Field(default_factory=Tracked.unknown)
    height_cm: Tracked[float] = Field(default_factory=Tracked.unknown)

    @property
    def age(self) -> int | None:
        if self.birthdate is None:
            return None
        today = dt.date.today()
        return today.year - self.birthdate.year - (
            (today.month, today.day) < (self.birthdate.month, self.birthdate.day)
        )


class HealthBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")
    hrv_rmssd_ms: Tracked[float] = Field(default_factory=Tracked.unknown)
    hrv_baseline_7d_ms: Tracked[float] = Field(default_factory=Tracked.unknown)
    hrv_baseline_60d_ms: Tracked[float] = Field(default_factory=Tracked.unknown)
    hrv_swc_ms: Tracked[float] = Field(default_factory=Tracked.unknown)
    hrv_trend: Trend = Trend.UNKNOWN
    resting_hr_bpm: Tracked[float] = Field(default_factory=Tracked.unknown)
    resting_hr_baseline_bpm: Tracked[float] = Field(default_factory=Tracked.unknown)
    resting_hr_trend: Trend = Trend.UNKNOWN
    sleep_avg_hours: Tracked[float] = Field(default_factory=Tracked.unknown)
    recovery_score: Tracked[float] = Field(default_factory=Tracked.unknown)


class BikePhysiology(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ftp_w: Tracked[float] = Field(default_factory=Tracked.unknown)
    ftp_w_per_kg: Tracked[float] = Field(default_factory=Tracked.unknown)
    eftp_w: Tracked[float] = Field(default_factory=Tracked.unknown)
    cp_w: Tracked[float] = Field(default_factory=Tracked.unknown)
    w_prime_kj: Tracked[float] = Field(default_factory=Tracked.unknown)
    vo2max: Tracked[float] = Field(default_factory=Tracked.unknown)  # ml/kg/min, always estimated
    lthr_bpm: Tracked[float] = Field(default_factory=Tracked.unknown)
    max_hr_bpm: Tracked[float] = Field(default_factory=Tracked.unknown)


class RunPhysiology(BaseModel):
    model_config = ConfigDict(extra="forbid")
    threshold_pace_s_per_km: Tracked[float] = Field(default_factory=Tracked.unknown)
    critical_speed_m_per_s: Tracked[float] = Field(default_factory=Tracked.unknown)
    vo2max: Tracked[float] = Field(default_factory=Tracked.unknown)  # always estimated
    lthr_bpm: Tracked[float] = Field(default_factory=Tracked.unknown)
    max_hr_bpm: Tracked[float] = Field(default_factory=Tracked.unknown)


class SwimPhysiology(BaseModel):
    model_config = ConfigDict(extra="forbid")
    css_s_per_100m: Tracked[float] = Field(default_factory=Tracked.unknown)
    threshold_pace_s_per_100m: Tracked[float] = Field(default_factory=Tracked.unknown)


class LoadState(BaseModel):
    """CTL/ATL/TSB for a single series (a sport or the combined total)."""

    model_config = ConfigDict(extra="forbid")
    ctl: Tracked[float] = Field(default_factory=Tracked.unknown)  # fitness
    atl: Tracked[float] = Field(default_factory=Tracked.unknown)  # fatigue
    tsb: Tracked[float] = Field(default_factory=Tracked.unknown)  # form (ctl - atl)
    as_of: dt.date | None = None
    trend: Trend = Trend.UNKNOWN


class TrainingLoad(BaseModel):
    model_config = ConfigDict(extra="forbid")
    combined: LoadState = Field(default_factory=LoadState)
    swim: LoadState = Field(default_factory=LoadState)
    bike: LoadState = Field(default_factory=LoadState)
    run: LoadState = Field(default_factory=LoadState)


class Durability(BaseModel):
    """Late-session fade markers; matter more than peak FTP at long course."""

    model_config = ConfigDict(extra="forbid")
    decoupling_pct: Tracked[float] = Field(default_factory=Tracked.unknown)
    efficiency_factor: Tracked[float] = Field(default_factory=Tracked.unknown)


class RaceConditions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    swim_type: str | None = None     # "pool" | "ows"
    wetsuit: bool | None = None
    terrain: str | None = None       # "flat" | "rolling" | "hilly"
    heat_c: float | None = None
    modified_distance: bool = False
    notes: str | None = None


class RaceResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    bucket: DistanceBucket
    date: dt.date
    total_time_s: float | None = None
    swim_time_s: float | None = None
    t1_s: float | None = None
    bike_time_s: float | None = None
    t2_s: float | None = None
    run_time_s: float | None = None
    conditions: RaceConditions = Field(default_factory=RaceConditions)
    dnf: bool = False
    relay: bool = False
    aquabike: bool = False
    event_name: str | None = None

    @property
    def comparable(self) -> bool:
        """A result is comparable for PR purposes only if it's a clean, full finish."""
        return not (self.dnf or self.relay or self.aquabike or self.conditions.modified_distance) \
            and self.total_time_s is not None


class BestTime(BaseModel):
    model_config = ConfigDict(extra="forbid")
    bucket: DistanceBucket
    total_time_s: float
    date: dt.date
    event_name: str | None = None


class AthleteProfile(BaseModel):
    """Top-level persisted profile (data/profiles/<athlete>.json)."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = SCHEMA_VERSION
    athlete_id: str = "athlete"
    generated_at: dt.datetime = Field(default_factory=lambda: dt.datetime.now(dt.UTC))

    anthropometrics: Anthropometrics = Field(default_factory=Anthropometrics)
    health: HealthBlock = Field(default_factory=HealthBlock)
    bike: BikePhysiology = Field(default_factory=BikePhysiology)
    run: RunPhysiology = Field(default_factory=RunPhysiology)
    swim: SwimPhysiology = Field(default_factory=SwimPhysiology)
    load: TrainingLoad = Field(default_factory=TrainingLoad)
    durability: dict[Sport, Durability] = Field(default_factory=dict)

    results: list[RaceResult] = Field(default_factory=list)

    training_age: TrainingAge = TrainingAge.INTERMEDIATE
    training_age_years: float | None = None
    weekly_volume_tolerance_hours: float | None = None
    weekly_volume_tolerance_by_sport_hours: dict[Sport, float] = Field(default_factory=dict)
    strengths: list[str] = Field(default_factory=list)
    limiters: list[str] = Field(default_factory=list)
    injury_history: list[str] = Field(default_factory=list)
    notes: str | None = None

    def best_times(self) -> dict[DistanceBucket, BestTime]:
        """Fastest comparable finish per bucket (ignores DNF/relay/aquabike/modified)."""
        best: dict[DistanceBucket, BestTime] = {}
        for r in self.results:
            if not r.comparable:
                continue
            cur = best.get(r.bucket)
            if cur is None or r.total_time_s < cur.total_time_s:  # type: ignore[operator]
                best[r.bucket] = BestTime(
                    bucket=r.bucket,
                    total_time_s=r.total_time_s,  # type: ignore[arg-type]
                    date=r.date,
                    event_name=r.event_name,
                )
        return best

    def enforce_estimate_only(self) -> AthleteProfile:
        """VO2max / lactate-threshold style fields can't be 'measured' off
        consumer data. The only escape hatch is an explicit ``source == "lab test"``.
        Covers VO2max plus the threshold/critical proxies (LTHR, threshold pace,
        CSS, CP) the agent might otherwise overclaim as measured."""
        self.bike.vo2max = estimate_only("bike.vo2max", self.bike.vo2max)
        self.run.vo2max = estimate_only("run.vo2max", self.run.vo2max)
        self.bike.lthr_bpm = estimate_only("bike.lthr_bpm", self.bike.lthr_bpm)
        self.bike.cp_w = estimate_only("bike.cp_w", self.bike.cp_w)
        self.run.lthr_bpm = estimate_only("run.lthr_bpm", self.run.lthr_bpm)
        self.run.threshold_pace_s_per_km = estimate_only(
            "run.threshold_pace_s_per_km", self.run.threshold_pace_s_per_km)
        self.swim.css_s_per_100m = estimate_only("swim.css_s_per_100m", self.swim.css_s_per_100m)
        return self
