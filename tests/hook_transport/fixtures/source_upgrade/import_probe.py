"""Observe the selected source/package and actually invoke its result handler."""
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys

import poise

root = Path(__file__).parent
packet = json.loads((root / 'probe-input.json').read_text(encoding='utf-8'))
try:
    infrastructure = importlib.import_module('apps.bootstrap.infrastructure')
    selected = importlib.import_module('apps.bootstrap.environment_result')
except ModuleNotFoundError as error:
    if error.name not in ('apps', 'apps.bootstrap', 'apps.bootstrap.infrastructure',
                          'apps.bootstrap.environment_result'):
        raise
    print(json.dumps({'status': 'missing_selected_handler', 'module': error.name}))
    raise SystemExit(0)

initial = subprocess.run([sys.executable, '-I', '-B', str(root / 'emit_result.py')],
                         capture_output=True, check=False)


def unexpected_repair(name):
    raise AssertionError('Ready input must not invoke correction: ' + name)


reply = infrastructure.load_handler(packet['handler'])(
    initial, unexpected_repair, packet['options'], packet['context'])
print(json.dumps({'status': 'observed', 'poise_origin': poise.__file__,
                  'handler_origin': selected.__file__, 'handler': reply,
                  'python_path': os.environ.get('PYTHONPATH'),
                  'project_config': os.environ.get('POISE_CONFIG')}, ensure_ascii=False))
