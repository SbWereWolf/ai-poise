"""Real delivery obstruction preserves source and foreign work until continuation."""
from copy import deepcopy

from batch.helpers import request
from result_integration.helpers import integration_input
from .helpers import (absent, agree, arrange, declaration, delivery_status,
                      file_output, fresh, settle)


def test_destination_conflict_blocks_delivery_until_the_obstruction_is_resolved(project):
    tools, root, source, commit = arrange(project)
    destination = project['root'] / 'customer-result.txt'
    destination.write_bytes(b'pre-existing foreign result\n')
    agree(tools, declaration([file_output(source, destination)]))
    assert tools.invoke(request('accept', {}))['status'] == 'completed'
    assert root.is_dir()
    foreign, foreign_root, foreign_source, _ = arrange(
        project, identifier='FOREIGN-WORK', session='foreign-owner')
    foreign_record = deepcopy(foreign.runtime.task_queries.record('FOREIGN-WORK'))
    foreign_history = foreign.runtime.task_queries.history('FOREIGN-WORK')
    history = tools.runtime.task_queries.history('T1')

    integrated = tools.invoke(request('integrate', integration_input(project, commit)))
    assert integrated['status'] == 'cleanup_blocked'
    blocked = settle(fresh(project))

    assert blocked['status'] == 'delivery_blocked'
    assert blocked['reason'] == 'destination_conflict'
    assert root.is_dir() and source.read_bytes() == b'customer result\n'
    assert destination.read_bytes() == b'pre-existing foreign result\n'
    assert tools.runtime.task_queries.history('T1')[:len(history)] == history
    assert foreign.runtime.task_queries.record('FOREIGN-WORK') == foreign_record
    assert foreign.runtime.task_queries.history('FOREIGN-WORK') == foreign_history
    assert foreign_source.read_bytes() == b'customer result\n'

    preserved = destination.with_name('preserved-foreign-result.txt')
    destination.rename(preserved)
    assert settle(fresh(project))['status'] == 'delivery_complete'
    assert delivery_status(fresh(project))['status'] == 'delivery_complete'
    absent(root)
    assert destination.read_bytes() == b'customer result\n'
    assert preserved.read_bytes() == b'pre-existing foreign result\n'
    assert foreign.runtime.task_queries.record('FOREIGN-WORK') == foreign_record
    assert foreign.runtime.task_queries.history('FOREIGN-WORK') == foreign_history
    assert foreign_root.is_dir() and foreign_source.read_bytes() == b'customer result\n'
