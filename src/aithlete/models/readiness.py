"""Readiness / monitoring models. Produced by analysis.readiness from daily
health metrics + knowledge/triathlon/readiness.yaml. Worst-wins aggregation.
"""

from __future__ import annotations

import datetime as dt
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from aithlete import SCHEMA_VERSION


class ReadinessFlag(str, Enum):
    GREEN = "green"
    AMBER = "amber"
    RED = "red"

    @property
    def severity(self) -> int:
        return {"green": 0, "amber": 1, "red": 2}[self.value]


def worst(flags: list[ReadinessFlag]) -> ReadinessFlag:
    if not flags:
        return ReadinessFlag.GREEN
    return max(flags, key=lambda f: f.severity)


class RuleResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rule: str
    flag: ReadinessFlag
    observed: float | str | None = None
    threshold: float | str | None = None
    message: str
    action: str | None = None


class ReadinessReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: int = SCHEMA_VERSION
    athlete_id: str = "athlete"
    date: dt.date
    overall_flag: ReadinessFlag = ReadinessFlag.GREEN
    recommended_action: str = "proceed as planned"
    results: list[RuleResult] = Field(default_factory=list)

    def recompute_overall(self) -> ReadinessReport:
        self.overall_flag = worst([r.flag for r in self.results])
        return self
