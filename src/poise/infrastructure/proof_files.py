"""Current Task proof bytes; no publication, digest or lifecycle persistence."""
import base64
from pathlib import Path

from ..common import PoiseError


class FileProofFiles:
    def __init__(self, root, *, managed_paths, managed_roots, artifact_paths):
        self.root = Path(root).resolve()
        self.managed_paths = {Path(p).resolve() for p in managed_paths}
        self.managed_roots = tuple(Path(p).resolve() for p in managed_roots)
        self.artifact_paths = {Path(p).resolve() for p in artifact_paths}

    @staticmethod
    def fail(prefix, path, reason):
        raise PoiseError(f'{prefix}: {path}: {reason}')

    def observe(self, raw):
        original = Path(raw)
        if not original.is_absolute():
            self.fail('proof_file_foreign_task', raw, 'Укажите абсолютный путь внутри выбранной Task')
        try:
            path = original.resolve()
            if path in self.managed_paths or any(path.is_relative_to(root) for root in self.managed_roots):
                self.fail('proof_file_managed_material', raw, 'Машинный материал сохраняет своего владельца; выберите отдельный файл')
            if not path.is_relative_to(self.root):
                self.fail('proof_file_foreign_task', raw, 'Файл находится вне выбранной Task')
            if path in self.artifact_paths:
                self.fail('proof_file_ordinary_artifact', raw, 'Зарегистрированный артефакт сохраняет свою неизменность; выберите отдельный файл')
            if not path.exists():
                self.fail('proof_file_missing', raw, 'Подготовьте файл по предъявленному расположению')
            if not path.is_file():
                self.fail('proof_file_not_regular', raw, 'Доказательство должно быть обычным файлом')
        except OSError as error:
            self.fail('proof_file_read_error', raw, str(error))
        return path

    def read(self, task_id, argument, representation):
        path = self.observe(argument['path'])
        try:
            content = path.read_bytes()
        except OSError as error:
            self.fail('proof_file_read_error', path, str(error))
        if representation == 'base64':
            value = base64.b64encode(content).decode('ascii')
        elif representation == 'text':
            try:
                value = content.decode('utf-8')
            except UnicodeError as error:
                self.fail('proof_file_decode_error', path, 'Требуется UTF-8; выберите representation=base64: ' + str(error))
        else:
            self.fail('proof_file_representation_invalid', path, 'Выберите явно text или base64')
        return {'status': 'read_only', 'task': task_id, 'argument_id': argument['id'],
                'path': argument['path'], 'representation': representation,
                'content': value, 'size_bytes': len(content)}
