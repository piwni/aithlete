"""Context digest — the bounded, provenance-tagged summary the agent reads.

This is the anti-hallucination contract: the agent never loads raw streams.
Every value carries a unit, the date range / sample count it summarizes, and a
provenance flag. Missing data is stated explicitly in ``missing`` rather than
silently omitted. Locked with golden-file tests.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json

from pydantic import BaseModel, ConfigDict, Field

from aithlete import SCHEMA_VERSION
from aithlete.models.common import Provenance, Sport, Trend
from aithlete.models.readiness import ReadinessFlag


class DigestValue(BaseModel):
    """A single summarized metric with full provenance."""

    model_config = ConfigDict(extra="forbid")
    value: float | None = None
    unit: str = ""
    provenance: Provenance = Provenance.UNKNOWN
    source: str | None = None
    date_from: dt.date | None = None
    date_to: dt.date | None = None
    sample_count: int = 0
    trend: Trend = Trend.UNKNOWN


class LoadSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ctl: DigestValue = Field(default_factory=DigestValue)
    atl: DigestValue = Field(default_factory=DigestValue)
    tsb: DigestValue = Field(default_factory=DigestValue)
    weekly_tss_last_4: list[float] = Field(default_factory=list)
    weekly_hours_last_4: list[float] = Field(default_factory=list)


class PRRow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    bucket: str
    total_time_s: float
    date: dt.date
    conditions: str | None = None


class UpcomingRace(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    date: dt.date
    bucket: str
    priority: str
    weeks_out: int


class ContextDigest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: int = SCHEMA_VERSION
    athlete_id: str = "athlete"
    generated_at: dt.datetime = Field(default_factory=lambda: dt.datetime.now(dt.UTC))
    window_from: dt.date | None = None
    window_to: dt.date | None = None

    # Physiology anchors (provenance-tagged).
    ftp_w: DigestValue = Field(default_factory=DigestValue)
    run_threshold_pace_s_per_km: DigestValue = Field(default_factory=DigestValue)
    css_s_per_100m: DigestValue = Field(default_factory=DigestValue)
    vo2max_bike: DigestValue = Field(default_factory=DigestValue)
    hrv_baseline_60d_ms: DigestValue = Field(default_factory=DigestValue)
    resting_hr_bpm: DigestValue = Field(default_factory=DigestValue)

    # Load: combined + per sport.
    load_combined: LoadSummary = Field(default_factory=LoadSummary)
    load_by_sport: dict[Sport, LoadSummary] = Field(default_factory=dict)

    # Durability, compliance, PRs, calendar, readiness.
    decoupling_pct_by_sport: dict[Sport, DigestValue] = Field(default_factory=dict)
    recent_compliance_pct: DigestValue = Field(default_factory=DigestValue)
    sessions_per_week_by_sport: dict[Sport, float] = Field(default_factory=dict)
    personal_records: list[PRRow] = Field(default_factory=list)
    upcoming_races: list[UpcomingRace] = Field(default_factory=list)
    readiness_flag: ReadinessFlag = ReadinessFlag.GREEN
    readiness_notes: list[str] = Field(default_factory=list)

    # Honesty: what we don't know.
    missing: list[str] = Field(default_factory=list)
    approx_token_budget: int = 1500

    def sha256(self) -> str:
        """Stable hash over the digest content (excluding generated_at) for plan provenance."""
        payload = self.model_dump(mode="json", exclude={"generated_at"})
        blob = json.dumps(payload, sort_keys=True, default=str)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()
