# Fixtures

These files let the whole pipeline run with **no accounts or API keys**.

## Synthetic athlete

When `AITHLETE_OFFLINE=true` (the default), the connectors serve a deterministic
synthetic athlete generated in code by
[`src/aithlete/connectors/synthetic.py`](../../src/aithlete/connectors/synthetic.py):

- ~16 weeks of multisport history (swim/bike/run) ending on a fixed anchor date,
- daily wellness (HRV rMSSD, resting HR, sleep, weight),
- sport settings (FTP/eFTP, run threshold pace, CSS, LTHR, VO2max estimate).

It is fabricated data for demos and tests, **not** real or medical data.

## Sample calendar

`calendar.sample.json` is an example competition calendar (A/B/C races). To use
it, copy it to the active location:

```bash
cp data/fixtures/calendar.sample.json data/calendar-athlete.json
```

## Recording real fixtures (optional)

To record real API responses for offline replay, set credentials in `.env`,
set `AITHLETE_OFFLINE=false`, and capture with `vcrpy` cassettes under
`data/fixtures/private/` (gitignored — keep your data private).
