"""Explicit project reads and exact instruction bytes; no mutable runtime setup."""
import hashlib
from pathlib import Path

from ..common import PoiseError, descendant
from ..modules.skills.catalog import SkillCatalog
from ..modules.skills.instructions import InstructionInputError
from .goal_config import strict_json
from .project_binding import ProjectBindingFailure, read_project_checkout


class FileInstructionSource:
    def __init__(self, catalog_path, instruction_root):
        self.catalog_path = Path(catalog_path)
        self.instruction_root = Path(instruction_root).resolve()

    def project_context(self, config_path, checkout):
        try:
            bound = read_project_checkout(config_path, checkout)
            return {'project': bound.config['project'], 'checkout': str(bound.checkout),
                    'instruction_root': str(self.instruction_root)}
        except ProjectBindingFailure as exc:
            if exc.code == 'invalid_project':
                raise InstructionInputError('invalid_project', 'project_config', 'provide:project_config') from exc
            raise InstructionInputError(exc.code, 'checkout', 'select:checkout') from exc

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
