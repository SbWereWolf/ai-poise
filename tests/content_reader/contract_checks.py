"""Test-owned complete consumers, address substitutions and external effects."""
from copy import deepcopy
import io
import json
from pathlib import Path
import sqlite3
import uuid


def equal(actual, expected):
    assert type(actual) is type(expected), (type(actual), type(expected))
    if isinstance(expected, dict):
        assert actual.keys() == expected.keys()
        for k in expected:
            equal(actual[k], expected[k])
    elif isinstance(expected, list):
        assert len(actual) == len(expected)
        for a, e in zip(actual, expected):
            equal(a, e)
    else:
        assert actual == expected


def address_shape(path, root):
    assert type(path) is str and Path(path).is_absolute()
    relative = Path(path).relative_to(root)
    assert relative.parts and relative.suffix == '.json'
    shape, identifiers = [], 0
    for part in relative.parts:
        stem = Path(part).stem if part.endswith('.json') else part
        try:
            value = uuid.UUID(stem)
        except ValueError:
            shape.append(part)
        else:
            assert stem in (value.hex, str(value))
            identifiers += 1
            shape.append(('@UUID' + str(len(stem)) + '@') + ('.json' if part.endswith('.json') else ''))
    assert identifiers > 0, 'Read address must contain its reserved response UUID'
    return tuple(shape), len(path.encode())


def complete_expected(s, *, items, summary, generation, path, status='ready', operation='read', error=None):
    from tests.content_reader.helpers import fixture, sha
    result = deepcopy(fixture()['first_response'])
    result.update(status=status, operation=operation, project=s.project,
                  checkout=str(s.checkout), policy_sha256=sha(s.policy.read_bytes()),
                  items=deepcopy(items), summary=deepcopy(summary), response_path=path,
                  error=deepcopy(error), details=None)
    result['context'] = {'caller_key': s.caller_key, 'generation': generation,
                         'admission_mode': 'native_hook', 'native_episode': deepcopy(s.episode),
                         'checkout_key': s.checkout_keys[str(s.checkout)]}
    return result


def read_root(s):
    assert s.resource is not None
    return s.resource / s.data['session']['response_directory']


def assert_complete_read(s, value, expected, *, calibration=None):
    from tests.content_reader.helpers import strict_json
    path = value['response_path']
    shape = address_shape(path, read_root(s))
    if calibration is not None:
        assert shape == address_shape(calibration, read_root(s))
    wanted = deepcopy(expected)
    wanted['response_path'] = path  # The sole response-address substitution.
    equal(value, wanted)
    equal(strict_json(Path(path).read_bytes()), wanted)
    return wanted


def header_expected(full, item):
    result = {k:deepcopy(v) for k,v in full.items() if k != 'items'}
    result['item'] = {k:deepcopy(v) for k,v in item.items() if k != 'content'}
    return result


def assert_header(actual, expected):
    equal(actual,expected)


def assert_text(s, raw, expected, *, calibration=None):
    from tests.content_reader.helpers import text_frames, strict_json
    frames = text_frames(raw)
    assert len(frames) == len(expected['items'])
    for (actual, body), item in zip(frames, expected['items']):
        header = {k: deepcopy(v) for k, v in expected.items() if k != 'items'}
        header['item'] = {k: deepcopy(v) for k, v in item.items() if k != 'content'}
        address_shape(actual['response_path'], read_root(s))
        if calibration is not None:
            assert address_shape(actual['response_path'], read_root(s)) == address_shape(calibration, read_root(s))
        header['response_path'] = actual['response_path']
        assert_header(actual, header)
        equal(body, item['content'].encode() if item.get('content_follows') else b'')
        saved = deepcopy(expected); saved['response_path'] = actual['response_path']
        equal(strict_json(Path(actual['response_path']).read_bytes()), saved)
    return frames


