CREATE TABLE meta (key TEXT PRIMARY KEY, value INTEGER NOT NULL);
CREATE TABLE tasks (
 task_id TEXT PRIMARY KEY,
 requirements_snapshot_revision INTEGER NOT NULL,
 state TEXT NOT NULL,
 sprint_id TEXT,
 claim_json TEXT,
 workspace_json TEXT,
 terminal_json TEXT
);
CREATE TABLE requests (
 request_id TEXT PRIMARY KEY,
 digest TEXT NOT NULL,
 response_json TEXT NOT NULL
);
