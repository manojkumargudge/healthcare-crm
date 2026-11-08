-- 1) interactions.attendees: JSON list of names (empty list for existing rows).
-- 2) materials: catalog for the "Materials shared" picker, unique by lower(name).
-- 3) Copy the existing free-text materials_shared tags of every interaction
--    into the catalog (trimmed, de-duplicated case-insensitively). The tags on
--    interactions are left as they are; they already equal the catalog names.
--
-- The starter catalog (app/catalog.py SEED_MATERIALS) is inserted by the
-- backend on startup, so it is not duplicated here. New databases get the
-- same schema from create_all and need nothing from this file.
--
-- Idempotent. Back up first, then run (compose):
--   docker compose exec -T postgres sh -c 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"' \
--     < backend/migrations/003_attendees_materials.sql

BEGIN;

ALTER TABLE interactions ADD COLUMN IF NOT EXISTS attendees JSON;
UPDATE interactions SET attendees = '[]'::json WHERE attendees IS NULL;

CREATE TABLE IF NOT EXISTS materials (
    id VARCHAR(36) PRIMARY KEY,
    name VARCHAR(200) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_materials_name_lower ON materials (lower(name));

DO $$
DECLARE
    before_count integer;
    tag_count integer;
    added integer;
BEGIN
    SELECT count(*) INTO before_count FROM materials;

    SELECT count(DISTINCT lower(btrim(m))) INTO tag_count
    FROM interactions i, json_array_elements_text(i.materials_shared) AS m
    WHERE json_typeof(i.materials_shared) = 'array' AND btrim(m) <> '';

    INSERT INTO materials (id, name, created_at)
    SELECT gen_random_uuid()::text, t.name, now()
    FROM (
        SELECT DISTINCT ON (lower(btrim(m))) left(btrim(m), 200) AS name
        FROM interactions i, json_array_elements_text(i.materials_shared) AS m
        WHERE json_typeof(i.materials_shared) = 'array' AND btrim(m) <> ''
        ORDER BY lower(btrim(m)), btrim(m)
    ) t
    ON CONFLICT DO NOTHING;
    GET DIAGNOSTICS added = ROW_COUNT;

    RAISE NOTICE 'materials: % before, % distinct existing tags, % added, % after',
        before_count, tag_count, added, before_count + added;
END $$;

COMMIT;
