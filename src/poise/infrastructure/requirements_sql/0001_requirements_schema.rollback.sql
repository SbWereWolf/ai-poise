-- Restore the empty application schema.
DROP TABLE requirement_actions;
DROP TABLE requirement_entities;
DROP TABLE domain_actions;
DROP TABLE domain_entities;
DROP TABLE domain_areas;
DROP TABLE requirement_links;
DROP TABLE requirement_requests;
DROP TABLE requirements;
DROP TABLE requirements_meta;
PRAGMA user_version=0;
