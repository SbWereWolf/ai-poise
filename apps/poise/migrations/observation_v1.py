"""Literal initial schema of the independent observation store."""


def prepare(connection,installation_id):
    connection.execute('CREATE TABLE observation_meta(id INTEGER PRIMARY KEY,schema_version INTEGER NOT NULL,installation_id TEXT NOT NULL)')
    connection.execute('''CREATE TABLE observation_events(
        seq INTEGER PRIMARY KEY AUTOINCREMENT,kind TEXT NOT NULL,source TEXT NOT NULL,
        stream TEXT NOT NULL,event_id TEXT NOT NULL,session_id TEXT,source_mode TEXT NOT NULL,
        event_json TEXT NOT NULL,contribution_json TEXT,duration_ns TEXT,
        UNIQUE(kind,source,stream,event_id))''')
    connection.execute('''CREATE TABLE observation_streams(
        source TEXT NOT NULL,stream TEXT NOT NULL,mode TEXT NOT NULL,
        last_sequence TEXT NOT NULL,last_sample TEXT NOT NULL,PRIMARY KEY(source,stream))''')
    connection.execute('INSERT INTO observation_meta(id,schema_version,installation_id) VALUES(?,?,?)',(1,1,installation_id))
    connection.execute('PRAGMA user_version=1')
