"""Installed, versioned schema-2 resource. Never applied to existing storage."""
SQL = """CREATE TABLE registry_schema (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    schema_version INTEGER NOT NULL
);
INSERT INTO registry_schema (singleton, schema_version) VALUES (1, 2);

CREATE TABLE pending_calls (
    session_id TEXT NOT NULL,
    turn_id TEXT NOT NULL,
    tool_use_id TEXT NOT NULL,
    epoch INTEGER NOT NULL,
    plan_hash TEXT NOT NULL,
    plans_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (session_id, tool_use_id)
);

CREATE TABLE read_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    turn_id TEXT NOT NULL,
    tool_use_id TEXT NOT NULL,
    epoch INTEGER NOT NULL,
    committed_at TEXT NOT NULL,
    path_label TEXT NOT NULL,
    version_key TEXT NOT NULL,
    plan_json TEXT NOT NULL,
    operation_key TEXT NOT NULL,
    UNIQUE (session_id, tool_use_id, path_label, operation_key)
);
CREATE INDEX read_events_session_path_version
    ON read_events (session_id, path_label, version_key, epoch);

CREATE TABLE compactions (
    session_id TEXT NOT NULL,
    turn_id TEXT NOT NULL,
    epoch INTEGER NOT NULL,
    trigger TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (session_id, turn_id)
);

CREATE TABLE content_read_cache (
    session_id TEXT NOT NULL,
    epoch INTEGER NOT NULL,
    path_label TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    normalized_range TEXT NOT NULL,
    reader_kind TEXT NOT NULL,
    content_bytes INTEGER NOT NULL,
    content_lines INTEGER NOT NULL,
    first_read_at TEXT NOT NULL,
    PRIMARY KEY (
        session_id, epoch, path_label, content_sha256, normalized_range, reader_kind
    )
);
CREATE INDEX content_read_cache_session_path
    ON content_read_cache (
        session_id, path_label, normalized_range, reader_kind, epoch
    );

CREATE TABLE content_read_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    epoch INTEGER NOT NULL,
    occurred_at TEXT NOT NULL,
    path_label TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    normalized_range TEXT NOT NULL,
    reader_kind TEXT NOT NULL,
    status TEXT NOT NULL,
    classification TEXT NOT NULL,
    content_bytes INTEGER NOT NULL,
    content_lines INTEGER NOT NULL,
    model_content_bytes INTEGER NOT NULL
);
CREATE INDEX content_read_events_session_epoch
    ON content_read_events (session_id, epoch, id);
"""
