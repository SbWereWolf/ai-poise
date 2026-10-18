"""Self-contained historical Sequencing storage migration, format 0 to 1."""


_PRECURSOR = {
    'meta': {'key', 'value'},
    'tasks': {'task_id', 'requirements_snapshot_revision', 'state', 'sprint_id',
              'claim_json', 'workspace_json', 'terminal_json'},
    'requests': {'request_id', 'digest', 'response_json'},
}

_DDL = {
    'meta': 'CREATE TABLE meta(key TEXT PRIMARY KEY,value INTEGER NOT NULL)',
    'tasks': '''CREATE TABLE tasks(
        task_id TEXT PRIMARY KEY NOT NULL,
        requirements_snapshot_revision INTEGER NOT NULL CHECK(requirements_snapshot_revision>0),
        state TEXT NOT NULL CHECK(state IN ('available','claimed','completed','cancelled')),
        terminal_json TEXT
    )''',
    'requests': '''CREATE TABLE requests(
        request_id TEXT PRIMARY KEY,digest TEXT NOT NULL,response_json TEXT NOT NULL
    )''',
    'sprints': '''CREATE TABLE sprints(
        sprint_id TEXT PRIMARY KEY NOT NULL,
        revision INTEGER NOT NULL CHECK(revision>0),
        state TEXT NOT NULL
    )''',
    'sprint_revisions': '''CREATE TABLE sprint_revisions(
        sprint_id TEXT NOT NULL REFERENCES sprints(sprint_id),
        revision INTEGER NOT NULL CHECK(revision>0),
        state TEXT NOT NULL,
        data TEXT NOT NULL,
        PRIMARY KEY(sprint_id,revision)
    )''',
    'sprint_members': '''CREATE TABLE sprint_members(
        sprint_id TEXT NOT NULL REFERENCES sprints(sprint_id),
        task_id TEXT NOT NULL UNIQUE REFERENCES tasks(task_id),
        PRIMARY KEY(sprint_id,task_id)
    )''',
    'sprint_dependencies': '''CREATE TABLE sprint_dependencies(
        sprint_id TEXT NOT NULL,
        predecessor TEXT NOT NULL,
        successor TEXT NOT NULL,
        kind TEXT NOT NULL CHECK(kind IN ('completion','result')),
        PRIMARY KEY(sprint_id,predecessor,successor),
        FOREIGN KEY(sprint_id,predecessor) REFERENCES sprint_members(sprint_id,task_id),
        FOREIGN KEY(sprint_id,successor) REFERENCES sprint_members(sprint_id,task_id),
        CHECK(predecessor<>successor)
    )''',
    'task_claims': '''CREATE TABLE task_claims(
        claim_id TEXT PRIMARY KEY NOT NULL,
        task_id TEXT NOT NULL REFERENCES tasks(task_id),
        claimant TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('active','released'))
    )''',
    'workspaces': '''CREATE TABLE workspaces(
        workspace_id TEXT PRIMARY KEY NOT NULL,
        task_id TEXT NOT NULL REFERENCES tasks(task_id),
        repository_id TEXT NOT NULL,
        base_commit TEXT NOT NULL,
        branch TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('reserved','ready','release_pending','released','failed')),
        read_only INTEGER NOT NULL CHECK(read_only IN (0,1))
    )''',
    'workspace_roots': '''CREATE TABLE workspace_roots(
        workspace_id TEXT NOT NULL REFERENCES workspaces(workspace_id),
        kind TEXT NOT NULL CHECK(kind IN ('code','main','artifact')),
        position INTEGER NOT NULL CHECK(position>=0),
        path TEXT NOT NULL,
        PRIMARY KEY(workspace_id,kind,position)
    )''',
    'workspace_bindings': '''CREATE TABLE workspace_bindings(
        workspace_id TEXT PRIMARY KEY NOT NULL REFERENCES workspaces(workspace_id),
        session_id TEXT NOT NULL UNIQUE
    )''',
    'sequencing_history': '''CREATE TABLE sequencing_history(
        seq INTEGER PRIMARY KEY,
        task_id TEXT REFERENCES tasks(task_id),
        sprint_id TEXT REFERENCES sprints(sprint_id),
        workspace_id TEXT REFERENCES workspaces(workspace_id),
        actor TEXT NOT NULL,
        event TEXT NOT NULL,
        data TEXT NOT NULL,
        at TEXT NOT NULL
    )''',
}

_INDEXES = (
    "CREATE UNIQUE INDEX active_task_claim ON task_claims(task_id) WHERE status='active'",
    "CREATE UNIQUE INDEX active_session_claim ON task_claims(claimant) WHERE status='active'",
    "CREATE UNIQUE INDEX current_task_workspace ON workspaces(task_id) WHERE status<>'released'",
)


def _source(connection):
    version = connection.execute('PRAGMA user_version').fetchone()[0]
    if version not in (0, 1):
        raise ValueError('Unsupported Sequencing format; select a format-1 store or a supported format-0 precursor')
    if version == 1:
        return 'current'
    tables = {row[0] for row in connection.execute(
        'SELECT name FROM sqlite_master WHERE type=? AND name NOT LIKE ?', ('table', 'sqlite_%'))}
    if not tables:
        return 'fresh'
    if tables != set(_PRECURSOR):
        raise ValueError('Unsupported Sequencing source; select an empty database or the three-table standalone precursor')
    for table, expected in _PRECURSOR.items():
        columns = {row[0] for row in connection.execute('SELECT name FROM pragma_table_info(?)', (table,))}
        if columns != expected:
            raise ValueError('Unsupported Sequencing source columns; use the original three-table standalone precursor')
    revision = connection.execute('SELECT value FROM meta WHERE key=?', ('revision',)).fetchall()
    if len(revision) != 1 or type(revision[0][0]) is not int or revision[0][0] < 0:
        raise ValueError('Sequencing source needs its existing nonnegative owner revision before migration')
    for row in connection.execute(
            'SELECT task_id,requirements_snapshot_revision,state,sprint_id,claim_json,workspace_json,terminal_json '
            'FROM tasks ORDER BY task_id'):
        if (not isinstance(row[0], str) or not row[0] or type(row[1]) is not int or row[1] < 1
                or row[2] != 'available' or any(value is not None for value in row[3:])):
            raise ValueError('Unsupported Sequencing source Task data; use the standalone registration precursor with empty future fields')
    return 'precursor'


def migrate(connection):
    """Prepare format 1 atomically on an idle, foreign-key-enabled connection."""
    if connection.in_transaction:
        raise ValueError('Finish the caller transaction before preparing Sequencing storage')
    connection.execute('BEGIN IMMEDIATE')
    try:
        source = _source(connection)
        if source == 'current':
            connection.commit()
            return
        if source == 'precursor':
            connection.execute('ALTER TABLE tasks RENAME TO _task_source_v0')
        for table, statement in _DDL.items():
            if source == 'precursor' and table in ('meta', 'requests'):
                continue
            connection.execute(statement)
        if source == 'precursor':
            connection.execute(
                'INSERT INTO tasks(task_id,requirements_snapshot_revision,state,terminal_json) '
                'SELECT task_id,requirements_snapshot_revision,state,terminal_json FROM _task_source_v0')
            connection.execute('DROP TABLE _task_source_v0')
        else:
            connection.execute('INSERT INTO meta(key,value) VALUES(?,?)', ('revision', 0))
        for statement in _INDEXES:
            connection.execute(statement)
        connection.execute('PRAGMA user_version=1')
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
