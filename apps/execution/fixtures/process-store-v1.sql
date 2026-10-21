CREATE TABLE meta(key TEXT PRIMARY KEY,value INTEGER NOT NULL);
CREATE TABLE executions(
 task_id TEXT PRIMARY KEY, generation INTEGER NOT NULL,
 contract_revision TEXT NOT NULL, contract_json TEXT NOT NULL,
 state TEXT NOT NULL, current_stage TEXT, iteration INTEGER NOT NULL,
 workspace_id TEXT NOT NULL, admission_id TEXT NOT NULL
);
CREATE TABLE requests(request_id TEXT PRIMARY KEY,digest TEXT NOT NULL,response_json TEXT NOT NULL);
CREATE TABLE execution_records(
 seq INTEGER PRIMARY KEY, task_id TEXT NOT NULL REFERENCES executions(task_id),
 kind TEXT NOT NULL, record_id TEXT NOT NULL, record_revision INTEGER NOT NULL,
 generation INTEGER NOT NULL, data_json TEXT NOT NULL,
 UNIQUE(task_id,kind,record_id,record_revision)
);
CREATE INDEX execution_records_task_order ON execution_records(task_id,seq);
PRAGMA user_version=1;
