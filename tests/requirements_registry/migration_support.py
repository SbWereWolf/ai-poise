"""Independent literal SQL fixtures and real public CLI/Task transport."""
from copy import deepcopy
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

from conftest import git, write_json
from poise.infrastructure.clock import SystemClock

FIXTURES = Path(__file__).parent / 'fixtures/native_migrations'
SOURCE = Path(__file__).resolve().parents[2] / 'src'
COLUMNS = {
    'requirements': ('id', 'level', 'status', 'text'),
    'requirement_links': ('system_id', 'application_id'),
    'requirements_meta': ('revision',),
    'requirement_requests': ('request_id', 'digest', 'response'),
    'domain_areas': ('id', 'business_name', 'definition'),
    'domain_entities': ('id', 'domain_id', 'business_name', 'code_name', 'definition'),
    'domain_actions': ('id', 'domain_id', 'business_name', 'code_name', 'definition'),
    'requirement_entities': ('requirement_id', 'domain_id', 'entity_id'),
    'requirement_actions': ('requirement_id', 'domain_id', 'action_id'),
}
INSERT_ORDER = (
    'requirements', 'domain_areas', 'domain_entities', 'domain_actions',
    'requirement_links', 'requirement_entities', 'requirement_actions',
    'requirement_requests', 'requirements_meta',
)


def schema_statements():
    return [item['sql'] for item in json.loads((FIXTURES / 'schema-reference.json').read_text())]


def create_database(path, *, populated=False):
    with sqlite3.connect(path) as db:
        db.execute('PRAGMA foreign_keys=ON')
        for statement in schema_statements():
            db.execute(statement)
        if populated:
            rows = json.loads((FIXTURES / 'data.json').read_text())
            for table in INSERT_ORDER:
                columns = COLUMNS[table]
                db.executemany(
                    f'INSERT INTO {table}({",".join(columns)}) VALUES({",".join("?" for _ in columns)})',
                    rows[table],
                )
        else:
            db.execute('INSERT INTO requirements_meta(revision) VALUES(?)', (0,))
        db.execute('PRAGMA user_version=2')


def populate_database(path):
    rows = json.loads((FIXTURES / 'data.json').read_text())
    with sqlite3.connect(path) as db:
        db.execute('PRAGMA foreign_keys=ON')
        for table in reversed(INSERT_ORDER):
            db.execute(f'DELETE FROM {table}')
        for table in INSERT_ORDER:
            columns = COLUMNS[table]
            db.executemany(
                f'INSERT INTO {table}({",".join(columns)}) VALUES({",".join("?" for _ in columns)})',
                rows[table],
            )


def snapshot(path):
    with sqlite3.connect(f'file:{path}?mode=ro', uri=True) as db:
        return {
            table: db.execute(
                f'SELECT {",".join(columns)} FROM {table} ORDER BY {",".join(columns)}'
            ).fetchall()
            for table, columns in COLUMNS.items()
        }


def sql(path, statement, parameters=()):
    with sqlite3.connect(path) as db:
        db.execute('PRAGMA foreign_keys=ON')
        return db.execute(statement, parameters).fetchall()


def schema_b_database(path):
    create_database(path, populated=True)
    sql(path, 'ALTER TABLE requirements ADD COLUMN note TEXT')
    sql(path, 'INSERT INTO requirements(id,level,status,text) VALUES(?,?,?,?)',
        ('TEMP', 'system', 'current', 'before'))
    for identifier, note in json.loads((FIXTURES / 'schema-b-notes.json').read_text()).items():
        sql(path, 'UPDATE requirements SET note=? WHERE id=?', (note, identifier))


def migration(root, name, forward, backward, *, depends=(), transactional=True):
    """Write test-owned standard yoyo files; never use the subject's generator."""
    root.mkdir(parents=True, exist_ok=True)
    source = ['from yoyo import step', f'__depends__ = {set(depends)!r}',
              f'__transactional__ = {transactional!r}', 'steps = [']
    source += [f'    step({up!r}, {down!r}),' for up, down in zip(forward, backward, strict=True)]
    source.append(']\n')
    path = root / (name + '.py')
    path.write_text('\n'.join(source), encoding='utf-8')
    return path


def schema_fixture(root):
    # SQL comes from the raw baseline schema, not a production schema builder.
    statements = schema_statements()
    rollback = ['DROP TABLE ' + item['name'] for item in
                json.loads((FIXTURES / 'schema-reference.json').read_text())]
    # Explicit FK-safe application order, independent of lexical sqlite_schema order.
    order = ['requirements', 'domain_areas', 'domain_entities', 'domain_actions',
             'requirement_links', 'requirement_entities', 'requirement_actions',
             'requirement_requests', 'requirements_meta']
    by_name = dict(zip([item['name'] for item in json.loads(
        (FIXTURES / 'schema-reference.json').read_text())], zip(statements, rollback), strict=True))
    pairs = [by_name[name] for name in order]
    pairs += [('INSERT INTO requirements_meta(revision) VALUES(0)',
               'DELETE FROM requirements_meta'), ('PRAGMA user_version=2', 'PRAGMA user_version=0')]
    return migration(root, '0001', [p[0] for p in pairs], [p[1] for p in pairs])


