"""Loaders + content hashes for the parameterized training/readiness rulebooks."""

from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from aithlete.config import get_config


def _path(name: str, subdir: str = "triathlon") -> Path:
    return get_config().knowledge_dir / subdir / name


@lru_cache(maxsize=16)
def _load(name: str, subdir: str = "triathlon") -> dict[str, Any]:
    path = _path(name, subdir)
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


def load_nutrition_rules() -> dict[str, Any]:
    return _load("rules.yaml", subdir="nutrition")


def file_sha256(name: str) -> str:
    return hashlib.sha256(_path(name).read_bytes()).hexdigest()


def knowledge_version() -> int:
    return int(load_rules().get("schema_version", 1))
