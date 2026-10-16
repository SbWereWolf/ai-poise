"""Application-owned normalized storage behind the shared public store owner."""
import json
from pathlib import Path

from poise.infrastructure.requirements_registry import RequirementsStore as SharedStore
from poise.modules.foundation.errors import PoiseError
from .graph import RequirementsGraph


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def RequirementsStore(database, lock, lock_seconds, poll_seconds):
    return SharedStore.standalone(
        database, lock, lock_seconds, poll_seconds, StandaloneBackend(),
        registry_type=RequirementsGraph,
    )


def _decoded(value, expected, where):
    try:
        result = json.loads(value)
    except (json.JSONDecodeError, TypeError) as exc:
        raise PoiseError(f'{where}: unreadable saved JSON') from exc
    if not isinstance(result, expected):
        raise PoiseError(f'{where}: invalid saved value')
    return result


class SavedRevisions:
    """Detached saved rows; decode only the record requested by the graph."""

    def __init__(self, statements, requirements, snapshots):
        self.statements = {(row['task_id'], row['statement_revision']): row for row in statements}
        self.requirements = requirements
        self.snapshots = {(row['task_id'], row['snapshot_revision']): row for row in snapshots}

    def has_statement(self, task_id, revision):
        return (task_id, revision) in self.statements

    def statement(self, task_id, statement_revision):
        row = self.statements.get((task_id, statement_revision))
        if row is None:
            raise PoiseError(f'unknown Task statement revision: {statement_revision}')
        return {'goal': row['goal'], 'definition_of_done': _decoded(
            row['definition_of_done'], list, 'Task definition_of_done',
        )}

    def task_history(self, task_id):
        return {'task_id': task_id, 'items': [
            {'statement_revision': revision, 'goal': row['goal']}
            for (identifier, revision), row in sorted(self.statements.items()) if identifier == task_id
        ]}

    def requirement_history(self, requirement_id):
        return {'requirement_id': requirement_id, 'items': [
            {'revision': row['revision'], 'status': row['status'], 'text': row['text'],
             'owner_revision': row['owner_revision'],
             'origin': _decoded(row['origin'], dict, 'requirement revision origin')}
            for row in self.requirements if row['id'] == requirement_id
        ]}

    def latest_snapshot_revision(self, task_id):
        return max((revision for identifier, revision in self.snapshots if identifier == task_id),
                   default=None)

    def snapshot(self, task_id, snapshot_revision):
        row = self.snapshots.get((task_id, snapshot_revision))
        if row is None:
            raise PoiseError(f'unknown Task snapshot revision: {snapshot_revision}')
        return _decoded(row['payload'], dict, 'Task snapshot')


