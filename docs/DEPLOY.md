# Deploying the demo: Render + Neon

This guide sets up a public demo on free plans.

```
browser ──► Render static site (frontend, built with VITE_API_URL)
        ──► Render web service (backend, Docker, free plan) ──► Neon Postgres (free)
                                                            ──► Groq API
GitHub Actions (nightly) ──► Neon: wipe and re-seed demo data
UptimeRobot (every 5 min) ──► backend /health
```

The repo already contains everything needed:
- `render.yaml`: the Render Blueprint, creating both Render services.
- `backend/Dockerfile`: honours Render's `PORT`.
- Migrations apply on startup.
- `python -m app.demo_seed`: the demo seed.
- `.github/workflows/demo-reset.yml`: the nightly reset.

You only create the accounts and paste values in.

Plan limits and dashboard menus change over time. If a screen looks different from what's described here, look for the same setting under a nearby name.

---

## 0. Before you start

You need:
- **Repo:** this repo on GitHub, with `main` up to date.
- **Groq key:** a Groq API key dedicated to the demo (see [step 1](#1-groq-a-dedicated-key-and-a-spend-limit)).
- **Demo passwords:** three demo passwords, 12+ characters each, for demo-rep, demo-manager and demo-admin.
  - They are shown publicly in the README, so don't reuse a real password.
  - Generate them with: `python -c "import secrets; print(secrets.token_urlsafe(12))"`

Never commit any of these values. They go into the Render dashboard and GitHub secrets only.

## 1. Groq: a dedicated key and a spend limit

A public demo lets anyone trigger LLM calls, so cap the cost:

1. **Create a key.** In [console.groq.com](https://console.groq.com/keys), create a **new API key** just for the demo (e.g. `hcp-crm-demo`). You can then revoke it without touching other projects.
2. **Set a spend limit.** Under **Settings → Billing** (spend limits / usage limits), set a **monthly spend limit** you're comfortable with, plus an alert below it.
   - On the free tier there is no bill. Groq's own rate limits apply instead, and the demo slows down when it hits them.
3. **Keep the app's limits.** The app has two:
   - **Per user:** `render.yaml` sets `RATE_LIMIT_LLM_PER_MINUTE=10`. This limiter is in memory, which is fine for the single free instance.
   - **Global:** `LLM_DAILY_CAP=500` AI requests per UTC day across everyone. It is counted in the database, so restarts don't reset it. When it is reached, users see a friendly message and the form still works.

## 2. Neon: the database

1. **Create a project.** Sign up at [neon.tech](https://neon.tech) and create a project:
   - Postgres **16**.
   - Region: **AWS US West (Oregon)**, to sit next to Render's `oregon` region in `render.yaml`. If you pick another Neon region, change `region:` in `render.yaml` to the closest Render region.
2. **Copy the connection string** from **Connection details**:
   - Use the **direct** connection (turn **Connection pooling off**, so the host has no `-pooler`).
   - It looks like `postgresql://neondb_owner:...@ep-xxx-123456.us-west-2.aws.neon.tech/neondb?sslmode=require&channel_binding=require`
   - The backend handles this format: it switches `postgresql://` to the async driver, turns `sslmode=require` into an encrypted asyncpg connection, and drops `channel_binding`.
   - Use `sslmode=verify-full` if you also want the certificate verified.
   - **Why direct:** the migration runner sends multi-statement SQL and the app uses asyncpg prepared statements, both simplest without a transaction pooler. The free tier's direct connection limit is plenty for one small instance.
3. You don't need to create tables. The backend applies every migration on startup.

Neon's free compute suspends when idle; the first query afterwards wakes it, which takes a moment. The backend checks and recycles pooled connections (`pool_pre_ping`, `pool_recycle=300`), so a suspended compute doesn't cause errors.

## 3. Render: backend and frontend

Service URLs follow the service names in `render.yaml`:
- API: `https://hcp-crm-api.onrender.com`
- Frontend: `https://hcp-crm-web.onrender.com`

If a name is taken, Render adds a suffix. You'll confirm the real URLs in step 4.

1. Sign up at [render.com](https://render.com) and connect your GitHub account.
2. Go to **New → Blueprint** and pick this repo. Render reads `render.yaml` and proposes two services: `hcp-crm-api` (Docker, free) and `hcp-crm-web` (static site).
3. Fill in the values Render asks for (the `sync: false` keys):

   | Service | Key | Value |
   |---|---|---|
   | hcp-crm-api | `DATABASE_URL` | the Neon connection string from step 2 |
   | hcp-crm-api | `GROQ_API_KEY` | the demo key from step 1 |
   | hcp-crm-api | `FRONTEND_ORIGIN` | `https://hcp-crm-web.onrender.com` (no trailing slash) |
   | hcp-crm-api | `DEMO_REP_PASSWORD`, `DEMO_MANAGER_PASSWORD`, `DEMO_ADMIN_PASSWORD` | the three demo passwords; optional here, needed only to seed from a Render shell |
   | hcp-crm-web | `VITE_API_URL` | `https://hcp-crm-api.onrender.com` (no trailing slash) |

   - `JWT_SECRET` is generated by Render (`generateValue: true`).
   - The other keys have values in `render.yaml`.
4. Click **Apply**. The first API deploy builds the Docker image, applies the migrations to Neon, and starts the service. Render then checks `/health` before it routes traffic.

## 4. Check the URLs, then fix them if Render added a suffix

1. Open both services in the dashboard and note their real `onrender.com` URLs.
2. If either differs from what you entered in step 3:
   - **API URL differs:** set the static site's `VITE_API_URL` to the real API URL, then on the static site run **Manual Deploy → Clear build cache & deploy**. The URL is baked into the build, so a rebuild is required.
   - **Frontend URL differs:** set the API's `FRONTEND_ORIGIN` to the real frontend URL. The API restarts with the new value.
   - **Custom domain:** add it to the API's `CORS_ORIGINS` (comma-separated, e.g. `https://crm.example.com`).
3. Test the API: `curl -i https://<api>/health` should print `HTTP/2 200` and `{"status":"healthy"}`.
   - The first request after idle can take up to about a minute, because free web services sleep.

## 5. Seed the demo data (GitHub Actions)

The nightly workflow also does the first seed.

1. Add repository secrets in GitHub under **Settings → Secrets and variables → Actions → New repository secret**:

   | Secret | Value |
   |---|---|
   | `DATABASE_URL` | the same Neon connection string as on Render |
   | `DEMO_REP_PASSWORD` | demo-rep's password |
   | `DEMO_MANAGER_PASSWORD` | demo-manager's password |
   | `DEMO_ADMIN_PASSWORD` | demo-admin's password |

2. Go to **Actions → Nightly demo reset → Run workflow**. It runs `python -m app.demo_seed --reset`, which does the following:
   - applies migrations
   - wipes all app data, refusing to do so if the database has users but no `demo-admin@example.com`
   - creates the three demo users
   - adds 5 HCPs, sample interactions (including one with a Vermont sample flag and one with a material pending approval), follow-ups, and the material and product catalogs
3. From then on it runs every night at 03:17 UTC.
   - Without the `DATABASE_URL` secret, the job logs a notice and does nothing. Forks and clones without a demo stay green.

To seed from your machine instead, run this from `backend/` with the requirements installed. The env vars override the root `.env`:

```bash
DATABASE_URL='postgresql://...neon.tech/neondb?sslmode=require' \
DEMO_REP_PASSWORD=... DEMO_MANAGER_PASSWORD=... DEMO_ADMIN_PASSWORD=... \
python -m app.demo_seed            # add --reset to wipe first
```

## 6. Uptime monitor (keeps the API awake)

Free Render web services sleep after about 15 minutes without traffic. A ping every 5 minutes keeps the API awake. One always-on free service fits in Render's monthly free instance hours.

1. Sign up at [uptimerobot.com](https://uptimerobot.com) (free plan).
2. Click **Add New Monitor**:
   - Type **HTTP(s)**.
   - URL `https://<api>/health`.
   - Interval **5 minutes**.
   - Method: HEAD or GET (both return 200).
3. Optional: add an alert contact (email) so you hear when the demo is down.

`/health` doesn't query the database, so the pings don't keep Neon awake and its free compute can still suspend when nobody is using the demo.

## 7. Finish the README

In the README's **Live demo** section:
- Replace the placeholder link with the real frontend URL.
- Replace "_to be added when the demo goes live_" with the three demo passwords (or say where to find them).

## 8. Check it works

- [ ] `https://<api>/health` returns 200.
- [ ] Sign in at `https://<frontend>` as each demo user:
  - The rep sees only their own interactions.
  - The manager sees everyone's.
  - The admin also has the **Admin** tab.
- [ ] Log an interaction via the form and via chat. Chat calls Groq, so this confirms the key works.
- [ ] As the admin, approve "CardioMax lunch-and-learn slides". The rep's flag clears.
- [ ] The next morning, the **Nightly demo reset** run is green and the data is back to the seed.

---

## Environment variables

### Backend (Render web service `hcp-crm-api`)

| Variable | Required | Default | Notes |
|---|---|---|---|
| `DATABASE_URL` | yes | (none) | Neon direct connection string. `postgres://` / `postgresql://` and `?sslmode=` are handled; `channel_binding` is dropped. |
| `GROQ_API_KEY` | yes | (none) | Dedicated demo key with a spend limit. |
| `JWT_SECRET` | yes | (none) | 32+ characters. Render generates it. Rotating it signs everyone out. |
| `FRONTEND_ORIGIN` | yes for a deploy | `http://localhost:5173` | Exact frontend origin, no trailing slash. |
| `CORS_ORIGINS` | no | empty | Extra allowed origins, comma-separated. |
| `RUN_MIGRATIONS_ON_STARTUP` | no | `true` | Applies `backend/migrations/*.sql` on startup (idempotent, advisory-locked). |
| `GROQ_MODEL` | no | `openai/gpt-oss-120b` | Agent and recommendations. |
| `GROQ_MODEL_FAST` | no | `openai/gpt-oss-20b` | Extraction and summaries. |
| `GROQ_TRANSCRIBE_MODEL` | no | `whisper-large-v3-turbo` | Voice notes. |
| `ACCESS_TOKEN_MINUTES` | no | `15` | Sign-in token lifetime (1–240). |
| `RATE_LIMIT_LLM_PER_MINUTE` | no | `20` (`render.yaml`: `10`) | AI requests per user per minute. |
| `LLM_DAILY_CAP` | no | `500` (`render.yaml`: `500`) | AI requests per UTC day across all users, counted in the database (survives restarts). `0` = no cap. Users see a friendly message when it is reached. |
| `DEMO_MODE` | no | `false` (`render.yaml`: `true`) | User management becomes read-only, so the demo accounts can't be deactivated, re-roled or have their passwords reset. The UI shows a "Demo mode" banner. |
| `ADMIN_EMAIL`, `ADMIN_PASSWORD` | no | (none) | Extra seed admin created on startup. Not needed for the demo, because demo-admin exists. |
| `DEMO_REP_PASSWORD`, `DEMO_MANAGER_PASSWORD`, `DEMO_ADMIN_PASSWORD` | only for `app.demo_seed` | (none) | 12+ characters each. |
| `PORT` | set by Render | `8000` | The container listens on it. |

`POSTGRES_*` and `BACKEND_PORT` are for local Docker Compose only. Don't set them on Render.

### Frontend (Render static site `hcp-crm-web`)

| Variable | Required | Notes |
|---|---|---|
| `VITE_API_URL` | yes | The API's public URL, no trailing slash. Baked in at build time, so rebuild after changing it. |
| `NODE_VERSION` | no | `22` (set in `render.yaml`). |

### GitHub Actions secrets (nightly reset)

| Secret | Notes |
|---|---|
| `DATABASE_URL` | Same Neon URL as the backend. Without it, the job is skipped. |
| `DEMO_REP_PASSWORD`, `DEMO_MANAGER_PASSWORD`, `DEMO_ADMIN_PASSWORD` | Required when `DATABASE_URL` is set. |

## Troubleshooting

| Symptom | Fix |
|---|---|
| Browser console shows a CORS error | `FRONTEND_ORIGIN` / `CORS_ORIGINS` must match the page origin exactly: scheme, host, no trailing slash. |
| The frontend calls `localhost:8000` | `VITE_API_URL` wasn't set at build time. Set it, then **Clear build cache & deploy**. |
| `Invalid configuration: DATABASE_URL (... sslmode must be one of ...)` | Fix the `sslmode=` value in the URL. |
| `unrecognized configuration parameter` in the logs | An unusual query parameter in `DATABASE_URL`. Remove everything after `?` except `sslmode=require`. |
| The first request takes about 50 s | Free-plan cold start. Check the uptime monitor is running. |
| Nightly reset: `refusing to reset` | The secret points at a database that has users but no demo admin. Check you used the demo database, not a real one. |
| Deploy fails with `Invalid configuration: ... (missing)` | A required variable is empty. The message names it. |
