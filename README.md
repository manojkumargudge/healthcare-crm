# AI-First HCP CRM

[![CI](https://github.com/manojkumargudge/healthcare-crm/actions/workflows/ci.yml/badge.svg)](https://github.com/manojkumargudge/healthcare-crm/actions/workflows/ci.yml)

A CRM module for pharmaceutical field representatives to log interactions with Healthcare Professionals (HCPs). A rep can log an interaction in two ways:

- **Structured form:** fill in the type, date, products, samples, materials, topics and notes.
- **Chat:** describe the visit in plain text. A LangGraph agent backed by Groq LLMs extracts the same structured fields, checks sample compliance, and can edit records, look up HCP context, schedule follow-ups and suggest a next step.

Both paths write to the same tables and apply the same sample validation and compliance rules (`backend/app/samples.py`). Edits keep a before/after history on the record.

This is a working prototype for a single company. Terms used throughout the UI, API and these docs:
- **Rep:** an app user, with role `rep`, `manager` or `admin`.
- **HCP:** a doctor.

Reps sign in, and every interaction, chat message and follow-up belongs to the rep who created it. See [Sign-in and access](#sign-in-and-access).

The Log Interaction screen follows the brief mockup ([`docs/brief-mockup.png`](docs/brief-mockup.png)); see [`docs/screenshots/form-vs-mockup.png`](docs/screenshots/form-vs-mockup.png) for a side-by-side. The mockup's "Summarize from Voice Note (Requires Consent)" button is built: see [Voice notes](#voice-notes) and [`docs/screenshots/voice-3-summary-in-topics.png`](docs/screenshots/voice-3-summary-in-topics.png).

## Live demo

**Link:** _coming soon_ (`https://hcp-crm-web.onrender.com`, placeholder until it is deployed)

| Sign in as | Email | Can do |
|---|---|---|
| Rep | `demo-rep@example.com` | Log, edit and chat about their own interactions |
| Manager | `demo-manager@example.com` | Read every rep's interactions |
| Admin | `demo-admin@example.com` | Everything, plus the Admin screen (reps, material approval) |

- **Passwords:** _to be added when the demo goes live._
- **Nightly reset:** the demo data is reset every night (03:17 UTC), so anything you add disappears.
- **First request is slow:** the free hosting plans sleep when idle, so the first request can take up to a minute.
- **Rate limit:** AI features (chat, follow-up suggestions, voice notes) are limited per user.
- **Deploying your own copy:** see [docs/DEPLOY.md](docs/DEPLOY.md).

## Architecture

```
 Browser (React 18 + Redux Toolkit, Vite)
   HCP sidebar · structured form · chat · interaction history
        │  HTTP (VITE_API_URL)
        ▼
 FastAPI backend (Python 3.11, async SQLAlchemy 2 + asyncpg)
   /api/hcps          HCP list / create / get
   /api/interactions  form create, list, get, PATCH edit
   /api/chat/turn     one chat turn ──► LangGraph agent
        │                                  │
        │                    agent node: Groq LLM with 6 tools bound
        │                    tools node: runs the requested tools,
        │                                loops back to the agent
        ▼                                  │
 PostgreSQL 16  ◄──────────────────────────┘
   hcps · interactions · follow_ups · chat_messages
```

- **Backend:** FastAPI app in `backend/app/`. Tables are created on startup (`create_all` in the FastAPI lifespan). Settings come from the environment; the app refuses to start without `DATABASE_URL` and `GROQ_API_KEY`.
- **Frontend:** React + Redux Toolkit in `frontend/src/`, with one slice each for HCPs, interactions, chat and the form draft.
- **Agent:** `backend/app/agent/graph.py` is a two-node LangGraph loop (`agent` → `tools` → `agent` …). It ends when the model replies without requesting a tool. Chat history for a session is kept in process memory, and each turn is also written to `chat_messages`.
- **LLM:** Groq via `langchain-groq`. Two models, both set by environment variables:
  - `GROQ_MODEL` (default `openai/gpt-oss-120b`) runs the tool-calling agent and next-best-action recommendations.
  - `GROQ_MODEL_FAST` (default `openai/gpt-oss-20b`) does extraction and summarization.
  - If Groq reports one model as decommissioned or unknown, `backend/app/agent/llm.py` retries once with the other. Other errors (auth, rate limit, network) are raised, not retried.
  - The assignment brief names `gemma2-9b-it` and `llama-3.3-70b-versatile`. Both are decommissioned on Groq (`gemma2-9b-it` shut down on 2025-10-08, `llama-3.3-70b-versatile` on 2026-08-16) and are not used.

### The 6 agent tools (`backend/app/agent/tools.py`)

| Tool | What it does |
|---|---|
| `log_interaction` | Sends the rep's notes to the fast model (`GROQ_MODEL_FAST`), extracts summary, products, samples, materials, attendees, topics and sentiment, and saves an interaction with the raw notes. Also accepts attendees and materials as explicit arguments; materials are matched to the catalog. Invalid samples in the LLM output are skipped with a logged warning. |
| `edit_interaction` | Updates given fields of an interaction, appends a before/after entry to its edit history, and re-runs the compliance check if samples changed. Accepts attendees and catalog materials; changing the sentiment label clears the AI score. The form's PATCH endpoint uses this same tool. |
| `get_hcp_context` | Returns an HCP's profile and 5 most recent interactions. |
| `check_sample_compliance` | Checks sample quantities against per-state limits: Vermont 0, California 2, Minnesota 2, otherwise 4. The ruleset is illustrative, not regulatory data. |
| _(compliance on save)_ | Every save path (form create, PATCH/`edit_interaction`, chat `log_interaction`) also flags materials that are still pending approval, e.g. `Materials pending approval: Rep flyer`. Flags are computed when the interaction is saved: approving a material later does not clear existing flags, and there is no approval endpoint yet. |
| `schedule_follow_up` | Creates a follow-up task for an HCP with a due date and reason. |
| `suggest_next_best_action` | Uses the main model (`GROQ_MODEL`) on recent history to recommend a next step. |

### API

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/hcps?q=` | List HCPs; `q` searches name, specialty and institution (attendee picker) |
| POST | `/api/hcps` | Create an HCP |
| GET | `/api/hcps/{hcp_id}` | Get one HCP |
| GET | `/api/interactions?hcp_id=` | List interactions, optionally for one HCP |
| POST | `/api/interactions` | Create from the form; runs compliance before saving |
| PATCH | `/api/interactions/{interaction_id}` | Edit an interaction |
| GET | `/api/interactions/{interaction_id}` | Get one interaction |
| GET | `/api/interactions/{interaction_id}/follow-ups` | Follow-ups for an interaction (AI suggestions and accepted/scheduled ones) |
| POST | `/api/interactions/{interaction_id}/follow-ups/suggestions` | Generate up to 3 AI follow-up suggestions (status `Suggested`); older unaccepted ones become `Superseded` |
| PATCH | `/api/follow-ups/{follow_up_id}` | Accept (`Pending`) or dismiss (`Dismissed`) a suggestion, or mark `Done`/`Cancelled` |
| GET | `/api/materials?q=&approved=` | Search the materials catalog, optionally by approval status |
| POST | `/api/materials` | Add a material to the catalog as **pending approval** (returns the existing one if the name already exists, case-insensitive) |
| POST | `/api/chat/turn` | One chat turn: reply, tools called, any interaction logged |
| GET | `/api/chat/{session_id}/history` | Stored messages for a chat session |
| GET | `/api/products?q=` | Search the product catalog (Products Discussed and sample products). Read-only: see [Product catalog](#product-catalog) |
| POST | `/api/voice-notes/summarize?consent=true` | Raw audio body (webm/ogg/wav/mp3/m4a/flac, max 10 MB) → transcript + "Topics Discussed" summary. Nothing is stored |
| GET | `/health` | Liveness check (no sign-in) |
| POST | `/auth/login` | OAuth2 password form (`username` = email, `password`) → short-lived bearer token |
| GET | `/auth/me` | The signed-in rep |
| GET | `/api/admin/materials/pending` | Admin: materials waiting for approval |
| POST | `/api/admin/materials/{id}/approve` | Admin: approve a material and re-run compliance on every interaction that used it |
| POST / PATCH / DELETE | `/api/admin/products[/{id}]` | Admin: add, rename, remove catalog products |
| GET / POST / PATCH | `/api/admin/reps[/{id}]` | Admin: list, add, change role / (de)activate / reset password |

Every endpoint except `/health` and `/auth/login` needs `Authorization: Bearer <token>`.

Interactive API docs: `http://127.0.0.1:8000/docs`.

## Quickstart (Docker Compose)

Needs Docker with Compose v2 and a Groq API key ([console.groq.com/keys](https://console.groq.com/keys)).

```bash
git clone https://github.com/manojkumargudge/healthcare-crm.git
cd healthcare-crm
cp .env.example .env
# edit .env: set POSTGRES_PASSWORD, GROQ_API_KEY, JWT_SECRET, ADMIN_EMAIL and ADMIN_PASSWORD
docker compose up -d --build --wait
curl http://127.0.0.1:8000/health      # {"status":"healthy"}
```

- **Port clash:** if another Postgres already uses port 5432 on your machine (common on Windows), `docker compose up` fails with "ports are not available". Set `POSTGRES_PORT=55432` (or any free port) in `.env` and run it again. This only changes the host port; the backend container always reaches Postgres on 5432.
- **Required values:** Compose refuses to start if `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`, `GROQ_API_KEY` or `JWT_SECRET` is missing from `.env`.
- **First sign-in:** the backend creates the seed admin from `ADMIN_EMAIL` / `ADMIN_PASSWORD` on startup. Sign in with those, then add reps under **Admin → Reps**.
- **Startup order:** `--wait` returns once both services report healthy. Postgres is checked with `pg_isready`, the backend with `/health`, and the backend only starts after Postgres is healthy.
- **Demo data:** to add 3 demo HCPs, run `docker compose exec backend python -m app.seed`. Each run adds another 3.
  - For the full demo set, set `DEMO_REP_PASSWORD`, `DEMO_MANAGER_PASSWORD` and `DEMO_ADMIN_PASSWORD` in `.env`, then run `python -m app.demo_seed`. This adds demo users, 5 HCPs, interactions, follow-ups and a pending material.
  - `--reset` wipes all app data first.
- **Frontend:** Compose runs only Postgres and the backend. Start the frontend as in [Frontend](#frontend).
- **Stopping:** `docker compose down` stops the stack; add `-v` to also delete the database volume.

## Local development without Docker

### Backend

Needs Python 3.11 and a PostgreSQL 16 server you can reach. That can be a local install, or the Compose Postgres from the Quickstart (`docker compose up -d postgres`). If the Compose backend is also running, stop it first (`docker compose stop backend`), because it already uses port 8000.

There is only one `.env` file: the root one from the Quickstart. The backend reads it directly, whatever directory you start it from.

```bash
cd backend
python -m venv venv
source venv/bin/activate        # Windows Git Bash: source venv/Scripts/activate; cmd: venv\Scripts\activate
pip install -r requirements.txt
python -m app.seed              # optional: 3 demo HCPs
uvicorn app.main:app --reload --port 8000
```

- **Database connection:** the backend builds its URL from `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` and `POSTGRES_PORT` in the root `.env`, connecting to `127.0.0.1`. To use another server, set `POSTGRES_HOST`, or set `DATABASE_URL` in `.env` or the environment, which takes precedence.
- **Strict settings:** an unknown key in `.env` stops the backend at startup with an error naming the key. This catches typos and retired settings such as `GROQ_FALLBACK_MODEL`.
- Use `127.0.0.1`, not `localhost`. On Windows `localhost` tries IPv6 first, and every connection stalls for about 2 seconds.

### Frontend

Needs Node 18 or newer.

```bash
cd frontend
npm install
cp .env.example .env            # VITE_API_URL=http://localhost:8000
npm run dev                     # http://localhost:5173
```

The backend only accepts browser requests from `FRONTEND_ORIGIN`, which defaults to `http://localhost:5173`. Change it in `.env` if the frontend runs elsewhere.

## Tests and CI

```bash
# Backend. The suite DROPS AND RECREATES all tables, so it refuses to run
# unless the database name ends in "_test". Use a throwaway database:
docker run -d --rm --name hcp-test-db -e POSTGRES_USER=hcp_user -e POSTGRES_PASSWORD=hcp_pass \
  -e POSTGRES_DB=hcp_crm_test -p 127.0.0.1:55433:5432 postgres:16-alpine
cd backend
pip install -r requirements-dev.txt
export TEST_DATABASE_URL=postgresql+asyncpg://hcp_user:hcp_pass@127.0.0.1:55433/hcp_crm_test
ruff check . && mypy && pytest
docker stop hcp-test-db

# Frontend
cd ../frontend
npm test
```

**What the tests cover:**
- HCP and interaction endpoints.
- Sample validation (bad input returns 422).
- Compliance rules, and compliance running on create and on PATCH.
- That a failed compliance check saves nothing.
- The LLM fallback chain, the agent tools, and a full chat turn.
- Config fail-fast, the startup lifespan, UTC timestamps, and the migration below.
- On the frontend, one smoke test per Redux slice.

**End-to-end tests** (Playwright, `frontend/e2e/`) run the real UI against the real backend and database. Only Groq is replaced, by a local fake server (`frontend/e2e/support/fake_groq.py`, reached through `GROQ_BASE_URL`). They cover:
- sign-in for each role
- logging via the form and via chat, and editing
- the voice-note consent gate
- material approval

CI runs them on every push. To run them locally (this wipes the `*_test` database you point them at):

```bash
cd frontend
E2E_DATABASE_URL=postgresql+asyncpg://hcp_user:hcp_pass@127.0.0.1:55433/hcp_crm_test E2E_PYTHON=../backend/venv/bin/python PW_CHANNEL=msedge npm run e2e   # PW_CHANNEL: use an installed Edge/Chrome
```

**No real LLM calls:** a fixture fails any test that tries to reach Groq, and LLM paths use a scripted fake.

**Live tests** (`tests/test_live_groq.py`, marker `live`) run a real chat session against Groq that exercises all 6 tools and checks the database rows. They are skipped unless enabled, and CI never enables them:

```bash
RUN_LIVE_GROQ=1 LIVE_GROQ_API_KEY=<your key> pytest -m live -v   # same TEST_DATABASE_URL as above
```

**CI** ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)) runs on every push to `main` and every pull request, with two jobs:
- **Backend:** ruff, mypy, and pytest against a Postgres 16 service.
- **Frontend:** `npm ci`, `npm run build`, and Vitest.

## Sign-in and access

- **Accounts:** every app user is a rep with a role. Accounts are created by an admin (Admin screen or `/api/admin/reps`) or with the CLI:

  ```bash
  docker compose exec -e ADMIN_CLI_PASSWORD='a-long-password' backend python -m app.admin create-user rep.one@example.com rep
  ```

- **Tokens:** `/auth/login` checks the argon2 password hash and returns a JWT signed with `JWT_SECRET`. There is no default secret. Tokens last `ACCESS_TOKEN_MINUTES` (default 15).
  - The frontend keeps the token in memory only. Reloading the page or getting any 401 returns the rep to the sign-in page.
  - Each request re-reads the rep from the database, so deactivating a rep or changing their role applies immediately.
- **Who can see and change what** (one rule set, `app/authz.py`, used by every route, every agent tool, chat sessions and voice notes):

  | Role | Interactions, chat sessions, follow-ups | Admin screens |
  |---|---|---|
  | rep | own only (others' return 404) | no |
  | manager | read everyone's; change only their own (403 otherwise) | no |
  | admin | read and change everything | yes |

  - HCPs (doctors), the material catalog and the product catalog are shared company data that every signed-in rep can read.
  - The agent's tools act as the signed-in rep. For example, a rep's chat can't read or edit another rep's interactions, and a chat session can't be continued by anyone else.
- **AI rate limit:** each rep has a budget of `RATE_LIMIT_LLM_PER_MINUTE` (default 20) requests per minute, shared across chat, follow-up suggestions and voice notes. Beyond that, requests get 429 with `Retry-After`. The limit is kept in memory per backend process.
- **Admin screen:** approve pending materials (this re-runs compliance and clears stale "pending approval" flags), manage products, and manage reps. The last active admin can't be demoted or deactivated.

## Product catalog

Products Discussed and sample products come from one **admin-only** catalog, managed on the Admin screen, through `/api/admin/products`, or with the CLI:

```bash
docker compose exec backend python -m app.admin list-products
docker compose exec backend python -m app.admin add-product "NeuroCalm"   # idempotent, case-insensitive
```

- **Form:** must pick from the catalog. Unknown products, including sample products, are rejected with 422, "Pick from the product list". Case differences are accepted and stored in the catalog spelling.
- **Chat:** product names the agent extracts are mapped to the closest catalog product. The match ignores case, spacing and punctuation and tolerates small typos.
  - The rep's original text is kept in `product_matches` and shown in the history as "CardioMax (from 'cardiomx')".
  - Products discussed with no close match are stored in `unmatched_products`.
  - A **sample** whose product can't be matched is never saved. The agent saves nothing, asks the rep to pick from the closest catalog products, and then logs the interaction.
  - Chat never adds to the catalog.

## Voice notes

In the form, **Summarize from Voice Note (Requires Consent)** opens a small panel. Recording stays disabled until the rep ticks *"HCP consents to this recording being sent to a third-party AI service for transcription. Audio is not stored."*

How it works:
- The browser records with `MediaRecorder`, for up to 3 minutes.
- It sends the audio once to `POST /api/voice-notes/summarize?consent=true`.
- The backend transcribes it with Groq Whisper (`GROQ_TRANSCRIBE_MODEL`, default `whisper-large-v3-turbo`; `whisper-large-v3` is the more accurate option). It then summarizes the transcript with `GROQ_MODEL_FAST`.
- The summary is added to **Topics Discussed**.

Privacy and limits:
- **Consent:** the endpoint refuses audio without consent. The saved interaction records `voice_note_consent` and the server's `voice_note_consent_at`.
- **Audio is never stored.** The request body is read into memory (no multipart parsing, so no temporary files), sent to Groq, and dropped. The transcript is shown to the rep but not saved, and neither is logged.
- **Limits:** 10 MB, and only real audio of the declared type is accepted (webm, ogg, wav, mp3, m4a, flac). Wrong types return 415, oversized uploads 413, recordings with no speech 422, and Groq failures 502.

## Migrations

There is no Alembic yet. Schema changes are hand-written, idempotent SQL files in `backend/migrations/`, applied in file-name order. `000_baseline.sql` recreates the app's original schema, so running every file on an **empty** database produces exactly the schema in `app/models.py`. CI checks this on every run and fails on any drift, and the test suite runs on that migrated schema.

The backend applies every migration at startup (`RUN_MIGRATIONS_ON_STARTUP=true`, the default):
- A Postgres advisory lock serializes two instances that start at once.
- Set it to `false` to apply migrations by hand.
- Back up before upgrading a database that holds real data.

| File | Purpose |
|---|---|
| `000_baseline.sql` | The original schema (generated from the first version's models). A no-op on existing databases. |
| `001_timestamps_to_timestamptz.sql` | Converts the timestamp columns to `timestamptz`, interpreting old values as UTC. Without it, writes fail with `can't subtract offset-naive and offset-aware datetimes`. |
| `002_chat_messages_dedupe.sql` | Adds `chat_messages.tool_call_id` and removes duplicate tool rows left by an old chat bug. Only tool rows are deduplicated; repeated user/assistant messages are real and kept. |
| `003_attendees_materials.sql` | Adds `interactions.attendees` and the `materials` catalog table, and copies existing free-text materials into the catalog. |
| `004_materials_approval.sql` | Adds `materials.approved`. Materials that existed when the column was added (starter catalog and migrated tags) are approved; materials added afterwards start as pending. Also makes `attendees` and `materials.created_at` NOT NULL. |
| `005_interaction_time_outcomes.sql` | Adds `interaction_time`, `outcomes` and `follow_up_actions` to interactions (fields from the brief's form). |
| `006_products_voice_consent.sql` | Adds the `products` catalog (backfilled from existing products and sample names) and `voice_note_consent` / `voice_note_consent_at` on interactions. |
| `007_unmatched_products.sql` | Adds `interactions.unmatched_products`: product names from chat that match no catalog product. |
| `008_users_ownership.sql` | Adds `users` and `owner_id` on interactions, chat messages and follow-ups, plus `product_matches`. Existing rows are assigned to the seed admin, so run it with the runner and with `ADMIN_EMAIL` / `ADMIN_PASSWORD` set. It stops with an error rather than leave rows without an owner. |

Every file is safe to run more than once. Back up first, then apply all of them:

```bash
docker compose exec -T postgres pg_dump -U hcp_user -d hcp_crm -Fc > hcp_crm_backup.dump
docker compose exec backend python -m app.db_migrations apply   # all migrations, in order
docker compose exec backend python -m app.db_migrations check   # compare the database with app/models.py
```

Replace `hcp_user` and `hcp_crm` with your `POSTGRES_USER` and `POSTGRES_DB`. Without Docker, run the same `python -m app.db_migrations apply` from `backend/`; it uses the root `.env`.

To add a migration, create the next numbered file, make it idempotent, update `app/models.py` to match, and run `python -m app.db_migrations check-fresh` against a throwaway `*_test` database. That command wipes the database, applies everything from empty, and reports any drift.

## Security notes

- **Secrets live only in `.env` files, which git ignores.**
  - There is a single `.env` at the repo root. Compose reads it for the containers, and the backend reads it directly when run without Docker.
  - Neither `docker-compose.yml` nor `app/config.py` contains credentials, and `backend/.dockerignore` keeps `.env` out of the image.
- **Ports are bound to `127.0.0.1`.** Postgres and the backend are published only on the loopback interface, so other machines on your network can't reach them.
- **The backend container runs as a non-root user.**
- **Sign-in required:**
  - Passwords are hashed with argon2.
  - Tokens are short-lived JWTs signed with `JWT_SECRET`, which has no default.
  - Access follows the role rules in [Sign-in and access](#sign-in-and-access).
  - Tokens can't be revoked before they expire, other than by deactivating the rep or rotating `JWT_SECRET`.
- **Not done yet:**
  - The agent writes to the database without asking for confirmation.
  - Edit history is a JSON column on the record, not an append-only audit table.
  - Rate limiting covers only the AI endpoints, and is in memory (one backend process).
- **Data:** this system stores data about doctors (HCPs), not patients.

## Project layout

```
backend/
  app/
    main.py        FastAPI app, lifespan, routers
    config.py      settings from environment (fail fast if required ones are missing)
    database.py    async engine and sessions
    db_url.py      hosted DATABASE_URL handling (postgres:// scheme, sslmode -> asyncpg ssl)
    db_migrations.py  migration runner and drift check
    demo_seed.py   demo users + sample data for a public demo (--reset wipes first)
    models.py      HCP, Interaction, FollowUp, ChatMessage
    schemas.py     request/response models
    samples.py     sample validation and compliance rules
    seed.py        demo HCPs
    agent/         llm.py (Groq + fallback), tools.py (6 tools), graph.py (LangGraph)
    routers/       hcp.py, interactions.py, chat.py
  migrations/      hand-written SQL for existing databases
  tests/           pytest suite
  Dockerfile, pyproject.toml, requirements.txt, requirements-dev.txt
frontend/
  src/             components/, store/ (Redux slices + tests), api/client.js
render.yaml        Render Blueprint (API on Docker + static frontend); see docs/DEPLOY.md
.github/workflows/ci.yml           backend, frontend and e2e jobs
.github/workflows/demo-reset.yml   nightly demo reset (skipped without the DATABASE_URL secret)
docker-compose.yml
.env.example       Compose settings (copy to .env)
```
