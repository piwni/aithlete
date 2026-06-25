"""Open Wearables connector — CANONICAL source for recovery/sleep/HRV/RHR from
Oura/Whoop and similar. Deferred for v1: self-host github.com/the-momentum/
open-wearables (docker compose up -d), then set OPEN_WEARABLES_* in .env.

Base URL ``http://localhost:8000/api/v1``; auth header ``X-Open-Wearables-API-Key``.
Offline mode reuses the synthetic athlete's wellness so the recovery code path is
exercised without a running instance.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from aithlete.config import Config, get_config
from aithlete.connectors import synthetic


class OpenWearablesClient:
    def __init__(self, config: Config | None = None) -> None:
        self.config = config or get_config()
        self.offline = self.config.offline or not self.config.open_wearables.configured

    def _client(self):
        import httpx

        cfg = self.config.open_wearables
        return httpx.Client(
            base_url=cfg.base_url,
            timeout=30.0,
            headers={
                "X-Open-Wearables-API-Key": cfg.api_key or "",
                "Accept": "application/json",
            },
        )

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        with self._client() as client:
            resp = client.get(path, params=params)
            resp.raise_for_status()
            return resp.json()

    def recovery_summary(self, oldest: dt.date, newest: dt.date) -> list[dict]:
        """Daily recovery rows: {date, hrv_rmssd_ms, resting_hr_bpm, sleep_hours, recovery_score}."""
        if self.offline:
            rows = []
            for w in synthetic.generate()["wellness"]:
                day = dt.date.fromisoformat(w["id"])
                if oldest <= day <= newest:
                    rows.append({
                        "date": day.isoformat(),
                        "hrv_rmssd_ms": w["hrv"],
                        "resting_hr_bpm": w["restingHR"],
                        "sleep_hours": round(w["sleepSecs"] / 3600, 2),
                        "recovery_score": None,
                        "source": "synthetic",
                    })
            return rows
        user_id = self.config.open_wearables.user_id
        return self._get(
            f"/users/{user_id}/summaries/recovery",
            params={"start": oldest.isoformat(), "end": newest.isoformat()},
        )
