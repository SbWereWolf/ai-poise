"""Placement bytes and filesystem observations through existing operational owners."""
from contextlib import contextmanager, ExitStack
import hashlib
import os
from pathlib import Path

from ..common import configured_root, encoded, PoiseError
from ..modules.placement.contracts import PlacementInputError
from .goal_config import atomic_write
from .locking import exclusive_lock
from .project_resources import ProjectRuntimeFence


class FilePlacementWorkspace:
    def __init__(self, bound, policy, caller_key):
        cfg = bound.config
        state = configured_root(bound.root, cfg['paths']['state'])
        directory = Path(policy.state['directory']).resolve()
        if not directory.is_relative_to(state):
            raise PlacementInputError('invalid_policy', 'policy', 'correct:policy')
        namespace = directory / caller_key / hashlib.sha256(str(bound.checkout).encode('utf-8')).hexdigest()
        self.file = namespace / policy.state['filename']
        self.path = str(self.file)
        self.lock = namespace / policy.state['lock']
        self.checkout = bound.checkout
        self.mode = cfg['batch']['file_mode']
        self.wait, self.poll = cfg['limits']['lock_seconds'], cfg['limits']['lock_poll_seconds']
        try:
            self.fence = ProjectRuntimeFence(bound.config_path, expected_state=state)
        except (PoiseError, OSError, ValueError) as exc:
            raise PlacementInputError('invalid_project', 'config', 'provide:config') from exc

    @contextmanager
    def locked(self, operation):
        with ExitStack() as stack:
            try:
                stack.enter_context(self.fence.locked())
            except (PoiseError, OSError, ValueError) as exc:
                raise PlacementInputError('invalid_project', 'config', 'provide:config') from exc
            # A first check cannot create even a lock/baseline as an observation side effect.
            if operation == 'check' and self.missing():
                yield
            else:
                stack.enter_context(exclusive_lock(self.lock, self.wait, self.poll, existing=operation == 'check'))
                yield

    def missing(self):
        return not self.file.exists()

    def read(self):
        try:
            return self.file.read_bytes()
        except FileNotFoundError:
            return None

    def baseline(self):
        return sorted(v.name for v in self.checkout.iterdir())

    def encode(self, document):
        return (encoded(document) + '\n').encode('utf-8')

    def write(self, content):
        atomic_write(self.file, content, self.mode)

    def confirm(self):
        with self.file.open('rb') as stream:
            os.fsync(stream.fileno())
        fd = os.open(self.file.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
