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
    settings = load_json(config.raw_dir / "intervals" / "settings.json", default={})
    return _merge_manual_metrics(settings, load_manual_metrics(config))


def load_manual_metrics(config: Config | None = None) -> dict:
    """User-authored metrics that no connector provides (e.g. Garmin VO2max).

    Lives at ``<data_dir>/manual_metrics.json`` — OUTSIDE raw/ so `fetch` never
    overwrites it.
    """
    config = config or get_config()
    return load_json(config.data_dir / "manual_metrics.json", default={})


def load_manual_races(config: Config | None = None) -> dict:
    """User-authored race results / names / leg splits not auto-detected.

    Lives at ``<data_dir>/manual_races.json`` — merged over auto-detection by date.
    """
    config = config or get_config()
    return load_json(config.data_dir / "manual_races.json", default={})


def _merge_manual_metrics(settings: dict, manual: dict) -> dict:
    """Overlay manual values onto fetched settings. Manual wins (it's the value
    the athlete explicitly supplied); connector nulls never clobber it."""
    if not manual:
        return settings
    out = dict(settings)
    for key, val in manual.items():
        if isinstance(val, dict):
            merged = dict(out.get(key) or {})
            merged.update({k: v for k, v in val.items() if v is not None})
            out[key] = merged
        elif val is not None:
            out[key] = val
    return out


def data_as_of(activities: pd.DataFrame, wellness: pd.DataFrame) -> dt.date:
    """The most recent date present in the data; used as 'now' for determinism."""
    candidates: list[dt.date] = []
    if not activities.empty:
        candidates.append(max(activities["date"]))
    if not wellness.empty:
        candidates.append(max(wellness["date"]))
    return max(candidates) if candidates else dt.date.today()
