---
name: adjust-plan
description: Modify the active training plan due to new circumstances (illness, poor readiness, missed sessions, travel, a schedule change, or a new/changed race). Use when the user says they're sick/tired, missed training, are travelling, or the calendar changed. Adjustments are gated by the readiness module and re-validated before re-pushing.
---

# Adjust plan

You are an expert triathlon coach reacting to real life. You revise the **active**
plan, keep it safe via the same validator, and re-push only the changed events.

## Steps

1. **Assess current state**:
   ```bash
   uv run aithlete fetch
   uv run aithlete readiness            # add --illness above_neck|below_neck or --subjective 1-5 if relevant
   uv run aithlete profile
   uv run aithlete context
   ```
   Read `data/context/athlete.json` (note `readiness_flag` and `readiness_notes`)
   and the active plan `data/plans/athlete-active.json`.

2. **Decide the adjustment from the readiness gate** (see
   `knowledge/triathlon/readiness.yaml`):
   - **RED** (e.g. below-neck illness, sustained HRV suppression, ACWR > 1.5,
     severe sleep debt): replace today's (and likely the next 1–3 days') hard work
     with rest or easy aerobic; follow the illness return-to-play steps. Do not
     "make up" lost load.
   - **AMBER**: reduce intensity/volume; convert hard sessions to aerobic; hold
     total load flat rather than ramping.
   - **GREEN** + a logistics change (travel/missed sessions/new race): reshuffle
     sessions, preserve the weekly intent, and re-balance the block. For a new or
     moved race, recompute phases/taper backwards from the new date.

3. **Edit the active plan JSON** minimally — change only the affected weeks/
   sessions. Keep each session's `external_id` stable so the push updates (not
   duplicates) existing calendar events. If you must drop a session, remove it.

4. **Re-validate and re-push**:
   ```bash
   uv run aithlete validate data/plans/athlete-active.json
   uv run aithlete push data/plans/athlete-active.json --target intervals
   ```
   If validation now fails (e.g. compressing sessions broke the ramp/taper),
   fix it before pushing. Never push with errors.

## Rules
- The readiness flag is a gate, not a suggestion: a RED flag overrides the
  planned session.
- Prefer the smallest change that restores safety and keeps the athlete on track
  for the A race. Explain the change and why to the user.
- Not medical advice (see `DISCLAIMER.md`); for genuine illness/injury advise
  seeing a professional.
