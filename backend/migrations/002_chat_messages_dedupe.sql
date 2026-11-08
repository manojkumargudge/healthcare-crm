-- 1) Add chat_messages.tool_call_id (new rows store the LangChain tool_call_id).
-- 2) Remove duplicate tool rows written by the old /api/chat/turn bug, which
--    re-saved every earlier tool message of the session on each new turn.
--
-- Duplicates = same (session_id, role, content, tool_call_id); the earliest
-- row (created_at, then id) is kept. Only role = 'tool' rows are touched:
-- the bug never re-saved user/assistant rows, so identical user/assistant
-- content in a session is a genuinely repeated message and must stay.
--
-- Limitation: rows written before this migration have tool_call_id NULL, so
-- two genuinely separate tool calls with byte-identical output in the same
-- session are indistinguishable from a re-save and collapse to one row.
--
-- Idempotent. Back up first, then run (compose):
--   docker compose exec -T postgres sh -c 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"' \
--     < backend/migrations/002_chat_messages_dedupe.sql

BEGIN;

ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS tool_call_id VARCHAR(100);

DO $$
DECLARE
    before_total integer;
    before_tool integer;
    removed integer;
BEGIN
    SELECT count(*), count(*) FILTER (WHERE role = 'tool') INTO before_total, before_tool FROM chat_messages;

    WITH ranked AS (
        SELECT id,
               row_number() OVER (
                   PARTITION BY session_id, role, content, tool_call_id
                   ORDER BY created_at, id
               ) AS rn
        FROM chat_messages
        WHERE role = 'tool'
    )
    DELETE FROM chat_messages c USING ranked r
    WHERE c.id = r.id AND r.rn > 1;
    GET DIAGNOSTICS removed = ROW_COUNT;

    RAISE NOTICE 'chat_messages: % rows before (% tool), % duplicate tool rows removed, % rows after',
        before_total, before_tool, removed, before_total - removed;
END $$;

COMMIT;
