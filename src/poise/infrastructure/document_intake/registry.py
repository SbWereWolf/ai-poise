"""One schema-2 ledger: generation and atomic prepared-read registration."""
from contextlib import contextmanager
import sqlite3
from ...modules.document_intake.contracts import ReadFailure
from ...modules.document_intake.domain import read_decision
from .template import validate_registry, TemplateError


def add_summary(summary, item):
    result = dict(summary)
    content = item['status'] == 'content'
    result['cache_entries'] += int(content)
    result['event_count'] += 1
    result['content_events' if content else 'unchanged_events'] += 1
    result['model_content_bytes' if content else 'avoided_bytes'] += item['content_bytes']
    return result


class PreparedRegistry:
    def __init__(self, path, timeout_ms, clock):
        self.path, self.timeout_ms, self.clock = path, timeout_ms, clock

    @contextmanager
    def open_existing(self, *, write=False, key=None):
        if not self.path.is_file():
            raise ReadFailure('registry_missing', key)
        connection = None
        try:
            connection = sqlite3.connect(self.path.as_uri() + '?mode=' + ('rw' if write else 'ro'),
                                         uri=True, timeout=self.timeout_ms / 1000)
            connection.execute(f'PRAGMA busy_timeout={self.timeout_ms}')
            yield connection
        except sqlite3.Error as exc:
            if connection is not None and connection.in_transaction:
                connection.rollback()
            raise ReadFailure('registry_write' if write else 'registry_read', key) from exc
        finally:
            if connection is not None:
                connection.close()

    def validate(self):
        try:
            with self.open_existing() as db:
                try:
                    marker = db.execute('SELECT singleton,schema_version FROM registry_schema').fetchall()
                except sqlite3.DatabaseError as exc:
                    raise ReadFailure('registry_invalid') from exc
                if marker != [(1, 2)]:
                    raise ReadFailure('registry_schema')
            validate_registry(self.path)
        except TemplateError as exc:
            raise ReadFailure('registry_schema') from exc

    @staticmethod
    def _generation(db, namespace):
        return db.execute('SELECT COALESCE(MAX(epoch),0) FROM compactions WHERE session_id=?',
                          (namespace,)).fetchone()[0]

    @staticmethod
    def _cache(db, namespace):
        return db.execute('SELECT epoch,path_label,content_sha256,normalized_range,reader_kind '
                          'FROM content_read_cache WHERE session_id=? ORDER BY epoch DESC, rowid DESC',
                          (namespace,)).fetchall()

    @staticmethod
    def _summary(db, namespace):
        caches = db.execute('SELECT COUNT(*) FROM content_read_cache WHERE session_id=?',
                            (namespace,)).fetchone()[0]
        row = db.execute('SELECT COUNT(*),COALESCE(SUM(status=?),0),COALESCE(SUM(status=?),0),'
                         'COALESCE(SUM(model_content_bytes),0),'
                         'COALESCE(SUM(content_bytes-model_content_bytes),0) '
                         'FROM content_read_events WHERE session_id=?',
                         ('content', 'unchanged', namespace)).fetchone()
        return dict(zip(('cache_entries', 'event_count', 'content_events', 'unchanged_events',
                         'model_content_bytes', 'avoided_bytes'), (caches, *row)))

    def context(self, scope):
        with self.open_existing() as db:
            db.execute('BEGIN')
            return self._generation(db, scope.namespace), self._summary(db, scope.namespace)

    def preview_batch(self, scope, prepared):
        with self.open_existing() as db:
            db.execute('BEGIN')
            generation = self._generation(db, scope.namespace)
            cache = self._cache(db, scope.namespace)
            summary = self._summary(db, scope.namespace)
        items = []
        for value in prepared:
            if isinstance(value, ReadFailure):
                items.append(dict(key=value.key, status='failed', error=value.as_dict()))
                continue
            item = read_decision(value, generation, cache)
            items.append(item)
            summary = add_summary(summary, item)
            if item['status'] == 'content':
                cache.insert(0, (generation, value.path, value.content_sha256,
                                 value.normalized_range, 'repository-content-read'))
        return generation, items, summary

    def register(self, scope, prepared, expected_generation):
        with self.open_existing(write=True, key=prepared.key) as db:
            db.execute('BEGIN IMMEDIATE')
            generation = self._generation(db, scope.namespace)
            if generation != expected_generation:
                raise ReadFailure('context_changed', prepared.key)
            item = read_decision(prepared, generation, self._cache(db, scope.namespace))
            at = self.clock.observe().audit_utc
            content = item['status'] == 'content'
            values = (scope.namespace, generation, prepared.path, prepared.content_sha256,
                      prepared.normalized_range, 'repository-content-read')
            if content:
                db.execute('INSERT INTO content_read_cache '
                           '(session_id,epoch,path_label,content_sha256,normalized_range,reader_kind,'
                           'content_bytes,content_lines,first_read_at) VALUES (?,?,?,?,?,?,?,?,?)',
                           (*values, prepared.content_bytes, prepared.content_lines, at))
            db.execute('INSERT INTO content_read_events '
                       '(session_id,epoch,occurred_at,path_label,content_sha256,normalized_range,reader_kind,'
                       'status,classification,content_bytes,content_lines,model_content_bytes) '
                       'VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                       (scope.namespace, generation, at, *values[2:], item['status'], item['classification'],
                        prepared.content_bytes, prepared.content_lines, prepared.content_bytes if content else 0))
            db.commit()
            return item

    def advance_context(self, scope, request_id, reason):
        with self.open_existing(write=True) as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT epoch,trigger FROM compactions WHERE session_id=? AND turn_id=?',
                             (scope.namespace, request_id)).fetchone()
            if old:
                if old[1] != reason:
                    raise ReadFailure('event_conflict')
                return old[0]
            if reason != 'manual':
                raise ReadFailure('invalid_request')
            generation = self._generation(db, scope.namespace) + 1
            db.execute('INSERT INTO compactions (session_id,turn_id,epoch,trigger,created_at) VALUES (?,?,?,?,?)',
                       (scope.namespace, request_id, generation, reason, self.clock.observe().audit_utc))
            db.commit()
            return generation