class StandaloneBackend:
    def initialize(self, connection):
        version = connection.execute('PRAGMA user_version').fetchone()[0]
        if version == 0:
            if connection.execute("SELECT name FROM main.sqlite_master WHERE type='table'").fetchone():
                raise PoiseError('Requirements database has an unknown schema')
            source = Path(__file__).with_name('schema.sql').read_text(encoding='utf-8')
            for statement in source.split(';'):
                if statement.strip():
                    connection.execute(statement)
            connection.execute('INSERT INTO main.requirements_meta(revision,schema_version) VALUES(0,1)')
            connection.execute('PRAGMA user_version=1')
        elif version != 1:
            raise PoiseError(f'Requirements database format version {version} is unsupported')
        rows = connection.execute('SELECT revision,schema_version FROM main.requirements_meta').fetchall()
        if (len(rows) != 1 or type(rows[0]['revision']) is not int
                or rows[0]['revision'] < 0 or rows[0]['schema_version'] != 1):
            raise PoiseError('Requirements metadata requires one valid owner revision and schema version')

    def registry_rows(self, connection):
        requirements = [dict(row) for row in connection.execute(
            'SELECT r.id,r.level,v.status,v.text FROM main.requirements r '
            'JOIN main.requirement_revisions v ON v.id=r.id AND v.revision=r.current_revision '
            'ORDER BY r.id'
        )]
        links = [{'system': row['system_id'], 'application': row['application_id']}
                 for row in connection.execute(
                     'SELECT system_id,application_id FROM main.system_application_versions '
                     'WHERE valid_to IS NULL ORDER BY system_id,application_id')]
        identities = [dict(row) for row in connection.execute(
            'SELECT task_id,current_statement_revision FROM main.task_identities ORDER BY task_id'
        )]
        versions = [dict(row) for row in connection.execute(
            'SELECT task_id,statement_revision,key,position,status,text '
            'FROM main.task_requirement_versions ORDER BY task_id,statement_revision,position'
        )]
        task_links = [dict(row) for row in connection.execute(
            'SELECT task_id,statement_revision,key,application_id FROM main.application_task_links '
            'ORDER BY task_id,statement_revision,key,application_id'
        )]
        statements = [dict(row) for row in connection.execute(
            'SELECT task_id,statement_revision,goal,definition_of_done '
            'FROM main.task_statements ORDER BY task_id,statement_revision'
        )]
        requirement_revisions = [dict(row) for row in connection.execute(
            'SELECT id,revision,status,text,owner_revision,origin '
            'FROM main.requirement_revisions ORDER BY id,revision'
        )]
        snapshots = [dict(row) for row in connection.execute(
            'SELECT task_id,snapshot_revision,payload FROM main.task_snapshots '
            'ORDER BY task_id,snapshot_revision'
        )]
        return {
            'requirements': requirements, 'links': links, 'task_identities': identities,
            'task_requirement_versions': versions, 'application_task_links': task_links,
            'revisions': SavedRevisions(statements, requirement_revisions, snapshots),
        }

    def prepare_queries(self, connection):
        connection.execute(
            'CREATE TEMP VIEW requirements AS '
            'SELECT r.id,r.level,v.status,v.text FROM main.requirements r '
            'JOIN main.requirement_revisions v ON v.id=r.id AND v.revision=r.current_revision'
        )
        connection.execute(
            'CREATE TEMP VIEW requirement_links AS '
            'SELECT system_id,application_id FROM main.system_application_versions WHERE valid_to IS NULL'
        )

    def previous_request(self, connection, namespace, request_id):
        row = connection.execute(
            'SELECT intent,response FROM main.requirement_requests '
            'WHERE protocol_namespace=? AND request_id=?', (namespace, request_id),
        ).fetchone()
        if row is None:
            return None
        try:
            intent = json.loads(row['intent'])
            response = json.loads(row['response'])
        except json.JSONDecodeError as exc:
            raise PoiseError('Requirements saved request or response contains unreadable JSON') from exc
        if not isinstance(intent, dict) or not isinstance(response, dict):
            raise PoiseError('Requirements saved request and response must be JSON objects')
        return {'intent': intent, 'response': response}

    def persist_apply(self, connection, before, after, revision, request_id, intent, response):
        origin = _json({'type': 'explicit', 'request_id': request_id})
        for identifier, value in after['requirements'].items():
            previous = before['requirements'].get(identifier)
            if previous == value:
                continue
            if previous is None:
                local_revision = 1
                connection.execute(
                    'INSERT INTO main.requirements(id,level,current_revision) VALUES(?,?,?)',
                    (identifier, value['level'], local_revision),
                )
            else:
                local_revision = connection.execute(
                    'SELECT current_revision FROM main.requirements WHERE id=?', (identifier,),
                ).fetchone()[0] + 1
                connection.execute(
                    'UPDATE main.requirements SET current_revision=? WHERE id=?',
                    (local_revision, identifier),
                )
            connection.execute(
                'INSERT INTO main.requirement_revisions(id,revision,status,text,owner_revision,origin) '
                'VALUES(?,?,?,?,?,?)',
                (identifier, local_revision, value['status'], value['text'], revision, origin),
            )
        existing = {(link['system'], link['application']) for link in before['links']}
        active = {(link['system'], link['application']) for link in after['links']}
        for system, application in existing - active:
            connection.execute(
                'UPDATE main.system_application_versions SET valid_to=? '
                'WHERE system_id=? AND application_id=? AND valid_to IS NULL',
                (revision, system, application),
            )
        for link in after['links']:
            if (link['system'], link['application']) not in existing:
                connection.execute(
                    'INSERT INTO main.system_application_versions(system_id,application_id,valid_from,valid_to) '
                    'VALUES(?,?,?,NULL)', (link['system'], link['application'], revision),
                )
        for statement in after['statements']:
            self._persist_statement(connection, statement, origin)
        connection.execute('UPDATE main.requirements_meta SET revision=?', (revision,))
        connection.execute(
            'INSERT INTO main.requirement_requests(protocol_namespace,request_id,intent,response) '
            'VALUES(?,?,?,?)', ('registry.apply', request_id, _json(intent), _json(response)),
        )

    @staticmethod
    def _persist_statement(connection, statement, origin):
        task_id = statement['task_id']
        revision = statement['statement_revision']
        connection.execute(
            'INSERT INTO main.task_statements'
            '(task_id,statement_revision,goal,definition_of_done,origin) VALUES(?,?,?,?,?)',
            (task_id, revision, statement['goal'], _json(statement['definition_of_done']), origin),
        )
        for position, entry in enumerate(statement['task_requirements']):
            connection.execute(
                'INSERT INTO main.task_requirement_keys(task_id,key) VALUES(?,?) '
                'ON CONFLICT(task_id,key) DO NOTHING', (task_id, entry['key']),
            )
            connection.execute(
                'INSERT INTO main.task_requirement_versions'
                '(task_id,statement_revision,key,position,status,text) VALUES(?,?,?,?,?,?)',
                (task_id, revision, entry['key'], position, entry['status'], entry['text']),
            )
            connection.executemany(
                'INSERT INTO main.application_task_links'
                '(task_id,statement_revision,key,application_id) VALUES(?,?,?,?)',
                [(task_id, revision, entry['key'], application) for application in entry['applications']],
            )
        connection.execute(
            'UPDATE main.task_identities SET current_statement_revision=? WHERE task_id=?',
            (revision, task_id),
        )
