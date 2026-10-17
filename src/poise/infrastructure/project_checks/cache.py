"""Optional reusable payloads; authoritative results remain with the ledger owner."""
import hashlib
import json
from pathlib import Path

from ...modules.project_checks.contracts import CheckFailure
from ..goal_config import atomic_write
from .inputs import canonical
from .evidence import file_reference


def retained_file(reference):
    if reference is None:
        return False
    try:
        raw = Path(reference['path']).read_bytes()
        return len(raw) == reference['bytes'] and hashlib.sha256(raw).hexdigest() == reference['sha256']
    except (OSError, KeyError, TypeError):
        return False


class NamedResultCache:
    def __init__(self, configuration, ledger):
        self.root = configuration.state / configuration.policy['storage']['cache_directory']
        self.project = configuration.bound.config['project']
        self.file_mode = configuration.bound.config['batch']['file_mode']
        self.ledger = ledger

    def lookup(self, key):
        try:
            payload = json.loads((self.root / (key + '.json')).read_text())
            if payload['schema'] != 'named-check-cache-1' or payload['project'] != self.project or payload['key'] != key:
                return None
            source = self.ledger.result(payload['result_id'], 'cache.source_result_id')
            if hashlib.sha256(canonical(source)).hexdigest() != payload['sha256']:
                return None
            if source['status'] != 'passed' or not source['steps']:
                return None
            if not all(retained_file(v) for v in payload['retained_records']):
                return None
            for step in source['steps']:
                record = step['record']
                saved = self.ledger.run(record['run_id'])
                if saved != record or not record['verified_success']:
                    return None
                if not retained_file(record['stdout']) or not retained_file(record['stderr']):
                    return None
                if any(not retained_file(v) for v in record['evidence']['files']):
                    return None
            return source
        except CheckFailure as exc:
            if exc.code == 'registry_invalid':
                raise
            return None
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def store(self, key, result):
        try:
            self._store(key, result)
        except OSError as exc:
            raise CheckFailure('cache_store_failed', 'policy.storage.cache_directory',
                               f'Не удалось сохранить необязательный кэш {self.root}: {exc}',
                               'Исправьте размещение кэша либо явно отключите повторное использование; '
                               'известный результат выполнения сохранён отдельно.') from exc

    def _store(self, key, result):
        # A payload is reusable only after lookup confirms the authoritative
        # ledger result AND its retained bytes. Preparing it before that commit
        # permits one final truthful result without overwriting saved outcomes.
        result_bytes = canonical(result) + b'\n'
        retained = [{'path': result['record_path'], 'bytes': len(result_bytes),
                     'sha256': hashlib.sha256(result_bytes).hexdigest()}]
        for step in result['steps']:
            record = step['record']
            retained.append(file_reference(Path(record['record_path'])))
            manifest = record['evidence']['manifest_path']
            if manifest is not None:
                retained.append(file_reference(Path(manifest)))
        payload = {'schema': 'named-check-cache-1', 'project': self.project, 'key': key,
                   'result_id': result['result_id'], 'sha256': hashlib.sha256(canonical(result)).hexdigest(),
                   'retained_records': retained}
        atomic_write(self.root / (key + '.json'), canonical(payload) + b'\n', self.file_mode)
