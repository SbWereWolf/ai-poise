"""Opt-in semantic progress on the sole existing capture/process owner."""
import codecs
import json
from copy import deepcopy
from pathlib import Path

from ...modules.project_checks.contracts import CheckFailure, UnstartedCheck
from ...execution import RegisteredLaunchFailure
from ...modules.project_checks.parsers import parse_lines
from ...modules.project_checks.profiles import resolved_selection
from ...modules.project_checks.progress import ProgressTracker
from ...modules.project_checks.timing import observation, elapsed
from .evidence import EvidenceCollector, file_reference


class ProgressObserver:
    def __init__(self, profile, clock, artifact, file_mode):
        self.tracker = ProgressTracker(profile)
        self.clock, self.artifact, self.file_mode = clock, artifact, file_mode
        self.decoders = {name: codecs.getincrementaldecoder('utf-8')(errors='replace')
                         for name in ('stdout', 'stderr')}
        self.buffers = {name: '' for name in self.decoders}

    def _line(self, line):
        if self.tracker.regex is None or self.tracker.regex.search(line) is None:
            return False
        event = self.tracker.observe_line(line, observation(self.clock.observe()))
        if event is None:
            return False
        self.artifact.parent.mkdir(parents=True, exist_ok=True)
        with self.artifact.open('a', encoding='utf-8', newline='\n') as stream:
            stream.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + '\n')
        self.artifact.chmod(self.file_mode)
        return True

    def __call__(self, stream, content):
        self.buffers[stream] += self.decoders[stream].decode(content)
        lines = self.buffers[stream].splitlines(keepends=True)
        self.buffers[stream] = ''
        if lines and not lines[-1].endswith(('\n', '\r')):
            self.buffers[stream] = lines.pop()
        advanced = False
        for line in lines:
            advanced = self._line(line.rstrip('\r\n')) or advanced
        return advanced

    def finish(self):
        for name in self.decoders:
            remaining = self.buffers[name] + self.decoders[name].decode(b'', final=True)
            if remaining:
                self._line(remaining)
            self.buffers[name] = ''


