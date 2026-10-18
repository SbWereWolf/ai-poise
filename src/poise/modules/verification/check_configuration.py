"""Pure companion configuration contract, independent of material transport."""
from copy import deepcopy
from pathlib import Path
from ..foundation.errors import PoiseError
from ..foundation.validation import configuration_fields


class CheckConfigurationSystems:
    @staticmethod
    def parse(check_runner):
        if 'systems' not in check_runner:
            return {}
        label = 'check_runner.systems'
        systems = configuration_fields(check_runner['systems'], {'poise', 'project'}, label, PoiseError)
        result = {}
        for owner in ('poise', 'project'):
            name = label + '.' + owner
            spec = configuration_fields(systems[owner], {'repository', 'configs'}, name, PoiseError)
            repository = spec['repository']
            if not isinstance(repository, str) or not repository or '\x00' in repository or not Path(repository).is_absolute():
                raise PoiseError(name + '.repository must be an absolute path')
            configs = spec['configs']
            if not isinstance(configs, list) or not configs:
                raise PoiseError(name + '.configs must be a nonempty list of absolute paths')
            if any(not isinstance(path, str) or not path or '\x00' in path or not Path(path).is_absolute() for path in configs):
                raise PoiseError(name + '.configs must contain absolute paths')
            result[owner] = {'repository': repository, 'configs': deepcopy(configs)}
        return result
