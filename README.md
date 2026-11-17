# AdRobot — Keitaro campaign creator & stream editor

A small FastAPI + SQLAlchemy service with a React frontend that drives the
[Keitaro](https://keitaro.io) tracker's admin API.

It does two things:

1. **Creates campaigns.** Give it a name, a country and an offer; it builds a Keitaro
   campaign with a configured domain, group and traffic source, and two flows —
   *Flow 1* sends everyone from that country to Google, *Flow 2* sends everyone else to
   the chosen offer.
2. **Edits the offers of an existing campaign.** Add and remove offers, re-weight their
   traffic shares, and push the result back to Keitaro.

<p align="center">
  <img src="docs/creator.png" alt="Campaign creator: name, geo, offer autocomplete, group, source and domain" width="420">
  <img src="docs/editor.png" alt="Stream editor with staged changes: added, removed and re-weighted offers awaiting a push" width="420">
</p>
<p align="center">
  <sub><b>Left:</b> the creator — name, geo and an offer search over the cached Keitaro catalogue.&nbsp;
  <b>Right:</b> the editor — one offer added, one staged for removal, one share pinned at 45%,
  all held locally until <i>Push to KT</i>.</sub>
</p>

---

## Quick start

Requirements: **Python 3.11+** and **Node 18+**. PostgreSQL is optional — the default
database is SQLite and needs no setup.

```bash
git clone https://github.com/GregoryPetrovLn/adrobot-keitaro.git
cd adrobot-keitaro

# 1. Configuration
cp .env.example .env
#    then set KEITARO_BASE_URL and KEITARO_API_KEY (see "Configuration" below)

# 2. Backend
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
alembic upgrade head                       # create the database schema

# 3. Frontend (builds into backend/app/static, which FastAPI serves)
cd ../frontend
npm install
npm run build

# 4. Run
cd ../backend
uvicorn app.main:app --reload
```

Open **http://127.0.0.1:8000**. The API docs are at **/docs**.

### Development mode (hot reload)

Run the two halves separately. Vite proxies `/api` to the backend, so the frontend does
not need rebuilding on every change.

```bash
# terminal 1
cd backend && source .venv/bin/activate && uvicorn app.main:app --reload   # :8000

# terminal 2
cd frontend && npm run dev                                                # :5173
```

Then open **http://127.0.0.1:5173**.

---

## Configuration

All settings are read from `.env` in the repository root (see `.env.example`).

| Variable | Required | Default | What it does |
| --- | --- | --- | --- |
| `KEITARO_BASE_URL` | yes | `https://in.posdk.xyz` | Tracker root. The admin API is expected at `<base>/admin_api/v1`. |
| `KEITARO_API_KEY` | yes | — | Keitaro admin → **Профиль → API ключи**. |
| `KEITARO_TIMEOUT` | no | `30.0` | Per-request timeout, seconds. |
| `DATABASE_URL` | no | `sqlite+aiosqlite:///./adrobot.db` | Async SQLAlchemy URL. Postgres: `postgresql+asyncpg://user:pass@host:5432/db`. |
| `DEFAULT_GROUP_ID` | no | — | Pre-selects a campaign group in the form. |
| `DEFAULT_TRAFFIC_SOURCE_ID` | no | — | Pre-selects a traffic source. |
| `DEFAULT_DOMAIN_ID` | no | — | Pre-selects a domain. |
| `CAMPAIGN_NAME_PREFIX` | no | *(empty)* | Prepended to every campaign name created here — handy on a shared tracker. |
| `GOOGLE_REDIRECT_URL` | no | `https://www.google.com` | Where Flow 1 sends matching traffic. |
| `CAMPAIGN_COST_TYPE` | no | `CPC` | Cost model for created campaigns. |
| `CAMPAIGN_COST_CURRENCY` | no | `USD` | Cost currency. |
| `CAMPAIGN_COST_AUTO` | no | `true` | Keitaro's "Из параметра `cost`" — read the cost off each click. |
| `CAMPAIGN_UNIQUENESS_METHOD` | no | `ip_ua` | `ip_ua` or `ip`. |
| `CAMPAIGN_COOKIES_TTL` | no | `24` | Uniqueness window, hours. |
| `CORS_ORIGINS` | no | `http://localhost:5173,…` | Comma-separated origins allowed in dev mode. |
| `LOG_LEVEL` | no | `INFO` | Standard Python log level. |

The group, traffic source and domain dropdowns are populated live from Keitaro, so the
`DEFAULT_*` values are conveniences, not requirements.

---

## Migrations

Alembic owns the schema; the app never calls `create_all`.

```bash
cd backend
source .venv/bin/activate

alembic upgrade head          # apply all migrations (run this first)
alembic downgrade -1          # roll back one
alembic current               # show the applied revision
alembic history --verbose     # list revisions

# after changing app/models.py:
alembic revision --autogenerate -m "describe the change"
```

Migrations run against whatever `DATABASE_URL` points at, so the same commands work for
SQLite and PostgreSQL. They are generated with `render_as_batch=True`, which lets SQLite
handle `ALTER TABLE` the way PostgreSQL does.

### PostgreSQL instead of SQLite

```bash
docker run -d --name adrobot-pg -p 5432:5432 \
  -e POSTGRES_USER=adrobot -e POSTGRES_PASSWORD=adrobot -e POSTGRES_DB=adrobot postgres:16

# in .env
DATABASE_URL=postgresql+asyncpg://adrobot:adrobot@localhost:5432/adrobot

cd backend && alembic upgrade head
```

---

## Tests

```bash
cd backend
source .venv/bin/activate
pip install -r requirements-dev.txt

pytest              # 102 tests, no network access — Keitaro is mocked with respx
ruff check .        # lint
```

The suite covers the exact payloads sent to Keitaro, the share arithmetic, the staging
lifecycle (add / remove / bring back / push / cancel), rollback when campaign creation
fails halfway, stats mapping, the audit log, and `tests/test_resilience.py` — stale-push
conflicts, concurrent edits, malformed tracker responses and failed-call auditing.

---

## How it works

### Part 1 — the campaign creator

One request creates three objects in Keitaro:

| | Keitaro call | What it sends |
| --- | --- | --- |
| Campaign | `POST /campaigns` | `type: position`, `cost_type: CPC` with `cost_auto` (Keitaro reads the real cost from each click's `cost` parameter), plus the chosen `group_id`, `traffic_source_id` and `domain_id`. Cost model, currency and uniqueness are `CAMPAIGN_*` settings. |
| Flow 1 | `POST /streams` | `schema: redirect` → `https://www.google.com`, filter `country accept [GEO]`, position 1 |
| Flow 2 | `POST /streams` | `schema: landings` with the chosen offer at 100%, position 2 |

Because the campaign is `type: position`, flows are evaluated top-down: Flow 1 catches the
chosen country, Flow 2 catches everyone else.

If a flow fails to create, the half-built campaign is deleted again rather than left
behind for someone to clean up by hand. If that cleanup fails too, the error names the
campaign id that is still sitting in the tracker so it can be removed by hand.

### Part 2 — the stream editor

Edits are **staged locally** and only reach Keitaro when you press **Push to KT**:

- **Add** an offer — it appears immediately, marked `added`.
- **Remove** an offer — it is greyed out and marked `removed`, with **Bring back** to undo.
  An offer that was never in Keitaro is dropped outright instead, since there is nothing
  to bring back to.
- **Shares.** Unpinned offers split the remaining percentage evenly; the leftover points
  from integer division go to the earliest offers. Three offers become `34 / 33 / 33`;
  dropping one of four while a 25% offer is pinned leaves `38 / 37 / 25`. Note this is an
  even split, not a proportional one — adding an offer to a 70/30 stream gives
  `34 / 33 / 33`, and **pinning (📌) is how you hold a share you do not want
  redistributed**. Typing a share pins it automatically.
- Edits are serialised per campaign, so two tabs cannot each rebalance a stale copy. That
  lock is process-local, which covers the single-process deployment above; behind several
  workers the push-time check is the backstop. Either way, if pins are set such that the
  total cannot reach 100%, the stream header says so and the push is refused rather than
  sending bad weights to the tracker.
- **Push to KT** sends one `PUT /streams/{id}` per changed stream and adopts Keitaro's
  response as the new baseline. Because that `PUT` replaces the offer list wholesale, each
  stream is re-read first: if anyone changed it in Keitaro since your last fetch the push
  is refused with a 409 instead of silently overwriting their work.
- **Cancel** throws the staged changes away; **Fetch streams from KT** re-reads the
  tracker (and warns first, because it discards staged changes too).

Each offer row shows clicks, uniques, bots, conversions and revenue for the selected
period, read from `POST /report/build` grouped by `stream_id` + `offer_id`. Those numbers
are cached for 20 seconds per campaign and period: every editing call returns the whole
view, and retyping a few shares should not mean a report build per keystroke on Keitaro's
heaviest endpoint. "Fetch streams from KT" bypasses the cache.

### Why there is a database at all

The local schema is a staging area in front of Keitaro, not a second source of truth:

- `campaigns` / `streams` / `stream_offers` — the last state read from Keitaro **plus** the
  pending edits layered on top. `stream_offers.kt_share` is what the tracker reported;
  `share`, `pinned` and `removed` are what you currently want. The difference is what
  "unsaved changes" means.
- `offers` — a cache of the tracker's offer catalogue. `GET /admin_api/v1/offers` ignores
  `name` and `limit` and returns the entire list every time, so the autocomplete searches
  the local copy instead of re-downloading it on each keystroke.
- `sync_logs` — every outbound Keitaro call with its payload, status and duration, exposed
  at `GET /api/campaigns/{id}/logs`. Rows are buffered during the request and written once
  its transaction has committed *or rolled back*, so a failed call is still recorded — a
  log that vanishes exactly when something breaks would be worse than none. (Writing them
  on a second connection instead deadlocks against the request's own open transaction on
  SQLite, which is how they were being lost before.)

### What I deliberately did not reproduce

The brief says the interface is up to me, so I built the behaviour the reference tool has
rather than its exact chrome. Two things from the video are not here, on purpose:

- **"Show all offers"** — in this UI every stream's offers are always on screen, so there
  is nothing to expand. The catalogue refresh that button's neighbour implied is here as
  **Refresh offer catalogue**, which matters more: without it an offer created in Keitaro
  after first boot would never appear in the pickers.
- **The "Trends" column** — it was empty in every frame of the reference video and I could
  not tell what it plotted. Rather than invent a sparkline, I spent the space on the
  numbers I could verify against `report/build`: clicks, uniques, bots, conversions and
  revenue, per offer and per stream.

Beyond the brief, the editor also lets you **import** an existing Keitaro campaign (part 2
says "an existing campaign", and something has to put one on the screen), browse the
tracker's campaigns to find it, and create a campaign group inline.

---

## Notes on the Keitaro API

Verified against Keitaro **10.3.16**. A few behaviours differ from what the documentation
suggests, and the client (`backend/app/keitaro/client.py`) documents each one inline:

- Streams are created with `POST /streams` and `campaign_id` **in the body**;
  `POST /campaigns/{id}/streams` returns 404 (it exists for `GET` only).
- `schema: "landings"` still requires `action_type` to be set — `"http"` with an empty
  payload is what the tracker's own UI sends.
- `PUT /streams/{id}` with an `offers` array is **declarative**: the array replaces the
  stream's offer list, so adding, removing and re-weighting is a single call.
- `GET /groups` requires a `type` query parameter (`campaigns`, `offers`, …).
- `GET /offers` ignores `name` and `limit`.
- Errors arrive in two shapes: HTTP 200 with `{"error": "..."}`, and HTTP 422 with
  `{"field": ["message"]}`. Both are normalised into one error type.
- An API key may be able to attach an offer it cannot see in `GET /offers`; the catalogue
  endpoint is permission-scoped while the stream write is not.
- Campaign names must be unique; a clash comes back as HTTP 422, which this app passes
  through as a 422 rather than dressing it up as a gateway error.
- Deleting a campaign **orphans its streams** instead of removing them: they stay readable
  *and* writable afterwards, so a push would otherwise report success while writing into a
  campaign that no longer exists. Push and fetch check the campaign itself for this.

---

## API reference

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/healthz` | Liveness plus whether Keitaro credentials are configured |
| `GET` | `/api/dictionaries` | Campaign groups, traffic sources and domains from Keitaro |
| `POST` | `/api/dictionaries/groups` | Create a campaign group |
| `GET` | `/api/countries` | ISO-3166 alpha-2 list for the geo picker |
| `GET` | `/api/offers?q=&limit=` | Search the cached offer catalogue |
| `POST` | `/api/offers/sync` | Refresh the cache from Keitaro (the "Refresh offer catalogue" button) |
| `POST` | `/api/campaigns` | **Part 1** — create a campaign with both flows |
| `GET` | `/api/campaigns` | Campaigns managed here |
| `GET` | `/api/campaigns/keitaro?q=` | Browse campaigns that exist in Keitaro |
| `POST` | `/api/campaigns/import` | Import an existing Keitaro campaign |
| `DELETE` | `/api/campaigns/{id}` | Stop managing it locally (Keitaro is untouched) |
| `GET` | `/api/campaigns/{id}/streams?interval=` | **Part 2** — staged streams with stats |
| `POST` | `/api/campaigns/{id}/fetch` | Re-read streams from Keitaro |
| `POST` | `/api/campaigns/{id}/streams/{sid}/offers` | Stage an offer addition |
| `DELETE` | `/api/campaigns/{id}/streams/{sid}/offers/{oid}` | Stage a removal |
| `POST` | `/api/campaigns/{id}/streams/{sid}/offers/{oid}/restore` | Bring a removed offer back |
| `PATCH` | `/api/campaigns/{id}/streams/{sid}/offers/{oid}` | Set a share or toggle its pin |
| `POST` | `/api/campaigns/{id}/push` | Apply staged changes to Keitaro |
| `POST` | `/api/campaigns/{id}/reset` | Discard staged changes |
| `GET` | `/api/campaigns/{id}/logs` | Keitaro calls made for this campaign |

Every editing endpoint returns the full campaign view, so the UI always renders from one
authoritative payload instead of patching a local copy.

---

## Layout

```
backend/
  app/
    keitaro/client.py      async Keitaro admin API client
    services/
      campaigns.py         part 1: create / import
      streams.py           part 2: staging, rebalancing, push, staleness checks
      shares.py            share arithmetic
      stats.py             report/build -> per-stream and per-offer numbers, cached briefly
      offers.py            offer catalogue cache and search
      logs.py              audit trail
      geo.py               ISO-3166 country list
      ids.py               id parsing helpers
    routers/               HTTP layer
    audit.py               per-request buffer of audit rows
    locks.py               per-campaign serialisation of staged edits
    db.py                  async engine, session, audit flush
    models.py              SQLAlchemy models
    schemas.py             request/response models
  alembic/                 migrations
  tests/                   pytest suite (Keitaro mocked with respx)
    test_shares.py           share arithmetic
    test_campaign_create.py  part 1 payloads, validation, rollback
    test_stream_editor.py    part 2 staging lifecycle
    test_resilience.py       staleness, concurrency, bad responses, auditing
    test_offer_search.py     autocomplete search
    test_stats_and_logs.py   stats mapping, intervals, caching, audit
frontend/
  src/
    pages/CampaignsPage.tsx      creator + import + list
    pages/StreamEditorPage.tsx   the editor
    components/OfferPicker.tsx   offer autocomplete
    api/client.ts                typed API client
```