class CheckExecution:
    def __init__(self, configuration, runner, clock, subject_owner):
        self.configuration, self.runner, self.clock = configuration, runner, clock
        self.subject_owner = subject_owner

    def run(self, step, addresses, timeout, stall, cache, subject):
        configuration = self.configuration
        profile, invocation = step['profile'], step['invocation']
        environment = dict(step['environment'])
        environment['REPO_CHECK_EVIDENCE_DIR'] = str(addresses['evidence'])
        if subject is not None and subject['declaration'] is not None:
            environment[profile['subject']['environment_key']] = json.dumps(subject['declaration'], ensure_ascii=False)
        elif subject is not None:
            environment['REPO_CHECK_SCOPE_PATHS_JSON'] = json.dumps(subject['paths'], ensure_ascii=False)
        mode = configuration.bound.config['batch']['file_mode']
        observer = ProgressObserver(profile['progress'], self.clock, addresses['progress'], mode)
        started = observation(self.clock.observe())
        control = getattr(self, 'invocation', None)
        coordination = {} if control is None else {
            'invocation_cancellation': control, 'cancellation_policy': control.policy}
        try:
            raw = self.runner.run(addresses['id'], invocation['argv'], Path(invocation['cwd']), environment,
                                  timeout['effective_seconds'], addresses['stdout'], addresses['stderr'],
                                  progress_gap_seconds=stall if profile['progress']['mode'] == 'regex' and stall else None,
                                  poll_seconds=configuration.policy['timing']['poll_seconds'],
                                  cleanup_seconds=configuration.policy['timing']['cleanup_seconds'],
                                  capture_observer=observer if profile['progress']['mode'] == 'regex' else None,
                                  **coordination)
        except RegisteredLaunchFailure as exc:
            raise UnstartedCheck('prerequisites_unavailable', step['field'] + '.argv[0]', str(exc),
                                 'Укажите действительный исполняемый файл для выбранной среды.',
                                 status='unavailable') from exc
        observer_error = raw.get('observer_error')
        try:
            observer.finish()
        except OSError as exc:
            observer_error = str(exc)
        finished = observation(self.clock.observe())
        duration = elapsed(started, finished)
        error = None
        status = ('cancelled' if raw['cancelled'] else
                  'stalled' if raw['timed_out'] and raw['timeout_reason'] == 'progress_gap' else
                  'timed_out' if raw['timed_out'] else
                  'passed' if raw['actual_exit_code'] == 0 else 'failed')
        references = {}
        for name in ('stdout', 'stderr'):
            try:
                references[name] = file_reference(addresses[name])
            except OSError:
                references[name] = None
        termination = {'capture_complete': raw['capture_complete'] and all(references.values()),
                       'child_reaped': raw['termination']['child_reaped'],
                       'process_group_stopped': raw['termination']['process_group_stopped'],
                       'timed_out': raw['timed_out'], 'cancelled': raw['cancelled'],
                       'timeout_reason': raw['timeout_reason']}
        parsed = None
        evidence = {'status': 'not_declared', 'manifest_path': None, 'files': []}
        if termination['capture_complete']:
            # Supplied full lines, independent of every agent preview.
            lines = []
            try:
                for name in ('stdout', 'stderr'):
                    lines.extend(addresses[name].read_text(encoding='utf-8', errors='replace').splitlines())
                parsed = parse_lines(lines, profile['parser'], profile['output']['max_failures'])
            except OSError:
                termination['capture_complete'] = False
            if termination['capture_complete']:
                evidence_id = profile.get('evidence_id')
                contract = None if evidence_id is None else configuration.profiles.evidence[evidence_id]
                try:
                    evidence = EvidenceCollector(mode).collect(addresses['evidence'], contract)
                except OSError:
                    evidence = {'status': 'invalid', 'manifest_path': None, 'files': []}
        try:
            self.subject_owner.recheck(subject)
        except CheckFailure as exc:
            error = exc
            if status == 'passed':
                status = 'unconfirmed'
        if not termination['capture_complete'] or observer_error is not None:
            error = CheckFailure('capture_failed', 'run.capture',
                                 observer_error if observer_error is not None else 'Полнота сохранённого вывода не подтверждена.',
                                 'Исправьте размещение материалов и выполните новую проверку.')
        elif duration is None or not observer.tracker.clock_comparable:
            error = CheckFailure('clock_incomparable', 'run.clock',
                                 'Наблюдения Clock относятся к несравнимым доменам или идут в обратном порядке.',
                                 'Выполните новую проверку с согласованным источником монотонных наблюдений.')
        elif parsed is not None and profile['require_parser_confirmation'] and not parsed['confirmed']:
            error = CheckFailure('parser_unconfirmed', 'profile.parser', 'Обязательная сводка не распознана.',
                                 'Проверьте выбранный парсер и настоящий вывод команды.')
        elif evidence['status'] not in ('not_declared', 'complete'):
            error = CheckFailure('evidence_incomplete', 'profile.evidence_id', 'Объявленные материалы неполны.',
                                 'Исправьте создание обязательных материалов и выполните новую проверку.')
        if error is not None and status == 'passed':
            status = 'unconfirmed'
        if control is not None:
            control.observed({**raw, 'termination': {
                **raw['termination'], 'capture_complete': termination['capture_complete']}})
        return {'schema': 'poise-named-check-run-1', 'run_id': addresses['id'],
                'project': configuration.bound.config['project'], 'checkout': str(configuration.bound.checkout),
                'selection': resolved_selection(profile), 'profile_sha256': configuration.profiles_sha256,
                'argv': list(invocation['argv']), 'cwd': invocation['cwd'], 'runtime': invocation['runtime'],
                'status': status, 'executed': True, 'actual_exit_code': raw['actual_exit_code'],
                'started': started, 'finished': finished, 'duration_seconds': duration,
                'termination': termination, 'timeout': timeout, 'progress': observer.tracker.snapshot(),
                'parser': parsed, 'evidence': evidence, 'cache': deepcopy(cache), 'subject': subject,
                'stdout': references['stdout'], 'stderr': references['stderr'],
                'record_path': addresses['record_path'], 'verified_success': status == 'passed' and error is None,
                'error': None if error is None else error.as_dict()}
