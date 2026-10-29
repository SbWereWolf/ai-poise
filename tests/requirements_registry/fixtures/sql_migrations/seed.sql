INSERT INTO requirements(id,level,status,text) VALUES('S1','system','current','System one.');
INSERT INTO requirements(id,level,status,text) VALUES('A1','application','future','Application one.');
INSERT INTO domain_areas(id,business_name,definition) VALUES('D1','Domain one','Domain fixture.');
INSERT INTO domain_entities(id,domain_id,business_name,code_name,definition) VALUES('E1','D1','Entity one','entity_one','Entity fixture.');
INSERT INTO domain_actions(id,domain_id,business_name,code_name,definition) VALUES('X1','D1','Action one','action_one','Action fixture.');
