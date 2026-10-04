"""Confined file effects composed with the existing artifact publisher/registry."""
import hashlib
import io
import json
from pathlib import Path

from ..artifacts import inspect_paths
from ..common import PoiseError
from .artifact_factory import FileArtifactFactory


class RuntimeAcceptanceRetention:
    def __init__(self, runtime):
        self.h = runtime

    def _path(self, task, relative):
        root = self.h._roots(task)['task']
        path = root / relative
        FileArtifactFactory._without_links(root, 'Retained Task owner')
        FileArtifactFactory._without_links(path, 'Retained file')
        if not path.resolve().is_relative_to(root.resolve()):
            raise PoiseError(f'Retained file leaves Task owner: {path}')
        return path

    def _check(self, task, path, expected):
        path = Path(path)
        root = self.h._roots(task)['task']
        if (not path.is_absolute() or not path.is_relative_to(root)
                or not path.resolve().is_relative_to(root.resolve())):
            raise PoiseError(f'Retained proof outside Task owner: {path}')
        FileArtifactFactory._without_links(root, 'Retained Task owner')
        try:
            observed = FileArtifactFactory._read_registered(path, 'Retained file')
        except OSError as exc:
            raise PoiseError(f'Retained file missing/unreadable: {path}: {exc}') from exc
        if observed != expected:
            raise PoiseError(f'Retained file digest changed: {path}')

    def inputs(self, task, declaration):
        facts = []
        records = {item['path']: item for item in self.h.store.artifact_records(task['id'])}
        for item in declaration.files:
            path = self._path(task, item['path'])
            self._check(task, path, item['digest'])
            record = records.get(str(path))
            if record is None or record['scope'] != 'task' or record['owner'] != task['id']:
                # Submitted input bytes become registered in final verified delivery.
                inspected = inspect_paths([str(path)], {'task': self.h._roots(task)['task']},
                                          {'task': task['id']})[0]
                if record is not None and any(inspected[key] != record[key]
                                               for key in ('id', 'scope', 'owner', 'digest')):
                    raise PoiseError(f'Retained input owner/digest mismatch: {path}')
            elif record['digest'] != item['digest']:
                raise PoiseError(f'Retained input registered digest mismatch: {path}')
            facts.append({key: item[key] for key in ('id', 'digest', 'producer', 'input_ref')}
                         | {'path': str(path)})
        return facts

    def evidence(self, task, method, receipt):
        facts = []
        for kind in ('stdout', 'stderr'):
            path = receipt[kind]
            checksum = receipt[kind + '_digest']
            self._check(task, path, checksum)
            facts.append({'kind': kind, 'path': path, 'digest': checksum})
        outputs = receipt.get('outputs', [])
        declarations = method['outputs']
        if len(outputs) != len(declarations):
            raise PoiseError('Required proof output inventory mismatch')
        for declaration, output in zip(declarations, outputs, strict=True):
            if any(output.get(key) != value for key, value in {
                'id': declaration['id'], 'declared_path': declaration['path'],
                'required': declaration['required'],
            }.items()):
                raise PoiseError(f"Required proof definition mismatch: {declaration['id']}")
            if output['status'] == 'captured':
                self._check(task, output['path'], output['digest'])
                facts.append({'kind': 'output', 'id': output['id'],
                              'path': output['path'], 'digest': output['digest']})
            elif declaration['required'] or output['status'] != 'missing':
                raise PoiseError(f"Required proof missing/invalid: {declaration['id']}")
        return facts

    def observe_manifest_directory(self, task, declaration):
        return self.h.work_resources.factory(task).observe_directory(
            'task', declaration.manifest_directory,
        )

    def publish(self, task, declaration, receipt_id, value):
        directory = self.observe_manifest_directory(task, declaration)
        prefix = self.h.cfg['batch']['artifact_directories']['task']
        artifact_root = self.h._roots(task)['task'] / prefix
        path = directory / (receipt_id + '.json')
        factory = self.h.work_resources.factory(task)
        factory.materialize(factory.prepare([{
            'scope': 'task', 'path': path.relative_to(artifact_root).as_posix(),
            'source': {'kind': 'text', 'text': json.dumps(value, sort_keys=True, indent=2) + '\n'},
        }]))
        return {'path': str(path), 'digest': hashlib.sha256(path.read_bytes()).hexdigest()}

    def read(self, task, reference):
        self._check(task, reference['path'], reference['digest'])
        try:
            material = io.BytesIO()
            actual = FileArtifactFactory._read_registered(
                Path(reference['path']), 'Acceptance manifest', material,
                max_bytes=self.h.cfg['batch']['max_artifact_bytes'],
            )
            if actual != reference['digest']:
                raise PoiseError(f'Acceptance manifest digest changed: {reference["path"]}')
            return json.loads(material.getvalue())
        except (ValueError, UnicodeError) as exc:
            raise PoiseError(f'Invalid acceptance manifest: {reference["path"]}') from exc

    def registered(self, task, submitted_paths):
        return self.h.validate_verification_artifacts(submitted_paths, task)
