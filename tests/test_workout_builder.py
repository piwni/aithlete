"""Workout-builder tests: text generation, round-trip parse, event payloads."""

from __future__ import annotations

import datetime as dt

from _helpers import good_plan
from aithlete.models.common import Sport
from aithlete.models.plan import EventCategory, PlannedSession
from aithlete.planning.workout_builder import (
    WorkoutBuildError,
    build_workout_text,
    parse_workout_text,
    plan_to_events,
    session_to_event,
)


def test_build_and_parse_round_trip():
    plan = good_plan()
    bike_vo2 = plan.weeks[0].sessions[2]
    text = build_workout_text(bike_vo2)
    assert "5x" in text and "%" in text
    steps = parse_workout_text(text)
    repeat = [s for s in steps if s["type"] == "repeat"][0]
    assert repeat["reps"] == 5 and repeat["substeps"] == 2


def test_parse_rejects_malformed():
    try:
        parse_workout_text("this is not a workout line")
    except WorkoutBuildError:
        return
    raise AssertionError("expected WorkoutBuildError")


def test_event_has_stable_external_id_and_type():
    s = PlannedSession(date=dt.date(2026, 6, 22), sport=Sport.RUN, name="Easy run",
                       planned_tss=40, planned_duration_s=3000)
    ev = session_to_event(s, athlete_id="athlete")
    assert ev["external_id"] == "aithlete-athlete-2026-06-22-run"
    assert ev["type"] == "Run"
    assert ev["category"] == "WORKOUT"


def test_race_and_note_events():
    race = PlannedSession(date=dt.date(2026, 7, 1), sport=Sport.RUN, name="A race",
                          category=EventCategory.RACE)
    note = PlannedSession(date=dt.date(2026, 7, 1), sport=Sport.OTHER, name="Build starts",
                          category=EventCategory.NOTE, description="Begin build")
    assert session_to_event(race)["category"] == "RACE"
    note_ev = session_to_event(note)
    assert note_ev["category"] == "NOTE" and "type" not in note_ev


def test_plan_to_events_count_matches_sessions():
    plan = good_plan()
    assert len(plan_to_events(plan)) == len(plan.all_sessions())
