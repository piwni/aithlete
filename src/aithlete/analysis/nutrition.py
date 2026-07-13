"""Nutrition analysis — deterministic fueling math.

Turns daily Fitatu totals + training load + wellness into the provenance-tagged
:class:`NutritionDigest`. Performance-fueling first (energy availability, carb
periodization, protein), then diet-quality/health, then observational
recovery associations. Thresholds come from ``knowledge/nutrition/rules.yaml``;
this module never makes judgments — it computes numbers and flags.

References: Mountjoy et al. (RED-S / LEA); Loucks (energy availability); Burke
et al. (carbohydrate periodization); Morton et al. (protein); Mifflin-St Jeor
(BMR). All exercise-energy and BMR figures are ESTIMATED.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd

from aithlete.analysis import metrics
from aithlete.knowledge import load_nutrition_rules
from aithlete.models.common import Provenance, Trend
from aithlete.models.context_digest import DigestValue
from aithlete.models.nutrition_digest import (
    CorrelationFinding,
    MicronutrientFlag,
    MonthSummary,
    NutritionDigest,
)
from aithlete.models.profile import AthleteProfile, Sex
from aithlete.models.readiness import ReadinessFlag

# MET (kcal/kg/h) by coarse sport family and intensity. Used only when power
# (cycling avg_w) is unavailable. Conservative endurance values.
_MET = {
    "run":   {"easy": 8.5, "moderate": 10.5, "hard": 12.5},
    "bike":  {"easy": 6.0, "moderate": 8.0,  "hard": 10.0},
    "swim":  {"easy": 6.0, "moderate": 8.3,  "hard": 10.0},
    "strength": {"easy": 3.5, "moderate": 5.0, "hard": 6.0},
    "other": {"easy": 4.0, "moderate": 6.0,  "hard": 8.0},
}
_BIKE_SPORTS = {"bike", "ride", "virtualride", "ebikeride"}
_SWIM_SPORTS = {"swim", "openwaterswim"}
_RUN_SPORTS = {"run", "trailrun", "treadmill"}
_STRENGTH_SPORTS = {"weighttraining", "workout", "yoga"}


def _sport_family(sport: str) -> str:
    s = (sport or "").lower()
    if s in _BIKE_SPORTS:
        return "bike"
    if s in _RUN_SPORTS:
        return "run"
    if s in _SWIM_SPORTS:
        return "swim"
    if s in _STRENGTH_SPORTS:
        return "strength"
    return "other"


def athlete_weight(profile: AthleteProfile | None, wellness: pd.DataFrame) -> tuple[float, str]:
    """Resolve body mass (kg) and a short source label."""
    if profile is not None and profile.anthropometrics.weight_kg.known:
        return float(profile.anthropometrics.weight_kg.value), "profile"
    if not wellness.empty and "weight_kg" in wellness and wellness["weight_kg"].notna().any():
        return float(wellness["weight_kg"].dropna().iloc[-1]), "intervals.icu wellness"
    return 72.0, "default (no weight on file)"


def bmr_kcal(profile: AthleteProfile | None, weight: float) -> tuple[float, str]:
    """Mifflin-St Jeor when sex/age/height are known; otherwise a weight-only RMR.

    Returns (bmr_kcal_per_day, assumption_note).
    """
    height = age = None
    sex = Sex.OTHER
    if profile is not None:
        sex = profile.anthropometrics.sex
        if profile.anthropometrics.height_cm.known:
            height = float(profile.anthropometrics.height_cm.value)
        age = profile.anthropometrics.age
    if height is not None and age is not None:
        const = {Sex.MALE: 5.0, Sex.FEMALE: -161.0}.get(sex, -78.0)  # sex-neutral midpoint
        bmr = 10 * weight + 6.25 * height - 5 * age + const
        note = "" if sex in (Sex.MALE, Sex.FEMALE) else "sex unknown -> neutral BMR constant"
        return round(bmr, 1), note
    return round(22.0 * weight, 1), "height/age unknown -> weight-only RMR (~22 kcal/kg)"


def exercise_energy_by_day(activities: pd.DataFrame, weight: float) -> pd.DataFrame:
    """Estimate exercise kcal (and hours) per day. CYCLING with power uses kJ
    work (~1 kJ work ≈ 1 kcal expended at ~24% gross efficiency); every other
    sport — including running/other activities that may carry a power field —
    uses MET * weight * hours.
    """
    if activities.empty:
        return pd.DataFrame(columns=["date", "exercise_kcal", "exercise_hours"])
    df = activities.copy()
    dur_h = df["duration_s"].fillna(0) / 3600.0
    fam = df["sport"].map(_sport_family)
    intensity = df.get("intensity_class")
    if intensity is None:
        intensity = pd.Series(["moderate"] * len(df), index=df.index)
    intensity = intensity.fillna("moderate")

    met = [
        _MET.get(f, _MET["other"]).get(i if i in ("easy", "moderate", "hard") else "moderate")
        for f, i in zip(fam, intensity, strict=False)
    ]
    met_kcal = pd.Series(met, index=df.index) * weight * dur_h

    # Power-based energy is a CYCLING approximation only (running/other power
    # meters don't share the ~24% efficiency identity), so gate on bike family.
    avgw = df["avg_w"] if "avg_w" in df.columns else pd.Series([pd.NA] * len(df), index=df.index)
    has_power = (fam == "bike") & avgw.notna()
    power_kcal = avgw.fillna(0) * df["duration_s"].fillna(0) / 1000.0  # kJ ≈ kcal

    kcal = met_kcal.where(~has_power, power_kcal)
    out = pd.DataFrame({"date": df["date"], "exercise_kcal": kcal, "exercise_hours": dur_h})
    return out.groupby("date", as_index=False)[["exercise_kcal", "exercise_hours"]].sum()


def _carb_tier(tss: float, tiers: dict) -> tuple[str, float, float]:
    """Pick the carbohydrate tier for a day's combined TSS."""
    order = ["rest", "moderate", "high", "very_high"]
    for name in order:
        cfg = tiers[name]
        cap = cfg.get("max_tss")
        if cap is None or tss <= cap:
            return name, float(cfg["min_g_per_kg"]), float(cfg["max_g_per_kg"])
    cfg = tiers["very_high"]
    return "very_high", float(cfg["min_g_per_kg"]), float(cfg["max_g_per_kg"])


