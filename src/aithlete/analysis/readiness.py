"""Readiness / monitoring engine.

Turns daily health + load data into red/amber/green flags using the thresholds
in knowledge/triathlon/readiness.yaml. Worst-wins aggregation. The resulting
flag GATES the adjust-plan skill (it can force a deload or rest day).

Optional same-day subjective inputs (wellness 1-5, illness symptoms) can be
passed in; otherwise those rules are skipped.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd

from aithlete.analysis import metrics
from aithlete.knowledge import load_readiness_rules
from aithlete.models.readiness import ReadinessFlag, ReadinessReport, RuleResult


def evaluate(
    wellness: pd.DataFrame,
    activities: pd.DataFrame,
    *,
    as_of: dt.date | None = None,
    subjective_wellness: float | None = None,
    illness: str | None = None,   # "above_neck" | "below_neck" | None
    athlete_id: str = "athlete",
) -> ReadinessReport:
    rules = load_readiness_rules()
    actions = rules["actions"]
    r = rules["rules"]

    if wellness.empty:
        as_of = as_of or (max(activities["date"]) if not activities.empty else dt.date.today())
    else:
        as_of = as_of or max(wellness["date"])

    report = ReadinessReport(athlete_id=athlete_id, date=as_of)
    results: list[RuleResult] = []

    w = wellness.sort_values("date") if not wellness.empty else wellness

    # --- HRV ---
    if not w.empty and w["hrv_rmssd_ms"].notna().any():
        last60 = w[w["date"] > as_of - dt.timedelta(days=60)]["hrv_rmssd_ms"].dropna()
        baseline = last60.mean() if len(last60) else None
        swc = 0.5 * (last60.std(ddof=1) / baseline) * baseline if baseline and len(last60) >= 14 else None
        latest = w["hrv_rmssd_ms"].dropna().iloc[-1]
        last4 = w[w["date"] > as_of - dt.timedelta(days=r["hrv"]["red_window_days"])]["hrv_rmssd_ms"].dropna()
        flag = ReadinessFlag.GREEN
        msg = "HRV within normal range"
        if baseline is not None and swc is not None:
            below = (last4 < baseline - swc).sum()
            if below >= r["hrv"]["red_if_below_for_days"]:
                flag, msg = ReadinessFlag.RED, f"HRV below baseline-SWC on {int(below)} of last {len(last4)} days"
            elif latest < baseline - swc:
                flag, msg = ReadinessFlag.AMBER, "HRV below baseline by more than SWC today"
        results.append(RuleResult(rule="hrv", flag=flag, observed=round(float(latest), 1),
                                  threshold=round(float(baseline - swc), 1) if (baseline and swc) else None,
                                  message=msg, action=actions[flag.value]))

    # --- Resting HR ---
    if not w.empty and w["resting_hr_bpm"].notna().any():
        last60 = w[w["date"] > as_of - dt.timedelta(days=60)]["resting_hr_bpm"].dropna()
        base = last60.mean() if len(last60) else None
        latest = w["resting_hr_bpm"].dropna().iloc[-1]
        flag, msg = ReadinessFlag.GREEN, "Resting HR normal"
        if base is not None:
            elev = latest - base
            if elev >= r["resting_hr"]["red_elevation_bpm"]:
                flag, msg = ReadinessFlag.RED, f"Resting HR elevated {elev:.0f} bpm vs baseline"
            elif elev >= r["resting_hr"]["amber_elevation_bpm"]:
                flag, msg = ReadinessFlag.AMBER, f"Resting HR elevated {elev:.0f} bpm vs baseline"
        results.append(RuleResult(rule="resting_hr", flag=flag, observed=round(float(latest), 1),
                                  threshold=round(float(base), 1) if base is not None else None,
                                  message=msg, action=actions[flag.value]))

    # --- Sleep debt ---
    if not w.empty and w["sleep_hours"].notna().any():
        cfg = r["sleep"]
        window = w[w["date"] > as_of - dt.timedelta(days=cfg["window_days"])]["sleep_hours"].dropna()
        debt = float((cfg["need_hours"] - window).clip(lower=0).sum())
        latest = float(w["sleep_hours"].dropna().iloc[-1])
        flag, msg = ReadinessFlag.GREEN, "Sleep adequate"
        if debt >= cfg["red_debt_hours"] or latest <= cfg["red_single_night_hours"]:
            flag, msg = ReadinessFlag.RED, f"Sleep debt {debt:.1f}h over {cfg['window_days']}d"
        elif debt >= cfg["amber_debt_hours"]:
            flag, msg = ReadinessFlag.AMBER, f"Sleep debt {debt:.1f}h over {cfg['window_days']}d"
        results.append(RuleResult(rule="sleep", flag=flag, observed=round(debt, 1),
                                  threshold=cfg["amber_debt_hours"], message=msg, action=actions[flag.value]))

    # --- ACWR ---
    acwr = _acwr(activities, as_of)
    if acwr is not None:
        cfg = r["acwr"]
        flag, msg = ReadinessFlag.GREEN, f"ACWR {acwr:.2f} in sweet spot"
        if acwr > cfg["red_above"]:
            flag, msg = ReadinessFlag.RED, f"ACWR {acwr:.2f} above danger threshold {cfg['red_above']}"
        elif acwr > cfg["amber_above"]:
            flag, msg = ReadinessFlag.AMBER, f"ACWR {acwr:.2f} elevated"
        elif acwr < cfg["amber_below"]:
            flag, msg = ReadinessFlag.AMBER, f"ACWR {acwr:.2f} low (under-load/detraining)"
        results.append(RuleResult(rule="acwr", flag=flag, observed=round(acwr, 2),
                                  threshold=cfg["red_above"], message=msg, action=actions[flag.value]))

    # --- Monotony (Foster) ---
    mono = _monotony(activities, as_of)
    if mono is not None:
        cfg = r["monotony"]
        flag, msg = ReadinessFlag.GREEN, f"Training monotony {mono:.2f} healthy"
        if mono > cfg["red_above"]:
            flag, msg = ReadinessFlag.RED, f"High monotony {mono:.2f} (vary load)"
        elif mono > cfg["amber_above"]:
            flag, msg = ReadinessFlag.AMBER, f"Elevated monotony {mono:.2f}"
        results.append(RuleResult(rule="monotony", flag=flag, observed=round(mono, 2),
                                  threshold=cfg["amber_above"], message=msg, action=actions[flag.value]))

    # --- Subjective wellness (optional) ---
    if subjective_wellness is not None:
        cfg = r["subjective_wellness"]
        flag, msg = ReadinessFlag.GREEN, "Subjective wellness good"
        if subjective_wellness < cfg["red_below"]:
            flag, msg = ReadinessFlag.RED, "Subjective wellness very low"
        elif subjective_wellness < cfg["amber_below"]:
            flag, msg = ReadinessFlag.AMBER, "Subjective wellness low"
        results.append(RuleResult(rule="subjective_wellness", flag=flag, observed=subjective_wellness,
                                  threshold=cfg["amber_below"], message=msg, action=actions[flag.value]))

    # --- Illness (optional) ---
    if illness in {"above_neck", "below_neck"}:
        cfg = r["illness"][illness]
        flag = ReadinessFlag(cfg["flag"])
        results.append(RuleResult(rule="illness", flag=flag, observed=illness,
                                  message=f"Illness ({illness.replace('_', '-')})",
                                  action=cfg["action"]))

    report.results = results
    report.recompute_overall()
    report.recommended_action = actions[report.overall_flag.value]
    return report


def _combined_to_asof(activities: pd.DataFrame, as_of: dt.date) -> pd.Series | None:
    """Daily combined TSS padded with zeros out to ``as_of`` so rest days after
    the last logged activity are counted (otherwise a layoff inflates monotony
    and distorts the acute window)."""
    if activities.empty:
        return None
    tss = metrics.daily_tss(activities).set_index("date")["combined"]
    if len(tss) == 0:
        return None
    start = min(tss.index)
    if as_of < start:
        return None
    idx = pd.date_range(start, as_of, freq="D").date
    return tss.reindex(idx, fill_value=0.0)


def _acwr(activities: pd.DataFrame, as_of: dt.date) -> float | None:
    tss = _combined_to_asof(activities, as_of)
    if tss is None:
        return None
    acute = tss[tss.index > as_of - dt.timedelta(days=7)].sum()
    chronic = tss[tss.index > as_of - dt.timedelta(days=28)].sum() / 4.0
    if chronic <= 0:
        return None
    return acute / chronic


def _monotony(activities: pd.DataFrame, as_of: dt.date) -> float | None:
    tss = _combined_to_asof(activities, as_of)
    if tss is None:
        return None
    last7 = tss[tss.index > as_of - dt.timedelta(days=7)]
    if len(last7) < 5 or last7.std(ddof=0) == 0:
        return None
    return float(last7.mean() / last7.std(ddof=0))
