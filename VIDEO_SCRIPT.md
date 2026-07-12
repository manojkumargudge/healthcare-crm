# Video Recording Script — AI-First HCP CRM (Log Interaction Screen)

Target length: **10–15 minutes**. Times below are cumulative suggestions —
adjust pacing to what feels natural, but keep each section roughly this size.

Before you hit record: have the backend and frontend both running, the
Swagger docs open in a spare tab (`http://localhost:8000/docs`), and 2–3
Groq API calls already "warmed up" once so you're not staring at a cold
first-request delay on camera.

---

## 1. Intro (0:00 – 1:00)

Say, in your own words:
- Your name, and that this is the AI-First HCP CRM — Log Interaction Screen
  assignment.
- One sentence on what the app does: lets a pharma field rep log HCP
  interactions either through a structured form or by chatting with an
  AI agent, and the agent turns free text into the same structured CRM
  records the form would produce.

## 2. Frontend walkthrough (1:00 – 3:30)

- Show the sidebar: the 3 seeded demo HCPs, click between them, point out
  the tier badge (A/B/C).
- Select an HCP. Point out the "pulse strip" (recent sentiment bars) —
  explain it's empty until interactions exist.
- Show the two tabs: **Conversational** and **Structured form**. Explain
  *why* both exist — reps are often on a phone between appointments; chat
  is fast, a form is precise, both write to the same data model.
- Quickly fill out the Structured Form once (pick a product, add 2 samples,
  add a topic, submit) so there's a baseline interaction in history before
  you move to the chat demo.
- Scroll to Interaction History, point out the summary, sentiment tag, and
  the "Edit interaction" button.

## 3. Live demo of all 6 tools (3:30 – 10:00)

Do this conversationally in the **Conversational** tab, narrating which
tool you expect to fire as you type each message. After each response,
briefly point at the "tool trace" line above the reply bubble (shows which
tool(s) ran).

1. **`get_hcp_context`** — Send: *"What do we know about this HCP so far?"*
   Point out the agent calls `get_hcp_context` to pull profile + recent
   history before answering.

2. **`log_interaction`** — Send something like: *"Just met Dr. Rao,
   discussed CardioMax, left 3 samples, she seemed positive but asked
   about pricing."* Show the reply, then scroll down to Interaction
   History and point out the new row — summary, products, sentiment —
   all extracted by the LLM from that one sentence.

3. **`check_sample_compliance`** — If the selected HCP is in a
   sample-restricted state (Vermont is set to a 0-sample limit in the
   seed data; California/Minnesota are capped at 2), the compliance flag
   should already show up automatically after step 2. If not, explicitly
   say: *"I dropped 5 samples of CardioMax, is that okay?"* and show the
   compliance warning in the reply and in the history card.

4. **`edit_interaction`** — Send: *"Actually, change that to 2 samples,
   not 3."* Show the reply confirming the edit, then point out the
   "Edited 1 time(s)" note that appears on the history card — that's the
   audit trail (`edit_history`), not just a silent overwrite.

5. **`schedule_follow_up`** — Send: *"She wants more pricing info, follow
   up with her in a week."* Confirm the reply. (Optional: hit
   `GET /api/hcps/{id}` or mention that follow-ups are stored in the
   `follow_ups` table — there's no dedicated UI panel for this in the
   assignment scope, so speaking to the Swagger response is fine.)

6. **`suggest_next_best_action`** — Send: *"What should I do next with
   this HCP?"* Show the model's concrete recommendation, grounded in the
   interaction history you just built up.

## 4. Code walkthrough (10:00 – 13:00)

Keep this high-level — you're explaining structure, not reading every line.

- **`backend/app/agent/graph.py`** — show the `StateGraph`: `agent` node
  calls Groq with tools bound; `tools` node executes whatever the model
  requested; loop continues until the model replies without a tool call.
  Mention the `START → agent ⇄ tools → END` shape out loud.
- **`backend/app/agent/tools.py`** — show `build_tools(db)` and one tool
  in detail (e.g. `log_interaction`): note the extraction prompt asking
  for structured JSON, the defensive `_extract_json` parsing, and the
  DB write.
- **`backend/app/agent/llm.py`** — mention the fallback chain briefly:
  Groq deprecates models over time, so each call tries the
  assignment-mandated model first and only falls through to a
  currently-supported one if Groq reports it decommissioned.
- **`backend/app/routers/`** — point out that the structured-form REST
  endpoints reuse the *same* `edit_interaction` / `check_sample_compliance`
  tool functions the chat agent uses, instead of duplicating logic — one
  audited code path regardless of which UI tab was used.
- **Frontend** — briefly show `store/` (3 Redux slices: hcps, interactions,
  chat) and `api/client.js` (thin fetch wrapper), and how
  `LogInteractionScreen.jsx` switches between `StructuredForm` and
  `ChatInterface`.

## 5. What you understood the task to be asking (13:00 – 14:30)

Summarize in your own words (see the README's "What I understood the task
to be asking for" section for the core idea, but say it naturally, don't
read it verbatim): the assignment is really about designing the workflow
between a rep's raw field notes and clean, structured CRM data, using an
LLM agent to do that structuring — while keeping every AI action visible,
correctable, and audited, since it's regulated pharmaceutical data.

## 6. Close (14:30 – 15:00)

- Mention the GitHub repo link is in the submission form.
- One sentence on what you'd add with more time (e.g. rep authentication,
  a dedicated follow-ups panel, a real compliance dataset instead of the
  illustrative one).

---

### Recording checklist
- [ ] Backend running, `/health` returns `{"status": "healthy"}`
- [ ] Frontend running, sidebar shows 3 seeded HCPs
- [ ] Groq API key valid (send one test chat message before recording)
- [ ] Screen resolution readable at normal video zoom (1080p or higher)
- [ ] Mic check — narration is the main signal here, not just screen capture
