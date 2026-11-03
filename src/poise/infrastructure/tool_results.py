"""Filesystem publication and selected-project placement of complete results."""
from copy import deepcopy
import hashlib
import codecs
import os
from pathlib import Path
import shutil
import tempfile
from uuid import UUID, uuid5, NAMESPACE_URL

from ..common import PoiseError, encoded as canonical
from ..modules.result_views.tool_results import (
    ToolResultFailure, encoded, response_text, reject, require_budget,
    saved_result, native_snapshot, select_parser,
)
from ..modules.placement.contracts import PlacementRequest
from .goal_config import atomic_write, strict_json
from .locking import exclusive_lock, blocking_file_lock


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def native_result(event, policy):
    """Decode only the selected actual wire; never invent missing process streams."""
    from ..modules.result_views.unknown_inputs import generic_profile
    try:
        selected = select_parser(event, policy)
    except ToolResultFailure as error:
        if error.code != 'unmapped_input': raise
        known = event['tool_name'] in policy.get('parsers', {}) or any(
            rule['tool_name'] == event['tool_name'] for rule in policy.get('selection', []))
        selected = ('generic', generic_profile('unknown_format' if known else 'unknown_tool'))
    if selected[1]['parser'] == 'generic':
        result = None
    else:
        try:
            result = native_snapshot(event, policy, selected=selected)
        except ToolResultFailure as error:
            if error.code != 'unmapped_input': raise
            selected = ('generic', generic_profile('unknown_format')); result = None
    if result is None or result.get('pending'):
        pending = result is not None and result.get('pending')
        raw = deepcopy(event['tool_response'])
        result = {'pending': bool(pending), 'tool': {'name': event['tool_name'],
            'kind': 'bash' if event['tool_name'] == 'Bash' else 'mcp' if event['tool_name'].startswith('mcp__') else 'generic'},
            'response': raw, 'capture': {'state': 'unknown', 'reason': 'native_process_pending'} if pending else {'state': 'complete', 'reason': None},
            'exit_code': raw.get('exit_code') if isinstance(raw, dict) and type(raw.get('exit_code')) is int else None}
        if pending: selected = ('generic', generic_profile('pending'))
    _, parser = selected
    if parser['format'] in ('saved-work-json', 'saved-work-text'):
        try:
            envelope = strict_json(result['response'] if parser['format'] == 'saved-work-text'
                                   else result['response']['output'])
            address = Path(envelope['response_path'])
            if not address.is_absolute() or not address.resolve().is_relative_to(Path(parser['source_root']).resolve()):
                reject('source_invalid', 'response_path', 'restore:source')
            result['response'] = strict_json(address.read_bytes().decode())
        except FileNotFoundError as exc:
            raise ToolResultFailure('source_missing', 'response_path', 'provide:source') from exc
        except (KeyError, TypeError, ValueError, UnicodeError, OSError, PoiseError) as exc:
            raise ToolResultFailure('source_invalid', 'response_path', 'restore:source') from exc
    result['_selected_parser'] = parser
    return result


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
        self.general_policy = None

    def prepare(self, paths):
        if self.general_policy is not None:
            for path in paths:
                self.general_policy.admit_harness_path(str(Path(path).resolve()), self.policy['placement'])
            return
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

    @classmethod
    def general(cls, policy, resources=None):
        from ..modules.placement.policy import PlacementPolicy
        instance = cls(None, policy, None)
        try:
            address = policy['placement']['policy']
            raw = Path(address).read_text() if resources is None else resources[address]
            instance.general_policy = PlacementPolicy.from_configuration(strict_json(raw))
            for root in instance.general_policy.roots.values():
                if root['path'] is not None: root['path'] = str(Path(root['path']).resolve())
        except (PoiseError, OSError, ValueError, UnicodeError) as exc:
            raise ToolResultFailure('invalid_policy', 'placement', 'correct:policy') from exc
        return instance


