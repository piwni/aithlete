"""Nutrition pipeline tests — Fitatu parsing, energy availability, carb tiers,
and the observational recovery correlations."""

from __future__ import annotations

import datetime as dt

import pandas as pd

from aithlete.analysis import nutrition as nut
from aithlete.connectors import fitatu
from aithlete.knowledge import load_nutrition_rules
from aithlete.models.common import Tracked
from aithlete.models.profile import AthleteProfile, Sex
from aithlete.models.readiness import ReadinessFlag

EMPTY_ACTS = pd.DataFrame(columns=["date", "sport", "tss", "duration_s", "avg_w", "intensity_class"])
EMPTY_WELL = pd.DataFrame(columns=["date", "hrv_rmssd_ms", "resting_hr_bpm", "sleep_hours", "weight_kg"])


def _profile(weight: float = 70.0) -> AthleteProfile:
    p = AthleteProfile()
    p.anthropometrics.weight_kg = Tracked.measured(weight, "test")
    p.anthropometrics.sex = Sex.MALE
    return p


def _daily(dates, **overrides) -> pd.DataFrame:
    """Build a daily nutrition frame with all required columns (defaults to 0)."""
    rows = []
    for i, d in enumerate(dates):
        row = {c: 0.0 for c in fitatu.NUTRIENT_FIELDS}
        row.update({"date": d, "meal_count": 4, "item_count": 8, "alcoholic_drinks": 0})
        for k, v in overrides.items():
            row[k] = v[i] if isinstance(v, (list, tuple)) else v
        rows.append(row)
    return pd.DataFrame(rows)[fitatu.OUTPUT_COLUMNS]


# --- parser ----------------------------------------------------------------
def test_parse_and_daily_totals(tmp_path):
    csv = tmp_path / "meal_plan.csv"
    csv.write_text(
        'Date,Meal,"Products and dishes","calories (kcal)","Protein (g)","Carbohydrates (g)","Caffeine (mg)"\n'
        '2026-01-01,Breakfast,Oats,300,10,50,0\n'
        '2026-01-01,Lunch,Chicken,400,40,0,0\n'
        '2026-01-01,Lunch,Coffee,5,0,0,80\n'
        '2026-01-02,Breakfast,Toast,200,6,30,\n'
    )
    daily = fitatu.daily_totals(fitatu.parse_export(csv))
    assert list(daily["date"]) == [dt.date(2026, 1, 1), dt.date(2026, 1, 2)]
    d1 = daily.iloc[0]
    assert d1["calories_kcal"] == 705.0
    assert d1["protein_g"] == 50.0
    assert d1["caffeine_mg"] == 80.0
    assert d1["meal_count"] == 2          # Breakfast + Lunch
    assert d1["item_count"] == 3
    # blank caffeine on day 2 coerced to 0, not NaN
    assert daily.iloc[1]["caffeine_mg"] == 0.0


def test_alcohol_proxy_excludes_non_alcoholic():
    assert fitatu._is_alcoholic("Red Wine") is True
    assert fitatu._is_alcoholic("Non-alcoholic beer") is False
    assert fitatu._is_alcoholic("Piwo bezalkoholowe IPA") is False
    assert fitatu._is_alcoholic("Banana") is False


def test_import_writes_partition(tmp_path, monkeypatch):
    monkeypatch.setenv("AITHLETE_DATA_DIR", str(tmp_path))
    from aithlete.config import get_config
    get_config.cache_clear()
    try:
        csv = tmp_path / "m.csv"
        csv.write_text(
            'Date,Meal,"Products and dishes","calories (kcal)"\n'
            '2026-03-01,Breakfast,Oats,300\n'
        )
        res = fitatu.import_exports([csv])
        assert res["days"] == 1 and res["years"] == [2026]
        out = tmp_path / "raw" / "fitatu" / "nutrition" / "2026" / "data.csv"
        assert out.exists()
    finally:
        get_config.cache_clear()


