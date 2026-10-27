"""Filesystem and SQLite boundary of the independent Poise state store."""
from contextlib import closing, contextmanager
from pathlib import Path
import sqlite3

from domain.generic import Problem
from migrations.v1 import UnsupportedVersion, prepare


class StateStore:
    def __init__(self, settings, config_path):
        path = Path(settings.database)
        self.database = (path if path.is_absolute() else config_path.parent / path).resolve()
        self.timeout = settings.sqlite_timeout_seconds

    @contextmanager
    def connection(self, *, readonly):
        try:
            if readonly:
                if not self.database.exists():
                    raise Problem('state_not_found', {'database': str(self.database)},
                                  'prepare:state', 'unavailable')
                source = self.database.as_uri() + '?mode=ro'
            else:
                self.database.parent.mkdir(parents=True, exist_ok=True)
                source = str(self.database)
            with closing(sqlite3.connect(source, timeout=self.timeout,
                                         isolation_level=None, uri=readonly)) as connection:
                connection.execute('PRAGMA foreign_keys=ON')
                yield connection
        except UnsupportedVersion as error:
            raise Problem('state_incompatible', {'version': error.version},
                          'select:state', 'unavailable') from None
        except sqlite3.Error as error:
            if getattr(error, 'sqlite_errorcode', 0) & 255 in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
                raise Problem('state_busy', {'database': str(self.database)},
                              'retry:state', 'unavailable') from None
            raise Problem('state_io', {'reason': str(error)},
                          'configure:state', 'unavailable') from None
        except OSError as error:
            raise Problem('state_io', {'reason': str(error)},
                          'configure:state', 'unavailable') from None

    def version(self):
        with self.connection(readonly=True) as connection:
            return connection.execute('PRAGMA user_version').fetchone()[0]

    def prepare(self):
        with self.connection(readonly=False) as connection:
            applied = prepare(connection)
            return connection.execute('PRAGMA user_version').fetchone()[0], applied
