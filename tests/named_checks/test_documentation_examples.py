"""Earliest GREEN: documentation; examples are inputs, not runtime evidence."""
import json
import tomllib

from .support import ROOT, entry, literal, schema


def test_documented_examples_match_independent_public_input_contract():
    entry()
    contracts = literal('contract.json')['schemas']['configuration']
    examples = ROOT / 'config/examples'
    policy = json.loads((examples / 'project-checks.example.json').read_text())
    profiles = tomllib.loads((examples / 'project-check-profiles.example.toml').read_text())
    packages = tomllib.loads((examples / 'project-check-packages.example.toml').read_text())
    suites = json.loads((examples / 'project-check-suites.example.json').read_text())
    for value, name in [(policy, 'policy'), (profiles, 'profiles_toml'),
                        (packages, 'packages_toml'), (suites, 'suites_json')]:
        schema(value, contracts[name])
    ids = {v['command_id'] for v in profiles['commands']}
    package_ids = {v['package_id'] for v in packages['packages']}
    drivers = {v['id'] for v in policy['drivers']}
    for package in packages['packages']:
        assert set(package['dependencies']) <= package_ids
        assert all(step['driver'] in drivers for step in package['steps'])
    for suite in suites['suites']:
        assert suite['profiles'] and suite['sources'] and suite['commands']
        for command in suite['commands']:
            if command['kind'] == 'profile':
                assert command['id'] in ids
            elif command['kind'] == 'package':
                assert command['id'] in package_ids
    for profile in profiles['commands']:
        if 'subject' in profile:
            assert profile['cache']['enabled'] is False
            mode = profile['subject']['mode']
            switch = profile['subject']['mode_argument']
            assert profile['argv'].count(switch) == 1
            assert profile['argv'][profile['argv'].index(switch) + 1] == mode
