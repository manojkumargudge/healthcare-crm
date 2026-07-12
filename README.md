# AI-First HCP CRM — Log Interaction Screen

An AI-first Customer Relationship Management module for pharmaceutical field
representatives, built for the "Log Interaction Screen" assignment. Reps can
log Healthcare Professional (HCP) interactions either through a **structured
form** or a **conversational chat interface** backed by a LangGraph agent.

## Why it's designed this way

Field reps are usually typing on a phone or tablet between appointments. A
structured form is precise but slow to fill in; a chat interface is fast but
unstructured. This app offers both, backed by the same CRM data model, so a
rep can pick whichever is faster in the moment — and the AI agent turns
free-form chat into the same structured records a form would produce.

## Tech stack

| Layer | Choice |
|---|---|
| Frontend | React + Redux Toolkit, Inter font |
| Backend | Python, FastAPI |
| AI agent framework | LangGraph |
| LLM | Groq `gemma2-9b-it` (primary), `llama-3.3-70b-versatile` (fallback for heavier reasoning) |
| Database | PostgreSQL (async SQLAlchemy) |

## Architecture

```mermaid
flowchart LR
    subgraph Client["Frontend — React + Redux"]
        Sidebar["HCP Roster\n(Sidebar)"]
        Screen["Log Interaction Screen"]
        Form["Structured Form"]
        Chat["Conversational Chat"]
        History["Interaction History"]
        Sidebar --> Screen
        Screen --> Form
        Screen --> Chat
        Screen --> History
    end

    subgraph API["Backend — FastAPI"]
        HcpRouter["/api/hcps"]
        InteractionRouter["/api/interactions"]
        ChatRouter["/api/chat/turn"]
    end

    subgraph Agent["LangGraph Agent"]
        AgentNode["agent node\n(Groq gemma2-9b-it,\ntool-bound)"]
        ToolsNode["tools node\n(ToolNode)"]
        AgentNode -->|tool call requested| ToolsNode
        ToolsNode -->|tool result| AgentNode
    end

    subgraph Tools["6 CRM Tools"]
        T1["log_interaction"]
        T2["edit_interaction"]
        T3["get_hcp_context"]
        T4["check_sample_compliance"]
        T5["schedule_follow_up"]
        T6["suggest_next_best_action"]
    end

    subgraph Data["PostgreSQL"]
        DB[("hcps · interactions ·\nfollow_ups · chat_messages")]
    end

    Form -->|REST POST/PATCH| InteractionRouter
    Chat -->|REST POST| ChatRouter
    Sidebar -->|REST GET| HcpRouter

    ChatRouter --> AgentNode
    ToolsNode --> Tools
    Tools --> DB
    InteractionRouter -->|reuses edit_interaction &\ncheck_sample_compliance tools| Tools
    HcpRouter --> DB

    AgentNode -.->|LLM calls| Groq[("Groq API\ngemma2-9b-it /\nllama-3.3-70b-versatile")]
```

**Key design point:** the structured-form REST endpoints (`POST /api/interactions`,
`PATCH /api/interactions/{id}`) call the *same* `edit_interaction` and
`check_sample_compliance` tool functions the chat agent uses, rather than
duplicating logic. Whichever path a rep uses to log or edit data, it goes
through one audited code path.

## LangGraph workflow

This is the actual compiled graph, generated directly from the running code
(`graph.get_graph().draw_mermaid()`), not hand-drawn:

```mermaid
graph TD;
    __start__([__start__]):::first
    agent(agent)
    tools(tools)
    __end__([__end__]):::last
    __start__ --> agent;
    tools --> agent;
    agent -.->|tool call requested| tools;
    agent -.->|no tool call — final reply| __end__;
    classDef default fill:#f2f0ff,line-height:1.2
    classDef first fill-opacity:0
    classDef last fill:#bfb6fc
```

- **`agent`**: calls Groq with the full message history + the 6 tool schemas
  bound via `bind_tools()`. Decides whether to call a tool or respond.
- **`tools`**: a LangGraph `ToolNode` — executes whatever the model requested
  (potentially multiple tools in one turn, e.g. `log_interaction` then
  `check_sample_compliance`) and appends results as `ToolMessage`s.
