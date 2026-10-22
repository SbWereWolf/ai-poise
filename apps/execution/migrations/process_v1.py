"""Initial own process editor schema; independent of core store format."""


def prepare(connection):
    version = connection.execute('PRAGMA process_editor.user_version').fetchone()[0]
    if version == 1:
        return
    if version != 0:
        raise ValueError(f'Unsupported process editor schema version {version}')
    tables = connection.execute(
        'SELECT name FROM process_editor.sqlite_master WHERE type=? AND name NOT LIKE ?',
        ('table', 'sqlite_%')).fetchall()
    if tables:
        raise ValueError('Process editor schema version0 must be empty')
    try:
        connection.execute('BEGIN IMMEDIATE')
        connection.execute('''CREATE TABLE process_editor.config_operations(
            request_id TEXT PRIMARY KEY, request_digest TEXT NOT NULL,
            goal_type TEXT NOT NULL, source_revision TEXT, candidate TEXT NOT NULL,
            receipt TEXT NOT NULL, phase TEXT NOT NULL)''')
        connection.execute('CREATE TABLE process_editor.config_heads(goal_type TEXT PRIMARY KEY,revision TEXT NOT NULL)')
        connection.execute('CREATE INDEX process_editor.config_operations_goal ON config_operations(goal_type,phase)')
        connection.execute('PRAGMA process_editor.user_version=1')
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
