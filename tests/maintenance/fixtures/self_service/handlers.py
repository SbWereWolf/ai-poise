"""Two independently chosen implementations of the existing handler port."""
import json


def first(initial, invoke, options, context):
    return {'exit_code': 0, 'selected': 'first', 'operation': context['operation'],
            'stdout': initial.stdout.decode(), 'stderr': initial.stderr.decode(),
            'actual_exit': initial.returncode}


def second(initial, invoke, options, context):
    return {'exit_code': 7, 'selected': 'second', 'operation': context['operation'],
            'stdout': initial.stdout.decode(), 'stderr': initial.stderr.decode(),
            'actual_exit': initial.returncode}


class StatusInterpreter:
    def interpret(self, raw, options):
        value = json.loads(raw['stdout'])
        success = raw['exit_code'] in options['accept'] and value['status'] in options['statuses']
        return {'success': success, 'value': value,
                'error': None if success else 'independent negative resource state'}


status_result = StatusInterpreter()
