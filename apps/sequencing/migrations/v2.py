"""Historical Sequencing format 1 to 2: truthful statement or snapshot origins."""


def migrate(connection):
    """Rebuild Task references atomically; retain every other owner table."""
    if connection.in_transaction:
        raise ValueError('Finish the caller transaction before preparing Sequencing storage')
    version = connection.execute('PRAGMA user_version').fetchone()[0]
    if version == 2:
        return
    if version != 1:
        raise ValueError('Select a supported format-1 Sequencing store before migration to format 2')
    if connection.execute('PRAGMA foreign_keys').fetchone()[0] != 1:
        raise ValueError('Enable foreign keys before preparing Sequencing storage')
    connection.execute('PRAGMA foreign_keys=OFF')
    try:
        connection.execute('BEGIN IMMEDIATE')
        connection.execute('''CREATE TABLE _tasks_v2(
            task_id TEXT PRIMARY KEY NOT NULL,
            requirements_snapshot_revision INTEGER,
            requirements_statement_revision INTEGER,
            state TEXT NOT NULL CHECK(state IN ('available','claimed','completed','cancelled')),
            terminal_json TEXT,
            CHECK((requirements_snapshot_revision IS NOT NULL AND requirements_statement_revision IS NULL
                AND typeof(requirements_snapshot_revision)='integer' AND requirements_snapshot_revision>0)
              OR (requirements_statement_revision IS NOT NULL AND requirements_snapshot_revision IS NULL
                AND typeof(requirements_statement_revision)='integer' AND requirements_statement_revision>0))
        )''')
        connection.execute(
            'INSERT INTO _tasks_v2(task_id,requirements_snapshot_revision,requirements_statement_revision,state,terminal_json) '
            'SELECT task_id,requirements_snapshot_revision,NULL,state,terminal_json FROM tasks')
        connection.execute('DROP TABLE tasks')
        connection.execute('ALTER TABLE _tasks_v2 RENAME TO tasks')
        if connection.execute('PRAGMA foreign_key_check').fetchall():
            raise ValueError('Sequencing references must identify their existing owner records')
        connection.execute('PRAGMA user_version=2')
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.execute('PRAGMA foreign_keys=ON')
