"""Loaders + content hashes for the parameterized training/readiness rulebooks."""

from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from aithlete.config import get_config


def _path(name: str) -> Path:
    return get_config().knowledge_dir / "triathlon" / name


@lru_cache(maxsize=8)
def _load(name: str) -> dict[str, Any]:
    path = _path(name)
    if not path.exists():
        raise FileNotFoundError(f"knowledge file missing: {path}")
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def load_rules() -> dict[str, Any]:
    return _load("rules.yaml")


def load_readiness_rules() -> dict[str, Any]:
    return _load("readiness.yaml")


def load_zones() -> dict[str, Any]:
    return _load("zones.yaml")


def file_sha256(name: str) -> str:
    return hashlib.sha256(_path(name).read_bytes()).hexdigest()


def knowledge_version() -> int:
    return int(load_rules().get("schema_version", 1))
