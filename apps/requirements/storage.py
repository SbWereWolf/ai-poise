"""Application-owned normalized storage behind the shared public store owner."""
import json
from pathlib import Path

from poise.infrastructure.requirements_registry import RequirementsStore as SharedStore
from poise.modules.foundation.errors import PoiseError


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def RequirementsStore(database, lock, lock_seconds, poll_seconds):
    return SharedStore.standalone(database, lock, lock_seconds, poll_seconds, StandaloneBackend())


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
        return {'requirements': requirements, 'links': links}

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
        return {'intent': json.loads(row['intent']), 'response': json.loads(row['response'])}

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
        for link in after['links']:
            if (link['system'], link['application']) not in existing:
                connection.execute(
                    'INSERT INTO main.system_application_versions(system_id,application_id,valid_from,valid_to) '
                    'VALUES(?,?,?,NULL)', (link['system'], link['application'], revision),
                )
        connection.execute('UPDATE main.requirements_meta SET revision=?', (revision,))
        connection.execute(
            'INSERT INTO main.requirement_requests(protocol_namespace,request_id,intent,response) '
            'VALUES(?,?,?,?)', ('registry.apply', request_id, _json(intent), _json(response)),
        )