def test_reimport_merges_and_supersedes_without_data_loss(tmp_path, monkeypatch):
    """A second import extends the store (B1) and updates overlapping dates by
    keep-last instead of summing or dropping prior months."""
    monkeypatch.setenv("AITHLETE_DATA_DIR", str(tmp_path))
    from aithlete.analysis.loaders import load_nutrition
    from aithlete.config import get_config
    get_config.cache_clear()
    try:
        a = tmp_path / "a.csv"
        a.write_text(
            'Date,Meal,"Products and dishes","calories (kcal)"\n'
            '2026-01-01,Breakfast,Oats,300\n'
        )
        fitatu.import_exports([a])
        b = tmp_path / "b.csv"
        b.write_text(
            'Date,Meal,"Products and dishes","calories (kcal)"\n'
            '2026-01-02,Breakfast,Toast,200\n'
            '2026-01-01,Breakfast,"Bigger breakfast",500\n'  # supersedes the first import
        )
        fitatu.import_exports([b])
        get_config.cache_clear()
        stored = load_nutrition().set_index("date")
        assert len(stored) == 2                                   # Jan-01 not lost (B1)
        assert stored.loc[dt.date(2026, 1, 1), "calories_kcal"] == 500.0  # superseded, not summed (B2)
        assert stored.loc[dt.date(2026, 1, 2), "calories_kcal"] == 200.0
    finally:
        get_config.cache_clear()


def test_overlapping_dates_across_files_are_not_summed(tmp_path, monkeypatch):
    """Two files in one import containing the same day -> keep-last, not summed (B2)."""
    monkeypatch.setenv("AITHLETE_DATA_DIR", str(tmp_path))
    from aithlete.analysis.loaders import load_nutrition
    from aithlete.config import get_config
    get_config.cache_clear()
    try:
        f1 = tmp_path / "f1.csv"
        f1.write_text('Date,Meal,"Products and dishes","calories (kcal)"\n2026-01-01,B,x,100\n')
        f2 = tmp_path / "f2.csv"
        f2.write_text('Date,Meal,"Products and dishes","calories (kcal)"\n2026-01-01,B,y,400\n')
        fitatu.import_exports([f1, f2])
        get_config.cache_clear()
        stored = load_nutrition()
        assert len(stored) == 1
        assert stored.iloc[0]["calories_kcal"] == 400.0  # later file wins, not 500.0
    finally:
        get_config.cache_clear()


# --- energy availability ---------------------------------------------------
def test_low_energy_availability_flags_red():
    dates = [dt.date(2026, 4, 1) + dt.timedelta(days=i) for i in range(20)]
    nutrition = _daily(dates, calories_kcal=1900.0, carbs_g=200.0, protein_g=120.0, fat_g=60.0)
    digest = nut.build_digest(nutrition, EMPTY_ACTS, EMPTY_WELL, profile=_profile(70))
    # EA = 1900 / 70 ≈ 27.1 -> below 30 -> RED
    assert digest.energy_availability_flag is ReadinessFlag.RED
    assert digest.energy_availability_kcal_per_kg.value < 30


def test_adequate_energy_availability_flags_green():
    dates = [dt.date(2026, 4, 1) + dt.timedelta(days=i) for i in range(20)]
    nutrition = _daily(dates, calories_kcal=3400.0, carbs_g=450.0, protein_g=130.0, fat_g=90.0)
    digest = nut.build_digest(nutrition, EMPTY_ACTS, EMPTY_WELL, profile=_profile(70))
    # EA = 3400 / 70 ≈ 48.6 -> GREEN
    assert digest.energy_availability_flag is ReadinessFlag.GREEN
    assert digest.protein_flag is ReadinessFlag.GREEN  # 130/70 ≈ 1.86 g/kg


