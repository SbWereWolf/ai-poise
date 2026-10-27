PRAGMA user_version=13;
CREATE TABLE tasks(id TEXT PRIMARY KEY,status TEXT,claimed_by TEXT,version INTEGER);
CREATE TABLE sessions(id TEXT PRIMARY KEY,task_id TEXT);
CREATE TABLE submissions(seq INTEGER PRIMARY KEY,task_id TEXT,stage TEXT,iteration INTEGER);
