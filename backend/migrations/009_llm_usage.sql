-- Global daily cap on AI requests (LLM_DAILY_CAP): one counter row per UTC day,
-- shared by every backend instance and kept across restarts.
--
-- Idempotent. Back up first, then: python -m app.db_migrations apply

BEGIN;

CREATE TABLE IF NOT EXISTS llm_usage (
    day DATE PRIMARY KEY,
    calls INTEGER NOT NULL DEFAULT 0
);

COMMIT;
