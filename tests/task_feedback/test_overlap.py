"""TD-01: one ordinary append/cancellation overlap with real persisted outcomes."""
from pathlib import Path
from threading import Event, Thread, current_thread

from batch.helpers import request
from poise.modules.foundation.errors import PoiseError
from .helpers import (DATA, append, arrange, assert_reason, client, configure_case,
                      protected, read, read_bytes, source)


def test_append_and_cancellation_have_a_legal_serialized_result(project, monkeypatch):
    from poise.infrastructure.file_publication import BinaryFilePublisher
    configure_case(project)
    owner, _ = arrange(project, 'active')
    author = client(project)
    # A public prerequisite isolates missing API RED from worker/coordination errors.
    assert read(author)['comments'] == []
    item, expected = source(project)
    entered, release, cancel_started, cancel_finished = Event(), Event(), Event(), Event()
    results = {}
    original = BinaryFilePublisher.publish_bytes

    def pause_publication(cls, content, target, mode):
        if (current_thread().name == 'feedback-append' and
                Path(target).is_relative_to(project['material_root']) and not entered.is_set()):
            entered.set()
            if not release.wait(10):
                raise RuntimeError('FIXTURE-WATCHDOG: publication barrier was not released')
        return original(content, target, mode)

    def add_worker():
        try:
            results['append'] = ('ok', append(client(project), attachments=[item]))
        except BaseException as error:
            results['append'] = ('error', error)

    def cancel_worker():
        try:
            executor = client(project, 'executor')
            cancel_started.set()
            out = executor.invoke(request('cancel', {'reason': 'Fixture executor ends its work.'}))
            assert out['status'] == 'cancelled'
            # This snapshot is authoritative only if obtained before paused append resumes.
            results['terminal_snapshot'] = read(client(project, 'terminal-observer'))
            results['terminal_protected'] = protected(executor)
            results['cancel'] = ('ok', out)
        except BaseException as error:
            results['cancel'] = ('error', error)
        finally:
            cancel_finished.set()

    a = Thread(target=add_worker, name='feedback-append')
    b = Thread(target=cancel_worker, name='feedback-cancel')
    with monkeypatch.context() as patch:
        patch.setattr(BinaryFilePublisher, 'publish_bytes', classmethod(pause_publication))
        a.start()
        try:
            assert entered.wait(10), f'FIXTURE-WATCHDOG: append did not reach publication: {results}'
            b.start()
            assert cancel_started.wait(10), 'FIXTURE-WATCHDOG: cancellation did not start'
            # A short observation opportunity chooses no required product latency/order.
            # If serialization holds a needed resource, A is resumed without waiting for B.
            completed_while_paused = cancel_finished.wait(0.05)
        finally:
            release.set()
            a.join(10)
            if b.ident is not None:
                b.join(10)
        assert not a.is_alive() and not b.is_alive(), 'FIXTURE-WATCHDOG: workers did not terminate'
    assert results['cancel'][0] == 'ok', results['cancel']
    page = read(client(project, 'final-reader'))
    assert page['task']['status'] == 'cancelled'
    kind, value = results['append']
    if completed_while_paused:
        assert kind == 'error', 'New logical append committed after the terminal snapshot'
        assert isinstance(value, PoiseError), value
        assert_reason(value, 'terminal')
        assert page['task'] == results['terminal_snapshot']['task']
        assert page['comments'] == results['terminal_snapshot']['comments']
        assert protected(owner) == results['terminal_protected']
        assert page['comments'] == []
    elif kind == 'ok':
        assert page['comments'] == value['comments'] and len(page['comments']) == 1
        assert read_bytes(client(project, 'byte-reader'), page['comments'][0]['attachments'][0]) == expected
    else:
        assert isinstance(value, PoiseError), value
        assert_reason(value, 'terminal')
        assert page['comments'] == []
    assert owner.runtime.task_queries.record('T1')['status'] == 'cancelled'
