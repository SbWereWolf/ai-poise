from yoyo import step

__depends__ = set()
__transactional__ = True
steps = [
    step("INSERT INTO requirements_meta(revision) VALUES(17)", "DELETE FROM requirements_meta"),
    step("INSERT INTO requirements(id,level,status,text) VALUES('SYS-PRIVATE-ENV','system','current','Тестовое окружение')", "DELETE FROM requirements WHERE id='SYS-PRIVATE-ENV'"),
    step("INSERT INTO requirements(id,level,status,text) VALUES('APP-PRIVATE-ENV','application','current','Тестовая подготовка окружения')", "DELETE FROM requirements WHERE id='APP-PRIVATE-ENV'"),
    step("INSERT INTO requirement_links(system_id,application_id) VALUES('SYS-PRIVATE-ENV','APP-PRIVATE-ENV')", "DELETE FROM requirement_links WHERE system_id='SYS-PRIVATE-ENV' AND application_id='APP-PRIVATE-ENV'"),
]
