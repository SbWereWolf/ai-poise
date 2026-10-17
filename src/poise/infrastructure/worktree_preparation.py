"""Configured command, input and immutable material adapters for preparation."""
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from string import Template

from apps.bootstrap.infrastructure import CommandConfiguration, CommandExecutor, load_handler
from ..common import PoiseError
from .goal_config import atomic_write
from .work import WorkResources


def checksum(content):
    return hashlib.sha256(content).hexdigest()


@dataclass(frozen=True)
class PreparationConfiguration:
    command: CommandConfiguration
    path: Path
    coordinates: dict

    @classmethod
    def from_bytes(cls, raw, path, coordinates):
        command = CommandConfiguration.from_bytes(raw)
        data = command.raw
        if not isinstance(data['catalog_dir'], str) or not data['catalog_dir']:
            raise ValueError('catalog_dir: требуется явный шаблон')
        if (not isinstance(data['operations'], list) or not data['operations']
                or any(not isinstance(name, str) or name not in data['commands'] for name in data['operations'])):
            raise ValueError('operations: требуется последовательность выбранных команд')
        if not isinstance(data['inputs'], list):
            raise ValueError('inputs: требуется явный список входов')
        for item in data['inputs']:
            if (not isinstance(item, dict) or set(item) != {'source', 'destination'}
                    or any(not isinstance(v, str) or not v for v in item.values())):
                raise ValueError('inputs: требуются source и destination')
        return cls(command, path, coordinates)

    def render(self):
        values = {**self.command.raw['parameters'], **self.coordinates}
        catalog = Template(self.command.raw['catalog_dir']).substitute(values)
        values['catalog_dir'] = catalog

        def leaves(value):
            if isinstance(value, str):
                return Template(value).substitute(values)
            if isinstance(value, dict):
                return {key: leaves(item) for key, item in value.items()}
            if isinstance(value, list):
                return [leaves(item) for item in value]
            return value

        return {'inputs': leaves(self.command.raw['inputs']),
                'commands': self.command.rendered_commands(catalog, values=values),
                'options': leaves(self.command.handler_options),
                'operations': self.command.raw['operations'],
                'handler': self.command.handler_selector}


class CapturedCommands:
    def __init__(self, executor, material, publish):
        self.executor, self.material, self.publish = executor, material, publish
        self.commands = []
        self.sequence = 0

    def invoke(self, name):
        process = self.executor.run(name)
        number = self.sequence
        self.sequence += 1
        command = {'name': name, 'argv': list(process.args), 'cwd': str(self.executor.cwd),
                   'returncode': process.returncode}
        for stream in ('stdout', 'stderr'):
            raw = getattr(process, stream)
            record = self.publish(self.material, f'commands/{number}.{stream}', raw)
            command[stream + '_path'] = record['path']
            command[stream + '_sha256'] = record['sha256']
        self.commands.append(command)
        return process


