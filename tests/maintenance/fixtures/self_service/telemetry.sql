PRAGMA user_version=13;
CREATE TABLE accounting_usage(id TEXT PRIMARY KEY,project TEXT,source TEXT,sequence INTEGER);
CREATE TABLE accounting_cycles(id TEXT PRIMARY KEY,task_id TEXT,session_id TEXT,started_at TEXT,ended_at TEXT);
