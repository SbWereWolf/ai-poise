"""Filesystem publication and selected-project placement of complete results."""
from copy import deepcopy
import hashlib
import codecs
import os
from pathlib import Path
import shutil
import tempfile

from ..common import PoiseError, encoded as canonical
from ..modules.result_views.tool_results import ToolResultFailure, encoded, response_text, reject, saved_result
from ..modules.placement.contracts import PlacementRequest
from .goal_config import atomic_write, strict_json
from .locking import exclusive_lock


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def metadata(raw, path, nul_binary):
    try:
        raw.decode('utf-8'); binary = nul_binary and b'\x00' in raw
    except UnicodeError:
        binary = True
    return {'path': str(path), 'bytes': len(raw),
            'lines': raw.count(b'\n') + int(bool(raw) and not raw.endswith(b'\n')),
            'sha256': digest(raw), 'binary': binary}


def stream_metadata(path, nul_binary, chunk_bytes):
    checksum = hashlib.sha256(); size = lines = 0; last = b''; binary = False
    decoder = codecs.getincrementaldecoder('utf-8')('strict')
    try:
        with Path(path).open('rb') as source:
            while block := source.read(chunk_bytes):
                checksum.update(block); size += len(block); lines += block.count(b'\n'); last = block[-1:]
                if nul_binary and b'\x00' in block: binary = True
                try: decoder.decode(block)
                except UnicodeError: binary = True; decoder = codecs.getincrementaldecoder('utf-8')('ignore')
            try: decoder.decode(b'', final=True)
            except UnicodeError: binary = True
    except FileNotFoundError as exc:
        raise ToolResultFailure('source_missing', 'source', 'provide:source') from exc
    except OSError as exc:
        raise ToolResultFailure('source_unreadable', 'source', 'restore:source') from exc
    return {'path': str(path), 'bytes': size, 'lines': lines+int(bool(size) and last != b'\n'),
            'sha256': checksum.hexdigest(), 'binary': binary}


class FileStoredSource:
    def __init__(self, item):
        self.metadata = item
        self.path = Path(item['path'])

    def lines(self):
        with self.path.open('rb') as source:
            for line in source: yield line.decode('utf-8')

    def bytes(self, start, end):
        with self.path.open('rb') as source:
            source.seek(start); return source.read(end-start)

    def boundary(self, offset):
        return offset in (0, self.metadata['bytes']) or (self.bytes(offset, offset+1)[0] & 0xC0) != 0x80


class ResultPlacement:
    def __init__(self, service, policy, max_items):
        self.service, self.policy, self.max_items = service, policy, max_items

    def prepare(self, paths):
        root = Path(self.policy['storage']['root'])
        intent = self.policy['placement']
        items = [{'key': digest(str(p).encode()), 'name': Path(p).name,
                  'path': str(Path(p).relative_to(root)),
                  **{k: intent[k] for k in ('purpose', 'recipient', 'lifetime')}} for p in paths]
        result = self.service.execute(PlacementRequest.parse(
            {'operation': 'prepare', 'items': items, 'authorizations': []}, self.max_items))
        if result['status'] != 'ready' or len(result['items']) != len(items):
            reject('invalid_policy', 'placement', 'correct:policy')
        expected = {str(p) for p in paths}
        actual = {r['absolute_path'] for r in result['items'] if r['reserved'] and r['versioning'] == 'ignored'}
        if actual != expected: reject('invalid_policy', 'placement', 'correct:policy')


