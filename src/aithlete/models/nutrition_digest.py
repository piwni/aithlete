"""Nutrition digest — the bounded, provenance-tagged fueling summary the
nutritionist skill reads.

Same contract as the training context digest: Python computes every number here
(from Fitatu daily totals + training load + wellness); the agent never recomputes
them. Missing/assumed data is stated explicitly rather than silently filled.
Persisted to ``data/context/nutrition-athlete.json``.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json

from pydantic import BaseModel, ConfigDict, Field

from aithlete import SCHEMA_VERSION
from aithlete.models.context_digest import DigestValue
from aithlete.models.readiness import ReadinessFlag


class MicronutrientFlag(BaseModel):
    model_config = ConfigDict(extra="forbid")
    nutrient: str
    mean_intake: float
    target: float
    unit: str
    pct_of_target: float
    flag: ReadinessFlag
    coverage_pct: float = 100.0   # % of logged days with a non-zero value for this nutrient
    reliable: bool = True         # False when coverage is too low to trust the flag


class CorrelationFinding(BaseModel):
    """An observational association (never causal)."""

    model_config = ConfigDict(extra="forbid")
    driver: str          # e.g. "energy_availability"
    outcome: str         # e.g. "next_day_hrv_rmssd_ms"
    method: str          # "pearson" | "spearman"
    r: float
    n: int
    lag_days: int        # 0 = same day, 1 = next-day outcome
    notable: bool
    note: str | None = None


class MonthSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")
    month: str           # "2026-01"
    days_logged: int
    mean_intake_kcal: float | None = None
    mean_energy_availability: float | None = None
    mean_carbs_g_per_kg: float | None = None
    mean_protein_g_per_kg: float | None = None


class NutritionDigest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: int = SCHEMA_VERSION
    athlete_id: str = "athlete"
    generated_at: dt.datetime = Field(default_factory=lambda: dt.datetime.now(dt.UTC))
    window_from: dt.date | None = None
    window_to: dt.date | None = None
    days_logged: int = 0

    # Energy.
    mean_intake_kcal: DigestValue = Field(default_factory=DigestValue)
    mean_exercise_kcal: DigestValue = Field(default_factory=DigestValue)
    energy_availability_kcal_per_kg: DigestValue = Field(default_factory=DigestValue)
    energy_availability_flag: ReadinessFlag = ReadinessFlag.GREEN
    mean_energy_balance_kcal: DigestValue = Field(default_factory=DigestValue)
    low_ea_days_pct: DigestValue = Field(default_factory=DigestValue)

    # Macros (per kg body mass/day means).
    carbs_g_per_kg: DigestValue = Field(default_factory=DigestValue)
    protein_g_per_kg: DigestValue = Field(default_factory=DigestValue)
    fat_g_per_kg: DigestValue = Field(default_factory=DigestValue)
    protein_flag: ReadinessFlag = ReadinessFlag.GREEN
    key_session_fuel_pct: DigestValue = Field(default_factory=DigestValue)  # % of key days meeting carb min
    carb_underfuel_days_pct: DigestValue = Field(default_factory=DigestValue)

    # Micronutrients + diet quality.
    micronutrient_flags: list[MicronutrientFlag] = Field(default_factory=list)
    sugars_pct_energy: DigestValue = Field(default_factory=DigestValue)
    saturated_pct_energy: DigestValue = Field(default_factory=DigestValue)
    fibre_g: DigestValue = Field(default_factory=DigestValue)
    caffeine_mg: DigestValue = Field(default_factory=DigestValue)
    alcohol_drinks_per_week: DigestValue = Field(default_factory=DigestValue)
    weight_kg: DigestValue = Field(default_factory=DigestValue)

    # Trends + associations.
    monthly: list[MonthSummary] = Field(default_factory=list)
    recovery_correlations: list[CorrelationFinding] = Field(default_factory=list)

    # Honesty.
    assumptions: list[str] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)
    approx_token_budget: int = 1500

    def sha256(self) -> str:
        payload = self.model_dump(mode="json", exclude={"generated_at"})
        blob = json.dumps(payload, sort_keys=True, default=str)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()
