"""Historical own empty format0 to Poise format1; local SQLite literals only."""


class UnsupportedVersion(ValueError):
    def __init__(self, version):
        super().__init__(f'Unsupported Poise state version {version}')
        self.version = version


def prepare(connection):
    if connection.in_transaction:
        raise ValueError('Finish the caller transaction before preparing Poise state')
    version = connection.execute('PRAGMA user_version').fetchone()[0]
    if version == 1:
        return []
    if version != 0:
        raise UnsupportedVersion(version)
    try:
        connection.execute('BEGIN IMMEDIATE')
        version = connection.execute('PRAGMA user_version').fetchone()[0]
        if version == 1:
            connection.commit()
            return []
        if version != 0:
            raise UnsupportedVersion(version)
        connection.execute('''CREATE TABLE poise_sessions(
            id TEXT PRIMARY KEY NOT NULL,
            origin TEXT NOT NULL CHECK(origin IN ('native','generated')),
            status TEXT NOT NULL CHECK(status IN ('open','closed')),
            revision INTEGER NOT NULL CHECK(revision>=1))''')
        connection.execute('''CREATE TABLE poise_bindings(
            identity_key TEXT PRIMARY KEY NOT NULL,
            session_id TEXT NOT NULL UNIQUE REFERENCES poise_sessions(id),
            inventory TEXT NOT NULL)''')
        connection.execute('''CREATE TABLE poise_role_assignments(
            session_id TEXT PRIMARY KEY NOT NULL REFERENCES poise_sessions(id),
            role TEXT NOT NULL,data TEXT NOT NULL)''')
        connection.execute('''CREATE TABLE poise_hook_bindings(
            id TEXT PRIMARY KEY NOT NULL,
            session_id TEXT NOT NULL REFERENCES poise_sessions(id),data TEXT NOT NULL)''')
        connection.execute('''CREATE TABLE poise_hook_events(
            seq INTEGER PRIMARY KEY AUTOINCREMENT,
            binding_id TEXT NOT NULL REFERENCES poise_hook_bindings(id),
            event_id TEXT NOT NULL,event TEXT NOT NULL,data TEXT NOT NULL,
            UNIQUE(binding_id,event_id))''')
        connection.execute('''CREATE TABLE poise_reader_state(
            session_id TEXT PRIMARY KEY NOT NULL REFERENCES poise_sessions(id),
            data TEXT NOT NULL)''')
        connection.execute('''CREATE TABLE poise_cursors(
            session_id TEXT NOT NULL REFERENCES poise_sessions(id),
            path TEXT NOT NULL,data TEXT NOT NULL,PRIMARY KEY(session_id,path))''')
        connection.execute('PRAGMA user_version=1')
        connection.commit()
        return [1]
    except BaseException:
        if connection.in_transaction:
            connection.rollback()
        raise
