-- Authentication and row ownership.
--   users: email (unique, case-insensitive), argon2 password_hash, role rep|manager|admin, is_active
--   owner_id on interactions, chat_messages and follow_ups (the rep who created the row)
--   interactions.product_matches: fuzzy product matches from chat, with the rep's original text
--
-- Backfill: every existing row is assigned to the seed admin (ADMIN_EMAIL /
-- ADMIN_PASSWORD). Run this file through the runner, which passes the admin's
-- email and an argon2 hash (never the plain password) as session settings:
--     python -m app.db_migrations apply
-- If rows exist and no seed admin is configured, the migration stops with an
-- error instead of leaving rows without an owner. Idempotent.

BEGIN;

CREATE TABLE IF NOT EXISTS users (
    id VARCHAR(36) NOT NULL,
    email VARCHAR(254) NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    role VARCHAR(20) NOT NULL,
    is_active BOOLEAN DEFAULT true NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT ck_users_role CHECK (role IN ('rep', 'manager', 'admin'))
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_users_email_lower ON users (lower(email));

ALTER TABLE interactions ADD COLUMN IF NOT EXISTS owner_id VARCHAR(36) REFERENCES users (id);
ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS owner_id VARCHAR(36) REFERENCES users (id);
ALTER TABLE follow_ups ADD COLUMN IF NOT EXISTS owner_id VARCHAR(36) REFERENCES users (id);
CREATE INDEX IF NOT EXISTS ix_interactions_owner_id ON interactions (owner_id);
CREATE INDEX IF NOT EXISTS ix_chat_messages_owner_id ON chat_messages (owner_id);
CREATE INDEX IF NOT EXISTS ix_follow_ups_owner_id ON follow_ups (owner_id);

ALTER TABLE interactions ADD COLUMN IF NOT EXISTS product_matches JSON NOT NULL DEFAULT '[]'::json;

DO $$
DECLARE
    admin_email text := nullif(current_setting('app.seed_admin_email', true), '');
    admin_hash text := nullif(current_setting('app.seed_admin_password_hash', true), '');
    admin_id text;
    orphans integer;
BEGIN
    IF admin_email IS NOT NULL THEN
        SELECT id INTO admin_id FROM users WHERE lower(email) = lower(admin_email);
        IF admin_id IS NULL AND admin_hash IS NOT NULL THEN
            admin_id := gen_random_uuid()::text;
            INSERT INTO users (id, email, password_hash, role, is_active, created_at)
            VALUES (admin_id, admin_email, admin_hash, 'admin', true, now());
            RAISE NOTICE 'seed admin % created', admin_email;
        END IF;
    END IF;

    SELECT (SELECT count(*) FROM interactions WHERE owner_id IS NULL)
         + (SELECT count(*) FROM chat_messages WHERE owner_id IS NULL)
         + (SELECT count(*) FROM follow_ups WHERE owner_id IS NULL)
      INTO orphans;

    IF orphans > 0 THEN
        IF admin_id IS NULL THEN
            RAISE EXCEPTION 'migration 008: % existing rows need an owner. Set ADMIN_EMAIL and ADMIN_PASSWORD, then run: python -m app.db_migrations apply', orphans;
        END IF;
        UPDATE interactions SET owner_id = admin_id WHERE owner_id IS NULL;
        UPDATE chat_messages SET owner_id = admin_id WHERE owner_id IS NULL;
        UPDATE follow_ups SET owner_id = admin_id WHERE owner_id IS NULL;
        RAISE NOTICE 'backfilled % rows to seed admin %', orphans, admin_email;
    END IF;
END $$;

ALTER TABLE interactions ALTER COLUMN owner_id SET NOT NULL;
ALTER TABLE chat_messages ALTER COLUMN owner_id SET NOT NULL;
ALTER TABLE follow_ups ALTER COLUMN owner_id SET NOT NULL;

COMMIT;
