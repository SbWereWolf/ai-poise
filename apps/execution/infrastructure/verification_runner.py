"""Local committed-source observation and one actual retained process capture."""
import json
import os
from contextlib import ExitStack
from pathlib import Path
import subprocess

from apps.execution.domain.records import RecordsError
from apps.execution.domain.verification import KnownLaunchFailure


class VerificationRunner:
    def __init__(self, settings):
        self.settings = settings
        self.root = settings['repository']

    def git(self, *args, check=True):
        try:
            return subprocess.run(['git', *args], cwd=self.root, capture_output=True, check=check)
        except (OSError, subprocess.CalledProcessError) as exc:
            raise RecordsError('verification.repository Git observation failed: ' + str(exc)) from exc

    def source(self):
        root = self.git('rev-parse', '--show-toplevel').stdout.decode().strip()
        if Path(root).resolve() != self.root:
            raise RecordsError('verification.repository must name the prepared Git checkout root')
        return {'commit': self.git('rev-parse', 'HEAD').stdout.decode().strip(),
                'tree': self.git('rev-parse', 'HEAD^{tree}').stdout.decode().strip()}

    def require_ancestor(self, commit, ref):
        if self.git('merge-base', '--is-ancestor', commit, ref, check=False).returncode != 0:
            raise RecordsError('Accepted source commit must be integrated into configured base_ref and available locally')

    def directory(self, run):
        return self.settings['output_root'] / run['run_id']

    @staticmethod
    def save_capture(path, value):
        temporary = path.with_name(path.name + '.writing')
        temporary.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
        temporary.replace(path)

    @staticmethod
    def capture_outputs(declarations, directory):
        complete, outputs = True, []
        for declaration in declarations:
            path = directory / declaration['path']
            status = 'captured' if path.is_file() else 'invalid' if path.exists() else 'missing'
            if status == 'invalid' or status == 'missing' and declaration['required']:
                complete = False
            outputs.append({'id': declaration['id'], 'declared_path': declaration['path'],
                            'required': declaration['required'], 'status': status,
                            'path': str(path.resolve()) if status == 'captured' else None,
                            'size': path.stat().st_size if status == 'captured' else None})
        return outputs, complete

    def run(self, data, run, method, subject_rules):
        attempt = data['value']
        directory = self.directory(run)
        with ExitStack() as streams:
            try:
                directory.mkdir(parents=True, exist_ok=True)
                cwd = (self.root / method['cwd']).resolve()
                before = self.source()
                environment = {**os.environ, **method['environment'], 'POISE_RUN_OUTPUT_DIR': str(directory)}
                out = streams.enter_context((directory / 'stdout.bin').open('wb'))
                err = streams.enter_context((directory / 'stderr.bin').open('wb'))
                child = subprocess.Popen(method['argv'], cwd=cwd, env=environment,
                                         stdout=out, stderr=err, start_new_session=True)
            except (OSError, RecordsError) as exc:
                error = str(exc)
                try:
                    self.save_capture(directory / 'launch-failure.json', {
                        'launch_id': data['requests'][run['run_id']]['launch_id'],
                        'attempt_id': attempt['attempt_id'], 'run_id': run['run_id'],
                        'command_not_started': True, 'error': error})
                except OSError as storage_error:
                    error += '; launch capture unavailable: ' + str(storage_error)
                raise KnownLaunchFailure(error) from exc
            # Once Popen returns, the command may have effects. Later failures
            # retain the committed start and never restore launch permission.
            child.communicate()
        try:
            os.killpg(child.pid, 0)
            stopped = False
        except ProcessLookupError:
            stopped = True
        stdout, stderr = (directory / 'stdout.bin').read_bytes(), (directory / 'stderr.bin').read_bytes()
        try:
            stdout.decode('utf-8'); stderr.decode('utf-8')
            interpretable = True
        except UnicodeDecodeError:
            interpretable = False
        outputs, complete = self.capture_outputs(method['outputs'], directory)
        after = self.source()
        termination = {'schema': 'run-termination-1', 'child_reaped': True,
                       'process_group_stopped': stopped, 'capture_complete': True}
        text_out, text_err = stdout.decode('utf-8', 'replace'), stderr.decode('utf-8', 'replace')
        passed = (complete and interpretable and stopped and child.returncode == method['expected_exit_code']
                  and all(x in text_out for x in method['stdout_contains'])
                  and all(x in text_err for x in method['stderr_contains'])
                  and before == after == attempt['source'])
        receipt = {'id': run['run_id'], 'method': method['id'], 'obligations': [method['id']],
                   'argv': method['argv'], 'cwd': method['cwd'], 'environment': method['environment'],
                   'actual_exit_code': child.returncode, 'stdout': text_out, 'stderr': text_err,
                   'capture_complete': complete, 'interpretable': interpretable, 'timed_out': False,
                   'guard': method['id'] not in subject_rules, 'passed': passed,
                   'commit': attempt['source']['commit'], 'tree': attempt['source']['tree'],
                   'source_before': before, 'source_after': after, 'source_unchanged': before == after == attempt['source'],
                   'attempt_id': attempt['attempt_id'], 'run_id': run['run_id'],
                   'execution_key': data['execution_key'], 'stdout_bytes_hex': stdout.hex(),
                   'stderr_bytes_hex': stderr.hex(), 'resolved_cwd': str(cwd),
                   'output_directory': str(directory), 'outputs': outputs, 'termination': termination}
        self.save_capture(directory / 'capture.json', {
            'launch_id': data['requests'][run['run_id']]['launch_id'], 'receipt': receipt})
        return receipt

    def recover(self, data, run):
        directory = self.directory(run)
        launch_id = data['requests'][run['run_id']]['launch_id']
        path = directory / 'capture.json'
        capture = json.loads(path.read_text(encoding='utf-8')) if path.is_file() else None
        if capture is None or capture['launch_id'] != launch_id:
            failed_path = directory / 'launch-failure.json'
            failed = json.loads(failed_path.read_text(encoding='utf-8')) if failed_path.is_file() else None
            if failed is not None and failed['launch_id'] == launch_id:
                if (failed['attempt_id'] != data['value']['attempt_id']
                        or failed['run_id'] != run['run_id'] or failed['command_not_started'] is not True):
                    raise RecordsError('Select the launch outcome for this actual reserved run')
                raise KnownLaunchFailure(failed['error'])
            return None
        receipt = capture['receipt']
        if (receipt['attempt_id'] != data['value']['attempt_id'] or receipt['run_id'] != run['run_id']
                or receipt['execution_key'] != data['execution_key']):
            raise RecordsError('Retained run capture belongs to another reserved attempt')
        if not receipt['termination']['child_reaped'] or not receipt['termination']['process_group_stopped']:
            return None
        return receipt
