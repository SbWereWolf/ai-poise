"""Immutable check-time companions for explicitly declared system configs."""
from pathlib import Path

from ..common import PoiseError, file_digest
from .recovery_paths import recovery_absolute


class EvidenceConfigurationCapture:
    def __init__(self, systems, git):
        self.systems = systems
        self.git = git

    def preflight(self):
        result = {}
        for owner, spec in self.systems.items():
            commit = self.git(Path(spec['repository']), 'rev-parse', 'HEAD')
            configs = []
            for name in spec['configs']:
                path = recovery_absolute(name)
                if path.is_symlink() or not path.is_file():
                    raise PoiseError(f'Missing regular check config: {path}')
                configs.append({'path': name, 'digest': file_digest(path)})
            result[owner] = {'commit': commit, 'configs': configs}
        return result

    def observe_after(self, expected):
        """Postflight failures cannot erase a command's recorded termination."""
        try:
            actual = self.preflight()
        except (PoiseError, OSError) as exc:
            return False, {'error': str(exc)}
        return actual == expected, {'systems': actual}

    def capture(self, directory, expected):
        if self.preflight() != expected:
            raise PoiseError('Check configurations changed before command start')
        result = {}
        for owner, system in expected.items():
            configs = []
            for index, item in enumerate(system['configs']):
                target = directory / 'configurations' / owner / (str(index) + '-' + Path(item['path']).name)
                target.parent.mkdir(parents=True, exist_ok=True)
                content = Path(item['path']).read_bytes()
                if target.exists() and target.read_bytes() != content:
                    raise PoiseError('Immutable check configuration companion changed')
                target.write_bytes(content)
                if file_digest(target) != item['digest']:
                    raise PoiseError('Check configuration changed during capture')
                configs.append({**item, 'captured_path': str(target)})
            result[owner] = {'commit': system['commit'], 'configs': configs}
        return result
