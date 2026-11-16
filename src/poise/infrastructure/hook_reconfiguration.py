"""Reconfigure one hook installation through its existing registry and fence.

The prepared journal owns publication recovery; it is retired on completion.
Task state, native identities, messages and prior events are never rewritten.
"""
from contextlib import ExitStack
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from ..common import PoiseError, digest, encoded, exact_keys, file_digest
from ..modules.foundation.validation import nonempty
from ..modules.hook_transport.domain import HookDefinition, merge_hook_document
from ..modules.ownership.domain import Liveness
from . import hook_transport as transport
from .goal_config import read_document
from .locking import exclusive_lock


def _bytes(value, *, sorted_keys=False):
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=sorted_keys) + '\n').encode()


def _file(path, body, mode):
    path = Path(path)
    before = path.read_bytes() if path.exists() else None
    return {'path': str(path), 'before': None if before is None else before.hex(),
            'after': body.hex(), 'mode': mode}


def _observed_file(item):
    path = Path(item['path'])
    return path.read_bytes().hex() if path.exists() else None


def _bindings(db, settings_path):
    return {key: raw for key, raw in db.execute('SELECT id,data FROM bindings')
            if json.loads(raw)['settings'] == str(settings_path)
            and transport.HookRegistry.binding_liveness(db, key) is Liveness.LIVE}


def _binding_change(service, proposed, key, raw, definition_path, installation_id):
    record = json.loads(raw)
    # Use the normal binding owner before preparing any publication bytes.
    observed, _ = service._record(Path(record['binding_path']))
    if observed != record:
        raise PoiseError('Binding changed before reconfiguration: ' + key)
    if service.definition(record['definition_path']).data['id'] != installation_id:
        raise PoiseError('Binding belongs to another installation: ' + key)
    source = service.settings
    files = []
    if record['launcher'] is not None:
        launcher = Path(record['launcher'])
        allowed = [source.launcher_bytes(record['binding_path']), *source.prior_launcher_bytes(record['binding_path'])]
        if launcher.read_bytes() not in allowed:
            raise PoiseError('Foreign launcher content before reconfiguration: ' + key)
        files.append(_file(launcher, proposed.launcher_bytes(record['binding_path']), proposed.raw['executable_mode']))
    after = {**record, 'settings_digest': digest(proposed.raw), 'definition_path': str(definition_path)}
    return ({'id': key, 'before': raw, 'after': encoded(after)}, [
        _file(record['binding_path'], _bytes(after), proposed.raw['file_mode']), *files])


