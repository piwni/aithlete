"""Runtime configuration loaded from environment / .env.

Centralises every tunable so connectors, analysis, and the CLI agree on paths
and credentials. Nothing here reads athlete data; it only describes *where* and
*how* to read it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

try:  # optional dependency at import time; CLI ensures it is installed
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - dotenv is a declared dependency
    def load_dotenv(*_args, **_kwargs):  # type: ignore[misc]
        return False


def _project_root() -> Path:
    # src/aithlete/config.py -> repo root is three parents up.
    return Path(__file__).resolve().parents[2]


def _as_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class IntervalsConfig:
    api_key: str | None
    athlete_id: str

    @property
    def configured(self) -> bool:
        return bool(self.api_key)


@dataclass(frozen=True)
class OpenWearablesConfig:
    base_url: str
    api_key: str | None
    user_id: str | None

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.user_id)


@dataclass(frozen=True)
class Config:
    data_dir: Path
    fixtures_dir: Path
    knowledge_dir: Path
    offline: bool
    intervals: IntervalsConfig
    open_wearables: OpenWearablesConfig

    # Convenience sub-paths under data_dir.
    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def profiles_dir(self) -> Path:
        return self.data_dir / "profiles"

    @property
    def plans_dir(self) -> Path:
        return self.data_dir / "plans"

    @property
    def context_dir(self) -> Path:
        return self.data_dir / "context"


@lru_cache(maxsize=1)
def get_config() -> Config:
    root = _project_root()
    load_dotenv(root / ".env")

    data_dir = Path(os.environ.get("AITHLETE_DATA_DIR") or (root / "data")).resolve()

    return Config(
        data_dir=data_dir,
        fixtures_dir=data_dir / "fixtures",
        knowledge_dir=root / "knowledge",
        offline=_as_bool(os.environ.get("AITHLETE_OFFLINE"), default=True),
        intervals=IntervalsConfig(
            api_key=os.environ.get("INTERVALS_API_KEY") or None,
            athlete_id=os.environ.get("INTERVALS_ATHLETE_ID") or "0",
        ),
        open_wearables=OpenWearablesConfig(
            base_url=os.environ.get("OPEN_WEARABLES_BASE_URL")
            or "http://localhost:8000/api/v1",
            api_key=os.environ.get("OPEN_WEARABLES_API_KEY") or None,
            user_id=os.environ.get("OPEN_WEARABLES_USER_ID") or None,
        ),
    )
