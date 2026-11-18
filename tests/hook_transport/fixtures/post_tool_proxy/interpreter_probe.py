"""Transparent Popen observer delegating every actual command unchanged."""
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys

options = json.loads(Path(__file__).with_suffix('.json').read_text())
arguments = sys.argv[1:]
module = arguments.index('-m') if '-m' in arguments else -1
legacy = module >= 0 and arguments[module + 1:module + 3] == ['poise', 'hook']
if os.environ.get('POST_TOOL_SPAWN_RECORD') and module >= 0 and not legacy:
    original = subprocess.Popen

    def observed(argv, *args, **kwargs):
        text = ' '.join(map(str, argv))
        target = None
        if options['metric_script'] in text:
            target = 'metric'
        elif ' -m poise hook ' in text:
            for number, path in enumerate(options['member_probes']):
                if path in text:
                    target = number
                    break
        if target is not None:
            with Path(os.environ['POST_TOOL_SPAWN_RECORD']).open('a') as stream:
                stream.write(json.dumps({'target': target, 'argv': argv}) + '\n')
        if target == os.environ.get('POST_TOOL_FAIL_TARGET'):
            raise OSError('fixture actual foreign Popen failure')
        return original(argv, *args, **kwargs)

    subprocess.Popen = observed
    sys.argv = ['poise', *arguments[module + 2:]]
    runpy.run_module('poise', run_name='__main__')
else:
    # Existing recorder stays the owner of fixed/delegate native callback probes.
    exec(compile(Path(options['probe_source']).read_bytes(), options['probe_source'], 'exec'),
         {'__name__': '__main__', '__file__': __file__})
