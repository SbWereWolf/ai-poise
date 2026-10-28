"""Finite fixture-owned public policy/read/write/selection acceptance groups."""
from copy import deepcopy

from fs_support import Files, load
from reader_support import exact


def cases(group):
    setup = load('setup.json')
    for case in load('cases.json')['groups'][group]:
        with Files(reader=case.get('reader', False)) as app:
            app.record['case'] = case['id']
            if 'policy' in case:
                app.session.config['paths'] = app.bind(case['policy'], scope='policy')
            if case.get('paths_absent'):
                app.session.config.pop('paths', None)
            options = deepcopy(case.get('config_options', {}))
            app.session.config.update(options)
            if 'expected_config_options' in case:
                exact({key: app.session.config[key] for key in options}, case['expected_config_options'])
            for spec in case.get('documents', []):
                app.document(spec)
            if case.get('context'):
                app.step(setup['reader_context'])
            for step in case['steps']:
                app.step(step)


def policy():
    cases('policy')


def reader():
    cases('reader')


def writer():
    cases('writer')


def selection():
    cases('selection')


GROUPS = [('policy', policy), ('reader', reader), ('writer', writer), ('selection', selection)]
