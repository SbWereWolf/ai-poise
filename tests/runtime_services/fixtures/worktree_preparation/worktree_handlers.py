"""Two interchangeable fixture handlers; neither calls a product renderer."""


def accept(initial, invoke, options, context):
    return {'status': 'fixture-accepted', 'exit_code': initial.returncode,
            'marker': options['marker'], 'options': dict(options),
            'context': dict(context), 'stdout': initial.stdout.decode(),
            'stderr': initial.stderr.decode()}


def alternate(initial, invoke, options, context):
    # Deliberately omit streams: orchestration still owes their raw capture.
    return {'status': 'another-success-vocabulary', 'exit_code': initial.returncode,
            'marker': 'second-handler', 'options': dict(options), 'context': dict(context)}
