-- 1) materials.approved: catalog entries need compliance approval before use.
--    When the column is first added, every existing row (the starter catalog
--    and the tags migrated by 003) is marked approved. Materials added later
--    by reps start as approved = false ("Pending approval").
-- 2) Tighten nullability added by 003 to match the models (drift check):
--    interactions.attendees and materials.created_at become NOT NULL.
--
-- Idempotent: re-running never re-approves rep-added materials, because the
-- approve-all step only runs in the same transaction that adds the column.
--
-- Back up first, then run (compose):
--   docker compose exec -T postgres sh -c 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"' \
--     < backend/migrations/004_materials_approval.sql

BEGIN;

DO $$
DECLARE
    approved_count integer;
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = current_schema() AND table_name = 'materials' AND column_name = 'approved'
    ) THEN
        ALTER TABLE materials ADD COLUMN approved BOOLEAN NOT NULL DEFAULT false;
        UPDATE materials SET approved = true;
        GET DIAGNOSTICS approved_count = ROW_COUNT;
        RAISE NOTICE 'materials.approved added; % existing materials marked approved', approved_count;
    ELSE
        RAISE NOTICE 'materials.approved already present; nothing re-approved';
    END IF;
END $$;

UPDATE materials SET created_at = now() WHERE created_at IS NULL;
ALTER TABLE materials ALTER COLUMN created_at SET NOT NULL;

UPDATE interactions SET attendees = '[]'::json WHERE attendees IS NULL;
ALTER TABLE interactions ALTER COLUMN attendees SET NOT NULL;

COMMIT;