- The loop (`agent ⇄ tools`) continues until the model produces a plain-text
  reply with no further tool calls, at which point the graph reaches `END`
  and that reply is sent back to the rep.

## API endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/hcps` | List all HCPs (roster sidebar) |
| `POST` | `/api/hcps` | Create an HCP |
| `GET` | `/api/hcps/{hcp_id}` | Get one HCP's profile |
| `GET` | `/api/interactions?hcp_id=` | List interactions, optionally filtered by HCP |
| `POST` | `/api/interactions` | Log an interaction via the **structured form** path (runs `check_sample_compliance` if samples were dropped) |
| `PATCH` | `/api/interactions/{id}` | Edit a logged interaction (routes through the `edit_interaction` tool) |
| `GET` | `/api/interactions/{id}` | Fetch a single interaction |
| `POST` | `/api/chat/turn` | Send one message to the LangGraph agent (the **conversational** path); returns the reply, which tool(s) were called, and the resulting interaction if one was logged |
| `GET` | `/api/chat/{session_id}/history` | Replay a chat session's persisted messages |
| `GET` | `/health` | Liveness check |

Full interactive docs (request/response schemas, try-it-out) are auto-generated
by FastAPI at `http://localhost:8000/docs` once the backend is running.

## Project structure

```
hcp-crm/
├── backend/
│   ├── app/
│   │   ├── main.py              # FastAPI app + routers
│   │   ├── config.py            # env-based settings
│   │   ├── database.py          # async SQLAlchemy engine/session
│   │   ├── models.py            # HCP, Interaction, FollowUp, ChatMessage
│   │   ├── schemas.py           # Pydantic request/response models
│   │   ├── seed.py              # demo HCP data
│   │   ├── agent/
│   │   │   ├── llm.py           # Groq LLM clients
│   │   │   ├── tools.py         # the 6 LangGraph tools
│   │   │   └── graph.py         # LangGraph StateGraph (the agent)
│   │   └── routers/
│   │       ├── hcp.py           # HCP profile CRUD
│   │       ├── interactions.py  # structured-form path
│   │       └── chat.py          # conversational path -> LangGraph agent
│   └── requirements.txt
├── frontend/
│   └── src/
│       ├── components/          # Sidebar, LogInteractionScreen, StructuredForm,
│       │                        # ChatInterface, InteractionHistory, PulseStrip
│       ├── store/                # Redux slices (hcps, interactions, chat)
│       └── api/client.js         # fetch wrapper for the backend
└── docker-compose.yml            # local Postgres
```

## Running it locally

### 1. Database

```bash
docker compose up -d
```

By default this starts Postgres on `localhost:5432` with the credentials
already wired into `backend/.env.example`:

```
DATABASE_URL=postgresql+asyncpg://hcp_user:hcp_pass@localhost:5432/hcp_crm
```

