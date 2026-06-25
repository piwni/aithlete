"""Shared test fixtures. Everything runs offline against the synthetic athlete."""

from __future__ import annotations

import pandas as pd
import pytest

from aithlete.config import get_config
from aithlete.connectors import synthetic
from aithlete.connectors.dedup import merge_wellness
from aithlete.connectors.fetch import _normalize_activity


@pytest.fixture
def tmp_data_dir(tmp_path, monkeypatch):
    """Isolated data dir so persistence tests don't touch the real ./data."""
    monkeypatch.setenv("AITHLETE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("AITHLETE_OFFLINE", "true")
    get_config.cache_clear()
    yield tmp_path
    get_config.cache_clear()


@pytest.fixture
def synth_frames():
    """Normalized (activities, wellness, settings) built in-memory — no disk."""
    data = synthetic.generate()
    acts = pd.DataFrame([_normalize_activity(a) for a in data["activities"]])
    acts["date"] = pd.to_datetime(acts["date"]).dt.date
    well = pd.DataFrame(merge_wellness(data["wellness"], []))
    well["date"] = pd.to_datetime(well["date"]).dt.date
    return acts, well, data["settings"]
