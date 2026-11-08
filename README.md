# AI-First HCP CRM

[![CI](https://github.com/manojkumargudge/healthcare-crm/actions/workflows/ci.yml/badge.svg)](https://github.com/manojkumargudge/healthcare-crm/actions/workflows/ci.yml)

A CRM module for pharmaceutical field representatives to log interactions with Healthcare Professionals (HCPs). A rep can log an interaction in two ways:

- **Structured form:** fill in the type, date, products, samples, materials, topics and notes.
- **Chat:** describe the visit in plain text. A LangGraph agent backed by Groq LLMs extracts the same structured fields, checks sample compliance, and can edit records, look up HCP context, schedule follow-ups and suggest a next step.

Both paths write to the same tables and apply the same sample validation and compliance rules (`backend/app/samples.py`). Edits keep a before/after history on the record.

This is a working prototype. There is no authentication; see [Security notes](#security-notes).

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
| `log_interaction` | Sends the rep's notes to the fast model (`GROQ_MODEL_FAST`), extracts summary, products, samples, materials, topics and sentiment, and saves an interaction with the raw notes. Invalid samples in the LLM output are skipped with a logged warning. |
| `edit_interaction` | Updates given fields of an interaction, appends a before/after entry to its edit history, and re-runs the compliance check if samples changed. The form's PATCH endpoint uses this same tool. |
| `get_hcp_context` | Returns an HCP's profile and 5 most recent interactions. |
| `check_sample_compliance` | Checks sample quantities against per-state limits: Vermont 0, California 2, Minnesota 2, otherwise 4. The ruleset is illustrative, not regulatory data. |
| `schedule_follow_up` | Creates a follow-up task for an HCP with a due date and reason. |
| `suggest_next_best_action` | Uses the main model (`GROQ_MODEL`) on recent history to recommend a next step. |

### API

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/hcps` | List HCPs |
| POST | `/api/hcps` | Create an HCP |
| GET | `/api/hcps/{hcp_id}` | Get one HCP |
| GET | `/api/interactions?hcp_id=` | List interactions, optionally for one HCP |
| POST | `/api/interactions` | Create from the form; runs compliance before saving |
| PATCH | `/api/interactions/{interaction_id}` | Edit an interaction |
| GET | `/api/interactions/{interaction_id}` | Get one interaction |
| POST | `/api/chat/turn` | One chat turn: reply, tools called, any interaction logged |
| GET | `/api/chat/{session_id}/history` | Stored messages for a chat session |
| GET | `/health` | Liveness check |

Interactive API docs: `http://127.0.0.1:8000/docs`.

## Quickstart (Docker Compose)

Needs Docker with Compose v2 and a Groq API key ([console.groq.com/keys](https://console.groq.com/keys)).

```bash
git clone https://github.com/manojkumargudge/healthcare-crm.git
cd healthcare-crm
cp .env.example .env
# edit .env: set POSTGRES_PASSWORD and GROQ_API_KEY (GROQ_MODEL / GROQ_MODEL_FAST have working defaults)
docker compose up -d --build --wait
curl http://127.0.0.1:8000/health      # {"status":"healthy"}
```

- **Port clash:** if another Postgres already uses port 5432 on your machine (common on Windows), `docker compose up` fails with "ports are not available". Set `POSTGRES_PORT=55432` (or any free port) in `.env` and run it again. This only changes the host port; the backend container always reaches Postgres on 5432.
- **Required values:** Compose refuses to start if `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` or `GROQ_API_KEY` is missing from `.env`.
- **Startup order:** `--wait` returns once both services report healthy. Postgres is checked with `pg_isready`, the backend with `/health`, and the backend only starts after Postgres is healthy.
- **Demo data:** to add 3 demo HCPs, run `docker compose exec backend python -m app.seed`. Each run adds another 3.
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

**No real LLM calls:** a fixture fails any test that tries to reach Groq, and LLM paths use a scripted fake.

**Live tests** (`tests/test_live_groq.py`, marker `live`) run a real chat session against Groq that exercises all 6 tools and checks the database rows. They are skipped unless enabled, and CI never enables them:

```bash
RUN_LIVE_GROQ=1 LIVE_GROQ_API_KEY=<your key> pytest -m live -v   # same TEST_DATABASE_URL as above
```

**CI** ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)) runs on every push to `main` and every pull request, with two jobs:
- **Backend:** ruff, mypy, and pytest against a Postgres 16 service.
- **Frontend:** `npm ci`, `npm run build`, and Vitest.

## Migrations

There is no Alembic yet. New databases get their schema from `create_all` at startup. Changes to existing databases are hand-written SQL in `backend/migrations/`:

| File | Purpose |
|---|---|
| `001_timestamps_to_timestamptz.sql` | Converts the timestamp columns to `timestamptz`, interpreting old values as UTC. Needed once for databases created before timestamps became timezone-aware. Without it, writes fail with `can't subtract offset-naive and offset-aware datetimes`. Safe to run more than once. |
| `002_chat_messages_dedupe.sql` | Adds `chat_messages.tool_call_id` and removes duplicate tool rows left by an old chat bug that re-saved earlier tool messages on every turn. Only tool rows are deduplicated; repeated user/assistant messages are real and kept. Safe to run more than once. |

Back up first, then run them in order against the Compose database:

```bash
docker compose exec -T postgres pg_dump -U hcp_user -d hcp_crm -Fc > hcp_crm_backup.dump
docker compose exec -T postgres sh -c 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"' \
  < backend/migrations/001_timestamps_to_timestamptz.sql
# then the same command with 002_chat_messages_dedupe.sql
```

Replace `hcp_user` and `hcp_crm` with your `POSTGRES_USER` and `POSTGRES_DB`.

## Security notes

- **Secrets live only in `.env` files, which git ignores.**
  - There is a single `.env` at the repo root. Compose reads it for the containers, and the backend reads it directly when run without Docker.
  - Neither `docker-compose.yml` nor `app/config.py` contains credentials, and `backend/.dockerignore` keeps `.env` out of the image.
- **Ports are bound to `127.0.0.1`.** Postgres and the backend are published only on the loopback interface, so other machines on your network can't reach them.
- **The backend container runs as a non-root user.**
- **No authentication yet:** every API endpoint is open to anyone who can reach it. Don't expose it beyond localhost.
- **Not done yet:**
  - The agent writes to the database without asking for confirmation.
  - Edit history is a JSON column on the record, not an append-only audit table.
  - There is no rate limiting.
- **Data:** this system stores data about doctors (HCPs), not patients.

## Project layout

```
backend/
  app/
    main.py        FastAPI app, lifespan, routers
    config.py      settings from environment (fail fast if required ones are missing)
    database.py    async engine and sessions
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
.github/workflows/ci.yml
docker-compose.yml
.env.example       Compose settings (copy to .env)
```
