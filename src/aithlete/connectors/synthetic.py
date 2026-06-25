"""Deterministic synthetic athlete.

Generates a believable ~16-week multisport history (activities + daily wellness)
plus athlete sport-settings. Used by:
- offline connectors (so the whole pipeline runs with no accounts), and
- the sample athlete fixture / tests.

Deterministic given a seed and an anchor end-date, so golden-file tests are
stable. This is fabricated data, not advice — see DISCLAIMER.md.
"""

from __future__ import annotations

import datetime as dt
import math
import random

# Fixed anchor so fixtures + golden tests are reproducible regardless of "today".
ANCHOR_END = dt.date(2026, 6, 15)
WEEKS = 16
SEED = 1337

# Athlete sport-settings (the "truth" the analysis should recover).
SETTINGS = {
    "weight_kg": 72.0,
    "bike": {"ftp_w": 268, "eftp_w": 272, "lthr_bpm": 162, "max_hr_bpm": 188, "cp_w": 265, "w_prime_kj": 19.5},
    "run": {"threshold_pace_s_per_km": 246, "lthr_bpm": 168, "max_hr_bpm": 190, "vo2max": 56.5},
    "swim": {"css_s_per_100m": 95.0},
    "vo2max_bike": 58.0,
}

# Weekly micro-structure: (weekday, sport, base_minutes, base_if, intensity_class)
# weekday: 0=Mon ... 6=Sun
WEEK_TEMPLATE = [
    (0, "Run", 50, 0.78, "easy"),
    (1, "Swim", 60, 0.95, "moderate"),
    (1, "Ride", 75, 0.80, "easy"),
    (2, "Run", 55, 0.98, "hard"),     # threshold/VO2 run
    (3, "Swim", 55, 0.88, "easy"),
    (3, "Ride", 70, 0.95, "hard"),    # bike intervals
    (5, "Ride", 180, 0.72, "easy"),   # long ride
    (6, "Run", 95, 0.75, "easy"),     # long run (+ brick off Sat sometimes)
]


def _sport_key(activity_type: str) -> str:
    return {"Swim": "swim", "Ride": "bike", "Run": "run"}[activity_type]


def _tss_for(minutes: float, intensity_factor: float) -> float:
    # TSS normalized to 100 = 1h @ threshold: sec * IF^2 / 3600 * 100.
    return round((minutes * 60) * (intensity_factor**2) / 3600 * 100, 1)


def generate(end: dt.date = ANCHOR_END, weeks: int = WEEKS, seed: int = SEED) -> dict:
    """Return {"activities": [...], "wellness": [...], "settings": {...}}."""
    rng = random.Random(seed)
    start = end - dt.timedelta(weeks=weeks)

    activities: list[dict] = []
    wellness: list[dict] = []

    # Progressive overload: load multiplier ramps then drops on recovery weeks (3:1).
    for w in range(weeks):
        week_start = start + dt.timedelta(weeks=w)
        is_recovery = (w % 4) == 3
        ramp = 0.82 + 0.018 * w  # gentle CTL build
        week_mult = ramp * (0.6 if is_recovery else 1.0)

        for weekday, atype, base_min, base_if, iclass in WEEK_TEMPLATE:
            day = week_start + dt.timedelta(days=weekday)
            if day > end:
                continue
            # Occasionally skip a session (missed training -> realistic compliance).
            if rng.random() < 0.08:
                continue
            minutes = max(20, base_min * week_mult * rng.uniform(0.92, 1.08))
            intensity = min(1.15, base_if * rng.uniform(0.97, 1.03))
            tss = _tss_for(minutes, intensity)
            start_dt = dt.datetime.combine(day, dt.time(hour=6 + (weekday % 3) * 4))
            act = {
                "id": f"syn-{day.isoformat()}-{atype.lower()}",
                "start_date_local": start_dt.isoformat(),
                "type": atype,
                "name": f"{atype} ({iclass})",
                "moving_time": int(minutes * 60),
                "elapsed_time": int(minutes * 60 * rng.uniform(1.0, 1.05)),
                "icu_training_load": tss,
                "icu_intensity": round(intensity * 100, 1),
                "_intensity_class": iclass,
            }
            if atype == "Ride":
                np_w = SETTINGS["bike"]["ftp_w"] * intensity
                act["average_watts"] = round(np_w * rng.uniform(0.9, 0.97), 0)
                act["icu_weighted_avg_watts"] = round(np_w, 0)  # NP
                act["icu_ftp"] = SETTINGS["bike"]["ftp_w"]
                # Decoupling worse on long rides.
                act["icu_efficiency"] = round(rng.uniform(1.6, 1.9), 2)
                act["decoupling"] = round(rng.uniform(2.0, 4.0) + (6.0 if minutes > 150 else 0.0), 1)
            elif atype == "Run":
                tp = SETTINGS["run"]["threshold_pace_s_per_km"]
                speed_pct = intensity  # rough
                act["average_pace_s_per_km"] = round(tp / max(0.6, speed_pct), 0)
                act["decoupling"] = round(rng.uniform(2.5, 5.5) + (5.0 if minutes > 80 else 0.0), 1)
            elif atype == "Swim":
                css = SETTINGS["swim"]["css_s_per_100m"]
                act["average_pace_s_per_100m"] = round(css / max(0.7, intensity), 0)
            activities.append(act)

        # Daily wellness for the whole week.
        for d in range(7):
            day = week_start + dt.timedelta(days=d)
            if day > end:
                continue
            # HRV oscillates around baseline 68ms; dips after hard/long weeks.
            phase = math.sin(w / 2.0)
            base_hrv = 68 - (5 if not is_recovery else -2)
            hrv = round(base_hrv + 6 * phase + rng.gauss(0, 4), 1)
            rhr = round(48 + (3 if not is_recovery else 0) - 2 * phase + rng.gauss(0, 1.5), 1)
            sleep_h = round(rng.uniform(6.6, 8.2), 2)
            wellness.append({
                "id": day.isoformat(),
                "hrv": hrv,                 # rMSSD ms
                "restingHR": rhr,
                "sleepSecs": int(sleep_h * 3600),
                "weight": round(72.0 + rng.gauss(0, 0.4), 1),
            })

    return {"activities": activities, "wellness": wellness, "settings": SETTINGS}
