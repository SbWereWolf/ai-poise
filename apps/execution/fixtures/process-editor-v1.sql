CREATE TABLE config_operations(
 request_id TEXT PRIMARY KEY, request_digest TEXT NOT NULL,
 goal_type TEXT NOT NULL, source_revision TEXT, candidate TEXT NOT NULL,
 receipt TEXT NOT NULL, phase TEXT NOT NULL CHECK(phase IN ('pending','completed'))
);
CREATE TABLE config_heads(goal_type TEXT PRIMARY KEY,revision TEXT NOT NULL);
CREATE INDEX config_pending ON config_operations(goal_type,phase);
PRAGMA user_version=1;
