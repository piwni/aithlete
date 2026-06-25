# Disclaimer — Not Medical Advice

Aithlete is an open-source tool for analysing training data and generating
training plans. It is **not** a medical device and does **not** provide medical
advice, diagnosis, or treatment.

- The health metrics, scores, training-load numbers, and plans produced by this
  software are **estimates** derived from consumer wearables and third-party
  APIs. They can be wrong, incomplete, or based on proxy measurements
  (for example, VO2max and lactate threshold are estimates, not lab values).
- Always consult a qualified physician before starting, changing, or
  intensifying any exercise program, especially if you have a medical
  condition, are injured, ill, pregnant, or experiencing unusual symptoms.
- If you feel chest pain, dizziness, or other warning signs, stop and seek
  medical attention. Do not "train through" them because a plan said so.
- The plan validator and readiness guardrails reduce risk but do not eliminate
  it. You are responsible for your own training decisions.

By using this software you accept that the authors and contributors are not
liable for any injury, loss, or damage arising from its use, to the fullest
extent permitted by law (see `LICENSE`).

## Data & third-party services

- Your health and training data is private. The repository's `.gitignore`
  excludes everything under `data/` for this reason. Keep it that way.
- This project talks to third-party services (intervals.icu, optionally
  Open Wearables and the providers behind it). Respect their Terms of Service
  and API agreements. In particular, workouts are delivered to Garmin **only**
  via intervals.icu's native Garmin Connect sync; this project does not log in
  to Garmin directly.
