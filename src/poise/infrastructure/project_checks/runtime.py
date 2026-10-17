"""Exact runtime invocation data and actual launch prerequisite observation."""
import os
from pathlib import Path
import platform
import shutil
import subprocess

from ...modules.project_checks.contracts import CheckFailure


class RuntimeAdapter:
    def __init__(self, checkout, adapters, environment):
        self.checkout, self.adapters, self.environment = Path(checkout), adapters, environment

    def resolve(self, profile, *, argv=None, cwd=None):
        candidates = [v for v in self.adapters if v['id'] == profile['runtime_class']
                      and v['invocation'] == profile['invocation']
                      and set(v['platforms']) & set(profile['platforms'])]
        if len(candidates) != 1:
            raise CheckFailure('runtime_unavailable', 'profile.runtime_class',
                               f'Нет единственного адаптера {profile["runtime_class"]}.',
                               'Объявите выбранный runtime adapter в policy.', status='unavailable')
        adapter = candidates[0]
        payload = list(profile['argv'] if argv is None else argv)
        resolved = payload if adapter['invocation'] == 'exec' else [
            adapter['command_processor'], '/d', '/s', '/c', subprocess.list2cmdline(payload)]
        target = self.checkout / (profile['cwd'] if cwd is None else cwd)
        if not target.resolve().is_relative_to(self.checkout.resolve()):
            raise CheckFailure('invalid_profiles', 'profile.cwd', 'cwd выходит за выбранный checkout.',
                               'Укажите cwd внутри выбранной рабочей копии.')
        actual_platform = platform.system().lower()
        available = (os.name == 'posix' and adapter['invocation'] == 'exec'
                     and actual_platform in adapter['platforms']
                     and actual_platform in profile['platforms'])
        return {'argv': resolved, 'cwd': str(target.resolve()),
                'runtime': {'executable': shutil.which(resolved[0], path=self.environment.get('PATH')),
                            'platform': actual_platform,
                            'invocation': profile['invocation']}, 'available': available}

    def prerequisites(self, profile, invocation, environment, field):
        if not invocation['available']:
            raise CheckFailure('runtime_unavailable', field,
                               f'Среда {profile["runtime_class"]} недоступна на этом host.',
                               'Выберите доступную платформу и исполняемый runtime.', status='unavailable')
        missing = []
        for index, executable in enumerate([invocation['argv'][0], *profile['prerequisites']['executables']]):
            if shutil.which(executable, path=environment.get('PATH')) is None:
                target = field + '.argv[0]' if index == 0 else f'{field}.prerequisites.executables[{index - 1}]'
                missing.append((target, 'executable:' + executable))
        for index, path in enumerate(profile['prerequisites']['files']):
            if not (self.checkout / path).exists():
                missing.append((f'{field}.prerequisites.files[{index}]', 'file:' + path))
        for index, key in enumerate(profile['prerequisites']['environment']):
            if not environment.get(key):
                missing.append((f'{field}.prerequisites.environment[{index}]', 'environment:' + key))
        if not Path(invocation['cwd']).is_dir():
            missing.append((field + '.cwd', 'cwd:' + invocation['cwd']))
        if missing:
            raise CheckFailure('prerequisites_unavailable', missing[0][0],
                               'Недоступны ' + ', '.join(v[1] for v in missing),
                               'Подготовьте перечисленные обязательные средства и повторите запрос.',
                               status='unavailable')