def _prepare(service, proposed, definition, packet, request_digest, locks):
    source = service.settings
    if digest(source.raw) != packet['expected_settings_digest'] or read_document(source.path) != source.raw:
        raise PoiseError('Hook settings changed before reconfiguration')
    if service.revision() != packet['expected_revision']:
        raise PoiseError('Source native hooks changed before reconfiguration')
    target_revision = file_digest(proposed.hooks_file) if proposed.hooks_file.exists() else None
    if target_revision != packet['target_revision']:
        raise PoiseError('Target native hooks changed before reconfiguration')
    with service.registry.transaction() as db:
        row = db.execute('SELECT data FROM installations WHERE id=?', (definition.data['id'],)).fetchone()
        if row is None:
            raise PoiseError('Reconfiguration requires an existing owned installation')
        installed_raw = row[0]
        installation = json.loads(installed_raw)
        bindings = _bindings(db, source.path)
    if installation.get('migrated_to') is not None:
        raise PoiseError('A retired installation cannot be reconfigured')
    old_definition = service.definition(installation['receipt']['definition_path'])
    for field in ('id', 'agent_id', 'message_source'):
        if old_definition.data[field] != definition.data[field]:
            raise PoiseError('Reconfiguration cannot change hook ' + field)
    definition_path = proposed.definitions / (digest(definition.data) + '.json')
    definition_body = _bytes(definition.data, sorted_keys=True)
    if definition_path.exists() and definition_path.read_bytes() != definition_body:
        raise PoiseError('Immutable hook definition conflicts with the requested definition')
    groups = transport.FileHookRepository(proposed, None)._groups(definition, definition_path)
    from .session_start_proxy import snapshot, project_document, snapshot_path, body as proxy_body
    active_proxy = snapshot(source)
    target_proxy = snapshot(proposed)
    if target_proxy and target_proxy['active'] and source.hooks_file != proposed.hooks_file:
        raise PoiseError('Restore target SessionStart through its owner before moving this installation')
    if active_proxy and active_proxy['active'] and source.hooks_file != proposed.hooks_file:
        raise PoiseError('Restore SessionStart through its owner before changing the shared native file')
    native = service.repository.logical_document(read_document(source.hooks_file))
    service.repository._reject_unregistered_groups(native, installation['groups'], definition)
    if source.hooks_file == proposed.hooks_file:
        candidate = merge_hook_document(native, installation['groups'], groups)
        if active_proxy and active_proxy['active']:
            from ..modules.hook_transport.session_start_proxy import InitialSessionStartProjection
            candidate = InitialSessionStartProjection.preserve_member_order(native, candidate, installation['groups'], groups)
        native_files = [_file(source.hooks_file, _bytes(candidate, sorted_keys=True), proposed.raw['file_mode'])]
    else:
        target = read_document(proposed.hooks_file) if proposed.hooks_file.exists() else None
        if target is not None:
            service.repository._reject_unregistered_groups(target, [], definition)
            transport.FileHookRepository(proposed, None)._reject_unregistered_groups(target, [], definition)
        candidate = merge_hook_document(target, [], groups)
        retired = merge_hook_document(native, installation['groups'], [])
        # Publish the new discovery source before retiring this installation's old groups.
        native_files = [_file(proposed.hooks_file, _bytes(candidate, sorted_keys=True), proposed.raw['file_mode']),
                        _file(source.hooks_file, _bytes(retired, sorted_keys=True), source.raw['file_mode'])]
    proxy_after = None
    if source.hooks_file == proposed.hooks_file:
        candidate, proxy_after = project_document(proposed, candidate, settings_overrides={str(source.path): proposed.raw})
        if proxy_after is not None and proxy_after['active']:
            proxy_after['publication'] = {'owner_settings_path': str(source.path), 'request_id': packet['request_id']}
        native_files = [_file(source.hooks_file, _bytes(candidate, sorted_keys=True), proposed.raw['file_mode'])]
        if proxy_after is not None and proxy_after['active']:
            native_files.insert(0, _file(snapshot_path(source), proxy_body(proxy_after), proposed.raw['file_mode']))
    files = [_file(definition_path, definition_body, proposed.raw['file_mode']), *native_files]
    changed = []
    for key, raw in bindings.items():
        change, binding_files = _binding_change(
            service, proposed, key, raw, definition_path, definition.data['id'])
        changed.append(change)
        files.extend(binding_files)
    files.append(_file(source.path, _bytes(proposed.raw), proposed.raw['file_mode']))
    receipt = {'status': 'installed', 'request_id': packet['request_id'],
               'hooks_path': str(proposed.hooks_file), 'previous_hooks_path': str(source.hooks_file),
               'definition_path': str(definition_path), 'settings_path': str(source.path),
               'events': [event['event'] for event in definition.data['events']],
               'binding_count': len(changed),
               'trust_status': 'requires_user_review', 'live_codex': 'not_observed'}
    # Native revision uses the same complete file bytes as the ordinary installer.
    receipt['revision'] = hashlib.sha256(_bytes(candidate, sorted_keys=True)).hexdigest()
    receipt['configuration_revision'] = transport.FileHookRepository(proposed, service.registry)._configuration_revision(
        receipt['revision'], {definition.data['id']: definition.data}, settings_raw=proposed.raw,
        proxy_snapshot=proxy_after if source.hooks_file == proposed.hooks_file else ...)
    operation = {'proxy_after': proxy_after, 'kind': 'hook_reconfiguration', 'packet': request_digest, 'phase': 'prepared',
                 'locks': locks, 'files': files, 'bindings': changed,
                 'installation_id': definition.data['id'], 'installation_before': installed_raw,
                 'installation_after': encoded({**installation, 'groups': groups, 'receipt': receipt}), 'receipt': receipt}
    with service.registry.transaction() as db:
        current = db.execute('SELECT data FROM installations WHERE id=?', (definition.data['id'],)).fetchone()
        if current is None or current[0] != installed_raw or _bindings(db, source.path) != bindings:
            raise PoiseError('Hook registry changed before reconfiguration was prepared')
        db.execute('INSERT INTO operations VALUES(?,?)', (packet['request_id'], encoded(operation)))
    return operation