If port `5432` is already in use on your machine (common if you have a
local Postgres service running, e.g. on Windows), remap Docker's Postgres
to a free host port in `docker-compose.yml` (e.g. `"5433:5432"`) and use
that same port in `backend/.env`'s `DATABASE_URL` instead. See
[Troubleshooting](#troubleshooting) below.

### 2. Backend

```bash
cd backend
python -m venv venv && source venv/bin/activate   # or your tool of choice
pip install -r requirements.txt
cp .env.example .env
# edit .env and paste your Groq API key (https://console.groq.com/keys)
python -m app.seed          # populates 3 demo HCPs
uvicorn app.main:app --reload --port 8000
```

The API is now at `http://localhost:8000` (interactive docs at `/docs`).

Before moving on to the frontend, confirm the database, backend, and seed
step are all actually wired together correctly:

```bash
curl http://localhost:8000/api/hcps
```

This should return the 3 seeded HCPs. If it returns an empty list or an
error, fix that first — a frontend that "loads but looks empty" is almost
always this step silently failing upstream.

### 3. Frontend

```bash
cd frontend
npm install
cp .env.example .env        # points VITE_API_URL at the backend
npm run dev
```

Open `http://localhost:5173`.

## Recording the demo video

See [`VIDEO_SCRIPT.md`](./VIDEO_SCRIPT.md) for a beat-by-beat shotlist
covering the frontend walkthrough, a live demo of all 6 tools, and the code
explanation, timed to fit the 10–15 minute requirement.

## The LangGraph agent

### Role

The agent sits behind the **Conversational** tab of the Log Interaction
Screen. Its job is to take a rep's free-form message — "Just met Dr. Rao,
discussed CardioMax, left 3 samples, she seemed positive but asked about
pricing" — and turn it into the same structured CRM records the form would
produce, while also handling the surrounding workflow: pulling up HCP
context, flagging compliance issues on samples, scheduling follow-ups, and
letting the rep correct a record conversationally later ("actually make that
2 samples, not 3"). It is a **tool-calling ReAct-style agent**: at each turn
it decides whether to answer directly or call one or more CRM tools, reads
the tool results, and either calls another tool or replies to the rep.

Graph shape (`backend/app/agent/graph.py`):

```
START → agent ⇄ tools → END
```

- **`agent` node** — calls Groq (`gemma2-9b-it`) with the conversation
  history and the tool schemas bound to it; the model decides what to do
  next.
- **`tools` node** — a LangGraph `ToolNode` that actually executes whichever
  tool(s) the model requested, and feeds the results back to `agent`.
- The loop continues until the model responds without requesting a tool,
  at which point the graph ends and the reply is returned to the rep.

### The 5 (+1) tools

| # | Tool | Purpose |
|---|---|---|
| 1 | **`log_interaction`** *(mandatory)* | Takes the rep's raw notes/chat text, sends them to the Groq LLM with an extraction prompt, and gets back structured JSON: a short summary, products discussed, samples dropped, materials shared, key topics, and HCP sentiment (label + score). This is saved as a new `Interaction` row. |
| 2 | **`edit_interaction`** *(mandatory)* | Takes an `interaction_id` and a JSON object of fields to change (e.g. correcting a sample quantity, or amending the notes). Applies the update and appends a before/after entry to `edit_history` so every change is auditable. |
| 3 | **`get_hcp_context`** | Looks up an HCP's profile and their 5 most recent interactions. The agent calls this first when it needs grounding — e.g. to know the HCP's specialty and history before logging or recommending. |
| 4 | **`check_sample_compliance`** | Checks samples dropped against a simple state-level ruleset (a stand-in for something like the US Sunshine Act / state sample caps) and flags anything over the limit, e.g. Vermont has much tighter sample restrictions than the default. |
| 5 | **`schedule_follow_up`** | Creates a follow-up task tied to an HCP (and optionally a specific interaction) with a due date, e.g. "HCP asked about pricing — follow up in 7 days." |
| 6 | **`suggest_next_best_action`** | Given an HCP's recent interaction history, asks the (heavier) `llama-3.3-70b-versatile` model for a concrete recommendation on what the rep should do next. |

### Log Interaction tool, in detail

1. Rep sends a message in the chat interface.
2. The agent (if it doesn't already have HCP context in the conversation)
   calls `get_hcp_context` to fetch the HCP's name/specialty — this makes
   the extraction prompt more accurate.
3. The agent calls `log_interaction(hcp_id, raw_text, interaction_type)`.
4. Inside the tool, a purpose-built extraction prompt is sent to
   `gemma2-9b-it` asking for **structured JSON only** (summary, products,
   samples, materials, topics, sentiment). The response is parsed
   defensively (handles stray markdown fences, malformed JSON, etc.).
5. A new `Interaction` row is written to Postgres with both the raw text
   (for auditability) and the structured/derived fields.
6. If samples were dropped, the agent typically follows up by calling
   `check_sample_compliance` in the same turn and reports any flag back to
   the rep.

### Edit Interaction tool, in detail

1. Rep says something like "actually, change that to 2 samples of
   CardioMax" (possibly referencing an interaction logged earlier in the
   same or a previous session).
2. The agent calls `edit_interaction(interaction_id, updates, edit_reason)`
   with only the fields that changed.
3. The tool re-reads the current row, snapshots the "before" values for
   just the changed fields, applies the "after" values, and appends both to
   a JSON `edit_history` array on the row — so the full change log is
   visible in the UI (each history card shows "Edited N time(s)") without
   needing a separate audit table.
4. The same tool backs the **Edit interaction** button in the Structured
   Form's history view, so both the chat and the form path go through
   identical, audited edit logic.

## What I understood the task to be asking for

The brief is really about designing the workflow layer between a rep's
raw field notes and clean CRM data, using an LLM agent to do the structuring
work a rep would otherwise have to do by hand — while keeping every AI action
(logging, editing, compliance checks) visible, correctable, and audited,
since this is regulated pharmaceutical data. The two required tools (log +
edit) cover the core data lifecycle; the extra tools (context lookup,
compliance, follow-up, next-best-action) reflect what a life-sciences field
rep actually needs day-to-day beyond raw data entry.

## Troubleshooting

### `asyncpg.exceptions.InvalidPasswordError` on backend startup

This means the backend is connecting to a Postgres instance that isn't the
one Docker just started for it — almost always because something else on
your machine is already listening on port `5432` (a common case: **Windows
users with a local Postgres service already installed**).

1. Check what Docker actually published:
   ```bash
   docker compose ps
   ```
   If the `PORTS` column shows something like `0.0.0.0:5433->5432/tcp`,
   Docker's Postgres is reachable on host port `5433`, not `5432` — your
   `DATABASE_URL` needs to match that, not the container-internal port.
2. Cross-check what's listening on 5432 itself (useful when you're not
   sure *what's* squatting on the port, e.g. a local Postgres install):
   ```powershell
   netstat -ano | findstr :5432
   ```
3. If something other than your `docker compose` container owns it, remap
   Docker's Postgres to a free host port instead (don't touch the existing
   Postgres service unless you're sure nothing else depends on it). In
   `docker-compose.yml`, change:
   ```yaml
   ports:
     - "5433:5432"
   ```
   then `docker compose up -d` again.
4. Make sure `backend/.env` actually exists (copy it from `.env.example` if
   it doesn't — `pydantic-settings` silently falls back to in-code defaults
   if `.env` is missing, which looks identical to a "the file didn't work"
   bug but is really "there was no file to read").
5. Update `DATABASE_URL` in `backend/.env` to match whatever host port
   Docker is actually using:
   ```
   DATABASE_URL=postgresql+asyncpg://hcp_user:hcp_pass@localhost:5433/hcp_crm
   ```
6. Restart `uvicorn`.

### Groq returns a "model decommissioned" / "does not exist" error

Groq periodically retires older models. `gemma2-9b-it` and
`llama-3.3-70b-versatile` — the two models this assignment specifies — may
or may not be live on Groq's platform depending on when you're reading
this (check `console.groq.com/docs/deprecations`). You shouldn't need to
do anything: `app/agent/llm.py` tries the assignment-mandated model first
on every call and automatically falls through to a currently-supported
Groq model (`llama-3.1-8b-instant` → `openai/gpt-oss-20b` for the primary
slot; `openai/gpt-oss-120b` → `qwen/qwen3-32b` for the heavier-reasoning
slot) only if Groq reports the requested model as unavailable. If you see
a Groq error that *isn't* about a deprecated model (auth, rate limit, etc.)
it will surface immediately rather than being retried.

### Frontend loads but the sidebar is empty

You likely haven't run the seed script yet:
```bash
cd backend
python -m app.seed
```

### CORS errors in the browser console

`FRONTEND_ORIGIN` in `backend/.env` must exactly match the URL the
frontend is actually served from (default `http://localhost:5173`). If you
changed Vite's port, update this value and restart the backend.

## Notes / assumptions

- Groq API key must be supplied by you (free tier at console.groq.com) —
  not committed to the repo.
- Chat session history is kept in-memory per `session_id` for simplicity;
  it's also persisted to the `chat_messages` table for replay/debugging.
- The compliance ruleset in `check_sample_compliance` is illustrative, not
  a real regulatory dataset.
- No authentication layer — out of scope for this assignment; a real
  deployment would add rep login and HCP-territory scoping.
