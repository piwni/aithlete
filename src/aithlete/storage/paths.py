"""Canonical on-disk locations for persisted artifacts.

Single athlete for v1, so most artifacts use a stable id ("athlete"). Raw data
is partitioned by year and sport so incremental fetch stays cheap and files
stay readable.
"""

from __future__ import annotations

from pathlib import Path

from aithlete.config import get_config

DEFAULT_ATHLETE = "athlete"


def profile_path(athlete: str = DEFAULT_ATHLETE) -> Path:
    return get_config().profiles_dir / f"{athlete}.json"


def context_path(athlete: str = DEFAULT_ATHLETE) -> Path:
    return get_config().context_dir / f"{athlete}.json"


def active_plan_path(athlete: str = DEFAULT_ATHLETE) -> Path:
    """Pointer file to the currently active plan (so adjust-plan finds it)."""
    return get_config().plans_dir / f"{athlete}-active.json"


def plan_path(plan_id: str) -> Path:
    return get_config().plans_dir / f"{plan_id}.json"


def calendar_path(athlete: str = DEFAULT_ATHLETE) -> Path:
    return get_config().data_dir / f"calendar-{athlete}.json"


def raw_partition_path(source: str, dataset: str, year: int, sport: str | None = None) -> Path:
    """e.g. data/raw/intervals/activities/2026/run.csv

    `sport` is optional; datasets like wellness are not sport-partitioned.
    """
    base = get_config().raw_dir / source / dataset / str(year)
    name = f"{sport}.csv" if sport else "data.csv"
    return base / name


def watermark_path(source: str) -> Path:
    """Per-source incremental-fetch watermark (last successfully synced date)."""
    return get_config().raw_dir / source / "_watermark.json"
