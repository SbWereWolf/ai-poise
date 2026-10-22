"""Execution format1→2: an explicitly optional Workspace, preserving all records."""


def prepare(connection):
    if connection.in_transaction:
        raise ValueError('Finish the caller transaction before preparing Execution storage')
    version = connection.execute('PRAGMA user_version').fetchone()[0]
    if version == 2:
        return
    if version != 1:
        raise ValueError(f'Unsupported Execution format {version}')
    connection.execute('PRAGMA foreign_keys=OFF')
    try:
        connection.execute('BEGIN IMMEDIATE')
        connection.execute('''CREATE TABLE executions_v2(
            task_id TEXT PRIMARY KEY, generation INTEGER NOT NULL,
            contract_revision TEXT NOT NULL, contract_json TEXT NOT NULL,
            state TEXT NOT NULL, current_stage TEXT, iteration INTEGER NOT NULL,
            workspace_id TEXT, admission_id TEXT NOT NULL)''')
        connection.execute('''INSERT INTO executions_v2(
            task_id,generation,contract_revision,contract_json,state,current_stage,
            iteration,workspace_id,admission_id)
            SELECT task_id,generation,contract_revision,contract_json,state,current_stage,
            iteration,workspace_id,admission_id FROM executions''')
        connection.execute('DROP TABLE executions')
        connection.execute('ALTER TABLE executions_v2 RENAME TO executions')
        connection.execute('PRAGMA user_version=2')
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.execute('PRAGMA foreign_keys=ON')