def test_exercise_energy_lowers_ea():
    weight = 70.0
    acts = pd.DataFrame([
        {"date": dt.date(2026, 4, 5), "sport": "bike", "tss": 100, "duration_s": 7200,
         "avg_w": 200.0, "intensity_class": "moderate"},
    ])
    eee = nut.exercise_energy_by_day(acts, weight)
    # 200 W * 7200 s / 1000 = 1440 kJ ≈ 1440 kcal
    assert abs(eee.iloc[0]["exercise_kcal"] - 1440.0) < 1.0


# --- carb periodization ----------------------------------------------------
def test_carb_tier_selection():
    tiers = load_nutrition_rules()["carbohydrate"]["tiers"]
    assert nut._carb_tier(10, tiers)[0] == "rest"
    assert nut._carb_tier(60, tiers)[0] == "moderate"
    assert nut._carb_tier(120, tiers)[0] == "high"
    assert nut._carb_tier(400, tiers)[0] == "very_high"


def test_key_session_underfueling_detected():
    # A hard day (high TSS) with very low carbs should count as under-fuelled.
    d = dt.date(2026, 4, 10)
    nutrition = _daily([d], calories_kcal=2500.0, carbs_g=150.0, protein_g=120.0)  # 150/70 ≈ 2.1 g/kg
    acts = pd.DataFrame([
        {"date": d, "sport": "run", "tss": 120, "duration_s": 5400,
         "avg_w": None, "intensity_class": "hard"},
    ])
    digest = nut.build_digest(nutrition, acts, EMPTY_WELL, profile=_profile(70))
    assert digest.key_session_fuel_pct.value == 0.0  # the one key day missed its carb target


# --- recovery correlations -------------------------------------------------
def test_recovery_correlation_detected():
    dates = [dt.date(2026, 4, 1) + dt.timedelta(days=i) for i in range(20)]
    carbs = [100.0 + 10 * i for i in range(20)]
    nutrition = _daily(dates, calories_kcal=[2500.0] * 20, carbs_g=carbs, protein_g=120.0)
    # next-morning HRV rises with prior-day carbs (deterministic, strong).
    well = pd.DataFrame({
        "date": [d + dt.timedelta(days=1) for d in dates],
        "hrv_rmssd_ms": [50.0 + 0.3 * c for c in carbs],
        "resting_hr_bpm": [50.0] * 20,
        "sleep_hours": [7.5] * 20,
        "weight_kg": [70.0] * 20,
    })
    digest = nut.build_digest(nutrition, EMPTY_ACTS, well, profile=_profile(70))
    finding = next((c for c in digest.recovery_correlations
                    if c.driver == "carbs_g_per_kg" and c.outcome == "next_day_hrv_rmssd_ms"), None)
    assert finding is not None
    assert finding.n >= 14
    assert finding.r > 0.9 and finding.notable


def test_wellness_missing_column_does_not_crash():
    """A non-empty wellness frame missing a column (e.g. sleep_hours) must not
    raise (S1) — correlations needing it are simply skipped."""
    dates = [dt.date(2026, 4, 1) + dt.timedelta(days=i) for i in range(20)]
    nutrition = _daily(dates, calories_kcal=3000.0, carbs_g=400.0, protein_g=120.0)
    well = pd.DataFrame({
        "date": [d + dt.timedelta(days=1) for d in dates],
        "hrv_rmssd_ms": [60.0] * 20,
        # no resting_hr_bpm, no sleep_hours, no weight_kg
    })
    digest = nut.build_digest(nutrition, EMPTY_ACTS, well, profile=_profile(70))
    assert digest.days_logged == 20
    # sleep-based correlations are absent, but the build still succeeds
    assert all("sleep_hours" not in c.outcome for c in digest.recovery_correlations)


def test_empty_nutrition_is_safe():
    digest = nut.build_digest(pd.DataFrame(), EMPTY_ACTS, EMPTY_WELL, profile=_profile())
    assert digest.days_logged == 0
    assert digest.missing
