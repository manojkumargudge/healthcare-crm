-- Convert naive UTC timestamps to timestamptz.
--
-- Needed once for databases created before timezone-aware datetimes were
-- introduced: the app now writes aware UTC datetimes, which asyncpg refuses
-- to bind to `timestamp without time zone` columns. New databases created by
-- the app's startup `create_all` already have timestamptz and need nothing.
--
-- Existing values were written with datetime.utcnow(), so they are
-- interpreted AT TIME ZONE 'UTC'. Idempotent: only alters columns that are
-- still `timestamp without time zone`, so it is safe to run more than once.
--
-- Run (compose):
--   docker compose exec -T postgres sh -c 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"' \
--     < backend/migrations/001_timestamps_to_timestamptz.sql

DO $$
DECLARE
    col record;
BEGIN
    FOR col IN
        SELECT c.table_name, c.column_name
        FROM information_schema.columns c
        WHERE c.table_schema = current_schema()
          AND c.data_type = 'timestamp without time zone'
          AND (c.table_name, c.column_name) IN (
              ('hcps', 'created_at'),
              ('interactions', 'created_at'),
              ('interactions', 'updated_at'),
              ('follow_ups', 'created_at'),
              ('chat_messages', 'created_at')
          )
    LOOP
        EXECUTE format(
            'ALTER TABLE %I ALTER COLUMN %I TYPE timestamptz USING %I AT TIME ZONE ''UTC''',
            col.table_name, col.column_name, col.column_name
        );
        RAISE NOTICE 'converted %.% to timestamptz', col.table_name, col.column_name;
    END LOOP;
END $$;
