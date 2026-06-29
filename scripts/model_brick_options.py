"""Compare brick-structure variants against the deterministic guardrails.

Exploratory: builds the base plan from the season spec, applies each brick
variant (only touching the IM-build weekend long ride / brick run / long run),
runs validate_plan, and prints a side-by-side metrics table. Does NOT touch the
committed plan.

Run: uv run python scripts/model_brick_options.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_plan as bp  # noqa: E402

from aithlete.planning.validator import _daily_plan_tss, validate_plan  # noqa: E402

SPEC = Path(__file__).resolve().parents[1] / "examples" / "season-2026-malbork.yaml"

LONG_RIDE = "Long ride (Z2) + brick"
BRICK_RUN = "Brick run off bike"
LONG_RUN = "Long run (Z2)"

# Each variant: {week_index: {"long_ride": min, "brick": min, "long_run": min|None}}
# None for long_run => remove the standalone long run that week.
VARIANTS: dict[str, dict] = {
    "V0 baseline (committed)": {},
    "V1 progressive brick": {
        5: {"brick": 45, "long_run": 90},
        6: {"brick": 60, "long_run": 90},
        8: {"brick": 75, "long_run": 75},
        9: {"brick": 90, "long_run": 60},
    },
    "V2 coach 3h+2h recurring": {
        5: {"brick": 45, "long_run": 90},
        6: {"long_ride": 180, "brick": 120, "long_run": None},
        8: {"long_ride": 180, "brick": 120, "long_run": None},
        9: {"long_ride": 180, "brick": 120, "long_run": None},
    },
    "V3 bike-dominant IM-sim": {
        5: {"brick": 45, "long_run": 90},
        6: {"brick": 60, "long_run": 90},
        8: {"long_ride": 240, "brick": 90, "long_run": None},
        9: {"long_ride": 270, "brick": 90, "long_run": None},
    },
    "V4 4h+2h (3+2, more bike)": {
        5: {"brick": 45, "long_run": 90},
        6: {"long_ride": 240, "brick": 120, "long_run": None},
        8: {"long_ride": 240, "brick": 120, "long_run": None},
        9: {"long_ride": 270, "brick": 120, "long_run": None},
    },
}


def set_minutes(sess, minutes: float) -> None:
    sess.planned_duration_s = int(round(minutes * 60))
    sess.planned_tss = round(bp.RATE[(sess.sport, sess.intensity_class)] * minutes / 60.0, 1)


def apply_variant(plan, overrides: dict):
    plan = plan.model_copy(deep=True)
    by_index = {w.week_index: w for w in plan.weeks}
    for widx, ov in overrides.items():
        wk = by_index[widx]
        named = {s.name: s for s in wk.sessions}
        if "long_ride" in ov and LONG_RIDE in named:
            set_minutes(named[LONG_RIDE], ov["long_ride"])
        if "brick" in ov and BRICK_RUN in named:
            set_minutes(named[BRICK_RUN], ov["brick"])
        if "long_run" in ov:
            if ov["long_run"] is None:
                wk.sessions = [s for s in wk.sessions if s.name != LONG_RUN]
            elif LONG_RUN in named:
                set_minutes(named[LONG_RUN], ov["long_run"])
    return plan


def peak_acwr(plan) -> float:
    daily = _daily_plan_tss(plan)
    if daily.empty:
        return 0.0
    s = daily.reset_index(drop=True)
    cum = s.cumsum()
    worst = 0.0
    for i in range(len(s)):
        if i < 27:
            continue
        acute = cum.iloc[i] - cum.iloc[i - 7]
        chronic = (cum.iloc[i] - (cum.iloc[i - 28] if i >= 28 else 0.0)) / 4.0
        if chronic > 0:
            worst = max(worst, acute / chronic)
    return worst


def metrics(name: str, plan):
    rep = validate_plan(plan, starting_ctl=plan.starting_ctl,
                        weekly_hours_tolerance=None, training_age="intermediate")
    weeks = plan.weeks
    peak_h = max(w.planned_hours for w in weeks)
    peak_tss = max(w.planned_tss for w in weeks)

    # biggest single training day (sum sessions per date, excluding the races)
    day_tot: dict = {}
    big_session_h = 0.0
    for w in weeks:
        for s in w.sessions:
            if s.category.value == "RACE":
                continue
            day_tot.setdefault(s.date, [0.0, 0.0])
            day_tot[s.date][0] += s.planned_duration_s / 3600
            day_tot[s.date][1] += s.planned_tss
            big_session_h = max(big_session_h, s.planned_duration_s / 3600)
    big_day_h, big_day_tss = max(day_tot.values(), key=lambda v: v[1])

    # run-off-bike volume in the IM block (W5-9)
    brick_min = sum(s.planned_duration_s / 60 for w in weeks if 5 <= w.week_index <= 9
                    for s in w.sessions if s.is_brick and s.sport.value == "run")

    # max peak-to-peak load-week TSS ramp (BUILD/PEAK/BASE, non-recovery)
    loads = [w.planned_tss for w in weeks
             if w.phase.value in ("build", "peak", "base") and not w.is_recovery]
    max_ramp = 0.0
    for a, b in zip(loads, loads[1:], strict=False):
        if a > 0:
            max_ramp = max(max_ramp, (b - a) / a * 100)

    ctl = rep.projected_ctl_by_week
    max_ctl_ramp = 0.0
    prev = plan.starting_ctl or 0.0
    for c in ctl:
        max_ctl_ramp = max(max_ctl_ramp, c - prev)
        prev = c

    err_codes = sorted({i.code for i in rep.errors})
    return {
        "name": name,
        "pass": "PASS" if rep.ok else "FAIL",
        "err": len(rep.errors),
        "warn": len(rep.warnings),
        "tsb": rep.projected_race_tsb,
        "peak_h": peak_h,
        "peak_tss": peak_tss,
        "big_day_h": big_day_h,
        "big_day_tss": big_day_tss,
        "big_sess_h": big_session_h,
        "brick_min": brick_min,
        "ramp": max_ramp,
        "ctl_ramp": max_ctl_ramp,
        "acwr": peak_acwr(plan),
        "err_codes": err_codes,
    }


def main() -> None:
    spec = yaml.safe_load(SPEC.read_text())
    base = bp.build_plan(spec)
    rows = [metrics(name, apply_variant(base, ov)) for name, ov in VARIANTS.items()]

    hdr = (f"{'variant':<26}{'res':>5}{'wn':>3}{'TSB':>6}{'pkH':>6}{'pkTSS':>7}"
           f"{'bigDay':>8}{'bigRun':>7}{'rampTSS':>8}{'ctl/w':>7}{'ACWR':>6}")
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        print(f"{r['name']:<26}{r['pass']:>5}{r['warn']:>3}"
              f"{(r['tsb'] if r['tsb'] is not None else 0):>6.1f}"
              f"{r['peak_h']:>6.1f}{r['peak_tss']:>7.0f}"
              f"{r['big_day_h']:>6.1f}h{r['big_day_tss']:>6.0f}"
              f"{r['brick_min']:>6.0f}'"
              f"{r['ramp']:>7.0f}%{r['ctl_ramp']:>7.1f}{r['acwr']:>6.2f}")
        if r["err_codes"]:
            print(f"{'':<26}errors: {', '.join(r['err_codes'])}")
    print()
    print("Legend: pkH/pkTSS=biggest week; bigDay=biggest single training day (h / TSS);")
    print("bigRun=total brick-run minutes across IM block (W5-9); rampTSS=max peak-to-peak")
    print("load-week TSS jump; ctl/w=max projected CTL gain/week (cap 7); ACWR cap 1.5.")


if __name__ == "__main__":
    main()
