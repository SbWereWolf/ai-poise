DELETE FROM requirement_actions;
DELETE FROM requirement_entities;
DELETE FROM requirement_links;
DELETE FROM domain_actions;
DELETE FROM domain_entities;
DELETE FROM domain_areas;
DELETE FROM requirement_requests;
DELETE FROM requirements;
UPDATE requirements_meta SET revision=0;
