"""Small, dependency-light read/write helpers with atomic JSON writes."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def _ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def save_json(path: Path, data: Any, *, indent: int = 2) -> Path:
    """Atomically write JSON (write to temp then rename) to avoid partial files."""
    _ensure_parent(path)
    payload = json.dumps(data, indent=indent, ensure_ascii=False, default=str)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return path


def load_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def write_csv(path: Path, df) -> Path:
    """Write a pandas DataFrame to CSV, creating parents as needed."""
    _ensure_parent(path)
    df.to_csv(path, index=False)
    return path


def read_csv(path: Path):
    import pandas as pd

    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path)
