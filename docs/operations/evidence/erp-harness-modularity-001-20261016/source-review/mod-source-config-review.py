"""Independent literal oracle for MOD-SOURCE-CONFIG-001; no task-state writes."""
import copy
import importlib.util
import json
from pathlib import Path
import sys

source = Path(sys.argv[1])
spec = importlib.util.spec_from_file_location('reviewed_profile_owner', source)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

BASE = {
    'command_id': 'review-fixture', 'workload_class': 'default', 'runtime_class': 'test-host',
    'platforms': ['linux'], 'invocation': 'exec', 'argv': ['python3', '--version'],
    'cwd': '.', 'parser': 'generic', 'require_parser_confirmation': False,
    'execution_timeout_seconds': 1, 'observed_max_success_seconds': 0,
    'observed_max_progress_gap_seconds': 0, 'stall_timeout_seconds': 0,
    'progress': {'mode': 'unavailable'},
    'output': {'success_bytes': 1024, 'failure_bytes': 1024, 'timeout_bytes': 1024, 'max_failures': 1},
    'prerequisites': {'executables': [], 'files': [], 'environment': []},
    'cache': {'enabled': False, 'input_paths': [], 'environment': []},
    'initial_limit_reason': 'Independent review fixture', 'last_calibrated_at': '2026-10-16T00:00:00Z',
}
PATHS = [
    ('cache',), ('stall_timeout_seconds',), ('observed_max_progress_gap_seconds',),
    ('cache', 'enabled'), ('cache', 'input_paths'), ('cache', 'environment'),
    ('prerequisites', 'executables'), ('prerequisites', 'files'), ('prerequisites', 'environment'),
]
failures = []
results = []
def check(name, raw, rejected):
    try:
        module.CommandProfile.from_raw(raw, 0)
    except module.ProfileError as error:
        actual = 'rejected'
        detail = str(error)
    else:
        actual = 'accepted'
        detail = None
    expected = 'rejected' if rejected else 'accepted'
    results.append({'case': name, 'expected': expected, 'actual': actual, 'error': detail})
    if actual != expected:
        failures.append(name)

check('valid-zero-and-empty-values', copy.deepcopy(BASE), False)
for path in PATHS:
    for mutation in ('missing', 'null', 'wrong-type'):
        raw = copy.deepcopy(BASE)
        parent = raw if len(path) == 1 else raw[path[0]]
        key = path[-1]
        if mutation == 'missing':
            del parent[key]
        else:
            parent[key] = None if mutation == 'null' else 'invalid-known-value'
        check(mutation + ':' + '.'.join(path), raw, True)

raw = copy.deepcopy(BASE)
raw['future_parameter'] = {'anything': True}
for table in ('cache', 'prerequisites', 'progress', 'output'):
    raw[table]['future_parameter'] = {'anything': True}
check('unknown-keys-ignored-at-all-affected-levels', raw, False)
for value in (False, -1, 0.5):
    raw = copy.deepcopy(BASE); raw['stall_timeout_seconds'] = value
    check('invalid-stall:' + repr(value), raw, True)
for value in (False, -1, float('inf'), float('nan')):
    raw = copy.deepcopy(BASE); raw['observed_max_progress_gap_seconds'] = value
    check('invalid-gap:' + repr(value), raw, True)
raw = copy.deepcopy(BASE); raw['cache']['enabled'] = True
check('explicit-cache-enabled-empty-inputs', raw, False)
print(json.dumps({'finding_id': 'MOD-SOURCE-CONFIG-001', 'cases': len(results),
                  'failed_cases': failures, 'results': results}, ensure_ascii=False))
raise SystemExit(1 if failures else 0)
