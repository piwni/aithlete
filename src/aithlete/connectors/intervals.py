"""intervals.icu connector — CANONICAL source for activities, training load,
power/pace-duration curves and eFTP. Also the push target for planned workouts.

Auth: HTTP basic with username ``API_KEY`` and the personal key as password
(per the intervals.icu API cookbook). Athlete id ``0`` means "the athlete this
key belongs to".

Offline mode (``AITHLETE_OFFLINE=true``, the default) serves the deterministic
synthetic athlete so the whole pipeline runs with no account. Pushes are written
to ``data/raw/intervals/_pushed_events.json`` instead of the network.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from aithlete.config import Config, get_config
from aithlete.connectors import synthetic
from aithlete.storage.io import load_json, save_json

API_BASE = "https://intervals.icu/api/v1"


class IntervalsClient:
    def __init__(self, config: Config | None = None) -> None:
        self.config = config or get_config()
        self.offline = self.config.offline or not self.config.intervals.configured
        self.athlete_id = self.config.intervals.athlete_id

    # --- internal http -----------------------------------------------------
    def _client(self):
        import httpx

        key = self.config.intervals.api_key or ""
        return httpx.Client(
            base_url=API_BASE,
            auth=("API_KEY", key),
            timeout=30.0,
            headers={"Accept": "application/json"},
        )

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        with self._client() as client:
            resp = client.get(path, params=params)
            resp.raise_for_status()
            return resp.json()

    # --- reads -------------------------------------------------------------
    def list_activities(self, oldest: dt.date, newest: dt.date) -> list[dict]:
        if self.offline:
            data = synthetic.generate()
            return [
                a for a in data["activities"]
                if oldest <= _date_of(a["start_date_local"]) <= newest
            ]
        return self._get(
            f"/athlete/{self.athlete_id}/activities",
            params={"oldest": oldest.isoformat(), "newest": newest.isoformat()},
        )

    def get_wellness(self, oldest: dt.date, newest: dt.date) -> list[dict]:
        if self.offline:
            data = synthetic.generate()
            return [
                w for w in data["wellness"]
                if oldest <= dt.date.fromisoformat(w["id"]) <= newest
            ]
        return self._get(
            f"/athlete/{self.athlete_id}/wellness",
            params={"oldest": oldest.isoformat(), "newest": newest.isoformat()},
        )

    def get_sport_settings(self) -> dict:
        """Anchors: FTP/eFTP, threshold pace, CSS, LTHR, VO2max estimate.

        Returns the internal shape: {"weight_kg", "bike": {...}, "run": {...},
        "swim": {...}, "vo2max_bike"}. Offline uses the synthetic athlete; live
        maps the real intervals.icu athlete record.
        """
        if self.offline:
            return synthetic.generate()["settings"]
        athlete = self._get(f"/athlete/{self.athlete_id}")
        return _map_athlete_settings(athlete)

    # --- writes ------------------------------------------------------------
    def push_events(self, events: list[dict], upsert: bool = True) -> dict:
        """Bulk upsert calendar events (workouts/races/notes) keyed by external_id."""
        if self.offline:
            path = self.config.raw_dir / "intervals" / "_pushed_events.json"
            existing = load_json(path, default={"events": []})
            by_id = {e.get("external_id"): e for e in existing["events"] if e.get("external_id")}
            for e in events:
                if e.get("external_id"):
                    by_id[e["external_id"]] = e
            merged = list(by_id.values()) + [e for e in events if not e.get("external_id")]
            save_json(path, {"events": merged})
            return {"offline": True, "upserted": len(events), "total": len(merged)}

        with self._client() as client:
            resp = client.post(
                f"/athlete/{self.athlete_id}/events/bulk",
                params={"upsert": str(upsert).lower()},
                json=events,
            )
            resp.raise_for_status()
            return {"status": resp.status_code, "upserted": len(events)}


def _date_of(start_date_local: str) -> dt.date:
    return dt.datetime.fromisoformat(start_date_local).date()


_SPORT_GROUP = {
    "Ride": "bike", "VirtualRide": "bike", "Run": "run", "VirtualRun": "run",
    "Swim": "swim", "OpenWaterSwim": "swim",
}


def _map_athlete_settings(athlete: dict) -> dict:
    """Map the real intervals.icu athlete record into Aithlete's internal shape.

    intervals exposes per-sport anchors under ``sportSettings`` (a list whose
    entries carry ``types`` and fields like ``ftp``, ``lthr``, ``threshold_pace``
    or ``pace_zones`` anchor). Field names can vary by API version, so this is
    defensive: anything missing simply stays absent and becomes ``unknown``
    downstream rather than crashing.
    """
    if any(k in athlete for k in ("bike", "run", "swim")):
        return athlete  # already internal shape (e.g. injected fixture)

    out: dict = {"weight_kg": athlete.get("icu_weight") or athlete.get("weight"),
                 "bike": {}, "run": {}, "swim": {}}

    for s in athlete.get("sportSettings", []) or []:
        types = s.get("types") or []
        group = next((_SPORT_GROUP[t] for t in types if t in _SPORT_GROUP), None)
        if group is None:
            continue
        bucket = out[group]
        if s.get("ftp") is not None:
            bucket["ftp_w"] = s["ftp"]
            bucket["eftp_w"] = s.get("indoor_ftp") or s.get("ftp")
        if s.get("lthr") is not None:
            bucket["lthr_bpm"] = s["lthr"]
        if s.get("max_hr") is not None:
            bucket["max_hr_bpm"] = s["max_hr"]
        # Threshold pace: intervals stores pace anchors per sport (m/s or s/km).
        thr = s.get("threshold_pace") or s.get("pace_threshold")
        if thr is not None:
            if group == "run":
                # Heuristic: values < 12 look like m/s -> convert to s/km.
                bucket["threshold_pace_s_per_km"] = round(1000 / thr, 1) if thr < 12 else thr
            elif group == "swim":
                bucket["css_s_per_100m"] = round(100 / thr, 1) if thr < 5 else thr

    out["vo2max_bike"] = athlete.get("icu_vo2max") or athlete.get("vo2max")
    return out
