# Triathlon training knowledge — rationale & citations

This document explains the numbers in `rules.yaml`, `readiness.yaml`, and
`zones.yaml`, and gives the agent standing context on how great amateurs and
pros actually train. The machine-readable rules are the source of truth; this is
the "why".

## Stress score & load model (used everywhere)

All three sports normalize to **TSS where 100 = one hour at threshold**, so a
combined daily load is a clean additive sum across swim/bike/run.

- **Bike (power TSS).** `IF = NP / FTP`; `TSS = (sec × NP × IF) / (FTP × 3600) × 100`.
  NP is the 30-s rolling-power, 4th-power normalized average. Source: Allen &
  Coggan, *Training and Racing with a Power Meter*.
- **Run (rTSS).** Based on Normalized Graded Pace vs threshold pace:
  `rTSS = (sec × (NGP/threshold_pace)^2) / 3600 × 100`. Fallback **hrTSS** uses
  time-in-HR-zone weighting when no pace/power. Source: TrainingPeaks rTSS/hrTSS.
- **Swim (sTSS).** `sTSS = (sec × (swim_speed/CSS_speed)^3) / 3600 × 100`
  (cubic because swim drag scales with velocity). Source: TrainingPeaks sTSS / CSS.
- **CTL/ATL/TSB (Banister impulse-response).** Exponentially-weighted averages of
  daily TSS: `CTL_today = CTL_yest + (TSS_today − CTL_yest)(1 − e^(−1/42))`,
  `ATL` with τ = 7. `TSB = CTL − ATL` ("Form"). We **fetch** these from
  intervals.icu and only recompute to validate. Source: Banister (1975); Coggan
  Performance Manager; intervals.icu fitness model.

## Estimating physiology (and why most of it is `estimated`, not `measured`)

- **FTP / eFTP.** Prefer intervals.icu eFTP (modelled from the power curve) or
  95% of a 20-min max, or a ramp test. Confidence `estimated` unless from a
  dedicated, recent test → still `estimated` (only a lab/MLSS test is `measured`).
- **CP / W′.** Fit `P = W′/t + CP` over 3–5 maximal efforts (3–12 min).
  Source: Monod & Scherrer (1965); Skiba (W′bal).
- **Run threshold pace / critical speed.** From the speed-duration curve or
  Riegel extrapolation of a recent race: `T2 = T1 × (D2/D1)^1.06`. Source:
  Riegel (1981). VDOT pace zones: Daniels, *Daniels' Running Formula*.
- **Swim CSS.** From a 400 m and 200 m time trial:
  `CSS_pace = (400 − 200) / (t400 − t200)`. Source: Ginn (1993); Swim Smooth.
- **LTHR.** From a 30-min solo threshold effort (avg HR of last 20 min).
  Source: Friel.
- **VO2max.** Consumer devices report a **Firstbeat estimate** from HR vs
  speed/power. Always `estimated`. A lab graded test is the only `measured`
  source. Source: Firstbeat VO2max white paper.
- **Lactate threshold.** True LT needs blood lactate (lab). Otherwise we infer a
  threshold proxy (LTHR / threshold pace / DFA-a1) and tag it `estimated`.

> Rule enforced in code: VO2max and lactate-threshold fields can only be
> `measured` when `source == "lab test"`. Otherwise the model coerces them to
> `estimated`.

## How great triathletes actually train (context for plan authoring)

- **Polarized / pyramidal, lots of easy.** Elite endurance athletes spend
  ~75–85% of training time easy (below aerobic threshold) and a small, potent
  fraction hard. Beginners skew pyramidal; advanced athletes more polarized.
  Source: Seiler; Stöggl & Sperlich.
- **Consistency beats heroics.** The best age-groupers ramp CTL gradually
  (≈3–7/week), bank lots of aerobic volume in base, and treat recovery weeks as
  non-negotiable. Injuries and illness — not lack of hard days — are what derail
  amateurs.
- **Specificity rises toward race day.** Build phase adds race-pace work and
  bricks; long sessions approach (but for IM never reach) race duration.
- **Taper works.** A 1–3 week taper that cuts volume 40–60% while holding
  intensity reliably improves performance ~2–3%; aim for race-day TSB +10..+25
  for an A race. Source: Mujika & Padilla; Bosquet meta-analysis.
- **Durability matters at long course.** Aerobic decoupling (Pw:Hr drift) and
  efficiency factor late in long sessions predict 70.3/IM performance better than
  peak FTP. We track these explicitly.

## Reverse periodization (allowed variant)

For long-course age-groupers training through winter, intensity-first
(reverse) periodization is acceptable: build threshold early indoors, add
volume as the event nears. The validator does not forbid it; it only enforces
ramp caps, recovery cadence, taper shape, and intensity bounds.