class FilePreparationEffects:
    def __init__(self, runtime, task, configuration):
        self.runtime, self.task = runtime, task
        path = Path(configuration)
        self.configuration = path if path.is_absolute() else runtime.config_path.parent / path
        self.factory = WorkResources(runtime).factory(task)

    def _publish(self, material, name, content, *, register=True):
        attempt = material['report']['attempt_id']
        relative = f'worktree-preparation/{attempt}/{name}'
        source = self.runtime.runtime / relative
        atomic_write(source, content, self.runtime.cfg['batch']['file_mode'])
        prepared = self.factory.prepare([{'scope': 'task', 'path': relative,
            'source': {'kind': 'file', 'path': str(source), 'digest': checksum(content)}}])
        paths = self.factory.materialize(prepared)
        record = {'path': paths[0], 'sha256': checksum(content)}
        if register:
            self.runtime.register_artifact_paths(paths, self.task)
            material['materials'].append(record)
        return record

    @staticmethod
    def _failure(material, exc):
        material['report'].update(status='error', reason=str(exc))

    def capture_configuration(self, attempt):
        report = {'schema': 'poise/worktree-preparation/v1', **attempt.coordinates,
                  'attempt_id': attempt.attempt_id, 'request_id': attempt.request_id,
                  'configuration': {'path': str(self.configuration), 'sha256': None, 'snapshot_path': None},
                  'inputs': [], 'operations': [], 'status': 'ready', 'reason': None}
        material = {'report': report, 'materials': []}
        try:
            raw = self.configuration.read_bytes()
            snapshot = self._publish(material, 'configuration.json', raw)
            report['configuration'].update(sha256=snapshot['sha256'], snapshot_path=snapshot['path'])
            material['selected'] = PreparationConfiguration.from_bytes(raw, self.configuration, attempt.coordinates).render()
        except Exception as exc:
            self._failure(material, exc)
        return material

    def publish_inputs(self, material):
        if material['report']['status'] != 'ready':
            return
        try:
            for number, declaration in enumerate(material['selected']['inputs']):
                source = Path(declaration['source'])
                if not source.is_absolute():
                    source = self.configuration.parent / source
                destination = Path(declaration['destination'])
                if not destination.is_absolute():
                    destination = Path(material['report']['worktree']) / destination
                content = source.read_bytes()
                snapshot = self._publish(material, f'inputs/{number}.bin', content)
                atomic_write(destination, content, self.runtime.cfg['batch']['file_mode'])
                material['report']['inputs'].append({'source': str(source), 'destination': str(destination),
                    'sha256': snapshot['sha256'], 'snapshot_path': snapshot['path']})
        except Exception as exc:
            self._failure(material, exc)

    def execute_operations(self, material):
        if material['report']['status'] != 'ready':
            return
        selected = material['selected']
        captured = CapturedCommands(CommandExecutor(selected['commands'], [], Path(material['report']['worktree'])),
                                    material, self._publish)
        try:
            handler = load_handler(selected['handler'])
            for name in selected['operations']:
                operation = {'name': name, 'result': None, 'commands': []}
                material['report']['operations'].append(operation)
                begin = len(captured.commands)
                try:
                    initial = captured.invoke(name)
                    result = handler(initial, captured.invoke, selected['options'],
                                     {'operation': name, 'dry_run': False})
                    operation['result'] = result
                    if not isinstance(result, dict) or type(result.get('exit_code')) is not int:
                        raise ValueError('Обработчик подготовки должен вернуть объект с целым exit_code')
                    if result['exit_code'] != 0:
                        raise ValueError(f'Подготовка {name} завершилась ошибкой: {result}')
                finally:
                    operation['commands'] = captured.commands[begin:]
        except Exception as exc:
            self._failure(material, exc)

    def save_capture(self, material):
        body = {'schema': 'poise/worktree-preparation-capture/v1',
                'report': material['report'], 'materials': list(material['materials'])}
        capture = self._publish(material, 'capture.json',
                                (json.dumps(body, ensure_ascii=False, indent=2) + '\n').encode())
        return capture

    def confirm_capture(self, capture):
        raw = Path(capture['path']).read_bytes()
        if checksum(raw) != capture['sha256']:
            raise PoiseError('Подтверждение результата подготовки: изменён capture')
        body = json.loads(raw)
        if body['schema'] != 'poise/worktree-preparation-capture/v1':
            raise PoiseError('Неизвестный сохранённый capture подготовки')
        for record in body['materials']:
            if checksum(Path(record['path']).read_bytes()) != record['sha256']:
                raise PoiseError('Подтверждение результата подготовки: изменён материал ' + record['path'])
        return body['report']

    def publish_receipt(self, report):
        material = {'report': report, 'materials': []}
        record = self._publish(material, 'receipt.json',
                               (json.dumps(report, ensure_ascii=False, indent=2) + '\n').encode(), register=False)
        records = self.runtime.validate_artifact_paths([record['path']], self.task)
        return {**record, 'status': report['status'], 'reason': report['reason']}, records
