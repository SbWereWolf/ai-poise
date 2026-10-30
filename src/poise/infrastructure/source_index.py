"""Product-local index storage, common lock and complete process captures."""
from contextlib import contextmanager
from pathlib import Path
import shlex
import uuid

from ..common import PoiseError
from ..execution import RegisteredLaunchFailure
from ..modules.foundation.errors import LockUnavailable
from ..modules.source_index.domain import IndexFailure, IndexPolicy
from .git_transport import run_git_receipt
from .goal_config import strict_json
from .locking import exclusive_lock


class LocalSourceIndex:
    def __init__(self, bound, policy_path, policy, environment, runner):
        self.bound, self.policy_path, self.policy = bound, Path(policy_path), policy
        self.checkout = str(bound.checkout)
        self.paths = {key: (bound.checkout / value).resolve() for key, value in policy.paths.items()}
        self.database = str(self.paths['database'])
        self.environment = {k: v for k, v in environment.items() if not k.startswith('GIT_')}
        self.environment.update(AST_INDEX_ROOT=self.checkout, AST_INDEX_DB_PATH=self.database)
        self.runner = runner
        self.directory = self.paths['runs'] / str(uuid.uuid4())
        self.ordinal = 0

    @classmethod
    def load(cls, bound, policy_path, environment, runner):
        if not Path(policy_path).is_absolute():
            raise IndexFailure('rejected', 'policy: требуется абсолютный путь', 'Укажите явный policy path.')
        try:
            policy = IndexPolicy.parse(strict_json(Path(policy_path).read_text(encoding='utf-8')))
        except IndexFailure:
            raise
        except (PoiseError, OSError, ValueError, UnicodeError, RecursionError) as exc:
            raise IndexFailure('rejected', f'policy: {exc}', 'Исправьте выбранную policy.') from exc
        workspace = cls(bound, policy_path, policy, environment, runner)
        for key, address in workspace.paths.items():
            if not address.is_relative_to(bound.checkout):
                raise IndexFailure('rejected', f'{key}: containment — адрес outside выбранной копии',
                                   f'Укажите paths.{key} внутри {bound.checkout}.')
            observed = workspace._git('check-ignore', '-q', '--', str(address))
            if observed['actual_exit_code'] != 0:
                raise IndexFailure('rejected', f'{key}: адрес должен быть Git-ignored',
                                   f'Настройте Git-ignore для paths.{key} в выбранном продукте.')
        return workspace

    def _git(self, *arguments):
        env = dict(self.environment, GIT_OPTIONAL_LOCKS='0')
        return run_git_receipt(self.bound.checkout, list(arguments),
                               self.bound.config['limits']['git_seconds'], env)

    def head(self):
        try:
            receipt = self._git('rev-parse', 'HEAD')
            if receipt['actual_exit_code'] != 0:
                raise PoiseError(receipt['stderr'])
            return receipt['stdout'].strip()
        except PoiseError as exc:
            raise IndexFailure('inconclusive', f'HEAD: {exc}', self.recovery('status')) from exc

    @contextmanager
    def hold(self):
        try:
            with exclusive_lock(self.paths['lock'], self.policy.limits['lock_seconds'],
                                self.policy.limits['lock_poll_seconds']):
                yield
        except LockUnavailable as exc:
            raise IndexFailure('busy', f'lock: {exc}',
                               'Дождитесь завершения операции этой копии. ' + self.recovery('status')) from exc
        except OSError as exc:
            raise IndexFailure('unavailable', f'index storage: {exc}',
                               'Восстановите доступ к выбранным product-local paths.') from exc

    def database_exists(self):
        return self.paths['database'].is_file()

    def run(self, operation, arguments):
        self.ordinal += 1
        prefix = f'{self.ordinal:02d}-{operation}'
        try:
            return self.runner.run(str(uuid.uuid4()), [*self.policy.command_argv, *arguments],
                self.bound.checkout, self.environment, self.policy.limits['run_seconds'],
                self.directory / (prefix + '.stdout.txt'), self.directory / (prefix + '.stderr.txt'),
                cleanup_seconds=self.policy.limits['cleanup_seconds'])
        except RegisteredLaunchFailure as exc:
            raise IndexFailure('unavailable', f'tool: {self.policy.command_argv[0]}: {exc}',
                               'Установите или исправьте явно выбранный command_argv.') from exc
        except (PoiseError, OSError) as exc:
            raise IndexFailure('failed', f'{operation}: {exc}', self.recovery(operation)) from exc

    def health_document(self, execution):
        try:
            return strict_json(Path(execution['stdout']).read_bytes().decode('utf-8'))
        except (PoiseError, OSError, UnicodeError, ValueError, RecursionError) as exc:
            raise IndexFailure('unavailable', f'index: неинтерпретируемый stats: {exc}',
                               self.recovery('rebuild')) from exc

    def recovery(self, operation):
        # This text teaches a request; execution always uses an argv array.
        import sys
        command = [sys.executable, '-B', '-m', 'poise', 'source-index', '--config',
                   str(self.bound.config_path), '--checkout', self.checkout,
                   '--policy', str(self.policy_path)]
        if operation == 'query':
            return 'Прочитайте полные captures, устраните причину и повторите исходный query через ' + shlex.join(command)
        return 'Устраните причину и передайте operation=' + operation + ', arguments=[] через ' + shlex.join(command)