def _corr(df: pd.DataFrame, xcol: str, ycol: str, method: str, min_n: int) -> tuple[float, int] | None:
    sub = df[[xcol, ycol]].dropna()
    if len(sub) < min_n:
        return None
    if sub[xcol].std(ddof=0) == 0 or sub[ycol].std(ddof=0) == 0:
        return None
    r = sub[xcol].corr(sub[ycol], method=method)
    if pd.isna(r):
        return None
    return float(round(r, 3)), int(len(sub))


def _dv(value, unit, *, samples=0, date_from=None, date_to=None, source, trend=Trend.UNKNOWN) -> DigestValue:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return DigestValue(value=None, unit=unit, provenance=Provenance.UNKNOWN)
    return DigestValue(
        value=round(float(value), 2), unit=unit, provenance=Provenance.ESTIMATED,
        source=source, date_from=date_from, date_to=date_to, sample_count=samples, trend=trend,
    )


def build_digest(
    nutrition: pd.DataFrame,
    activities: pd.DataFrame,
    wellness: pd.DataFrame,
    *,
    profile: AthleteProfile | None = None,
    athlete_id: str = "athlete",
) -> NutritionDigest:
    rules = load_nutrition_rules()
    digest = NutritionDigest(athlete_id=athlete_id)
    assumptions: list[str] = []
    missing: list[str] = []

    if nutrition.empty:
        digest.missing = ["no nutrition data (run `aithlete fitatu-import` first)"]
        return digest

    weight, weight_src = athlete_weight(profile, wellness)
    ffm = weight  # no body-fat input -> FFM proxied by body mass
    assumptions.append(f"body mass {weight:.1f} kg ({weight_src}); FFM proxied by body mass for EA")
    bmr, bmr_note = bmr_kcal(profile, weight)
    if bmr_note:
        assumptions.append(bmr_note)

    n = nutrition.copy()
    window_from, window_to = min(n["date"]), max(n["date"])
    digest.window_from, digest.window_to = window_from, window_to
    digest.days_logged = int(len(n))

    # --- merge exercise energy + combined TSS ---
    eee = exercise_energy_by_day(activities, weight)
    n = n.merge(eee, on="date", how="left")
    n["exercise_kcal"] = n["exercise_kcal"].fillna(0.0)
    if "exercise_hours" not in n.columns:
        n["exercise_hours"] = 0.0
    n["exercise_hours"] = n["exercise_hours"].fillna(0.0)

    tss = metrics.daily_tss(activities)
    if not tss.empty:
        n = n.merge(tss[["date", "combined"]].rename(columns={"combined": "tss"}), on="date", how="left")
    if "tss" not in n.columns:
        n["tss"] = 0.0
    n["tss"] = n["tss"].fillna(0.0)

    # --- energy availability + balance ---
    n["energy_availability"] = (n["calories_kcal"] - n["exercise_kcal"]) / ffm
    tef = rules["energy_balance"]["thermic_effect_of_food_pct"] / 100.0
    raf = rules["energy_balance"]["bmr_resting_activity_factor"]
    # bmr * raf already covers a full day's resting + non-exercise activity, so
    # subtract the resting cost of the exercise hours before adding gross
    # exercise kcal (otherwise resting energy during training is double-counted).
    resting_during_exercise = (bmr / 24.0) * n["exercise_hours"]
    n["tdee"] = bmr * raf + n["exercise_kcal"] - resting_during_exercise + n["calories_kcal"] * tef
    n["energy_balance"] = n["calories_kcal"] - n["tdee"]

    ea = rules["energy_availability"]
    mean_ea = float(n["energy_availability"].mean())
    low_ea_pct = float((n["energy_availability"] < ea["low_below"]).mean() * 100)
    if mean_ea < ea["low_below"]:
        digest.energy_availability_flag = ReadinessFlag.RED
    elif mean_ea < ea["optimal_min"]:
        digest.energy_availability_flag = ReadinessFlag.AMBER
    else:
        digest.energy_availability_flag = ReadinessFlag.GREEN

    digest.mean_intake_kcal = _dv(n["calories_kcal"].mean(), "kcal/day", samples=len(n),
                                  date_from=window_from, date_to=window_to, source="Fitatu daily totals")
    digest.mean_exercise_kcal = _dv(n["exercise_kcal"].mean(), "kcal/day", samples=len(n),
                                    date_from=window_from, date_to=window_to,
                                    source="estimated (power kJ / MET)")
    digest.energy_availability_kcal_per_kg = _dv(mean_ea, "kcal/kg FFM/day", samples=len(n),
                                                 date_from=window_from, date_to=window_to,
                                                 source="(intake - exercise) / body mass")
    digest.mean_energy_balance_kcal = _dv(n["energy_balance"].mean(), "kcal/day", samples=len(n),
                                          date_from=window_from, date_to=window_to,
                                          source="intake - estimated TDEE")
    digest.low_ea_days_pct = _dv(low_ea_pct, "% of days", samples=len(n),
                                 date_from=window_from, date_to=window_to,
                                 source=f"EA < {ea['low_below']} kcal/kg")

    # Under-logging plausibility: a day logged below BMR is almost certainly an
    # incomplete log, which understates intake and biases EA low. Surface this so
    # a RED EA flag isn't read as confirmed LEA without checking logging.
    under_logged_pct = float((n["calories_kcal"] < bmr).mean() * 100)
    if under_logged_pct > 0:
        assumptions.append(
            f"{under_logged_pct:.0f}% of days logged below BMR (~{bmr:.0f} kcal) -> likely "
            "incomplete logs; intake and EA read artificially low on those days")
    if digest.energy_availability_flag is ReadinessFlag.RED:
        missing.append("low energy-availability flag may be logging-driven; confirm intake "
                       "completeness and body-weight trend before treating it as RED-S risk")

    # --- macros per kg ---
    n["carbs_g_per_kg"] = n["carbs_g"] / weight
    n["protein_g_per_kg"] = n["protein_g"] / weight
    n["fat_g_per_kg"] = n["fat_g"] / weight
    digest.carbs_g_per_kg = _dv(n["carbs_g_per_kg"].mean(), "g/kg/day", samples=len(n),
                                date_from=window_from, date_to=window_to, source="Fitatu / body mass")
    digest.protein_g_per_kg = _dv(n["protein_g_per_kg"].mean(), "g/kg/day", samples=len(n),
                                  date_from=window_from, date_to=window_to, source="Fitatu / body mass")
    digest.fat_g_per_kg = _dv(n["fat_g_per_kg"].mean(), "g/kg/day", samples=len(n),
                              date_from=window_from, date_to=window_to, source="Fitatu / body mass")

    prot = rules["protein"]
    mean_pro = float(n["protein_g_per_kg"].mean())
    if mean_pro >= prot["min_g_per_kg"]:
        digest.protein_flag = ReadinessFlag.GREEN
    elif mean_pro >= prot["min_g_per_kg"] * 0.75:
        digest.protein_flag = ReadinessFlag.AMBER
    else:
        digest.protein_flag = ReadinessFlag.RED

    # --- carb periodization vs load ---
    tiers = rules["carbohydrate"]["tiers"]
    tier_min = n["tss"].map(lambda t: _carb_tier(float(t), tiers)[1])
    n["carb_underfuel"] = n["carbs_g_per_kg"] < tier_min
    digest.carb_underfuel_days_pct = _dv(float(n["carb_underfuel"].mean() * 100), "% of days",
                                         samples=len(n), date_from=window_from, date_to=window_to,
                                         source="carbs below load-matched tier min")
    key_tss = rules["carbohydrate"]["key_session_tss"]
    key_days = n[n["tss"] >= key_tss]
    if len(key_days):
        key_min = key_days["tss"].map(lambda t: _carb_tier(float(t), tiers)[1])
        met_pct = float((key_days["carbs_g_per_kg"].values >= key_min.values).mean() * 100)
        digest.key_session_fuel_pct = _dv(met_pct, "% of key days", samples=len(key_days),
                                          date_from=window_from, date_to=window_to,
                                          source=f"carbs >= tier min on TSS>={key_tss} days")
    else:
        missing.append("no key (hard/long) training days in window to assess session fueling")

    # --- micronutrients ---
    # Fitatu leaves most micronutrient cells blank per product, so a low mean is
    # usually under-logging, not deficiency. We report per-nutrient coverage and
    # mark a flag unreliable when too few days carry any value for it.
    micros = rules["micronutrients"]
    frac = micros["deficiency_fraction"]
    coverage_floor = 0.5
    unreliable_any = False
    for nutrient, cfg in micros.items():
        if not isinstance(cfg, dict) or nutrient not in n.columns:
            continue
        target = float(cfg.get("athlete_target", cfg.get("target")))
        mean_val = float(n[nutrient].mean())
        coverage = float((n[nutrient] > 0).mean())
        pct = (mean_val / target * 100) if target else 0.0
        reliable = coverage >= coverage_floor
        if not reliable:
            flag = ReadinessFlag.GREEN  # don't alarm on under-logged data
            unreliable_any = True
        elif pct < frac * 100 * 0.7:
            flag = ReadinessFlag.RED
        elif pct < frac * 100:
            flag = ReadinessFlag.AMBER
        else:
            flag = ReadinessFlag.GREEN
        digest.micronutrient_flags.append(MicronutrientFlag(
            nutrient=nutrient, mean_intake=round(mean_val, 2), target=target,
            unit=cfg.get("unit", ""), pct_of_target=round(pct, 1), flag=flag,
            coverage_pct=round(coverage * 100, 1), reliable=reliable,
        ))
    if unreliable_any:
        missing.append("some micronutrients are under-logged in Fitatu (low day coverage) -> "
                       "their intake is understated; treat those flags as 'unknown', not deficient")
    if profile is not None and profile.anthropometrics.sex == Sex.OTHER:
        assumptions.append("sex unknown -> male micronutrient targets (iron RDA differs for women)")

    # --- diet quality / health ---
    intake = n["calories_kcal"].replace(0, pd.NA)
    sugars_pct = float((n["sugars_g"] * 4 / intake * 100).mean())
    sat_pct = float((n["saturated_g"] * 9 / intake * 100).mean())
    digest.sugars_pct_energy = _dv(sugars_pct, "% of energy", samples=len(n),
                                   date_from=window_from, date_to=window_to, source="sugars*4 / kcal")
    digest.saturated_pct_energy = _dv(sat_pct, "% of energy", samples=len(n),
                                      date_from=window_from, date_to=window_to, source="satfat*9 / kcal")
    digest.fibre_g = _dv(n["fibre_g"].mean(), "g/day", samples=len(n),
                         date_from=window_from, date_to=window_to, source="Fitatu daily totals")
    digest.caffeine_mg = _dv(n["caffeine_mg"].mean(), "mg/day", samples=len(n),
                             date_from=window_from, date_to=window_to, source="Fitatu daily totals")
    digest.alcohol_drinks_per_week = _dv(float(n["alcoholic_drinks"].mean() * 7), "drinks/week",
                                         samples=len(n), date_from=window_from, date_to=window_to,
                                         source="name-matched proxy (excl. non-alcoholic)")

    # weight trend from wellness.
    if not wellness.empty and "weight_kg" in wellness and wellness["weight_kg"].notna().any():
        wk = wellness.dropna(subset=["weight_kg"]).sort_values("date")
        digest.weight_kg = _dv(float(wk["weight_kg"].iloc[-1]), "kg", samples=int(len(wk)),
                               date_from=min(wk["date"]), date_to=max(wk["date"]),
                               source="intervals.icu wellness", trend=metrics._trend(
                                   wk["weight_kg"].reset_index(drop=True), lookback=min(28, len(wk) - 1), eps=0.3))
    else:
        missing.append("no body-weight series for trend")

    # --- monthly trend ---
    n["_month"] = n["date"].map(lambda d: f"{d.year:04d}-{d.month:02d}")
    for month, part in n.groupby("_month"):
        digest.monthly.append(MonthSummary(
            month=month, days_logged=int(len(part)),
            mean_intake_kcal=round(float(part["calories_kcal"].mean()), 0),
            mean_energy_availability=round(float(part["energy_availability"].mean()), 1),
            mean_carbs_g_per_kg=round(float(part["carbs_g_per_kg"].mean()), 2),
            mean_protein_g_per_kg=round(float(part["protein_g_per_kg"].mean()), 2),
        ))

    # --- recovery correlations (observational) ---
    digest.recovery_correlations = _recovery_correlations(n, wellness, rules)
    if wellness.empty or not wellness.get("hrv_rmssd_ms", pd.Series(dtype=float)).notna().any():
        missing.append("no HRV/recovery data overlapping the nutrition window")

    missing.append("meal clock-times not in Fitatu export -> timing analysis is coarse")
    digest.assumptions = assumptions
    digest.missing = missing
    return digest


