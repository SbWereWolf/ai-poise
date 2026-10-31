-- transactional: true
INSERT INTO requirements(id,level,status,text,note) VALUES('SYS-A','system','current','Кавычка '' и точка; COMMIT
ROLLBACK остаются текстом','Система; новая колонка '' B');
INSERT INTO requirements(id,level,status,text,note) VALUES('APP-A','application','current','Application A','Application B value');
INSERT INTO requirements(id,level,status,text,note) VALUES('SYS-UNRELATED','system','current','Excluded from partial transfer','Unrelated preserved B value');
INSERT INTO requirements(id,level,status,text,note) VALUES('TEMP','system','current','before','New-schema value');
INSERT INTO domain_areas(id,business_name,definition) VALUES('D-A','Планирование','Область A');
INSERT INTO domain_entities(id,domain_id,business_name,code_name,definition) VALUES('E-A','D-A','Документ','document','Сущность A');
INSERT INTO domain_actions(id,domain_id,business_name,code_name,definition) VALUES('ACT-A','D-A','Согласовать','approve','Действие A');
INSERT INTO requirement_links(system_id,application_id) VALUES('SYS-A','APP-A');
INSERT INTO requirement_entities(requirement_id,domain_id,entity_id) VALUES('APP-A','D-A','E-A');
INSERT INTO requirement_actions(requirement_id,domain_id,action_id) VALUES('APP-A','D-A','ACT-A');
INSERT INTO requirement_requests(request_id,digest,response) VALUES('request-A','digest-A','{"text":"Служебное; значение '' COMMIT"}');
UPDATE requirements_meta SET revision=17;
