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
