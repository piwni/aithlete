"""Build a TrainingPlan from a declarative season spec (YAML).

Generic periodization engine: all athlete/season-specific input lives in the
spec file (see examples/season-2026-malbork.yaml), nothing is hardcoded here.
Each week is either generated from the swim-heavy build microcycle or listed
explicitly (fixed / taper / recovery / race weeks).

Run:
  uv run python scripts/build_plan.py [SPEC.yaml]
  AITHLETE_DATA_DIR=examples/data uv run python scripts/build_plan.py examples/season-2026-malbork.yaml

Then: uv run aithlete validate <plans_dir>/<plan_id>.json
"""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

import yaml

from aithlete.models.common import Sport
from aithlete.models.plan import (
    EventCategory,
    GoalRace,
    IntensityClass,
    Phase,
    PlannedSession,
    PlanWeek,
    TrainingPlan,
)
from aithlete.models.profile import DistanceBucket
from aithlete.storage.io import save_json
from aithlete.storage.paths import plan_path

DEFAULT_SPEC = Path(__file__).resolve().parents[1] / "examples" / "season-2026-malbork.yaml"

INTENSITY = {"easy": IntensityClass.EASY, "moderate": IntensityClass.MODERATE,
             "hard": IntensityClass.HARD}
CATEGORY = {"workout": EventCategory.WORKOUT, "race": EventCategory.RACE,
            "note": EventCategory.NOTE}

# TSS per hour by (sport, intensity). Calibrated to typical AG triathlete data
# (easy = Z1-2, moderate = tempo/threshold, hard = VO2/anaerobic).
RATE = {
    (Sport.SWIM, IntensityClass.EASY): 32, (Sport.SWIM, IntensityClass.MODERATE): 48,
    (Sport.SWIM, IntensityClass.HARD): 62,
    (Sport.BIKE, IntensityClass.EASY): 46, (Sport.BIKE, IntensityClass.MODERATE): 70,
    (Sport.BIKE, IntensityClass.HARD): 88,
    (Sport.RUN, IntensityClass.EASY): 55, (Sport.RUN, IntensityClass.MODERATE): 75,
    (Sport.RUN, IntensityClass.HARD): 95,
    (Sport.OTHER, IntensityClass.EASY): 22, (Sport.OTHER, IntensityClass.MODERATE): 30,
    (Sport.OTHER, IntensityClass.HARD): 35,
}


def mk_session(day: dt.date, *, sport: Sport, name: str, intensity: IntensityClass,
               minutes: float, brick: bool = False, desc: str = "",
               category: EventCategory = EventCategory.WORKOUT,
               tss: float | None = None) -> PlannedSession:
    dur = int(round(minutes * 60))
    if tss is None:
        tss = round(RATE[(sport, intensity)] * minutes / 60.0, 1)
    return PlannedSession(
        date=day, sport=sport, name=name, intensity_class=intensity,
        planned_tss=tss, planned_duration_s=dur, is_brick=brick,
        category=category, description=desc or None,
        external_id=f"aithlete-{day.isoformat()}-{sport.value}-{name[:12].replace(' ', '_')}",
    )


def session_from_spec(monday: dt.date, spec: dict) -> PlannedSession:
    return mk_session(
        monday + dt.timedelta(days=int(spec["day"])),
        sport=Sport(spec["sport"]),
        name=spec["name"],
        intensity=INTENSITY[spec.get("intensity", "easy")],
        minutes=float(spec["minutes"]),
        brick=bool(spec.get("brick", False)),
        desc=spec.get("desc", ""),
        category=CATEGORY[spec.get("category", "workout")],
        tss=spec.get("tss"),
    )


