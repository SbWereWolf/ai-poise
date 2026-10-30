"""Own a bounded verification window around an unchanged consumer run route."""
import json
import os
from pathlib import Path
import sys
from threading import Event

from poise.execution import run_command


def run_declared_entry(inputs, display_environment):
    root = Path(inputs['evidence'])
    argv = list(inputs['argv'])
    original = Path(inputs['parameters'])
    before = original.read_bytes()
    parameters = json.loads(before)
    # The GUI bindings are declared working inputs. The run action and every
    # non-display value remain the actual selected application configuration.
    if inputs['private_display']:
        assert 'display' in parameters and 'xauthority' in parameters
        parameters['display'] = display_environment['DISPLAY']
        parameters['xauthority'] = display_environment['XAUTHORITY']
        selected = root / 'private-display-parameters.json'
        selected.write_text(json.dumps(parameters) + '\n')
        index = argv.index('--parameters') + 1
        assert argv[index] == str(original)
        argv[index] = str(selected)
    outcome = run_command(argv, Path(inputs['base']), inputs['environment'],
        inputs['command_timeout'], root / 'declared-run.stdout', root / 'declared-run.stderr')
    (root / 'declared-run.json').write_text(json.dumps(outcome, indent=2) + '\n')
    assert original.read_bytes() == before
    return outcome


def main():
    inputs = json.loads(Path(sys.argv[1]).read_text())
    outcome = run_declared_entry(inputs, os.environ)
    if outcome['actual_exit_code'] != 0 or not outcome['capture_complete'] or outcome['timed_out']:
        return 1
    # The private PID namespace owns detached console children until this
    # window closes. This wait does not observe or rerun the consumer command.
    Event().wait(inputs['window_seconds'])
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
