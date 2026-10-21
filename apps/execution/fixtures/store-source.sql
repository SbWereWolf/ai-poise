CREATE TABLE meta (key TEXT PRIMARY KEY, value INTEGER NOT NULL);
CREATE TABLE executions (
 task_id TEXT PRIMARY KEY, generation INTEGER NOT NULL,
 contract_revision TEXT NOT NULL, contract_json TEXT NOT NULL,
 state TEXT NOT NULL, current_stage TEXT, iteration INTEGER NOT NULL,
 workspace_id TEXT NOT NULL, admission_id TEXT NOT NULL
);
CREATE TABLE requests (
 request_id TEXT PRIMARY KEY, digest TEXT NOT NULL, response_json TEXT NOT NULL
);
PRAGMA user_version=0;