class FileToolResultStore:
    def __init__(self, bound, caller, policy, placement):
        self.policy, self.placement = policy, placement
        self.root = Path(policy['storage']['root'])
        self.prepared = 'session' in policy
        if self.prepared:
            session = policy['session']
            self.scope = caller.key
            self.session = Path(session['root']) / caller.key
            if not self.session.resolve().is_relative_to(self.root.resolve()):
                reject('invalid_policy', 'session.root', 'correct:policy')
            self.namespace = self.session / session['runs']
            self.last = self.session / session['last']
            return
        self.scope = digest(encoded([bound.config['project'], str(bound.checkout), caller.key]).encode())
        self.namespace = self.root / self.scope
        self.wait, self.poll = bound.config['limits']['lock_seconds'], bound.config['limits']['lock_poll_seconds']

    def address(self, request_id):
        if self.prepared:
            run = str(uuid5(NAMESPACE_URL, encoded([self.scope, request_id])))
            return str(self.namespace / run / self.policy['storage']['response'])
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
            value = saved_result(strict_json(raw.decode()), str(path), self.scope, prepared=self.prepared)
            if self.prepared:
                expected = self.namespace / value['run_id']
                if (path != expected / self.policy['storage']['response']
                        or value['full_path'] != str(expected / self.policy['session']['full'])
                        or value['expanded_path'] != str(expected / self.policy['session']['expanded'])
                        or value['last_path'] != str(self.last)):
                    reject('source_invalid', 'source', 'restore:source')
            return value
        except (PoiseError, TypeError, KeyError, ValueError, UnicodeError, RecursionError) as exc:
            raise ToolResultFailure('source_invalid', 'source', 'restore:source') from exc

    def _sync(self, path):
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try: os.fsync(fd)
        finally: os.close(fd)

    def capture(self, request, *, prepare=None, source_text=None):
        if self.prepared:
            return self._capture_prepared(request, prepare, source_text)
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

    def _capture_prepared(self, request, prepare, source_text):
        s, session = self.policy['storage'], self.policy['session']
        final = Path(self.address(request['request_id']))
        directory = final.parent
        raw_inputs = {name: stream_metadata(path, self.policy['display']['binary']['nul_byte_is_binary'],
                                            s['chunk_bytes'])
                      for name, path in request['streams'].items() if path is not None}
        identity = {'request': request, 'streams': {k: v['sha256'] for k, v in raw_inputs.items()}}
        if source_text is not None:
            identity['source_text'] = source_text
        fingerprint = digest(canonical(identity).encode('utf-8', 'backslashreplace'))
        stage = None
        try:
            self.placement.prepare([final, directory / session['full'], directory / session['expanded'], self.last])
            with blocking_file_lock(self.session / '.capture.lock'):
                if final.is_file():
                    record = self.load(str(final))
                    if record['_fingerprint'] != fingerprint:
                        reject('request_conflict', 'request_id', 'choose:new_key')
                    self.validate_prepared(record)
                    if not record['_published']:
                        self._publish_last(record)
                    # A completed replay does not rewind the session's last tool.
                    return record
                self.namespace.mkdir(parents=True, exist_ok=True)
                stage = Path(tempfile.mkdtemp(prefix='.' + directory.name + '.', dir=self.namespace))
                record = {'schema': 'poise-tool-result-1', 'status': 'stored', 'response_path': str(final),
                          **{k: deepcopy(request[k]) for k in ('tool', 'response', 'capture', 'exit_code')},
                          'streams': {'stdout': None, 'stderr': None}, '_scope': self.scope,
                          '_fingerprint': fingerprint, 'run_id': directory.name,
                          '_published': False,
                          'full_path': str(directory / session['full']),
                          'expanded_path': str(directory / session['expanded']), 'last_path': str(self.last)}
                nul = self.policy['display']['binary']['nul_byte_is_binary']
                sources = {}
                for name, original in raw_inputs.items():
                    target = stage / s[name]
                    with Path(original['path']).open('rb') as src, target.open('xb') as dest:
                        os.fchmod(dest.fileno(), s['file_mode'])
                        shutil.copyfileobj(src, dest, s['chunk_bytes'])
                        dest.flush(); os.fsync(dest.fileno())
                    copied = stream_metadata(target, nul, s['chunk_bytes'])
                    if {**copied, 'path': original['path']} != original:
                        reject('source_invalid', 'streams', 'restore:source')
                    record['streams'][name] = {**copied, 'path': str(directory / s[name])}
                    sources[name] = FileStoredSource(copied)
                raw = (response_text(request['response']) if source_text is None else source_text).encode()
                atomic_write(stage / s['text'], raw, s['file_mode'])
                record['_response_source'] = metadata(raw, directory / s['text'], nul)
                sources['response'] = FileStoredSource(metadata(raw, stage / s['text'], nul))
                for channel in ('stdout', 'stderr'): sources.setdefault(channel, None)
                record['_prepared'] = prepare(record, sources)
                atomic_write(stage / session['full'], raw, s['file_mode'])
                atomic_write(stage / session['expanded'], record['_prepared']['expanded'].encode(), s['file_mode'])
                atomic_write(stage / s['response'], response_text(record).encode(), s['file_mode'])
                self._sync(stage)
                os.rename(stage, directory); stage = None
                self._sync(self.namespace)
                self._publish_last(record)
                return self.load(str(final))
        except ToolResultFailure:
            raise
        except (PoiseError, OSError, ValueError) as exc:
            raise ToolResultFailure('storage_write', 'storage', 'retry:capture') from exc
        finally:
            if stage is not None: shutil.rmtree(stage)

    def _publish_last(self, record):
        mode = self.policy['storage']['file_mode']
        atomic_write(self.last, encoded({'uuid': record['run_id']}).encode(), mode)
        self._sync(self.session)
        record['_published'] = True
        atomic_write(Path(record['response_path']), response_text(record).encode(), mode)
        self._sync(Path(record['response_path']).parent)

    def validate_prepared(self, record):
        self.source(record, 'response')
        for channel in ('stdout', 'stderr'):
            if record['streams'][channel] is not None: self.source(record, channel)
        full = self._raw(record['full_path'])
        if digest(full) != record['_response_source']['sha256']:
            reject('source_invalid', 'full', 'restore:source')
        expanded = self._raw(record['expanded_path'])
        if expanded != record['_prepared']['expanded'].encode():
            reject('source_invalid', 'expanded', 'restore:source')

    def latest(self):
        try:
            value = strict_json(self._raw(self.last).decode())
            if set(value) != {'uuid'} or str(UUID(value['uuid'])) != value['uuid']:
                reject('source_invalid', 'last', 'restore:source')
        except (PoiseError, ValueError, TypeError, KeyError, UnicodeError) as exc:
            if isinstance(exc, ToolResultFailure): raise
            raise ToolResultFailure('source_invalid', 'last', 'restore:source') from exc
        record = self.load(str(self.namespace / value['uuid'] / self.policy['storage']['response']))
        if not record['_published']:
            reject('source_invalid', 'publication', 'retry:capture')
        self.validate_prepared(record)
        return record

    def expanded(self, record):
        return self._raw(record['expanded_path']).decode()

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