def build_microcycle(monday: dt.date, *, target_h: float, key: IntensityClass,
                     long_ride_h: float, long_run_h: float, brick_run_h: float = 0.5,
                     long_ride_blocks: str | None = None, swim_css: str | None = None,
                     half_focus: bool = False) -> list[PlannedSession]:
    """Swim-heavy build week (Mon-Sun). Fixed long ride/brick/long run anchor
    endurance; support sessions scale so weekly hours land near target_h. Keeps
    swim>=3/wk. Set long_run_h=0 to drop the standalone long run (e.g. when the
    long brick run already covers run-endurance that week). long_ride_blocks, if
    given (e.g. "4x30' @ 200 W"), prescribes IM-pace work inside the long ride.
    swim_css (e.g. "1:45-1:48/100m") comes from the spec's meta, not hardcoded."""
    def S(off, sport, name, inten, mins, **kw):
        return mk_session(monday + dt.timedelta(days=off), sport=sport, name=name,
                          intensity=inten, minutes=mins, **kw)

    bike_key_h = 0.95 if target_h >= 15 else 0.8
    bike_tempo_h = 1.1 if target_h >= 15 else 0.9
    brick_desc = ("First 10' @ IM/half race pace off the bike, then Z2." if brick_run_h <= 0.75
                  else "IM-pace brick run off the long ride: rehearse race legs, pacing and fuel.")
    long_ride_desc = ("On the TT bike in aero. Hold race position; fuel 70-90g carbs/h. "
                      "New disc/aero setup -> validate position + power-speed.")
    if long_ride_blocks:
        long_ride_desc = (f"On the TT bike in aero. IM-pace work: {long_ride_blocks} "
                          "(rest of ride steady Z2). Hold race position; fuel 70-90g carbs/h. "
                          "Convert the new disc/aero setup into free IM speed.")
    E, M = IntensityClass.EASY, IntensityClass.MODERATE
    out = [
        S(0, Sport.SWIM, "Swim technique + aerobic", E, 50,
          desc="Drills + 8-12x100 aerobic @ CSS+5s. Limiter focus: form under low fatigue."),
        S(0, Sport.OTHER, "Strength (lower + core)", E, 45,
          desc="Heavy-ish posterior chain + core; injury prevention, not fatigue."),
        S(1, Sport.BIKE, "Bike key set", key, bike_key_h * 60,
          desc=("Half-IM race-power over/unders 3x10' @ 95-102% FTP" if half_focus
                else "VO2/threshold 5x4' @ 110-118% FTP or 3x12' @ 95-100% FTP")),
        S(2, Sport.SWIM, "Swim threshold", M, 55,
          desc=(f"Main set ~2000m @ CSS (target {swim_css}). Build swim-specific fitness."
                if swim_css else "Main set ~2000m @ CSS. Build swim-specific fitness.")),
        S(2, Sport.RUN, "Run easy + strides", E, 45,
          desc="Z2 aerobic + 6x20s strides. Off-feet recovery from bike key day."),
        S(3, Sport.BIKE, "Bike tempo/sweet-spot", M, bike_tempo_h * 60,
          desc="2-3x20' @ 88-94% FTP (sweet spot). Aerobic-power durability."),
        S(4, Sport.SWIM, "Swim aerobic endurance", E, 50,
          desc="Continuous + pull/paddles; 2500-3000m steady. Third swim = the needle-mover."),
        S(4, Sport.OTHER, "Mobility / core", E, 30, desc="Optional; keep easy."),
        S(5, Sport.BIKE, "Long ride (Z2) + brick", E, long_ride_h * 60, desc=long_ride_desc),
        S(5, Sport.RUN, "Brick run off bike", E, brick_run_h * 60, brick=True, desc=brick_desc),
    ]
    if long_run_h > 0:
        out.append(S(6, Sport.RUN, "Long run (Z2)", E, long_run_h * 60,
                     desc="Aerobic long run; last 15-20' @ race effort when fresh enough."))
    anchor = long_ride_h + max(long_run_h, 0.0) + brick_run_h
    flex = sum(x.planned_duration_s for x in out) / 3600 - anchor
    want_flex = max(target_h - anchor, flex * 0.5)
    factor = want_flex / flex if flex > 0 else 1.0
    anchors = {"Long ride (Z2) + brick", "Long run (Z2)", "Brick run off bike"}
    scaled = []
    for x in out:
        if x.name in anchors:
            scaled.append(x)
        else:
            scaled.append(mk_session(
                dt.date.fromisoformat(x.date.isoformat()), sport=x.sport, name=x.name,
                intensity=x.intensity_class, minutes=x.planned_duration_s / 60 * factor,
                brick=x.is_brick, desc=x.description or ""))
    return scaled


def build_plan(spec: dict) -> TrainingPlan:
    meta = spec["meta"]
    a_race = next(r for r in spec["races"] if str(r.get("priority", "")).upper() == "A")
    plan = TrainingPlan(
        athlete_id=meta["athlete_id"],
        plan_id=meta["plan_id"],
        start_date=dt.date.fromisoformat(str(meta["start_date"])),
        starting_ctl=float(meta["starting_ctl"]),
        goal_race=GoalRace(name=a_race["name"], date=dt.date.fromisoformat(str(a_race["date"])),
                           bucket=DistanceBucket(a_race["bucket"]), priority="A"),
        rationale=meta.get("rationale", "").strip() or None,
    )
    weeks: list[PlanWeek] = []
    for w in spec["weeks"]:
        monday = dt.date.fromisoformat(str(w["start"]))
        if w["type"] == "microcycle":
            mc = dict(w["microcycle"])
            mc["key"] = INTENSITY[mc["key"]]
            mc.setdefault("swim_css", meta.get("swim_css"))
            sessions = build_microcycle(monday, **mc)
            sessions += [session_from_spec(monday, s) for s in w.get("extra_sessions", [])]
        else:
            sessions = [session_from_spec(monday, s) for s in w["sessions"]]
        weeks.append(PlanWeek(week_index=int(w["index"]), start_date=monday,
                              phase=Phase(w["phase"]), is_recovery=bool(w.get("is_recovery", False)),
                              sessions=sessions))
    plan.weeks = weeks
    return plan


def main() -> None:
    spec_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_SPEC
    spec = yaml.safe_load(spec_path.read_text())
    plan = build_plan(spec)
    out = plan_path(plan.plan_id)
    save_json(out, plan.model_dump(mode="json"))

    print(f"Wrote {out}  (from {spec_path})")
    print(f"{'wk':>2} {'start':>10} {'phase':>10} {'rec':>3} {'h':>5} {'TSS':>6}  swim/bike/run")
    for w in plan.weeks:
        cnt = {sp.value: c for sp, c in w.sessions_by_sport().items()}
        print(f"{w.week_index:>2} {w.start_date.isoformat():>10} {w.phase.value:>10} "
              f"{'Y' if w.is_recovery else '-':>3} {w.planned_hours:>5.1f} {w.planned_tss:>6.0f}  "
              f"{cnt.get('swim', 0)}/{cnt.get('bike', 0)}/{cnt.get('run', 0)}")


if __name__ == "__main__":
    main()
