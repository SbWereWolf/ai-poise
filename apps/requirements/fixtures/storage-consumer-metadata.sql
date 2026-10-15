-- Independent ordinary metadata arrangement for the public consumer checks.
CREATE TABLE domain_areas(id TEXT PRIMARY KEY, business_name TEXT NOT NULL, definition TEXT NOT NULL);
CREATE TABLE domain_entities(id TEXT PRIMARY KEY, domain_id TEXT NOT NULL REFERENCES domain_areas(id), business_name TEXT NOT NULL, code_name TEXT NOT NULL, definition TEXT NOT NULL, UNIQUE(domain_id,id));
CREATE TABLE domain_actions(id TEXT PRIMARY KEY, domain_id TEXT NOT NULL REFERENCES domain_areas(id), business_name TEXT NOT NULL, code_name TEXT NOT NULL, definition TEXT NOT NULL, UNIQUE(domain_id,id));
CREATE TABLE requirement_entities(requirement_id TEXT NOT NULL REFERENCES requirements(id), domain_id TEXT NOT NULL, entity_id TEXT NOT NULL, PRIMARY KEY(requirement_id,domain_id,entity_id), FOREIGN KEY(domain_id,entity_id) REFERENCES domain_entities(domain_id,id));
CREATE TABLE requirement_actions(requirement_id TEXT NOT NULL REFERENCES requirements(id), domain_id TEXT NOT NULL, action_id TEXT NOT NULL, PRIMARY KEY(requirement_id,domain_id,action_id), FOREIGN KEY(domain_id,action_id) REFERENCES domain_actions(domain_id,id));
