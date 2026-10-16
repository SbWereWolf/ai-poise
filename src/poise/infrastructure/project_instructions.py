"""Explicit project reads and exact instruction bytes; no mutable runtime setup."""
import hashlib
import os
from pathlib import Path

from ..common import PoiseError, descendant, load_config
from ..modules.skills.catalog import SkillCatalog
from ..modules.skills.instructions import InstructionInputError
from .goal_config import strict_json
from .git_transport import run_git_receipt


class FileInstructionSource:
    def __init__(self, catalog_path, instruction_root):
        self.catalog_path = Path(catalog_path)
        self.instruction_root = Path(instruction_root).resolve()

    def project_context(self, config_path, checkout):
        try:
            _, cfg, _ = load_config(Path(config_path))
        except (PoiseError, OSError, ValueError, TypeError, KeyError, RecursionError) as exc:
            raise InstructionInputError('invalid_project', 'project_config', 'provide:project_config') from exc
        environment = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
        environment['GIT_OPTIONAL_LOCKS'] = '0'

        def git(root, *arguments):
            result = run_git_receipt(root, list(arguments), cfg['limits']['git_seconds'], environment)
            if result['actual_exit_code'] != 0:
                raise PoiseError(result['stderr'])
            return result['stdout'].removesuffix('\n')

        try:
            repository, target = Path(cfg['git']['repository']).resolve(), Path(checkout).resolve()
            if Path(git(repository, 'rev-parse', '--show-toplevel')).resolve() != repository:
                raise PoiseError('Configured repository is not a checkout root')
            if Path(git(target, 'rev-parse', '--show-toplevel')).resolve() != target:
                raise PoiseError('Requested checkout is not its Git root')
            common_args = ('rev-parse', '--path-format=absolute', '--git-common-dir')
            if git(repository, *common_args) != git(target, *common_args):
                raise PoiseError('Requested checkout belongs to another repository')
            if f'worktree {target}\n' not in git(repository, 'worktree', 'list', '--porcelain') + '\n':
                raise PoiseError('Requested checkout is not registered')
            return {'project': cfg['project'], 'checkout': str(target),
                    'instruction_root': str(self.instruction_root)}
        except (PoiseError, OSError, ValueError) as exc:
            raise InstructionInputError('project_binding', 'checkout', 'select:checkout') from exc

    def catalogue_metadata(self):
        try:
            return SkillCatalog.from_configuration(strict_json(self.catalog_path.read_bytes().decode('utf-8')))
        except (PoiseError, OSError, ValueError, TypeError, RecursionError) as exc:
            raise InstructionInputError('invalid_policy', 'policy', 'correct:policy') from exc

    def policy_document(self, path):
        try:
            data = Path(path).read_bytes()
        except OSError as exc:
            raise InstructionInputError('unreadable_policy', 'policy', 'restore:policy') from exc
        try:
            return strict_json(data.decode('utf-8')), hashlib.sha256(data).hexdigest()
        except (PoiseError, ValueError, RecursionError) as exc:
            raise InstructionInputError('invalid_policy', 'policy', 'correct:policy') from exc

    def existing_rule_paths(self, checkout, paths):
        # ERP collect_effective_agents parent enumeration, adapted to file scopes.
        # Probe only exact ancestors; no unrelated tree walk or forced root rule.
        candidates = {}
        for path in paths:
            parts = path.split('/')[:-1]
            for count in range(len(parts) + 1):
                name = '/'.join([*parts[:count], 'AGENTS.md'])
                candidates.setdefault(name, []).append(path)
        result = []
        for name, scopes in sorted(candidates.items()):
            try:
                descendant(Path(checkout), name).stat()
            except FileNotFoundError:
                continue
            except (OSError, PoiseError):
                # An unreadable candidate is observed as unavailable, not skipped.
                pass
            result.append((name, scopes))
        return result

    def observe_materials(self, context, specs):
        observations, cache = [], {}
        for spec in specs:
            root = context['checkout'] if spec.root == 'checkout' else context['instruction_root']
            key = (root, spec.path)
            if key not in cache:
                try:
                    data = descendant(Path(root), spec.path).read_bytes()
                    content = data.decode('utf-8')
                    cache[key] = {'available': True, 'content': content,
                                  'sha256': hashlib.sha256(data).hexdigest()}
                except (OSError, PoiseError, UnicodeError):
                    cache[key] = {'available': False, 'content': None, 'sha256': None}
            observations.append({**spec.public(), **cache[key]})
        return observations
