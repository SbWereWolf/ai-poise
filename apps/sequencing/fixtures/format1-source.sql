CREATE TABLE meta(key TEXT PRIMARY KEY,value INTEGER NOT NULL);
CREATE TABLE tasks(
 task_id TEXT PRIMARY KEY NOT NULL,
 requirements_snapshot_revision INTEGER NOT NULL CHECK(typeof(requirements_snapshot_revision)='integer' AND requirements_snapshot_revision>0),
 state TEXT NOT NULL CHECK(state IN ('available','claimed','completed','cancelled')),
 terminal_json TEXT
);
CREATE TABLE requests(request_id TEXT PRIMARY KEY,digest TEXT NOT NULL,response_json TEXT NOT NULL);
CREATE TABLE sprints(
 sprint_id TEXT PRIMARY KEY NOT NULL,
 revision INTEGER NOT NULL CHECK(typeof(revision)='integer' AND revision>0),
 state TEXT NOT NULL
);
CREATE TABLE sprint_revisions(
 sprint_id TEXT NOT NULL REFERENCES sprints(sprint_id),
 revision INTEGER NOT NULL CHECK(typeof(revision)='integer' AND revision>0),
 state TEXT NOT NULL,data TEXT NOT NULL,PRIMARY KEY(sprint_id,revision)
);
CREATE TABLE sprint_members(
 sprint_id TEXT NOT NULL REFERENCES sprints(sprint_id),
 task_id TEXT NOT NULL UNIQUE REFERENCES tasks(task_id),
 PRIMARY KEY(sprint_id,task_id)
);
CREATE TABLE sprint_dependencies(
 sprint_id TEXT NOT NULL,predecessor TEXT NOT NULL,successor TEXT NOT NULL,
 kind TEXT NOT NULL CHECK(kind IN ('completion','result')),
 PRIMARY KEY(sprint_id,predecessor,successor),
 FOREIGN KEY(sprint_id,predecessor) REFERENCES sprint_members(sprint_id,task_id),
 FOREIGN KEY(sprint_id,successor) REFERENCES sprint_members(sprint_id,task_id),
 CHECK(predecessor<>successor)
);
CREATE TABLE task_claims(
 claim_id TEXT PRIMARY KEY NOT NULL,task_id TEXT NOT NULL REFERENCES tasks(task_id),
 claimant TEXT NOT NULL,status TEXT NOT NULL CHECK(status IN ('active','released'))
);
CREATE TABLE workspaces(
 workspace_id TEXT PRIMARY KEY NOT NULL,task_id TEXT NOT NULL REFERENCES tasks(task_id),
 repository_id TEXT NOT NULL,base_commit TEXT NOT NULL,branch TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('reserved','ready','release_pending','released','failed')),
 read_only INTEGER NOT NULL CHECK(read_only IN (0,1))
);
CREATE TABLE workspace_roots(
 workspace_id TEXT NOT NULL REFERENCES workspaces(workspace_id),
 kind TEXT NOT NULL CHECK(kind IN ('code','main','artifact')),
 position INTEGER NOT NULL CHECK(typeof(position)='integer' AND position>=0),
 path TEXT NOT NULL,PRIMARY KEY(workspace_id,kind,position)
);
CREATE TABLE workspace_bindings(
 workspace_id TEXT PRIMARY KEY NOT NULL REFERENCES workspaces(workspace_id),
 session_id TEXT NOT NULL UNIQUE
);
CREATE TABLE sequencing_history(
 seq INTEGER PRIMARY KEY,task_id TEXT REFERENCES tasks(task_id),
 sprint_id TEXT REFERENCES sprints(sprint_id),workspace_id TEXT REFERENCES workspaces(workspace_id),
 actor TEXT NOT NULL,event TEXT NOT NULL,data TEXT NOT NULL,at TEXT NOT NULL
);
CREATE UNIQUE INDEX active_task_claim ON task_claims(task_id) WHERE status='active';
CREATE UNIQUE INDEX active_session_claim ON task_claims(claimant) WHERE status='active';
CREATE UNIQUE INDEX current_task_workspace ON workspaces(task_id) WHERE status<>'released';
PRAGMA user_version=1;