def _pending_state(service, db, operation, *, allow_arrivals=False):
    selected = _bindings(db, service.settings.path)
    expected = {item['id'] for item in operation['bindings']}
    if not expected.issubset(selected) or (not allow_arrivals and set(selected) != expected):
        raise PoiseError('Binding inventory changed during pending reconfiguration')
    for item in operation['bindings']:
        if selected[item['id']] not in (item['before'], item['after']):
            raise PoiseError('Binding changed during pending reconfiguration: ' + item['id'])
    installation = db.execute('SELECT data FROM installations WHERE id=?',
                              (operation['installation_id'],)).fetchone()
    if installation is None or installation[0] not in (operation['installation_before'], operation['installation_after']):
        raise PoiseError('Installation changed during pending reconfiguration')
    for item in operation['files']:
        if _observed_file(item) not in (item['before'], item['after']):
            raise PoiseError('File changed during pending reconfiguration: ' + item['path'])
    return selected


def _refresh_bindings(service, proposed, operation):
    # The native-file leases protect publication. Events retain their own writer.
    with service.registry.transaction() as db:
        saved = db.execute('SELECT data FROM operations WHERE id=?',
                           (operation['receipt']['request_id'],)).fetchone()
        if saved is None or json.loads(saved[0]) != operation:
            raise PoiseError('Pending reconfiguration intent changed; retry the same request through its current installation owner')
        selected = _bindings(db, service.settings.path)
        retired = {}
        for item in operation['bindings']:
            if item['id'] in selected:
                continue
            proof = service.registry.released_binding_record(db, item['id'])
            if proof is None or proof[1] not in (json.loads(item['before']), json.loads(item['after'])):
                raise PoiseError('Binding inventory changed during pending reconfiguration: '
                                 + item['id'] + '; restore or finish that session through its matching native entry, then retry the same request')
            retired[item['id']] = proof
    arrivals = set(selected) - {item['id'] for item in operation['bindings']}
    previous = json.loads(operation['installation_before'])
    definitions = {previous['receipt']['definition_path'], operation['receipt']['definition_path']}
    expanded = deepcopy(operation)
    removed_paths = set()
    file_paths = {item['path'] for item in operation['files']}
    for key, (_, record) in retired.items():
        paths = {record['binding_path']}
        if record['launcher'] is not None:
            paths.add(record['launcher'])
        if not paths.issubset(file_paths):
            raise PoiseError('Released binding does not match its pending file entries: ' + key
                             + '; preserve the pending journal and restore the matching session projections before retry')
        for path in paths:
            if Path(path).exists():
                raise PoiseError('Released session projection still exists: ' + path
                                 + '; preserve its content and resolve ownership through the session cleanup owner before retry')
        removed_paths.update(paths)
    expanded['bindings'] = [item for item in expanded['bindings'] if item['id'] not in retired]
    expanded['files'] = [item for item in expanded['files'] if item['path'] not in removed_paths]
    for key in sorted(arrivals):
        if json.loads(selected[key])['definition_path'] not in definitions:
            raise PoiseError('New binding does not belong to the pending native entries: ' + key)
        change, files = _binding_change(service, proposed, key, selected[key],
            operation['receipt']['definition_path'], operation['installation_id'])
        expanded['bindings'].append(change)
        expanded['files'].extend(files)
    if arrivals or retired:
        expanded['receipt']['binding_count'] = len(expanded['bindings'])
        installation = json.loads(expanded['installation_after'])
        installation['receipt'] = expanded['receipt']
        expanded['installation_after'] = encoded(installation)
    request_id = operation['receipt']['request_id']
    body = encoded(expanded)
    with service.registry.transaction() as db:
        saved = db.execute('SELECT data FROM operations WHERE id=?', (request_id,)).fetchone()
        if saved is None or json.loads(saved[0]) != operation:
            raise PoiseError('Pending reconfiguration intent changed during binding refresh; retry the same request through its current installation owner')
        if any(service.registry.released_binding_record(db, key) != proof for key, proof in retired.items()):
            raise PoiseError('Released session changed during binding refresh; retry the same reconfiguration request against its current session inventory')
        if _pending_state(service, db, expanded) != selected:
            raise PoiseError('Binding inventory changed during binding refresh; retry the same reconfiguration request')
        for path in removed_paths:
            if Path(path).exists():
                raise PoiseError('Released session projection changed during binding refresh: ' + path
                                 + '; preserve its content and resolve its session ownership before retry')
        if arrivals or retired:
            # Journal every added/retired projection before the next file effect.
            db.execute('UPDATE operations SET data=? WHERE id=?', (body, request_id))
    return expanded


