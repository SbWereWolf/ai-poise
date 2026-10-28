PRAGMA user_version=2;
CREATE TABLE requirements_meta(revision INTEGER);
INSERT INTO requirements_meta(revision) VALUES(0);
CREATE TABLE requirements(id TEXT PRIMARY KEY,level TEXT,status TEXT,text TEXT);
CREATE TABLE requirement_links(system_id TEXT,application_id TEXT);
CREATE TABLE domain_areas(id TEXT PRIMARY KEY,business_name TEXT,definition TEXT);