def _recovery_correlations(n: pd.DataFrame, wellness: pd.DataFrame, rules: dict) -> list[CorrelationFinding]:
    if wellness.empty:
        return []
    min_n = rules["correlation"]["min_samples"]
    notable = rules["correlation"]["notable_abs_r"]
    # reindex (not hard-index) so a wellness frame missing a column yields NaN
    # rather than a KeyError; _corr then skips it via dropna + min_n.
    well = wellness.reindex(columns=["date", "hrv_rmssd_ms", "resting_hr_bpm", "sleep_hours"])

    # Next-day join: nutrition day d -> wellness day d+1.
    nxt = n.copy()
    nxt["_join"] = nxt["date"].map(lambda d: d + dt.timedelta(days=1))
    nxt = nxt.merge(well.rename(columns={"date": "_join"}), on="_join", how="left")
    # Same-day join for sleep that night / caffeine.
    same = n.merge(well, on="date", how="left")

    specs = [
        (nxt, "energy_availability", "hrv_rmssd_ms", "pearson", 1, "low EA the day before vs next-morning HRV"),
        (nxt, "carbs_g_per_kg", "hrv_rmssd_ms", "pearson", 1, "carbohydrate intake vs next-morning HRV"),
        (nxt, "calories_kcal", "hrv_rmssd_ms", "pearson", 1, "total intake vs next-morning HRV"),
        (nxt, "alcoholic_drinks", "hrv_rmssd_ms", "pearson", 1, "alcohol vs next-morning HRV"),
        (nxt, "energy_availability", "resting_hr_bpm", "pearson", 1, "low EA vs next-morning resting HR"),
        (same, "caffeine_mg", "sleep_hours", "pearson", 0, "caffeine vs that night's sleep (timing unknown)"),
        (same, "alcoholic_drinks", "sleep_hours", "pearson", 0, "alcohol vs that night's sleep"),
    ]
    findings: list[CorrelationFinding] = []
    for frame, xcol, ycol, method, lag, note in specs:
        if xcol not in frame.columns or ycol not in frame.columns:
            continue
        res = _corr(frame, xcol, ycol, method, min_n)
        if res is None:
            continue
        r, count = res
        findings.append(CorrelationFinding(
            driver=xcol, outcome=("next_day_" if lag else "same_day_") + ycol,
            method=method, r=r, n=count, lag_days=lag, notable=abs(r) >= notable, note=note,
        ))
    return findings