def _publish(service, proposed, definition, operation):
    revision = transport.FileHookRepository(proposed, service.registry)._configuration_revision(
        operation['receipt']['revision'], {definition.data['id']: definition.data}, settings_raw=proposed.raw,
        proxy_snapshot=operation.get('proxy_after', ...))
    if revision != operation['receipt']['configuration_revision']:
        raise PoiseError('Pending reconfiguration inputs changed; restore the recorded handler/project inputs and retry')
    with service.registry.transaction() as db:
        _pending_state(service, db, operation)
        for item in operation['files']:
            if _observed_file(item) != item['after']:
                transport.atomic_write(Path(item['path']), bytes.fromhex(item['after']), item['mode'])
        for item in operation['bindings']:
            db.execute('UPDATE bindings SET data=? WHERE id=?', (item['after'], item['id']))
        db.execute('UPDATE installations SET data=? WHERE id=?',
                   (operation['installation_after'], operation['installation_id']))
        receipt = operation['receipt']
        completed = {'kind': operation['kind'], 'packet': operation['packet'],
                     'phase': 'completed', 'receipt': receipt}
        db.execute('UPDATE operations SET data=? WHERE id=?', (encoded(completed), receipt['request_id']))
        service.registry.event_in(db, None, 'reconfigure.completed', {
            'request_id': receipt['request_id'], 'binding_count': receipt['binding_count'],
            'previous_hooks_path': receipt['previous_hooks_path'], 'hooks_path': receipt['hooks_path']})
    return service.repository._public_receipt(receipt)


def reconfigure(settings_path, packet):
    exact_keys(packet, {'request_id', 'expected_settings_digest', 'expected_revision', 'target_revision',
                        'settings', 'definition'}, 'hook reconfiguration')
    nonempty(packet['request_id'], 'hook reconfiguration.request_id')
    service = transport.HookService(settings_path)
    proposed = transport.HookSettings.proposed(settings_path, packet['settings'])
    if ('repository_scope' not in proposed.raw
            and proposed.hooks_file != service.settings.hooks_file):
        raise PoiseError('Moving native hooks requires explicit repository_scope and hooks_lock')
    definition = HookDefinition.parse(packet['definition'])
    if definition.data['schema'] != 'hook-definition-2':
        raise PoiseError('Reconfiguration requires explicit hook-definition-2')
    from .harness_config import load_harness_config
    from .hook_dispatch import FileDispatchConfiguration
    _, profile, _ = load_harness_config(proposed.project_config)
    for event in definition.data['events']:
        FileDispatchConfiguration.load_plan(definition, event['event'], profile, proposed)
    if profile['batch']['message_source'] != {'id': definition.data['message_source'], 'mode': 'runtime_event'}:
        raise PoiseError('Project message source must explicitly match the hook event source')
    allowed = {'hooks_file', 'hooks_lock', 'repository_scope', 'source_root', 'project_launcher',
               'capability_summary', 'lifecycle_consumer'}
    unchanged = lambda value: {key: item for key, item in value.items() if key not in allowed}
    if unchanged(service.settings.raw) != unchanged(proposed.raw):
        raise PoiseError('Hook reconfiguration cannot change project, stores or other transport settings')
    request_digest = digest(packet)
    with service.project_fence.locked():
        with service.registry.transaction() as db:
            saved = db.execute('SELECT data FROM operations WHERE id=?', (packet['request_id'],)).fetchone()
        operation = None if saved is None else json.loads(saved[0])
        if operation is not None:
            if operation.get('kind') != 'hook_reconfiguration' or operation.get('packet') != request_digest:
                raise PoiseError('Reconfiguration request identity already has another intent')
            if operation['phase'] == 'completed':
                return service.repository._public_receipt(operation['receipt'])
        locks = (operation['locks'] if operation is not None else
                 sorted({str(path) for path in (service.settings.hooks_lock, proposed.hooks_lock) if path is not None}))
        with ExitStack() as stack:
            for lock in locks:
                stack.enter_context(exclusive_lock(Path(lock), service.settings.raw['lock_seconds'],
                                                   service.settings.raw['lock_poll_seconds']))
            if operation is None:
                operation = _prepare(service, proposed, definition, packet, request_digest, locks)
            else:
                operation = _refresh_bindings(service, proposed, operation)
            return _publish(service, proposed, definition, operation)
