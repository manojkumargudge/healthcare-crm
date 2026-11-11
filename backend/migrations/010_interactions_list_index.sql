-- Index for the paged interaction history (GET /api/interactions?hcp_id=...):
-- one HCP's interactions, newest first (Postgres scans the index backwards).
--
-- Idempotent. Back up first, then: python -m app.db_migrations apply

BEGIN;

CREATE INDEX IF NOT EXISTS ix_interactions_hcp_date
    ON interactions (hcp_id, interaction_date, created_at);

COMMIT;
