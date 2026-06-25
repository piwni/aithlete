"""Shared primitives for the JSON contract.

The load-bearing primitive is ``Tracked[T]``: every metric carries a value plus
a provenance/confidence flag and a source. Its invariant — ``unknown`` iff the
value is ``None`` — lets the validator and the agent reason about confidence
uniformly and forces honesty about missing/estimated data.
"""

from __future__ import annotations

import datetime as dt
from enum import Enum
from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, model_validator

T = TypeVar("T")


class Provenance(str, Enum):
    """How much we trust a value."""

    MEASURED = "measured"      # a real, direct measurement (or lab test)
    ESTIMATED = "estimated"    # modelled / derived / device-estimated
    UNKNOWN = "unknown"        # we have no value


class Trend(str, Enum):
    UP = "up"
    DOWN = "down"
    FLAT = "flat"
    UNKNOWN = "unknown"


class Sport(str, Enum):
    SWIM = "swim"
    BIKE = "bike"
    RUN = "run"
    OTHER = "other"


class Tracked(BaseModel, Generic[T]):
    """A value with provenance. Invariant: provenance == UNKNOWN iff value is None."""

    model_config = ConfigDict(extra="forbid")

    value: T | None = None
    provenance: Provenance = Provenance.UNKNOWN
    source: str | None = None
    as_of: dt.date | None = None

    @model_validator(mode="after")
    def _check_invariant(self) -> Tracked[T]:
        if self.value is None and self.provenance is not Provenance.UNKNOWN:
            raise ValueError("Tracked with no value must have provenance=unknown")
        if self.value is not None and self.provenance is Provenance.UNKNOWN:
            raise ValueError("Tracked with a value must declare provenance (measured/estimated)")
        return self

    @property
    def known(self) -> bool:
        return self.value is not None

    @classmethod
    def unknown(cls) -> Tracked[T]:
        return cls(value=None, provenance=Provenance.UNKNOWN)

    @classmethod
    def measured(cls, value: T, source: str, as_of: dt.date | None = None) -> Tracked[T]:
        return cls(value=value, provenance=Provenance.MEASURED, source=source, as_of=as_of)

    @classmethod
    def estimated(cls, value: T, source: str, as_of: dt.date | None = None) -> Tracked[T]:
        return cls(value=value, provenance=Provenance.ESTIMATED, source=source, as_of=as_of)


def estimate_only(field_label: str, t: Tracked[T]) -> Tracked[T]:
    """Coerce a value to ESTIMATED unless it comes from a lab test.

    Used for VO2max and lactate-threshold style metrics that consumer devices
    can only estimate. The single escape hatch is an explicit lab source.
    """
    if not t.known:
        return t
    if t.source and t.source.strip().lower() == "lab test":
        return t
    if t.provenance is Provenance.MEASURED:
        return Tracked[T](
            value=t.value,
            provenance=Provenance.ESTIMATED,
            source=t.source,
            as_of=t.as_of,
        )
    return t
