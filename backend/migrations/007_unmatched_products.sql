-- The product catalog is admin-only: the form must pick from it, and chat maps
-- product names to the closest catalog entry. Names the agent could not match
-- are kept on the interaction in unmatched_products (never added to the catalog).
--
-- Idempotent. Back up first, then: python -m app.db_migrations apply

BEGIN;

ALTER TABLE interactions ADD COLUMN IF NOT EXISTS unmatched_products JSON NOT NULL DEFAULT '[]'::json;

COMMIT;
