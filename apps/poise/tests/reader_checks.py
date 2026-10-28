"""Finite accepted reader scenarios using only public producer calls and direct SQL."""
from copy import deepcopy
import json

from reader_support import FIX, VECTORS, Reader, exact, load


def run_cases(cases, documents):
    for case in cases:
        with Reader() as app:
            app.record['case'] = case['id']
            app.arrange(case['setup'], documents)
            for step in case['steps']:
                app.step(step)
            if case['id'] == 'bounded-large-source':
                rows = app.rows()
                # Own metadata may contain coordinates, IDs and paths, never selected document text.
                for name in ('state', 'cursors'):
                    for row in rows[name]:
                        payload = json.loads(row[-1])
                        def strings(value):
                            if isinstance(value, str):
                                yield value
                            elif isinstance(value, dict):
                                for part in value.values():
                                    yield from strings(part)
                            elif isinstance(value, list):
                                for part in value:
                                    yield from strings(part)
                        assert all('first\n' not in value and 'A' * 64 not in value
                                   for value in strings(payload)), row


def contract_vectors():
    vector = load(VECTORS)
    run_cases(vector['cases'], vector['documents'])


def own_sessions_and_receipts():
    data = load(FIX / 'setup.json')
    vector = load(VECTORS)
    with Reader() as app:
        app.record['case'] = 'independent-generated-sessions'
        app.document(vector['documents']['sample'])
        app.step({'request': {'schema': 'stride-poise/v1', 'operation': 'reader.context', 'input': {}},
                  'expected_exit': 0, 'expected_response': data['context_response']})
        app.step(deepcopy(data['sample_first']))
        a = (app.identity, app.ids, app.cursors, app.receipts)
        app.session.generated('second-caller.json')
        child, record, response = app.session.call('session.ensure')
        exact(child.returncode, 0)
        app.identity = response['result']['session']['id']
        assert app.identity != a[0]
        exact(response['result']['session']['role'], None)
        app.ids, app.cursors, app.receipts = {}, [], {}
        app.step({'request': {'schema': 'stride-poise/v1', 'operation': 'reader.context', 'input': {}},
                  'expected_exit': 0, 'expected_response': data['context_response']})
        app.step(deepcopy(data['sample_first']))
        b = (app.identity, app.ids, app.cursors, app.receipts)
        app.session.generated('caller.json')
        app.identity, app.ids, app.cursors, app.receipts = a
        app.step(deepcopy(data['sample_ack']))
        b_rows = {key: [row for row in app.rows()[key] if row[0] == b[0]]
                  for key in ('state', 'cursors')}
        app.step(deepcopy(data['reset']))
        for key in ('state', 'cursors'):
            exact([row for row in app.rows()[key] if row[0] == b[0]], b_rows[key])
        app.session.generated('second-caller.json')
        app.identity, app.ids, app.cursors, app.receipts = b
        app.step({'request': {'schema': 'stride-poise/v1', 'operation': 'reader.context', 'input': {}},
                  'expected_exit': 0, 'expected_response': data['context_response'],
                  'effects': {'reader_metadata_unchanged': True}})
        app.step(deepcopy(data['sample_first']))
        ids = [row[0] for row in app.rows()['sessions']]
        exact(sorted(ids), sorted([a[0], b[0]]))
    supplemental = load(FIX / 'cases.json')
    run_cases([case for case in supplemental['cases']
               if case['id'] == 'receipt-capacity-and-eviction' or case['id'].startswith('ack-')],
              supplemental['documents'])


def configuration_and_sources():
    data = load(FIX / 'setup.json')
    for name, expected, exit_code in [('missing', data['not_configured'], 1),
                                      ('invalid', data['invalid_config'], 2)]:
        with Reader() as app:
            app.record['case'] = 'reader-config-' + name
            if name == 'missing':
                del app.session.config['reader']
            else:
                app.session.config['reader']['limits']['max_lines'] = True
            before = app.rows()
            app.step({'request': {'schema': 'stride-poise/v1', 'operation': 'reader.context', 'input': {}},
                      'expected_exit': exit_code, 'expected_response': expected,
                      'effects': {'reader_metadata_unchanged': True}})
            child, record, response = app.session.call('session.status')
            exact(child.returncode, 0)
            exact(response['result']['session'], app.record['session']['result']['session'])
            exact(app.rows(), before)
    supplemental = load(FIX / 'cases.json')
    run_cases([case for case in supplemental['cases']
               if case['id'] != 'receipt-capacity-and-eviction'
               and not case['id'].startswith('ack-')
               and case['id'] != 'partial-batch-no-ack-commit'], supplemental['documents'])


def failed_batch_and_state():
    data = load(FIX / 'cases.json')
    run_cases([case for case in data['cases'] if case['id'] == 'partial-batch-no-ack-commit'],
              data['documents'])


GROUPS = [('contract_vectors', contract_vectors),
          ('own_sessions_and_receipts', own_sessions_and_receipts),
          ('configuration_and_sources', configuration_and_sources),
          ('failed_batch_and_state', failed_batch_and_state)]
