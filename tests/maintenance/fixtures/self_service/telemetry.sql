PRAGMA application_id=1346456881;
PRAGMA user_version=1;
CREATE TABLE accounting_accounts(task_id TEXT PRIMARY KEY,project TEXT NOT NULL,created_at TEXT NOT NULL,data TEXT NOT NULL);
CREATE TABLE accounting_usage(id TEXT PRIMARY KEY,task_id TEXT,project TEXT NOT NULL,source TEXT NOT NULL,stream TEXT NOT NULL,sequence INTEGER NOT NULL,occurred_at TEXT NOT NULL,data TEXT NOT NULL,UNIQUE(source,stream,sequence));
CREATE TABLE accounting_cycles(id TEXT PRIMARY KEY,task_id TEXT,project TEXT NOT NULL,session_id TEXT NOT NULL,started_at TEXT NOT NULL,ended_at TEXT,data TEXT NOT NULL);
CREATE TABLE accounting_credits(seq INTEGER PRIMARY KEY AUTOINCREMENT,id TEXT NOT NULL UNIQUE,task_id TEXT,project TEXT NOT NULL,at TEXT NOT NULL,data TEXT NOT NULL);
CREATE TABLE accounting_findings(task_id TEXT NOT NULL,finding_id TEXT NOT NULL,data TEXT NOT NULL,PRIMARY KEY(task_id,finding_id));
