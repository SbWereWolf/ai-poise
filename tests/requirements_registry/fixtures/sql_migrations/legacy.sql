INSERT INTO requirement_links(system_id,application_id) VALUES('S1','A1');
INSERT INTO requirement_entities(requirement_id,domain_id,entity_id) VALUES('A1','D1','E1');
INSERT INTO requirement_actions(requirement_id,domain_id,action_id) VALUES('A1','D1','X1');
UPDATE domain_areas SET definition=CAST(X'410042' AS TEXT) WHERE id='D1';
INSERT INTO requirement_requests(request_id,digest,response)
SELECT 'source-only',digest,response FROM requirement_requests ORDER BY request_id LIMIT 1;