def render_text(result):
    from tests.content_reader.helpers import canonical
    out = ''
    for item in result['items']:
        header = {k: deepcopy(v) for k, v in result.items() if k != 'items'}
        header['item'] = {k: deepcopy(v) for k, v in item.items() if k != 'content'}
        out += canonical(header)
        if item.get('content_follows'):
            out += item['content']
    return out


class PrefixFailure(io.StringIO):
    """Accept exactly a chosen Unicode prefix, then really fail, across writes."""
    def __init__(self, limit):
        super().__init__()
        self.limit = limit
        self.failed = False

    def write(self, value):
        assert not self.failed, 'Subject retried a failed sink'
        remaining = self.limit - len(self.getvalue())
        if len(value) <= remaining and len(value) < remaining:
            return super().write(value)
        accepted = value[:remaining]
        super().write(accepted)
        self.failed = True
        raise OSError('fixture prefix delivery failure')


def assert_template(path):
    """Observe the actual schema; expected columns/keys come only from literals."""
    from tests.content_reader.helpers import fixture
    design = fixture('schema2-shape.json')
    with sqlite3.connect(Path(path).as_uri() + '?mode=ro', uri=True) as source:
        tables = {r[0] for r in source.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert tables == set(design['tables']) | {'sqlite_sequence'}
        assert source.execute('SELECT singleton,schema_version FROM registry_schema').fetchall() == [(1,2)]
        for name, spec in design['columns'].items():
            actual = source.execute('PRAGMA table_info("' + name + '")').fetchall()
            assert [(r[1],r[2],r[3],r[5]) for r in actual] == [tuple(r) for r in spec]
            index_signatures = set()
            for index in source.execute('PRAGMA index_list("' + name + '")'):
                index_name = index[1].replace('"','""')
                columns = tuple(r[2] for r in source.execute('PRAGMA index_info("' + index_name + '")'))
                index_signatures.add((bool(index[2]),columns))
            assert index_signatures == {(v['unique'],tuple(v['columns'])) for v in design['indexes'][name]}
            if name != 'registry_schema':
                assert source.execute('SELECT COUNT(1) FROM "' + name + '"').fetchone()[0] == 0
        assert [r[0] for r in design['columns']['content_read_cache']] == design['cache_columns']
        assert [r[0] for r in design['columns']['content_read_events']] == design['event_columns']
        with sqlite3.connect(':memory:') as probe:
            source.backup(probe)
            for name, row in design['valid_rows'].items():
                columns = list(row)
                sql = 'INSERT INTO "' + name + '" (' + ','.join('"'+c+'"' for c in columns) + ') VALUES (' + ','.join('?' for _ in columns) + ')'
                values = list(row.values())
                probe.execute('SAVEPOINT fixture_probe')
                try:
                    probe.execute(sql, values)
                    duplicate = values.copy()
                    if 'id' in columns and name not in design['duplicate_id_only_tables']:
                        duplicate[columns.index('id')] += 1
                    try:
                        probe.execute(sql, duplicate)
                    except sqlite3.IntegrityError:
                        pass
                    else:
                        raise AssertionError('Required unique/primary key missing: ' + name)
                finally:
                    probe.execute('ROLLBACK TO fixture_probe'); probe.execute('RELEASE fixture_probe')
                for field, _, not_null, _ in design['columns'][name]:
                    if not_null:
                        bad = values.copy(); bad[columns.index(field)] = None
                        try:
                            probe.execute(sql, bad)
                        except sqlite3.IntegrityError:
                            pass
                        else:
                            raise AssertionError('Required NOT NULL missing: ' + name + '.' + field)
                        probe.rollback()
            for singleton, version in [(2,2),(1,None)]:
                try:
                    probe.execute('INSERT INTO registry_schema(singleton,schema_version) VALUES (?,?)',(singleton,version))
                except sqlite3.IntegrityError:
                    pass
                else:
                    raise AssertionError('Required schema marker constraint missing')
                probe.rollback()
    return True
