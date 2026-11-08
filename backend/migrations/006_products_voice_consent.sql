-- 1) products: catalog shared by "Products Discussed" and sample products,
--    unique by lower(name). Backfilled from existing interactions
--    (products_discussed and samples_dropped[].product). The starter list is
--    inserted by the backend on startup (app/catalog.py SEED_PRODUCTS).
-- 2) Voice-note consent on interactions: voice_note_consent (false by default)
--    and voice_note_consent_at (when consent was given). Audio is never stored.
--
-- Idempotent. Back up first, then: python -m app.db_migrations apply

BEGIN;

CREATE TABLE IF NOT EXISTS products (
    id VARCHAR(36) PRIMARY KEY,
    name VARCHAR(200) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_products_name_lower ON products (lower(name));

DO $$
DECLARE
    added integer;
BEGIN
    INSERT INTO products (id, name, created_at)
    SELECT gen_random_uuid()::text, t.name, now()
    FROM (
        SELECT DISTINCT ON (lower(n)) n AS name
        FROM (
            SELECT left(btrim(p), 200) AS n
            FROM interactions i, json_array_elements_text(i.products_discussed) AS p
            WHERE json_typeof(i.products_discussed) = 'array'
            UNION ALL
            SELECT left(btrim(s ->> 'product'), 200)
            FROM interactions i, json_array_elements(i.samples_dropped) AS s
            WHERE json_typeof(i.samples_dropped) = 'array' AND json_typeof(s) = 'object'
        ) names
        WHERE n IS NOT NULL AND n <> ''
        ORDER BY lower(n), n
    ) t
    ON CONFLICT DO NOTHING;
    GET DIAGNOSTICS added = ROW_COUNT;
    RAISE NOTICE 'products: % added from existing interactions', added;
END $$;

ALTER TABLE interactions ADD COLUMN IF NOT EXISTS voice_note_consent BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE interactions ADD COLUMN IF NOT EXISTS voice_note_consent_at TIMESTAMP WITH TIME ZONE;

COMMIT;
