# Open Wearables integration (recovery data)

[Open Wearables](https://openwearables.io/) is a self-hosted unified API for
wearable health data (Oura, Whoop, Garmin, Apple Health, and more). In Aithlete
it is the **canonical source for recovery/sleep/HRV/RHR** — the signals that gate
training adjustments. intervals.icu remains canonical for activities and load.

It is **deferred for v1**: Aithlete runs fully offline on the synthetic athlete
without it. Wire it in when you want real recovery data feeding readiness.

## 1. Vendor it as a submodule

```bash
git submodule add https://github.com/the-momentum/open-wearables.git third_party/open-wearables
git submodule update --init --recursive
```

(The path `third_party/open-wearables/` is already gitignored as a working copy;
the submodule pointer is what gets committed.)

## 2. Run it

```bash
cd third_party/open-wearables
cp ./backend/config/.env.example ./backend/config/.env
cp ./frontend/.env.example ./frontend/.env
docker compose up -d
```

- API: <http://localhost:8000/api/v1> (Swagger at <http://localhost:8000/docs>)
- Developer portal: <http://localhost:3000> (default admin from `ADMIN_EMAIL` /
  `ADMIN_PASSWORD`). Create an API key there.
- Connect a provider (e.g. Oura/Whoop/Garmin) via the portal's OAuth flow and
  trigger a sync.

## 3. Point Aithlete at it

In Aithlete's `.env`:

```bash
AITHLETE_OFFLINE=false
OPEN_WEARABLES_BASE_URL=http://localhost:8000/api/v1
OPEN_WEARABLES_API_KEY=<your key>
OPEN_WEARABLES_USER_ID=<the user id from the portal>
```

Authentication uses the `X-Open-Wearables-API-Key` header. The connector
(`src/aithlete/connectors/openwearables.py`) reads the recovery summary endpoint
`/users/{user_id}/summaries/recovery` and merges it with intervals wellness,
preferring Open Wearables for HRV/RHR/sleep (see the source-of-truth matrix in
`src/aithlete/connectors/dedup.py`).
