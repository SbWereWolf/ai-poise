"""Optimistic selected-profile publication; report completion is separately truthful."""
import json
from pathlib import Path
import re
import tomllib

from ...modules.project_checks.contracts import CheckFailure
from ...modules.project_checks.profiles import resolved_selection
from ..goal_config import atomic_write
from ..locking import exclusive_lock
from .configuration import digest


class ProfilePublisher:
    def __init__(self, configuration, clock):
        self.configuration, self.clock = configuration, clock

    def admit(self, item, field):
        current = Path(self.configuration.policy['profiles_path']).read_bytes()
        if digest(current) != item['expected_profile_sha256']:
            raise CheckFailure('calibration_conflict', field + '.expected_profile_sha256',
                               'Выбранный файл профилей изменился после чтения.',
                               'Прочитайте текущий файл и повторите явную публикацию с его SHA256.')

    def publish(self, entries):
        configuration = self.configuration
        path = Path(configuration.policy['profiles_path'])
        limits = configuration.bound.config['limits']
        mode = configuration.bound.config['batch']['file_mode']
        lock = configuration.state / configuration.policy['storage']['lock_file']
        with exclusive_lock(lock, limits['lock_seconds'], limits['lock_poll_seconds']):
            for entry in entries:
                self.admit(entry['request'], entry['field'])
            before = path.read_bytes()
            raw = tomllib.loads(before.decode())
            rendered = before.decode()
            starts = []
            for match in re.finditer(r'^[ \t]*\[\[[^\n]+\]\][ \t]*(?:#[^\n]*)?$', rendered, re.MULTILINE):
                header = tomllib.loads(match.group() + '\n')
                if isinstance(header.get('commands'), list):
                    starts.append(match.start())
            spans = [(start, starts[i + 1] if i + 1 < len(starts) else len(rendered))
                     for i, start in enumerate(starts)]
            edits, changed = [], set()
            for entry in entries:
                chosen = entry['profile']
                index = next(i for i, v in enumerate(raw['commands'])
                             if all(v[k] == chosen[k] for k in ('command_id', 'workload_class', 'runtime_class')))
                values = entry['values']
                if all(raw['commands'][index][k] == v for k, v in values.items()):
                    continue
                start, end = spans[index]
                block = rendered[start:end]
                header_end = block.index(']]') + 2
                nested = re.search(r'^\s*\[', block[header_end:], re.MULTILINE)
                boundary = header_end + nested.start() if nested is not None else len(block)
                head, rest = block[:boundary], block[boundary:]
                updates = {**values, 'last_calibrated_at': self.clock.observe().audit_utc}
                for key, value in updates.items():
                    spelling = '(?:' + re.escape(key) + '|"' + re.escape(key) + '"|\'' + re.escape(key) + '\')'
                    head, count = re.subn(r'^([ \t]*' + spelling + r'[ \t]*=)[^\n]*',
                                         lambda m: m[1] + ' ' + json.dumps(value, ensure_ascii=False),
                                         head, flags=re.MULTILINE)
                    if count != 1:
                        raise CheckFailure('invalid_profiles', 'profiles.commands.' + key,
                                           'Поле не имеет единственного места публикации.',
                                           'Укажите однозначное поле выбранного профиля в TOML.')
                edits.append((start, end, head + rest))
                changed.add(index)
            for start, end, block in sorted(edits, reverse=True):
                rendered = rendered[:start] + block + rendered[end:]
            profile_written = rendered.encode() != before
            if profile_written:
                atomic_write(path, rendered.encode(), mode)
            results = []
            for entry in entries:
                profile = entry['profile']
                name = '-'.join(profile[k] for k in ('command_id', 'workload_class', 'runtime_class')) + '.md'
                report_path = Path(configuration.policy['publication']['report_directory']) / name
                report = ('# Наблюдённые ограничения проверки\n\n' +
                          json.dumps({'selection': resolved_selection(profile), 'values': entry['values'],
                                      'evidence_run_ids': entry['request']['evidence_run_ids']},
                                     ensure_ascii=False, sort_keys=True, indent=2) + '\n')
                report_written, report_changed, error = False, False, None
                try:
                    report_changed = not report_path.exists() or report_path.read_text() != report
                    if report_changed:
                        atomic_write(report_path, report.encode(), mode)
                    report_written = True
                except OSError as exc:
                    error = CheckFailure('calibration_report_failed', 'policy.publication.report_directory',
                                         str(exc), 'Исправьте выбранное размещение отчёта и повторите публикацию с текущим SHA256 профилей.').as_dict()
                status = ('unconfirmed' if not report_written else
                          'published' if profile_written or report_changed else 'unchanged')
                results.append({'kind': 'calibration', 'profile': resolved_selection(profile),
                                'evidence_run_ids': list(entry['request']['evidence_run_ids']),
                                'status': status, 'values': entry['values'],
                                'publication': {'profile_written': profile_written, 'report_written': report_written,
                                                'profile_sha256': digest(path.read_bytes()),
                                                'report_path': str(report_path) if report_written else None},
                                'error': error})
            return results
