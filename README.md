# AI-First HCP CRM

[![CI](https://github.com/manojkumargudge/healthcare-crm/actions/workflows/ci.yml/badge.svg)](https://github.com/manojkumargudge/healthcare-crm/actions/workflows/ci.yml)

A CRM module for pharmaceutical field representatives that logs interactions with Healthcare Professionals (HCPs) in two ways: a structured form, or a chat with a LangGraph agent that turns free-text notes into the same structured records. Every AI action (logging, editing, compliance checks) goes through one shared, tested code path, and every edit is recorded in an edit history.

**Status:** working prototype with tests and CI, being hardened toward production. Authentication and several other production pieces are not built yet. See [Production readiness](#production-readiness) for the honest list.

## Why it's designed this way

Field reps type on a phone or tablet between appointments. A form is precise but slow; chat is fast but unstructured. This app offers both on one data model: the agent converts a message like *"Met Dr. Rao, discussed CardioMax, left 3 samples, she was positive but asked about pricing"* into the same fields a form would produce, then checks sample compliance and can schedule a follow-up.

Design choices:

- **One code path for both channels.** The form's PATCH endpoint calls the same `edit_interaction` tool the agent uses, and both channels apply the same sample validation and compliance rules from `app/samples.py`, so logic is never duplicated and both paths behave the same.
- **Raw notes are kept.** Interactions logged through chat store the rep's original text next to the AI-extracted fields, so any AI output can be checked against its source.
- **Resilient LLM calls.** Groq retires models over time. Every call tries the configured model first and falls back to a supported one only when Groq reports the model as unavailable. Auth, rate-limit and network errors are raised, not hidden.

## Tech stack

| Layer | Choice |
|---|---|
| Frontend | React 18, Redux Toolkit, Vite |
| Backend | Python 3.11, FastAPI, async SQLAlchemy 2 |
| AI agent | LangGraph (tool-calling agent), LangChain Core |
| LLM | Groq: `gemma2-9b-it` (primary), `llama-3.3-70b-versatile` (heavier reasoning), with fallbacks |
| Database | PostgreSQL 16 |
| Quality | pytest, ruff, mypy, Vitest, GitHub Actions |
| Packaging | Docker, Docker Compose |

## Architecture

```mermaid
flowchart LR
    subgraph FE["Frontend: React + Redux"]
        roster["HCP roster"]
        form["Structured form"]
        chat["Chat"]
        history["Interaction history"]
    end

    subgraph BE["Backend: FastAPI"]
        hcps["/api/hcps"]
        interactions["/api/interactions"]
        turn["/api/chat/turn"]
        subgraph AG["LangGraph agent"]
            agentNode["agent node<br/>(LLM with tools bound)"]
            toolsNode["tools node"]
        end
        tools["6 CRM tools"]
    end

    db[("PostgreSQL<br/>hcps · interactions ·<br/>follow_ups · chat_messages")]
    groq["Groq API"]

    roster --> hcps
    form --> interactions
    history --> interactions
    chat --> turn
    turn --> agentNode
    agentNode -- "tool call" --> toolsNode
    toolsNode -- "result" --> agentNode
    toolsNode --> tools
    interactions -- "same edit + compliance<br/>functions" --> tools
    agentNode --> groq
    hcps --> db
    interactions --> db
    tools --> db
```

### Agent graph

The compiled LangGraph graph (structure generated from the running code with `graph.get_graph().draw_mermaid()`; edge labels added for readability):

```mermaid
graph TD
    start([start]) --> agent
    agent -. "tool call requested" .-> tools
    tools --> agent
    agent -. "final reply, no tool call" .-> finish([end])
```

- **agent:** calls the LLM with the conversation and the 6 tool schemas, and decides whether to call tools or reply.
- **tools:** a LangGraph `ToolNode` that runs the requested tools (possibly several in one turn, e.g. log then compliance) and returns their results.
- The loop ends when the model replies without asking for a tool.

## The 6 tools

| Tool | What it does |
|---|---|
| `log_interaction` | Sends the rep's notes to the LLM with an extraction prompt and saves the structured result: summary, products, samples, materials, topics, sentiment (label + score). Raw text is stored too; invalid samples in the LLM output are skipped with a logged warning. |
| `edit_interaction` | Changes only the given fields of an interaction and appends a before/after entry to its edit history. Re-runs the compliance check when samples change. Backs both chat corrections and the form's Edit button. |
| `get_hcp_context` | Returns an HCP's profile and 5 most recent interactions, so the agent has grounding before logging or recommending. |
| `check_sample_compliance` | Checks sample quantities against per-state limits (e.g. Vermont 0, California 2, default 4) and flags violations. The ruleset is illustrative, not regulatory data. |
| `schedule_follow_up` | Creates a follow-up task for an HCP, optionally linked to an interaction, with a due date and reason. |
| `suggest_next_best_action` | Uses the heavier model on the HCP's recent history to recommend a concrete next step. |

## API

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/hcps` | List HCPs |
| POST | `/api/hcps` | Create an HCP |
| GET | `/api/hcps/{hcp_id}` | Get one HCP |
| GET | `/api/interactions?hcp_id=` | List interactions, optionally for one HCP |
| POST | `/api/interactions` | Log via the form (runs the compliance check if samples were dropped) |
| PATCH | `/api/interactions/{id}` | Edit an interaction (through `edit_interaction`) |
| GET | `/api/interactions/{id}` | Get one interaction |
| POST | `/api/chat/turn` | One chat turn: returns the reply, the tools called, and any interaction logged |
| GET | `/api/chat/{session_id}/history` | Replay a chat session |
| GET | `/health` | Liveness check |

Interactive docs with request and response schemas: `http://localhost:8000/docs`.

## Running locally

Needs: Docker, Python 3.11, Node 18+, and a free Groq API key from [console.groq.com/keys](https://console.groq.com/keys).

### 1. Database

```bash
cp .env.example .env              # set POSTGRES_PASSWORD and GROQ_API_KEY
docker compose up -d postgres
```

Compose reads credentials from the root `.env` and refuses to start if any required value is missing (including `GROQ_API_KEY`, even when starting only Postgres). Postgres is published on `127.0.0.1:5432` only. If that port is taken (common on Windows with a local Postgres install), see [Troubleshooting](#troubleshooting).

To run the backend in Docker too, use `docker compose up -d --build` and skip step 2. The backend waits for Postgres to be healthy.

### 2. Backend

```bash
cd backend
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env              # set DATABASE_URL's password to POSTGRES_PASSWORD, paste your Groq key
python -m app.seed                # adds 3 demo HCPs
uvicorn app.main:app --reload --port 8000
```

The backend refuses to start if `DATABASE_URL` or `GROQ_API_KEY` is missing. Check it before starting the frontend:

```bash
curl http://localhost:8000/api/hcps    # should return the 3 seeded HCPs
```

### 3. Frontend

In a second terminal, from the repo root:

```bash
cd frontend
npm install
cp .env.example .env              # points VITE_API_URL at the backend
npm run dev
```

Open `http://localhost:5173`.

## Testing

CI runs on every push and pull request ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)):

| Job | What runs |
|---|---|
| Backend | ruff, mypy, pytest against a Postgres 16 service |
| Frontend | `npm ci`, production build, Vitest |

What the backend tests cover:

- Health endpoints; HCP create, list and get, including 404 and 422 cases.
- Interaction create, list, get and patch: edit history, untouched fields staying unchanged, and the compliance check on create.
- Compliance rules: state lookup (case and whitespace), quantities at and over the limit, Vermont's zero limit, several violations at once, error paths.
- The model fallback chain, JSON extraction from LLM replies, `log_interaction`, and a full `/api/chat/turn` with a tool call.
- **No real LLM calls in tests.** A fixture fails any test that tries to reach Groq; LLM paths use a scripted fake, and tests use a dummy API key.
- **Sample validation:** bad products or quantities return 422 on create and on PATCH; a failed compliance check on create saves nothing; PATCH re-runs compliance when samples change.
- **Config and startup:** the app fails fast without required settings; startup runs through the FastAPI lifespan; timestamps are timezone-aware UTC; migration 001 preserves existing values.

Known bugs are tracked as strict `xfail` tests until fixed, so they show up in every run. The first two (a non-numeric `qty` causing a 500, and PATCH not re-running compliance) were found this way and are now fixed and covered by normal tests.

Run the tests locally:

```bash
# Backend: the tests DROP AND RECREATE all tables.
# Use a throwaway database; the suite refuses to run unless its name ends in "_test".
docker run -d --rm --name hcp-test-db -e POSTGRES_USER=hcp_user -e POSTGRES_PASSWORD=hcp_pass \
  -e POSTGRES_DB=hcp_crm_test -p 127.0.0.1:55432:5432 postgres:16-alpine
cd backend
pip install -r requirements-dev.txt
export TEST_DATABASE_URL=postgresql+asyncpg://hcp_user:hcp_pass@127.0.0.1:55432/hcp_crm_test
ruff check . && mypy && pytest

# Frontend
cd ../frontend
npm test
```

## Production readiness

What is in place today, and what is still missing. This list is kept honest on purpose: this README is updated as each item lands.

| Area | Status |
|---|---|
| Tests and CI (lint, types, backend, frontend) | Done |
| Shared edit path for form and chat (one set of rules in `app/samples.py`) | Done |
| LLM fallback for retired models | Done |
| Authentication and roles (rep, manager) | Not yet: every endpoint is open |
| Ownership checks on agent tools (a rep can edit only their own records) | Not yet |
| Confirmation before the agent writes or edits data | Not yet |
| Append-only audit table with who changed what | Not yet: history is a JSON column on the row |
| Chat history stored in the database (works across restarts and workers) | Not yet: kept in process memory |
| Database migrations (Alembic) | Not yet: tables created at startup; one hand-written SQL migration in `backend/migrations/` |
| Input validation for sample quantities; compliance on create and edit | Done |
| Secrets out of compose; ports bound to localhost | Done |
| Pagination, rate limiting, structured logging | Not yet |
| Full stack in Docker Compose (with frontend and health checks) | Not yet: backend and Postgres have health checks; the frontend isn't in Compose |

**Data note:** this system stores data about doctors (HCPs), not patients. The main risks are personal contact data, unchecked AI writes, and pharma sample rules, not patient health records.

## Project structure

```
healthcare-crm/
├── backend/
│   ├── app/
│   │   ├── main.py            # FastAPI app, lifespan and routers
│   │   ├── config.py          # settings from environment (fails fast if required ones are missing)
│   │   ├── database.py        # async engine and sessions
│   │   ├── models.py          # HCP, Interaction, FollowUp, ChatMessage
│   │   ├── schemas.py         # request and response models
│   │   ├── samples.py         # shared sample validation + compliance rules
│   │   ├── seed.py            # demo HCPs
│   │   ├── agent/
│   │   │   ├── llm.py         # Groq clients and fallback chain
│   │   │   ├── tools.py       # the 6 tools
│   │   │   └── graph.py       # LangGraph agent
│   │   └── routers/           # hcp, interactions, chat
│   ├── migrations/            # hand-written SQL migrations for existing databases
│   ├── tests/                 # pytest suite
│   ├── Dockerfile             # non-root image
│   ├── pyproject.toml         # ruff, mypy and pytest config
│   ├── requirements.txt
│   └── requirements-dev.txt   # + pytest, ruff, mypy
├── frontend/
│   └── src/
│       ├── components/        # roster, form, chat, history
│       ├── store/             # Redux slices (+ slices.test.js)
│       └── api/client.js
├── .github/workflows/ci.yml
├── .env.example               # Compose settings (copy to .env)
└── docker-compose.yml
```

## Troubleshooting

**`InvalidPasswordError` when the backend starts.** Something else is on port 5432 (often a local Postgres on Windows), or the passwords don't match.

1. Run `docker compose ps` and check the published port.
2. If 5432 is taken, set `POSTGRES_PORT=5433` in the root `.env` and run `docker compose up -d postgres`.
3. Set the same port in `DATABASE_URL` in `backend/.env`, and make sure its password matches `POSTGRES_PASSWORD`, then restart uvicorn.

**`RuntimeError: Missing or invalid required setting(s)` at startup.** `backend/.env` is missing or lacks `DATABASE_URL` / `GROQ_API_KEY`. Copy `backend/.env.example` and fill both in.

**Writes fail with `can't subtract offset-naive and offset-aware datetimes`.** The database was created before timestamps became timezone-aware. Back it up, then run the one-off migration (safe to run twice):

```bash
docker compose exec -T postgres sh -c 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"' \
  < backend/migrations/001_timestamps_to_timestamptz.sql
```

**Groq says a model is decommissioned.** Usually nothing to do: the fallback chain switches to a supported model automatically. Other Groq errors (auth, rate limit) are shown immediately.

**The sidebar is empty.** Run `python -m app.seed` in `backend/`.

**CORS errors in the browser.** `FRONTEND_ORIGIN` in `backend/.env` must match the frontend's URL exactly (default `http://localhost:5173`).

## How this project was built

I designed the workflow, the data model and the agent's tools, and I review and test every change. I use Claude Code as an AI coding assistant, under rules I set for the repository; commits it helped with carry a co-author trailer.
