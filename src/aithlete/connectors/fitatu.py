"""Fitatu meal-plan ingestion.

Fitatu (a meal-planning / nutrition app) exports one CSV row *per logged product
per meal*. This connector flattens those rows into one row *per day* of totals,
normalizes the column names, and writes them to the partitioned raw store at
``data/raw/fitatu/nutrition/{year}/data.csv`` — the same year-partitioned shape
as activities/wellness, so ``loaders.load_nutrition`` can read it uniformly.

Only macro/micro *sums* are derived here (deterministic math). Judgment about
whether the diet is adequate lives in ``analysis/nutrition.py`` + the
nutritionist skill.
"""

from __future__ import annotations

import datetime as dt
import glob as globlib
import re
from pathlib import Path

import pandas as pd

from aithlete.storage.io import read_csv, write_csv
from aithlete.storage.paths import raw_partition_path

# Output field -> source column header in the Fitatu export. Source headers are
# matched case-insensitively after stripping, so minor export changes still map.
FIELD_MAP: dict[str, str] = {
    "calories_kcal": "calories (kcal)",
    "protein_g": "Protein (g)",
    "fat_g": "Fats (g)",
    "saturated_g": "Saturated (g)",
    "carbs_g": "Carbohydrates (g)",
    "sugars_g": "Sugars (g)",
    "fibre_g": "Fibre (g)",
    "sodium_mg": "Sodium (mg)",
    "caffeine_mg": "Caffeine (mg)",
    "iron_mg": "Iron (mg)",
    "calcium_mg": "Calcium (mg)",
    "magnesium_mg": "Magnesium (mg)",
    "potassium_mg": "Potassium (mg)",
    "zinc_mg": "Zinc (mg)",
    "vitamin_c_mg": "Vitamin C (mg)",
    "vitamin_d_ug": "Vitamin D (ug)",
    "omega3_g": "Omega 3 fatty acid (g)",
    "omega6_g": "Omega 6 fatty acid (g)",
    "cholesterol_mg": "Cholesterol (mg)",
    "salt_g": "Salt (g)",
}

NUTRIENT_FIELDS: list[str] = list(FIELD_MAP.keys())
OUTPUT_COLUMNS: list[str] = ["date", *NUTRIENT_FIELDS, "meal_count", "item_count", "alcoholic_drinks"]

# Rough alcohol proxy: the Fitatu export has no alcohol-grams column, so we count
# beverage rows whose name looks alcoholic UNLESS it is explicitly alcohol-free.
_ALCOHOL_RE = re.compile(r"\b(beer|piwo|wine|wino|vodka|w[oó]dka|whisky|whiskey|rum|gin|cydr|cider|prosecco|champagne|szampan|likier|liqueur)\b", re.IGNORECASE)
_NON_ALCOHOLIC_RE = re.compile(r"non[- ]?alcoholic|bezalkohol|alcohol[- ]?free|0[.,]0\s*%|0%", re.IGNORECASE)


def _is_alcoholic(name: str) -> bool:
    if not isinstance(name, str) or not name.strip():
        return False
    if _NON_ALCOHOLIC_RE.search(name):
        return False
    return bool(_ALCOHOL_RE.search(name))


def _resolve_columns(df: pd.DataFrame) -> dict[str, str]:
    """Map normalized header (lower/stripped) -> actual column name in df."""
    return {str(c).strip().lower(): c for c in df.columns}


def parse_export(path: Path) -> pd.DataFrame:
    """Read one Fitatu export CSV into a normalized per-row frame.

    Returns columns: ``date``, ``meal``, ``product`` plus every field in
    :data:`NUTRIENT_FIELDS` (numeric, blanks coerced to 0).
    """
    raw = pd.read_csv(path)
    if raw.empty:
        return pd.DataFrame(columns=["date", "meal", "product", *NUTRIENT_FIELDS])

    cols = _resolve_columns(raw)
    out = pd.DataFrame()

    date_col = cols.get("date")
    if date_col is None:
        raise ValueError(f"{path}: no 'Date' column found in Fitatu export")
    out["date"] = pd.to_datetime(raw[date_col]).dt.date
    out["meal"] = raw[cols["meal"]].astype(str) if "meal" in cols else ""
    out["product"] = raw[cols["products and dishes"]].astype(str) if "products and dishes" in cols else ""

    for field, header in FIELD_MAP.items():
        src = cols.get(header.strip().lower())
        if src is None:
            out[field] = 0.0
        else:
            out[field] = pd.to_numeric(raw[src], errors="coerce").fillna(0.0)
    return out


