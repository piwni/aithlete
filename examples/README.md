# Example: a real 2026 season (Syców 1/2 IM → Malbork full IM)

A complete, runnable example so you can see the plan + report pipeline end-to-end
without connecting your own accounts. It uses one athlete's **real** intervals.icu
history (shared with consent) as the data feed.

```
examples/
  season-2026-malbork.yaml      # the season spec — the only athlete/season-specific input
  data/                         # the data feed (point AITHLETE_DATA_DIR at it)
    profiles/athlete.json       # profile built by `aithlete profile`
    raw/intervals/...           # normalized activities + wellness (CSV partitions)
    plans/goal-2026-malbork.json    # generated, validated TrainingPlan
    reports/aithlete-plan-2026.pdf  # generated developer-style report (showcase)
```

## What the spec drives

`season-2026-malbork.yaml` holds everything specific to this athlete and season:
goal races (with target times and split tables), the weekly macro-structure, volume
ceilings, equipment notes, and the rationale prose. The engines are generic — to
plan a different season, copy this file and edit it.

## Regenerate it yourself

The two scripts are generic; they read the spec and the data dir:

```bash
export AITHLETE_DATA_DIR="$PWD/examples/data"

# 1. Build the plan from the spec, then gate it through the validator.
uv run python scripts/build_plan.py examples/season-2026-malbork.yaml
uv run aithlete validate examples/data/plans/goal-2026-malbork.json

# 2. Build the HTML report (metrics computed from examples/data, copy from the spec).
uv run python scripts/build_report.py examples/season-2026-malbork.yaml

# 3. Render the PDF (any headless Chromium works).
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
  --headless --disable-gpu --no-pdf-header-footer \
  --print-to-pdf="examples/data/reports/aithlete-plan-2026.pdf" \
  "file://$PWD/examples/data/reports/goal-2026-malbork.html"
```

The plan passes the deterministic validator (0 errors) with a projected race-day
TSB of +19.2 (inside the A-race +10..+25 band).

## Using your own data

Don't point `AITHLETE_DATA_DIR` at `examples/` — leave it unset (defaults to `data/`,
which is git-ignored). Then:

```bash
uv run aithlete fetch --reset --since 2025-01-01   # pull your intervals.icu history
uv run aithlete profile                            # build data/profiles/athlete.json
cp examples/season-2026-malbork.yaml data/my-season.yaml   # edit your races/targets
uv run python scripts/build_plan.py data/my-season.yaml
uv run python scripts/build_report.py data/my-season.yaml
```
