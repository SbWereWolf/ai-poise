"""Qualify real SQLite reader/COMMIT failure without implementing Session code."""
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile

from session_support import OBSERVATIONS
from state_checks import prerequisites as state_prerequisites
from state_sql import connect


def prerequisites():
    report = {'state_runtime': state_prerequisites(), 'commit': {}}
    OBSERVATIONS.append({'environment_qualification': report})
    detail = report['commit']
    with tempfile.TemporaryDirectory(prefix='t061-commit-qualification-') as root:
        path = Path(root) / 'primitive.sqlite'
        with closing(connect(path)) as writer, closing(connect(path)) as reader:
            try:
                detail['journal_mode'] = writer.execute('PRAGMA journal_mode=DELETE').fetchone()[0]
                writer.execute('CREATE TABLE q_commit(value TEXT NOT NULL)')
                writer.execute('INSERT INTO q_commit(value) VALUES(?)', ('original',))
                reader.execute('BEGIN')
                detail['reader_rows'] = [list(row) for row in reader.execute('SELECT value FROM q_commit')]
                writer.execute('BEGIN IMMEDIATE')
                detail['updated_rows'] = writer.execute('UPDATE q_commit SET value=?', ('changed',)).rowcount
                detail['writer_rows'] = [list(row) for row in writer.execute('SELECT value FROM q_commit')]
                detail['journal_bytes'] = Path(str(path) + '-journal').stat().st_size
                try:
                    writer.commit()
                except sqlite3.OperationalError as error:
                    detail['commit_error'] = {'code': error.sqlite_errorcode,
                                              'message': str(error), 'type': type(error).__name__}
                detail['writer_transaction_after_commit'] = writer.in_transaction
                writer.rollback()
                detail['writer_transaction_after_rollback'] = writer.in_transaction
                reader.rollback()
                with closing(connect(path, readonly=True)) as fresh:
                    detail['fresh_rows'] = [list(row) for row in fresh.execute('SELECT value FROM q_commit')]
                assert detail['journal_mode'] == 'delete'
                assert detail['reader_rows'] == [['original']]
                assert detail['updated_rows'] == 1 and detail['writer_rows'] == [['changed']]
                assert detail['journal_bytes'] > 0 and detail['commit_error']['code'] == 5
                assert detail['writer_transaction_after_commit'] is True
                assert detail['writer_transaction_after_rollback'] is False
                assert detail['fresh_rows'] == [['original']]
                detail['status'] = 'passed'
            except Exception as error:
                detail['error'] = type(error).__name__ + ': ' + str(error)
                raise RuntimeError('SQLite COMMIT qualification failed; actual observations retained') from error
            finally:
                detail['writer_in_transaction_before_close'] = writer.in_transaction
                detail['reader_in_transaction_before_close'] = reader.in_transaction
                if writer.in_transaction:
                    writer.rollback()
                if reader.in_transaction:
                    reader.rollback()
    return report
