"""Load normalized raw data from the partitioned store into DataFrames."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pandas as pd

from aithlete.config import Config, get_config
from aithlete.storage.io import load_json


def _read_partitions(root: Path) -> pd.DataFrame:
    if not root.exists():
        return pd.DataFrame()
    frames = [pd.read_csv(p) for p in sorted(root.rglob("*.csv"))]
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def load_activities(config: Config | None = None) -> pd.DataFrame:
    config = config or get_config()
    df = _read_partitions(config.raw_dir / "intervals" / "activities")
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df = df.sort_values("start_time").reset_index(drop=True)
    return df


def load_wellness(config: Config | None = None) -> pd.DataFrame:
    config = config or get_config()
    df = _read_partitions(config.raw_dir / "intervals" / "wellness")
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df = df.sort_values("date").reset_index(drop=True)
    return df


def load_settings(config: Config | None = None) -> dict:
    config = config or get_config()
    return load_json(config.raw_dir / "intervals" / "settings.json", default={})


def data_as_of(activities: pd.DataFrame, wellness: pd.DataFrame) -> dt.date:
    """The most recent date present in the data; used as 'now' for determinism."""
    candidates: list[dt.date] = []
    if not activities.empty:
        candidates.append(max(activities["date"]))
    if not wellness.empty:
        candidates.append(max(wellness["date"]))
    return max(candidates) if candidates else dt.date.today()