def _service_request(event, policy_path, settings, entry=None):
    """Validate the same entry, selection and optional recorded stdin for both outcomes."""
    import shlex
    command = event.get('tool_input', {}).get('command')
    argv = shlex.split(command, comments=True)
    request = None
    if '<<<' in argv:
        position = argv.index('<<<')
        if len(argv) != position + 2: raise ValueError('Ambiguous stdin')
        request = strict_json(argv[-1]); argv = argv[:position]
    if entry is not None and argv[:len(entry['argv'])] == entry['argv']:
        if Path(event['cwd']).resolve() != Path(entry['cwd']).resolve():
            raise ValueError('Different public entry cwd')
        rest = argv[len(entry['argv']):]
    else:
        if not argv or argv[0] != settings.raw['python']: raise ValueError('Different entry')
        pos = 1
        while pos < len(argv) and argv[pos] in ('-B', '-I', '-P', '-s'): pos += 1
        if argv[pos:pos + 3] != ['-m', 'poise', 'tool-result']: raise ValueError('Different operation')
        rest = argv[pos + 3:]
    if len(rest) % 2: raise ValueError('Invalid CLI arguments')
    arguments = {}
    for key, value in zip(rest[::2], rest[1::2]):
        if key not in ('--config', '--checkout', '--policy', '--audience') or key in arguments:
            raise ValueError('Invalid CLI selection')
        arguments[key] = value
    if (arguments.get('--policy') != str(policy_path)
            or arguments.get('--audience') != 'agent'
            or ('--config' in arguments) != ('--checkout' in arguments)):
        raise ValueError('Different policy or audience')
    if '--config' in arguments:
        if Path(arguments['--config']).resolve() != settings.project_config.resolve():
            raise ValueError('Different selected project')
        if Path(arguments['--checkout']).resolve() != Path(event['cwd']).resolve():
            raise ValueError('Different selected checkout')
    if request is not None and request not in (
            {'schema': 'poise-tool-result-request-1', 'operation': 'expanded'},
            {'schema': 'poise-tool-result-request-1', 'operation': 'full'}):
        raise ValueError('Different service request')
    return request


def service_delivery(event, owner, policy_path, settings):
    """Associate an actual owned service result or refusal, never a flag alone."""
    if event.get('tool_name') != 'Bash': return None
    native = event.get('tool_response')
    if isinstance(native, str):
        output = native
    elif isinstance(native, dict) and native.get('exit_code') in (0, 2):
        output = native.get('output')
    else: return None
    try:
        result = strict_json(output)
    except (PoiseError, KeyError, TypeError, ValueError):
        return None
    if not isinstance(result, dict): return None
    claimed = (result.get('purpose') == 'harness-service'
               and result.get('capability') == 'tool-result')
    if not claimed and result.get('status') != 'rejected': return None
    if isinstance(native, dict) and native['exit_code'] != (0 if claimed else 2):
        return None
    try:
        request = _service_request(event, policy_path, settings, owner.policy.get('service_entry'))
    except (PoiseError, KeyError, TypeError, ValueError, OSError) as exc:
        if not claimed: return None
        raise ToolResultFailure('source_invalid', 'service', 'inspect:source') from exc
    try:
        if not claimed:
            # Without recorded stdin, only these existing read operations can
            # authenticate a refusal. They read the same owned saved source;
            # neither captures, prepares, publishes or replaces last.
            operations = ('expanded', 'full') if request is None else (request['operation'],)
            for operation in operations:
                try:
                    owner.saved_service(operation)
                except ToolResultFailure as refusal:
                    if result == refusal.result():
                        require_budget(result, owner.policy)
                        return encoded(result)
            raise ValueError('Service refusal differs from owned saved source')
        operation = result['status']
        if operation not in ('expanded', 'full'): raise ValueError('Not a saved-output read')
        if request is not None and request != {'schema': 'poise-tool-result-request-1', 'operation': operation}:
            raise ValueError('Different service request')
        expected = owner.saved_service(operation)
        if result != expected: raise ValueError('Service result differs from owned saved bytes')
        return result['text'] if operation == 'expanded' else result['full_path']
    except (PoiseError, KeyError, TypeError, ValueError, OSError) as exc:
        raise ToolResultFailure('source_invalid', 'service', 'inspect:source') from exc
