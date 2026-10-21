"""Historical fresh/T020 Execution format0 to format1; stdlib and local literals only."""
import json


def prepare(connection):
    if connection.in_transaction:
        raise ValueError('Finish the caller transaction before preparing Execution storage')
    version = connection.execute('PRAGMA user_version').fetchone()[0]
    if version == 1:
        return
    if version != 0:
        raise ValueError(f'Unsupported Execution format {version}; select a fresh or T020 format0 own store')
    tables = {row[0] for row in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
    if tables and tables != {'meta', 'executions', 'requests'}:
        raise ValueError('Select a fresh or complete T020 meta/executions/requests store')
    try:
        connection.execute('BEGIN IMMEDIATE')
        if not tables:
            connection.execute('CREATE TABLE meta(key TEXT PRIMARY KEY,value INTEGER NOT NULL)')
            connection.execute('INSERT INTO meta(key,value) VALUES(?,?)', ('revision', 0))
            connection.execute('''CREATE TABLE executions(
                task_id TEXT PRIMARY KEY, generation INTEGER NOT NULL,
                contract_revision TEXT NOT NULL, contract_json TEXT NOT NULL,
                state TEXT NOT NULL, current_stage TEXT, iteration INTEGER NOT NULL,
                workspace_id TEXT NOT NULL, admission_id TEXT NOT NULL)''')
            connection.execute('CREATE TABLE requests(request_id TEXT PRIMARY KEY,digest TEXT NOT NULL,response_json TEXT NOT NULL)')
        connection.execute('''CREATE TABLE execution_records(
            seq INTEGER PRIMARY KEY, task_id TEXT NOT NULL REFERENCES executions(task_id),
            kind TEXT NOT NULL, record_id TEXT NOT NULL, record_revision INTEGER NOT NULL,
            generation INTEGER NOT NULL, data_json TEXT NOT NULL,
            UNIQUE(task_id,kind,record_id,record_revision))''')
        connection.execute('CREATE INDEX execution_records_task_order ON execution_records(task_id,seq)')
        rows = connection.execute(
            'SELECT task_id,generation,contract_revision,contract_json FROM executions ORDER BY task_id').fetchall()
        for task_id, generation, revision, contract_json in rows:
            try:
                contract = json.loads(contract_json)
            except (TypeError, ValueError) as error:
                raise ValueError(f'T020 execution {task_id} needs a valid stored contract document') from error
            document = json.dumps({'contract_revision': revision, 'contract': contract},
                                  ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
            connection.execute(
                'INSERT INTO execution_records(task_id,kind,record_id,record_revision,generation,data_json) VALUES(?,?,?,?,?,?)',
                (task_id, 'contract_snapshot', f'contract:{task_id}:{generation}', 1, generation, document))
        connection.execute('PRAGMA user_version=1')
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