def daily_totals(rows: pd.DataFrame) -> pd.DataFrame:
    """Aggregate normalized per-row data into one row per day."""
    if rows.empty:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)

    rows = rows.copy()
    rows["_alcoholic"] = rows["product"].map(_is_alcoholic)

    agg: dict[str, object] = {f: (f, "sum") for f in NUTRIENT_FIELDS}
    agg["meal_count"] = ("meal", lambda s: s[s.astype(str).str.strip() != ""].nunique())
    agg["item_count"] = ("product", "count")
    agg["alcoholic_drinks"] = ("_alcoholic", "sum")

    daily = rows.groupby("date").agg(**agg).reset_index()
    for f in NUTRIENT_FIELDS:
        daily[f] = daily[f].round(2)
    daily["alcoholic_drinks"] = daily["alcoholic_drinks"].astype(int)
    daily["meal_count"] = daily["meal_count"].astype(int)
    daily["item_count"] = daily["item_count"].astype(int)
    return daily[OUTPUT_COLUMNS].sort_values("date").reset_index(drop=True)


def import_exports(paths: list[Path]) -> dict:
    """Parse + aggregate Fitatu exports and MERGE them into the year-partitioned
    daily store.

    Re-import is genuinely incremental: each file is aggregated to daily totals
    first (so a day's products within a file are summed once), then overlapping
    dates *across* files or against the existing on-disk partition are resolved
    by keep-last (incoming supersedes existing) — never summed. Importing a new
    month therefore extends the store without dropping or double-counting prior
    months.
    """
    # Aggregate each file independently so overlapping calendar days across
    # exports are deduped (keep-last), not double-counted (B2).
    per_file = [daily_totals(parse_export(p)) for p in paths]
    per_file = [d for d in per_file if not d.empty]
    if not per_file:
        return {"files": len(paths), "days": 0, "years": [], "window": None}

    incoming = pd.concat(per_file, ignore_index=True)
    incoming = incoming.drop_duplicates(subset="date", keep="last")  # later file wins

    years_written: list[int] = []
    for year, part in incoming.groupby(incoming["date"].map(lambda d: d.year)):
        out = raw_partition_path("fitatu", "nutrition", int(year))
        existing = read_csv(out)
        if not existing.empty:
            existing["date"] = pd.to_datetime(existing["date"]).dt.date
            merged = pd.concat([existing[OUTPUT_COLUMNS], part[OUTPUT_COLUMNS]], ignore_index=True)
            merged = merged.drop_duplicates(subset="date", keep="last")  # incoming supersedes disk
        else:
            merged = part
        merged = merged.sort_values("date").reset_index(drop=True)
        write_csv(out, merged)
        years_written.append(int(year))

    return {
        "files": len(paths),
        "days": int(len(incoming)),
        "years": sorted(years_written),
        "window": (str(incoming["date"].min()), str(incoming["date"].max())),
    }


def resolve_inputs(spec: str | None, inbox: Path) -> list[Path]:
    """Resolve the ``--from`` argument into a sorted list of CSV paths.

    Accepts a single file, a directory (all ``*.csv`` inside), or a glob. When
    ``spec`` is ``None`` the conventional drop folder ``inbox`` is scanned.
    """
    if spec is None:
        return sorted(inbox.glob("*.csv")) if inbox.exists() else []
    p = Path(spec).expanduser()
    if p.is_dir():
        return sorted(p.glob("*.csv"))
    if p.exists():
        return [p]
    # Treat as a glob (relative or absolute); stdlib glob handles both.
    return sorted(Path(m) for m in globlib.glob(str(p)))


def latest_export_date(daily: pd.DataFrame) -> dt.date | None:
    if daily.empty:
        return None
    return max(daily["date"])
