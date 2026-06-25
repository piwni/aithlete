---
name: training-plan
description: Create a periodized multi-week triathlon training plan that targets the athlete's competition calendar. Use when the user asks to "build/create a training plan", "plan my next N weeks", or "get me ready for <race>". The plan is checked by a deterministic validator before anything is pushed.
---

# Training plan

You are an expert triathlon coach. You author a plan as JSON; a deterministic
Python validator enforces safety (ramp caps, recovery cadence, taper, race-day
form, intensity distribution, volume). **A plan that fails validation must not be
pushed** — fix it and re-validate.

## Steps

1. **Gather inputs** (run from repo root):
   ```bash
   uv run aithlete fetch
   uv run aithlete profile
   uv run aithlete readiness
   uv run aithlete context
   ```
   Then read:
   - `data/context/athlete.json` — current fitness (CTL/ATL/TSB per sport),
     baselines, PRs, durability, `missing` fields, readiness flag.
   - `data/calendar-athlete.json` — the competition calendar (A/B/C races).
     If it does not exist or lacks the target race, ask the user for the race
     name, date, distance bucket (sprint/olympic/half/full), and priority, and
     create/update this file.
   - `knowledge/triathlon/notes.md` — how great amateurs and pros train.
   - `knowledge/triathlon/rules.yaml` and `zones.yaml` — the numeric rules the
     validator will enforce. **Author within these from the start** (ramp,
     recovery weeks, taper, intensity distribution, frequency, long-session caps).

2. **Design the macro structure** working backwards from the A race: phases
   (base/build/peak/taper), a recovery week every 3rd–4th week, a taper of the
   right length, and an intensity distribution matching the athlete's level.
   Respect `weekly_volume_tolerance_hours` from the profile.

3. **Write the plan JSON** conforming to `src/aithlete/models/plan.py`
   (`TrainingPlan` → `weeks` → `sessions` → `structure` of `WorkoutStep`s).
   - Set `starting_ctl` from the digest's combined CTL.
   - Set per-session `planned_tss`, `planned_duration_s`, `intensity_class`,
     `sport`, and (for key sessions) a structured `structure` with `target`s as
     fractions of the anchor (e.g. power `low_pct: 0.95` = 95% FTP).
   - Mark bricks `is_brick: true`; include at least one per build week.
   - Add the goal race as `goal_race`.
   Save to `data/plans/<plan_id>.json`.

4. **Validate, then push** (validation gates the push):
   ```bash
   uv run aithlete validate data/plans/<plan_id>.json
   ```
   - If it FAILS: read each error, adjust the offending weeks/sessions, re-validate.
     Common fixes: lower a week's TSS to respect the ramp cap, insert/strengthen a
     recovery week, deepen the taper to hit the race-day TSB target, add easy volume
     to fix intensity distribution.
   - When it PASSES (warnings are acceptable, errors are not):
   ```bash
   uv run aithlete push data/plans/<plan_id>.json --target intervals
   ```
   This stamps plan provenance (context hash, knowledge version), builds intervals
   workout text, and upserts the events (idempotent by `external_id`). Note any
   Garmin-sync warnings to the user.

## Rules
- Never push a plan with validation errors.
- Honor `provenance`: if FTP/CSS/threshold is `unknown`, prescribe by RPE/HR and
  schedule a test, rather than inventing a number.
- Keep ~75–85% of weekly time easy unless peaking. This is not medical advice.
