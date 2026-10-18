"""Real capture drives test observation data; runner deadlines stay real.

No global time patch, fabricated execution result, or seeded successful ledger.
This helper delegates the existing runner and every original output operation.
"""
from copy import deepcopy
import inspect
from pathlib import Path
import threading


class CapturedProgressClock:
    def __init__(self, events, specification, case):
        self.events = events
        self.specification = specification
        self.case = case
        self.phase = -1
        self.finished = False
        self.buffer = b''
        self.captured = []
        self.forwarded = []
        self.results = []
        self.result_snapshots = []
        self.lock = threading.RLock()

    def observe(self):
        from poise.modules.accounting.clock import ClockObservation
        with self.lock:
            if self.finished:
                value = self.case['finish_ns']
            elif self.phase >= 0:
                value = self.case['progress_ns'][self.phase]
            else:
                value = self.specification['started_ns']
            return ClockObservation(self.specification['audit_utc'], value, self.case['domain'])

    def captured_bytes(self, content):
        with self.lock:
            self.buffer += content
            while b'\n' in self.buffer:
                line, self.buffer = self.buffer.split(b'\n', 1)
                if not line.startswith(b'PROGRESS '):
                    continue
                expected = ('PROGRESS ' + str(len(self.captured)) + '/2').encode()
                assert line == expected, ('unexpected real fixture progress', line)
                self.phase += 1
                observation = self.observe()
                self.captured.append((line, observation))
                # This acknowledgement follows a delegated real write+flush of
                # a complete line. The child cannot coalesce its next event.
                (self.events / ('captured-progress-' + str(self.phase))).write_text('captured\n')

    def install(self, patch):
        from poise.execution import RegisteredCheckRunner
        original_run = RegisteredCheckRunner.run
        original_open = Path.open
        clock = self

        class ObservedWriter:
            def __init__(self, stream):
                self.stream = stream

            def __getattr__(self, name):
                return getattr(self.stream, name)

            def __enter__(self):
                self.stream.__enter__()
                return self

            def __exit__(self, *args):
                return self.stream.__exit__(*args)

            def write(self, content):
                written = self.stream.write(content)
                self.stream.flush()
                clock.captured_bytes(content[:written])
                return written

        def watched(runner, *args, **kwargs):
            bound = inspect.signature(original_run).bind(runner, *args, **kwargs)
            bound.apply_defaults()
            stdout = Path(bound.arguments['stdout_path'])
            clock.forwarded.append({'progress_gap_seconds': bound.arguments['progress_gap_seconds'],
                                    'timeout': bound.arguments['timeout']})
            assert len(clock.forwarded) == 1, 'Each fixture clock belongs to one actual child attempt'
            for index in range(3):
                (clock.events / ('captured-progress-' + str(index))).unlink(missing_ok=True)

            def opened(path, mode='r', *open_args, **open_kwargs):
                stream = original_open(path, mode, *open_args, **open_kwargs)
                if Path(path) == stdout and 'b' in mode and ('w' in mode or 'a' in mode):
                    return ObservedWriter(stream)
                return stream

            with patch.context() as capture:
                capture.setattr(Path, 'open', opened)
                result = original_run(runner, *args, **kwargs)
            clock.results.append(result)
            clock.result_snapshots.append(deepcopy(result))
            clock.finished = True
            return result  # Same unmodified actual process/deadline/capture result.

        patch.setattr(RegisteredCheckRunner, 'run', watched)