class Scene:
    def __init__(self, project, *, prior=False, read_only=False):
        self.project = project
        self.cfg = deepcopy(project['cfg'])
        self.database = project['root'] / self.cfg['paths']['state'] / self.cfg['paths']['requirements_database']
        self.schema = project['app'] / 'schema-history'
        self.data_relative = Path('docs/requirements/data')
        self.bootstrap_relative = Path('docs/requirements/schema.sql')
        self.cfg['paths'].update(
            requirements_schema_migrations=str(self.schema),
            requirements_data_migrations=str(project['app'] / self.data_relative),
            requirements_schema_file=str(project['app'] / self.bootstrap_relative),
        )
        self.cfg['automatic_checks'] = []
        write_json(project['config_path'], self.cfg)
        schema_fixture(self.schema)
        (project['app'] / self.data_relative).mkdir(parents=True)
        if prior:
            migration(project['app'] / self.data_relative, '0009-prior', ['SELECT 1'], ['SELECT 1'])
        (project['app'] / self.bootstrap_relative).write_text(
            ';\n'.join(schema_statements()) + ';\n', encoding='utf-8')
        git(project['app'], 'add', '.')
        git(project['app'], 'commit', '-m', 'Independent schema and documentation fixture')
        # One real terminal stage makes completion observable without unrelated TDD.
        project['workflow']['stages'] = project['workflow']['stages'][:1]
        project['workflow']['stages'][0]['transitions'] = {'complete': None}
        self.task = deepcopy(project['task'])
        self.task['methods'] = []
        self.task['method_inputs'] = []
        self.task['checks'] = {stage['id']: [] for stage in project['workflow']['stages']}
        self.task['evidence_plan'] = {'tests': {'subject_methods': {}, 'arguments': [], 'review_arguments': []}}
        self.task['decomposition']['phases'] = self.task['decomposition']['phases'][:1]
        self.task['stage_contracts'] = self.task['stage_contracts'][:1]
        # Keep the existing public workflow, with no unrelated arithmetic checks.
        for stage in project['workflow']['stages']:
            stage['read_only'] = read_only
            stage['allowed_paths'] = [] if read_only else ['docs/**']
        write_json(project['root'] / 'config/workflows/development.json', project['workflow'])
        for contract in self.task['stage_contracts']:
            contract['allowed_paths'] = [] if read_only else ['docs/**']
        self.owner = 'requirements-migration-owner'
        self.binding = project['root'] / 'requirements-caller.json'
        write_json(self.binding, {'schema': 'poise-caller-binding-1', 'caller_id': self.owner})
        from poise.infrastructure.session_establishment import establish_direct_poise
        self.runtime = establish_direct_poise(
            project['config_path'], {'POISE_CALLER_BINDING': str(self.binding)},
            SystemClock(), read_only=False,
        ).runtime
        self.context = self.runtime.bootstrap(self.task)
        self.worktree = Path(self.context['worktree'])
        self.data = self.worktree / self.data_relative
        self.data.mkdir(parents=True, exist_ok=True)

    def environment(self, owned=True):
        environment = {key: value for key, value in os.environ.items()
                       if key not in {'CODEX_THREAD_ID', 'CODEX_SESSION_ID', 'POISE_CALLER_BINDING', 'POISE_CONFIG'}}
        environment['PYTHONPATH'] = os.pathsep.join([str(SOURCE.parent / '.runtime/dependencies'), str(SOURCE)])
        environment['POISE_CALLER_BINDING'] = str(self.binding if owned else self.binding.with_name('unclaimed.json'))
        return environment

    def invoke(self, operation, body, *, owned=True, binding=None):
        environment = self.environment(owned)
        if binding is not None:
            environment['POISE_CALLER_BINDING'] = str(binding)
        packet = {'operation': operation, 'input': body}
        # Legacy Task-source scenarios declare their actual source explicitly;
        # public Requirements no longer infers it from a Task or caller cwd.
        config_path = self.project['config_path']
        original = config_path.read_bytes()
        selected = json.loads(original)
        for key, relative in (
            ('requirements_data_migrations', self.data_relative),
            ('requirements_schema_file', self.bootstrap_relative),
        ):
            if selected['paths'][key] == str(self.project['app'] / relative):
                selected['paths'][key] = str(self.worktree / relative)
        try:
            write_json(config_path, selected)
            completed = subprocess.run(
                [sys.executable, '-B', '-m', 'poise', 'requirements', '--config', str(config_path)],
                input=json.dumps(packet, ensure_ascii=False), text=True, capture_output=True,
                cwd=self.worktree, env=environment, timeout=30,
            )
        finally:
            config_path.write_bytes(original)
        assert completed.stdout.strip(), completed.stderr
        value = json.loads(completed.stdout)
        if 'response_path' in value:
            value = json.loads(Path(value['response_path']).read_text())
        return completed.returncode, value

    def foreign_context(self):
        from poise.infrastructure.session_establishment import establish_direct_poise
        binding = self.project['root'] / 'foreign-caller.json'
        write_json(binding, {'schema': 'poise-caller-binding-1', 'caller_id': 'foreign-owner'})
        runtime = establish_direct_poise(
            self.project['config_path'], {'POISE_CALLER_BINDING': str(binding)},
            SystemClock(), read_only=False,
        ).runtime
        task = deepcopy(self.task)
        task['id'] = 'T2'
        context = runtime.bootstrap(task)
        return binding, runtime, context

    def command(self, name, forward, backward):
        return self.invoke('command', {'name': name, 'sql': forward, 'rollback_sql': backward})

    def files(self):
        return {path.name: path.read_bytes() for path in self.data.iterdir() if path.suffix in {'.py', '.sql'}}

    def complete(self):
        from poise.application.work import WorkTools
        tools = WorkTools(self.runtime)
        (self.worktree / 'docs/verification.txt').write_text('Independent lifecycle fixture.\n')
        result = deepcopy(self.context['result_template'])
        result['sections']['report'] = 'Verified migration-only documentation fixture.'
        result['commit_message'] = 'test: migration fixture'
        return tools.invoke({'operation': 'handoff', 'input': {
            'request_id': 'complete-migration-stage', 'task_id': self.task['id'],
            'reason': 'Independent migration lifecycle verification.', 'result': result, 'artifacts': [],
        }, 'messages': []})
