"""Fetch orchestrator: pull -> dedup/merge -> persist raw (partitioned) -> watermark.

Incremental: a per-source watermark records the last synced date so re-runs only
pull the new tail. Activities are partitioned by year/sport; wellness by year.
A normalized schema is written so the analysis layer never parses provider quirks.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd

from aithlete.config import Config, get_config
from aithlete.connectors import synthetic
from aithlete.connectors.dedup import dedup_activities, merge_wellness
from aithlete.connectors.intervals import IntervalsClient
from aithlete.connectors.openwearables import OpenWearablesClient
from aithlete.storage.io import load_json, read_csv, save_json, write_csv
from aithlete.storage.paths import raw_partition_path, watermark_path

DEFAULT_LOOKBACK_DAYS = 180

_TYPE_TO_SPORT = {"Swim": "swim", "Ride": "bike", "Run": "run"}


def _today(config: Config) -> dt.date:
    # Deterministic anchor offline so fixtures + tests are stable.
    return synthetic.ANCHOR_END if config.offline else dt.date.today()


def _normalize_activity(a: dict) -> dict:
    start = dt.datetime.fromisoformat(a["start_date_local"])
    sport = _TYPE_TO_SPORT.get(a.get("type", ""), a.get("type", "other").lower())
    intensity = (a.get("icu_intensity") or 0) / 100.0
    return {
        "id": a.get("id"),
        "date": start.date().isoformat(),
        "start_time": start.isoformat(),
        "sport": sport,
        "type": a.get("type"),
        "duration_s": int(a.get("moving_time") or a.get("elapsed_time") or 0),
        "tss": float(a.get("icu_training_load") or 0.0),
        "intensity_factor": round(intensity, 3) if intensity else None,
        "np_w": a.get("icu_weighted_avg_watts"),
        "avg_w": a.get("average_watts"),
        "ftp_w": a.get("icu_ftp"),
        "avg_pace_s_per_km": a.get("average_pace_s_per_km"),
        "avg_pace_s_per_100m": a.get("average_pace_s_per_100m"),
        "decoupling_pct": a.get("decoupling"),
        "efficiency_factor": a.get("icu_efficiency"),
        "intensity_class": a.get("_intensity_class"),
        "source": a.get("_source", "intervals"),
    }


def fetch_all(config: Config | None = None, *, full_resync: bool = False) -> dict:
    config = config or get_config()
    intervals = IntervalsClient(config)
    ow = OpenWearablesClient(config)

    end = _today(config)
    wm = load_json(watermark_path("intervals"), default={})
    if full_resync or not wm.get("last_synced"):
        start = end - dt.timedelta(days=DEFAULT_LOOKBACK_DAYS)
    else:
        # Re-pull a small overlap to catch late edits.
        start = dt.date.fromisoformat(wm["last_synced"]) - dt.timedelta(days=7)

    raw_acts = intervals.list_activities(start, end)
    # Real intervals data can have events with no start time; skip them rather
    # than crash on datetime.fromisoformat downstream.
    raw_acts = [a for a in raw_acts if a.get("start_date_local")]
    for a in raw_acts:
        a.setdefault("_source", "intervals")
    deduped = dedup_activities(raw_acts)
    norm_acts = [_normalize_activity(a) for a in deduped]

    iv_wellness = intervals.get_wellness(start, end)
    ow_recovery = ow.recovery_summary(start, end)
    wellness = merge_wellness(iv_wellness, ow_recovery)

    settings = intervals.get_sport_settings()

    _persist_activities(norm_acts)
    _persist_wellness(wellness)
    save_json(config.raw_dir / "intervals" / "settings.json", settings)
    save_json(watermark_path("intervals"), {"last_synced": end.isoformat(),
                                             "window_start": start.isoformat()})

    return {
        "window": [start.isoformat(), end.isoformat()],
        "activities": len(norm_acts),
        "wellness_days": len(wellness),
        "offline": config.offline,
    }


def _merge_into_partition(path, part: pd.DataFrame, key: str) -> None:
    """Read-merge-write so incremental fetch never destroys history.

    Existing rows are concatenated with the new slice and de-duplicated on
    ``key`` keeping the latest (so a re-pulled overlap absorbs late edits).
    """
    existing = read_csv(path)
    combined = pd.concat([existing, part], ignore_index=True) if not existing.empty else part
    if key in combined.columns:
        combined = combined.drop_duplicates(subset=[key], keep="last")
    sort_col = "start_time" if "start_time" in combined.columns else "date"
    combined = combined.sort_values(sort_col).reset_index(drop=True)
    write_csv(path, combined)


def _persist_activities(norm_acts: list[dict]) -> None:
    if not norm_acts:
        return
    df = pd.DataFrame(norm_acts)
    df["year"] = df["date"].str.slice(0, 4).astype(int)
    for (year, sport), part in df.groupby(["year", "sport"]):
        path = raw_partition_path("intervals", "activities", int(year), str(sport))
        _merge_into_partition(path, part.drop(columns=["year"]), key="id")


def _persist_wellness(wellness: list[dict]) -> None:
    if not wellness:
        return
    df = pd.DataFrame(wellness)
    df["year"] = df["date"].str.slice(0, 4).astype(int)
    for year, part in df.groupby("year"):
        path = raw_partition_path("intervals", "wellness", int(year))
        _merge_into_partition(path, part.drop(columns=["year"]), key="date")
