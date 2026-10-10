CREATE TABLE requirements_meta(revision INTEGER NOT NULL);
CREATE TABLE requirements(
  id TEXT PRIMARY KEY, level TEXT NOT NULL, status TEXT NOT NULL, text TEXT NOT NULL
);
CREATE TABLE requirement_links(
  system_id TEXT NOT NULL REFERENCES requirements(id),
  application_id TEXT NOT NULL REFERENCES requirements(id),
  PRIMARY KEY(system_id,application_id)
);
CREATE TABLE requirement_requests(
  request_id TEXT PRIMARY KEY, digest TEXT NOT NULL, response TEXT NOT NULL
);
PRAGMA user_version=1;
