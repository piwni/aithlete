# Nutrition coaching notes

Companion prose to `rules.yaml`. The deterministic thresholds live in the YAML;
this file is context for the nutritionist skill's *judgment*. Not medical advice
(see `DISCLAIMER.md`).

## Framing: performance fueling first

For an endurance/triathlon athlete the first question is **energy adequacy**, not
"clean eating". The failure mode that wrecks adaptation, recovery, HRV, sleep,
hormones, bone health and immunity is **low energy availability (LEA / RED-S)**,
not the occasional pizza. Read the digest in this priority order:

1. **Energy availability (EA)** — is the athlete fueling the work? `< 30 kcal/kg
   FFM/day` sustained is the red flag (RED-S risk). `30-45` is a yellow flag.
2. **Carbohydrate periodization** — are carbs scaled to the day's load? Chronic
   low-carb on hard/long days blunts quality and recovery ("train low" should be
   deliberate and rare, not accidental).
3. **Protein** — is daily protein in `1.6-2.2 g/kg` for repair, spread across the
   day where possible.
4. **Micronutrients** — iron first (endurance athletes, especially if female or
   plant-heavy), then vitamin D, calcium, magnesium, omega-3.
5. **Then** general diet quality — added sugar, saturated fat, fibre, alcohol.

## Reading the recovery correlations honestly

The digest joins prior-day nutrition with next-day HRV/RHR/sleep and same-day
caffeine/alcohol. These are **observational associations across one athlete**,
heavily confounded by training load, life stress, and measurement noise. Use
them to generate hypotheses ("low-carb days seem to precede lower HRV — worth a
deliberate test"), never as proof. Always report `n` and the caveat.

## Known data limitations of the Fitatu export

- **No clock times** — only meal labels (Breakfast/Lunch/Dinner/Snack). So
  meal-timing questions (late eating vs sleep, carb timing around sessions,
  caffeine cut-off) can only be answered coarsely. Say so.
- **Logging completeness varies** — a suspiciously low-intake day is often an
  under-logged day, not true starvation. Cross-check against a plausible floor
  before calling LEA; sustained patterns matter more than single days.
- **Alcohol** is a rough name-matched proxy (the export has no alcohol-grams
  column) and excludes products labelled non-alcoholic.
- **FFM is usually unknown** (no body-fat input), so EA uses body weight as the
  denominator and reads slightly low. Treat the EA bands as directional.
- **Hydration** beyond sodium is not captured.

## Practical adjustment levers (for recommendations)

- Under-fueling hard days -> add carbohydrate *around* the session (pre, during
  for >75-90 min, and within the recovery window).
- Low daily protein -> anchor 0.3 g/kg per main meal; add a post-session source.
- Low iron intake -> food-first (red meat, legumes + vitamin C); flag that
  serum ferritin needs a blood test before supplementing.
- Chronic low EA -> the fix is *more total energy*, prioritized over macro
  fiddling; reduce the training/eating gap rather than cutting training.
