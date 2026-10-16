"""One selected-project/registered-checkout proof shared by general tools."""
from dataclasses import dataclass
import os
from pathlib import Path

from ..common import PoiseError, load_config
from .git_transport import run_git_receipt


class ProjectBindingFailure(PoiseError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class BoundProject:
    root: Path
    config_path: Path
    config: dict
    workflows: dict
    checkout: Path


def read_project_checkout(config_path, checkout):
    try:
        root, cfg, workflows = load_config(Path(config_path))
    except (PoiseError, OSError, ValueError, TypeError, KeyError, RecursionError) as exc:
        raise ProjectBindingFailure('invalid_project') from exc
    environment = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
    environment['GIT_OPTIONAL_LOCKS'] = '0'

    def git(where, *arguments):
        receipt = run_git_receipt(where, list(arguments), cfg['limits']['git_seconds'], environment)
        if receipt['actual_exit_code'] != 0:
            raise PoiseError(receipt['stderr'])
        return receipt['stdout'].removesuffix('\n')

    try:
        repository, target = Path(cfg['git']['repository']).resolve(), Path(checkout).resolve()
        if Path(git(repository, 'rev-parse', '--show-toplevel')).resolve() != repository:
            raise PoiseError('Configured repository is not a checkout root')
        if Path(git(target, 'rev-parse', '--show-toplevel')).resolve() != target:
            raise PoiseError('Requested checkout is not its Git root')
        common = ('rev-parse', '--path-format=absolute', '--git-common-dir')
        if git(repository, *common) != git(target, *common):
            raise PoiseError('Requested checkout belongs to another repository')
        if f'worktree {target}\n' not in git(repository, 'worktree', 'list', '--porcelain') + '\n':
            raise PoiseError('Requested checkout is not registered')
    except (PoiseError, OSError, ValueError) as exc:
        raise ProjectBindingFailure('project_binding') from exc
    return BoundProject(root, Path(config_path).resolve(), cfg, workflows, target)
