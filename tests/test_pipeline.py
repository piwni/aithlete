"""End-to-end persistence test: fetch -> raw -> load, all offline in a tmp dir."""

from __future__ import annotations

from aithlete.analysis.loaders import load_activities, load_settings, load_wellness
from aithlete.connectors.fetch import fetch_all
from aithlete.connectors.intervals import IntervalsClient
from aithlete.storage.io import load_json
from aithlete.storage.paths import watermark_path


def test_fetch_persists_and_reloads(tmp_data_dir):
    res = fetch_all(full_resync=True)
    assert res["offline"] is True
    assert res["activities"] > 0

    acts = load_activities()
    well = load_wellness()
    settings = load_settings()
    assert not acts.empty and set(acts["sport"]) <= {"swim", "bike", "run"}
    assert not well.empty
    assert settings.get("bike", {}).get("ftp_w")

    wm = load_json(watermark_path("intervals"))
    assert wm.get("last_synced")


def test_incremental_fetch_preserves_history(tmp_data_dir):
    """Regression (C1): a re-run must merge partitions, not overwrite them, so
    the 7-day incremental window can never delete older activities."""
    fetch_all(full_resync=True)
    full = load_activities()
    assert not full.empty
    n_full = len(full)

    # Ordinary incremental re-run (watermark present -> only the recent tail).
    res = fetch_all()
    assert res["activities"] < n_full  # the incremental slice itself is small

    after = load_activities()
    assert len(after) == n_full, "incremental fetch dropped history"
    assert after["id"].is_unique, "merge introduced duplicate activity ids"


def test_offline_push_is_idempotent(tmp_data_dir):
    client = IntervalsClient()
    events = [
        {"category": "WORKOUT", "external_id": "x1", "name": "A", "start_date_local": "2026-06-01T09:00:00"},
        {"category": "WORKOUT", "external_id": "x2", "name": "B", "start_date_local": "2026-06-02T09:00:00"},
    ]
    client.push_events(events)
    client.push_events(events)  # same ids -> upsert, no duplicates
    stored = load_json(client.config.raw_dir / "intervals" / "_pushed_events.json")
    assert len(stored["events"]) == 2