class FileToolResultStore:
    def __init__(self, bound, caller, policy, placement):
        self.policy, self.placement = policy, placement
        self.root = Path(policy['storage']['root'])
        self.scope = digest(encoded([bound.config['project'], str(bound.checkout), caller.key]).encode())
        self.namespace = self.root / self.scope
        self.wait, self.poll = bound.config['limits']['lock_seconds'], bound.config['limits']['lock_poll_seconds']

    def address(self, request_id):
        return str(self.namespace / digest(request_id.encode()) / self.policy['storage']['response'])

    def _raw(self, path):
        try: return Path(path).read_bytes()
        except FileNotFoundError as exc:
            raise ToolResultFailure('source_missing', 'source', 'provide:source') from exc
        except OSError as exc:
            raise ToolResultFailure('source_unreadable', 'source', 'restore:source') from exc

    def load(self, address):
        path = Path(address)
        if not path.resolve().is_relative_to(self.root.resolve()):
            reject('source_invalid', 'response_path', 'restore:source')
        raw = self._raw(path)
        try:
            return saved_result(strict_json(raw.decode()), str(path), self.scope)
        except (PoiseError, TypeError, KeyError, ValueError, UnicodeError, RecursionError) as exc:
            raise ToolResultFailure('source_invalid', 'source', 'restore:source') from exc

    def _sync(self, path):
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try: os.fsync(fd)
        finally: os.close(fd)

    def capture(self, request):
        s = self.policy['storage']; final = Path(self.address(request['request_id'])); directory = final.parent
        raw_inputs = {}
        # Inputs are completed producer files. Never execute a producer to recover bytes.
        for name, path in request['streams'].items():
            if path is not None:
                raw_inputs[name] = stream_metadata(path, self.policy['display']['binary']['nul_byte_is_binary'], s['chunk_bytes'])
        fingerprint = digest(canonical({'request': request,
            'streams': {k: v['sha256'] for k, v in raw_inputs.items()}}).encode('utf-8', 'backslashreplace'))
        stage = None
        try:
            paths = [directory / s[k] for k in ('response', 'text')]
            paths += [directory / s[k] for k in raw_inputs]
            self.placement.prepare(paths)
            self.namespace.mkdir(parents=True, exist_ok=True)
            lock = self.namespace / (directory.name + '.lock')
            with exclusive_lock(lock, self.wait, self.poll):
                if directory.exists():
                    record = self.load(str(final))
                    if record['_fingerprint'] != fingerprint:
                        reject('request_conflict', 'request_id', 'choose:new_key')
                    self.source(record, 'response')
                    for name in ('stdout', 'stderr'):
                        if record['streams'][name] is not None: self.source(record, name)
                    self._sync(directory); self._sync(self.namespace)
                    return record
                stage = Path(tempfile.mkdtemp(prefix='.' + directory.name + '.', dir=self.namespace))
                record = {'schema': 'poise-tool-result-1', 'status': 'stored', 'response_path': str(final),
                          **{k: deepcopy(request[k]) for k in ('tool', 'response', 'capture', 'exit_code')},
                          'streams': {'stdout': None, 'stderr': None}, '_scope': self.scope,
                          '_fingerprint': fingerprint}
                nul = self.policy['display']['binary']['nul_byte_is_binary']
                for name, original in raw_inputs.items():
                    target = stage / s[name]
                    with Path(original['path']).open('rb') as source, target.open('xb') as dest:
                        os.fchmod(dest.fileno(), s['file_mode'])
                        while block := source.read(s['chunk_bytes']): dest.write(block)
                        dest.flush(); os.fsync(dest.fileno())
                    copied = stream_metadata(target, nul, s['chunk_bytes'])
                    if {**copied, 'path': original['path']} != original:
                        reject('source_invalid', 'streams', 'restore:source')
                    record['streams'][name] = {**copied, 'path': str(directory / s[name])}
                raw = response_text(request['response']).encode()
                atomic_write(stage / s['text'], raw, s['file_mode'])
                record['_response_source'] = metadata(raw, directory / s['text'], nul)
                body = (response_text(record)).encode()
                atomic_write(stage / s['response'], body, s['file_mode'])
                if (stage / s['response']).read_bytes() != body: raise OSError('Record readback differs')
                self._sync(stage)
                os.rename(stage, directory); stage = None
                self._sync(self.namespace)
                return self.load(str(final))
        except ToolResultFailure:
            raise
        except (PoiseError, OSError, ValueError) as exc:
            raise ToolResultFailure('storage_write', 'storage', 'retry:capture') from exc
        finally:
            if stage is not None: shutil.rmtree(stage)

    def source(self, record, channel):
        item = record['_response_source'] if channel == 'response' else record['streams'][channel]
        if item is None: reject('source_missing', 'channel', 'provide:source')
        if not Path(item['path']).resolve().is_relative_to(Path(record['response_path']).parent.resolve()):
            reject('source_invalid', 'source', 'restore:source')
        actual = stream_metadata(item['path'], self.policy['display']['binary']['nul_byte_is_binary'],
                                 self.policy['storage']['chunk_bytes'])
        if actual != item: reject('source_invalid', 'source', 'restore:source')
        return FileStoredSource(actual)

    def sources(self, record):
        sources = {}
        for name in ('stdout', 'stderr', 'response'):
            if name != 'response' and record['streams'][name] is None:
                sources[name] = None; continue
            sources[name] = self.source(record, name)
        return sources
