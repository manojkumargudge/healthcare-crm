-- Baseline: the schema the app's first version created with create_all
-- (generated from the models at commit 727cd3f with SQLAlchemy's PostgreSQL
-- DDL compiler). Migrations 001+ evolve it to the current models.
--
-- Running 000..N in order on an EMPTY database must produce exactly the
-- schema of app/models.py; CI checks this (python -m app.db_migrations check-fresh).
-- On an existing database every statement here is a no-op (IF NOT EXISTS).

BEGIN;

CREATE TABLE IF NOT EXISTS hcps (
    id VARCHAR(36) NOT NULL,
    name VARCHAR(150) NOT NULL,
    specialty VARCHAR(100),
    institution VARCHAR(200),
    email VARCHAR(150),
    phone VARCHAR(30),
    state VARCHAR(50),
    tier VARCHAR(20) NOT NULL,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    PRIMARY KEY (id)
);

CREATE TABLE IF NOT EXISTS interactions (
    id VARCHAR(36) NOT NULL,
    hcp_id VARCHAR(36) NOT NULL,
    interaction_type VARCHAR(50) NOT NULL,
    channel VARCHAR(20) NOT NULL,
    interaction_date DATE NOT NULL,
    products_discussed JSON NOT NULL,
    samples_dropped JSON NOT NULL,
    materials_shared JSON NOT NULL,
    key_topics JSON NOT NULL,
    hcp_sentiment VARCHAR(20),
    sentiment_score FLOAT,
    raw_notes TEXT,
    ai_summary TEXT,
    next_best_action TEXT,
    compliance_flag BOOLEAN NOT NULL,
    compliance_notes TEXT,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    edit_history JSON NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY (hcp_id) REFERENCES hcps (id)
);

CREATE TABLE IF NOT EXISTS follow_ups (
    id VARCHAR(36) NOT NULL,
    hcp_id VARCHAR(36) NOT NULL,
    interaction_id VARCHAR(36),
    due_date DATE NOT NULL,
    reason TEXT NOT NULL,
    status VARCHAR(20) NOT NULL,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY (hcp_id) REFERENCES hcps (id),
    FOREIGN KEY (interaction_id) REFERENCES interactions (id)
);

CREATE TABLE IF NOT EXISTS chat_messages (
    id VARCHAR(36) NOT NULL,
    session_id VARCHAR(36) NOT NULL,
    role VARCHAR(20) NOT NULL,
    content TEXT NOT NULL,
    tool_name VARCHAR(50),
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    PRIMARY KEY (id)
);
CREATE INDEX IF NOT EXISTS ix_chat_messages_session_id ON chat_messages (session_id);

COMMIT;
