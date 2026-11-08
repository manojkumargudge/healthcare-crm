-- Fields from the brief mockup's "Interaction Details" form:
--   interactions.interaction_time   (Time, next to Date)
--   interactions.outcomes           ("Outcomes": key outcomes or agreements)
--   interactions.follow_up_actions  ("Follow-up Actions": next steps or tasks, free text)
-- All nullable; existing rows keep NULL. Idempotent.
--
-- Back up first, then: python -m app.db_migrations apply

BEGIN;

ALTER TABLE interactions ADD COLUMN IF NOT EXISTS interaction_time TIME WITHOUT TIME ZONE;
ALTER TABLE interactions ADD COLUMN IF NOT EXISTS outcomes TEXT;
ALTER TABLE interactions ADD COLUMN IF NOT EXISTS follow_up_actions TEXT;

COMMIT;
