"""Invoke the declared runtime owner while the Task's application still exists."""
import hashlib
import os
from pathlib import Path
from uuid import uuid4

from ..common import PoiseError, descendant
from ..execution import run_command
from .task_paths import task_root
from .worktree_preparation import PreparationConfiguration


class ConfiguredWorktreeRetirement:
    """Configuration and subprocess effects; process ownership stays external."""

    def __init__(self, runtime):
        self.h = runtime

    def retire(self, record, integration_request_id):
        configured = self.h.cfg.get('worktree_preparation')
        if configured is None:
            return None
        receipt = {
            'task_id': record['id'], 'integration_request_id': integration_request_id,
            'cwd': record['worktree'], 'argv': [], 'actual_exit_code': None,
            'timed_out': False, 'cancelled': False, 'capture_complete': False,
            'stdout': '', 'stderr': '', 'error': None,
        }
        try:
            path = Path(configured['configuration'])
            path = path if path.is_absolute() else self.h.config_path.parent / path
            receipt['configuration_path'] = str(path)
            raw = path.read_bytes()
            receipt['configuration_digest'] = hashlib.sha256(raw).hexdigest()
            owner_root = task_root(self.h.state, self.h.paths, record['id'], record['sprint_id'])
            coordinates = {
                'task_id': record['id'], 'project_id': self.h.cfg['project'],
                'worktree': record['worktree'], 'task_root': str(owner_root),
                'base': record['base'],
            }
            configuration = PreparationConfiguration.from_bytes(raw, path, coordinates)
            if 'retire' not in configuration.command.raw['commands']:
                return None
            argv = configuration.render_retirement(integration_request_id)
            receipt['argv'] = argv
            directory = descendant(owner_root, self.h.paths['runs']) / str(uuid4())
            stdout = descendant(directory, self.h.paths['stdout'])
            stderr = descendant(directory, self.h.paths['stderr'])
            receipt.update(stdout_path=str(stdout), stderr_path=str(stderr))
            result = run_command(argv, Path(record['worktree']), dict(os.environ), None, stdout, stderr)
            receipt.update({key: value for key, value in result.items() if key not in ('stdout', 'stderr')})
        except (PoiseError, OSError, ValueError, KeyError, TypeError) as exc:
            receipt['error'] = str(exc)
        for stream in ('stdout', 'stderr'):
            if stream + '_path' not in receipt:
                continue
            try:
                raw = Path(receipt[stream + '_path']).read_bytes()
                receipt[stream + '_digest'] = hashlib.sha256(raw).hexdigest()
                receipt[stream] = raw.decode('utf-8')
            except (OSError, UnicodeError) as exc:
                error = str(exc)
                receipt['error'] = error if receipt['error'] is None else receipt['error'] + '; ' + error
        return receipt
