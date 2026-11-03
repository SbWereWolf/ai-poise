from yoyo import step

__depends__ = {'0001'}
__transactional__ = True
steps = [
    step(
        "INSERT INTO requirements(id,level,status,text) VALUES ('SYS-TASK-ADDRESSED-READ','system','future','Участник получает сведения о выбранной задаче и её текущей работе с сохранением действующих полномочий участников.');",
        "DELETE FROM requirements WHERE id='SYS-TASK-ADDRESSED-READ';",
    ),
    step(
        "INSERT INTO requirements(id,level,status,text) VALUES ('APP-POISE-TASK-ADDRESSED-READ','application','future','При обращении по идентификатору задачи приложение предоставляет сведения о ней и применимом рабочем контексте с сохранением действующего владения; для отсутствующей задачи сообщает отсутствие и способ продолжения.');",
        "DELETE FROM requirements WHERE id='APP-POISE-TASK-ADDRESSED-READ';",
    ),
    step(
        "INSERT INTO requirement_links(system_id,application_id) VALUES ('SYS-TASK-ADDRESSED-READ','APP-POISE-TASK-ADDRESSED-READ');",
        "DELETE FROM requirement_links WHERE system_id='SYS-TASK-ADDRESSED-READ' AND application_id='APP-POISE-TASK-ADDRESSED-READ';",
    ),
    step(
        "UPDATE requirements_meta SET revision=54 WHERE revision=53;",
        "UPDATE requirements_meta SET revision=53 WHERE revision=54;",
    ),
]
