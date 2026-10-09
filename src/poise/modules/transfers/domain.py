"""Portable saved-work contracts. No storage, process execution or implicit policy."""
from copy import deepcopy
from dataclasses import dataclass
import re
import json
from .placement import material_source, relocate_path
from ..foundation.errors import DomainError
from ..foundation.validation import validate_exact_keys
from ..artifact_factory.domain import exact, relative
from ..tasks.definition import path_identifier

TRANSFER_FORMAT = 'poise-recovery-1'


@dataclass(frozen=True)
class TransferPolicy:
    data: dict

    @classmethod
    def parse(cls, raw):
        paths = {'directory', 'archive', 'manifest', 'database', 'files_directory'}
        limits = {'max_tasks', 'max_files', 'max_file_bytes', 'max_total_bytes', 'chunk_bytes'}
        fields = paths | limits | {'file_mode'}
        if isinstance(raw, dict) and 'recovery' in raw:
            fields.add('recovery')
            RecoveryPolicy.parse(raw['recovery'])
        validate_exact_keys(raw, fields, 'transfer config', DomainError)
        for key in paths:
            try:
                relative(raw[key])
            except DomainError as exc:
                raise DomainError(f'runtime_services.transfer.{key}: {exc}') from exc
        names = [raw[k] for k in paths - {'directory'}]
        if len(names) != len(set(names)) or any(a.startswith(b + '/') or b.startswith(a + '/')
                                                for i,a in enumerate(names) for b in names[i+1:]):
            raise DomainError('Transfer paths must be distinct and non-overlapping')
        for key in limits:
            if type(raw[key]) is not int or raw[key] <= 0:
                raise DomainError(f'Explicit positive transfer.{key} required')
        if type(raw['file_mode']) is not int or not 0 <= raw['file_mode'] <= 0o777:
            raise DomainError('Explicit transfer.file_mode required')
        return cls(deepcopy(raw))


@dataclass(frozen=True)
class RecoveryPolicy:
    data: dict

    @classmethod
    def parse(cls, raw):
        fields = {'format', 'tool_argv', 'mapping', 'systems', 'repositories', 'paths',
                  'placement_decisions', 'create_flow', 'restore_flow', 'component_steps'}
        exact(raw, fields, 'recovery required fields')
        if raw['format'] != TRANSFER_FORMAT:
            raise DomainError('Unsupported recovery format')
        argv = raw['tool_argv']
        if not isinstance(argv, list) or not argv or any(not isinstance(x, str) or not x for x in argv):
            raise DomainError('recovery.tool_argv required')
        exact(raw['mapping'], {'version', 'materials'}, 'recovery.mapping')
        if not isinstance(raw['mapping']['version'], str) or not raw['mapping']['version']:
            raise DomainError('mapping.version required')
        if not isinstance(raw['mapping']['materials'], list):
            raise DomainError('mapping.materials required')
        seen = set()
        for item in raw['mapping']['materials']:
            exact(item, {'id', 'role', 'source', 'destination', 'include'}, 'mapping material')
            path_identifier(item['id'])
            if item['id'] in seen or not isinstance(item['role'], str) or not item['role']:
                raise DomainError('Unique material id and explicit role required')
            seen.add(item['id']); relative(item['source'])
            if type(item['include']) is not bool:
                raise DomainError('Explicit material include required')
            if item['include']:
                relative(item['destination'])
            elif item['destination'] is not None:
                raise DomainError('Excluded material destination must be null')
        exact(raw['systems'], {'poise', 'project'}, 'recovery.systems')
        for owner, spec in raw['systems'].items():
            exact(spec, {'repository', 'configs'}, 'systems.' + owner)
            if not isinstance(spec['repository'], str) or not spec['repository'].startswith('/'):
                raise DomainError('systems.' + owner + '.repository must be absolute')
            if not isinstance(spec['configs'], list) or not spec['configs'] or any(
                    not isinstance(x, str) or not x.startswith('/') for x in spec['configs']):
                raise DomainError('systems.' + owner + '.configs required')
        exact(raw['repositories'], {'task'}, 'recovery.repositories')
        if not isinstance(raw['repositories']['task'], str) or not raw['repositories']['task'].startswith('/'):
            raise DomainError('recovery.repositories.task must be absolute')
        exact(raw['paths'], {'staging', 'create_session', 'restore_session'}, 'recovery.paths')
        for value in raw['paths'].values(): relative(value)
        for key in ('placement_decisions', 'component_steps'):
            if not isinstance(raw[key], list): raise DomainError('recovery.' + key + ' required')
        for key in ('create_flow', 'restore_flow'):
            flow = raw[key]
            if not isinstance(flow, dict) or flow.get('schema') != 'workspace-recover/flow/v3' or not isinstance(flow.get('steps'), list) or not flow['steps']:
                raise DomainError('recovery.' + key + ' flow/v3 required')
        return cls(deepcopy(raw))


def placement_plan(source, target, decisions):
    included = [x for x in source['materials'] if x['include']]
    if source['version'] == target['version']:
        return {x['id']: x['destination'] for x in included}, None
    for decision in decisions:
        exact(decision, {'source_version', 'target_version', 'reason', 'destinations'}, 'placement decision')
        if (decision['source_version'], decision['target_version']) != (source['version'], target['version']):
            continue
        if not isinstance(decision['reason'], str) or not decision['reason'].strip():
            raise DomainError('Placement decision reason required')
        if set(decision['destinations']) != {x['id'] for x in included}:
            raise DomainError('Placement decision must cover all included materials')
        for path in decision['destinations'].values(): relative(path)
        return deepcopy(decision['destinations']), deepcopy(decision)
    return None, None


@dataclass(frozen=True)
class TransferRequest:
    data: dict

    @classmethod
    def parse(cls, raw, policy):
        if not isinstance(raw, dict) or raw.get('action') not in ('export', 'import'):
            raise DomainError('Unknown transfer action')
        if raw['action'] == 'export':
            exact(raw, {'action','request_id','task_ids','sprint_id','handoff'}, 'transfer export')
            ids,sid,checkpoint = raw['task_ids'],raw['sprint_id'],raw['handoff']
            if ids is not None:
                if not isinstance(ids,list) or not ids or len(ids)>policy['max_tasks']:
                    raise DomainError('Explicit nonempty bounded task selection required')
                for tid in ids:path_identifier(tid)
                if len(set(ids))!=len(ids):raise DomainError('Duplicate transfer task ID')
            if sid is not None:path_identifier(sid)
            if ids is not None and sid is not None:
                raise DomainError('Select tasks or one complete sprint, not both')
            if ids is None and sid is None and checkpoint is None:
                raise DomainError('Explicit owner selection or current handoff required')
            if checkpoint is not None:
                exact(checkpoint, {'request_id','reason','result','commit_message','artifact_paths'}, 'transfer handoff')
                path_identifier(checkpoint['request_id'])
                if not isinstance(checkpoint['reason'],str) or not checkpoint['reason'].strip():
                    raise DomainError('Handoff reason required')
                if not isinstance(checkpoint['artifact_paths'],list):raise DomainError('Handoff artifact paths required')
        else:
            exact(raw, {'action','request_id','package_path','package_digest'}, 'transfer import')
            if not isinstance(raw['package_path'],str) or not raw['package_path'].startswith('/'):
                raise DomainError('Explicit absolute package path required')
            if not isinstance(raw['package_digest'],str) or not re.fullmatch('[0-9a-f]{64}',raw['package_digest']):
                raise DomainError('Explicit SHA-256 package digest required')
        path_identifier(raw['request_id'])
        return cls(deepcopy(raw))


def validate_saved_work(records, released_ids):
    """A snapshot may preserve work; it cannot manufacture a release or acceptance."""
    for record in records:
        if record['claimed_by'] is not None:
            raise DomainError(f"Task {record['id']} is still owned: handoff before export")
        if record['status'] in ('active','verified','accepted') and record['id'] not in released_ids:
            raise DomainError(f"Task {record['id']} lacks an explicit released handoff")
        if record['status'] not in ('available','active','verified','accepted','completed','cancelled'):
            raise DomainError('Unsupported saved Task state')


def validate_package_manifest(manifest, policy, tables=None):
    """One pure package contract for archive preflight and extracted delivery."""
    fields = {'format', 'schema_version', 'project', 'execution_policy', 'source_config_hash',
              'task_ids', 'sprint_ids', 'owners', 'workspaces', 'files', 'database',
              'state_fingerprint', 'mapping', 'proofs', 'omitted_artifacts'}
    exact(manifest, fields, 'transfer manifest')
    for key in ('task_ids', 'sprint_ids', 'omitted_artifacts'):
        values = manifest[key]
        if not isinstance(values, list) or any(not isinstance(x, str) or not x for x in values) or len(values) != len(set(values)):
            raise DomainError('Unique explicit manifest ' + key + ' required')
    tasks, sprints = set(manifest['task_ids']), set(manifest['sprint_ids'])
    if not tasks or len(tasks) > policy['max_tasks']:
        raise DomainError('Explicit bounded manifest Task inventory required')
    for owner in tasks | sprints:
        path_identifier(owner)
    exact(manifest['owners'], {'task', 'sprint', 'worktree'}, 'manifest owners')
    for kind, expected in [('task', tasks), ('sprint', sprints), ('worktree', tasks)]:
        values = manifest['owners'][kind]
        if not isinstance(values, dict) or set(values) != expected:
            raise DomainError('Manifest owner inventory differs: ' + kind)
        for value in values.values():
            if value is None and kind == 'worktree':
                continue
            if not isinstance(value, str) or not value.startswith('/') or '..' in value.split('/'):
                raise DomainError('Explicit absolute manifest owner root required')
    if not isinstance(manifest['workspaces'], dict) or set(manifest['workspaces']) != tasks:
        raise DomainError('Manifest workspace inventory differs')
    exact(manifest['mapping'], {'version', 'materials'}, 'manifest mapping')
    if not isinstance(manifest['mapping']['version'], str) or not manifest['mapping']['version']:
        raise DomainError('Manifest mapping version required')
    materials = {}
    for item in manifest['mapping']['materials']:
        exact(item, {'id', 'role', 'source', 'destination', 'include'}, 'manifest material')
        path_identifier(item['id']); relative(item['source'])
        if item['id'] in materials or type(item['include']) is not bool or not isinstance(item['role'], str) or not item['role']:
            raise DomainError('Unique explicitly classified manifest material required')
        if item['include']:
            relative(item['destination'])
        elif item['destination'] is not None:
            raise DomainError('Excluded manifest destination must be null')
        materials[item['id']] = item
    locations = {} if tables is None else {row['task_id']: json.loads(row['data'])
                                            for row in tables['transfer_locations']}
    files = {}
    for item in manifest['files']:
        if not isinstance(item, dict) or not {'path', 'role', 'size', 'digest'} <= set(item):
            raise DomainError('Incomplete manifest file')
        relative(item['path'])
        if item['path'] in files or type(item['size']) is not int or item['size'] < 0 or not re.fullmatch('[0-9a-f]{64}', item['digest']):
            raise DomainError('Unique manifest file with exact size/digest required')
        files[item['path']] = item
        if 'material_id' in item:
            exact(item, {'path', 'role', 'size', 'digest', 'task_id', 'material_id', 'suffix', 'placement'}, 'material file')
            material = materials.get(item['material_id'])
            if material is None or not material['include'] or item['role'] != material['role'] or item['task_id'] not in tasks:
                raise DomainError('File lacks selected included material owner')
            suffix = item['suffix']
            if suffix:
                relative(suffix)
            destination = material['destination'] + ('/' + suffix if suffix else '')
            expected = 'materials/' + item['task_id'] + '/' + destination
            root = manifest['owners']['task'][item['task_id']]
            prefix = root.rstrip('/') + '/'
            if (not isinstance(item['placement'], str) or not item['placement'].startswith(prefix)
                    or item['path'] != expected):
                raise DomainError('Material file disagrees with explicit mapping')
            relative(item['placement'][len(prefix):])
            if tables is not None:
                source = material_source(root, material, locations.get(item['task_id'], {}))
                if source + ('/' + suffix if suffix else '') != item['placement']:
                    raise DomainError('Material file disagrees with explicit mapping')
        elif item['role'] == 'configuration':
            exact(item, {'path', 'role', 'size', 'digest', 'task_id', 'placement', 'configuration_destination'}, 'configuration file')
            if item['task_id'] not in tasks:
                raise DomainError('Configuration lacks selected Task owner')
            relative(item['configuration_destination'])
        else:
            exact(item, {'path', 'role', 'size', 'digest'}, 'protocol file')
            if item['role'] not in ('task-state', 'code-diff'):
                raise DomainError('Unclassified package file')
    protocol = {manifest['database']: 'task-state'}
    for tid, spec in manifest['workspaces'].items():
        if spec is None:
            if manifest['owners']['worktree'][tid] is not None:
                raise DomainError('Workspace missing its declared code facts')
            continue
        exact(spec, {'commit', 'tree', 'base', 'branch', 'diff'}, 'workspace')
        for key in ('commit', 'tree', 'base'):
            if not isinstance(spec[key], str) or not re.fullmatch('[0-9a-f]{40}|[0-9a-f]{64}', spec[key]):
                raise DomainError('Exact Git workspace object identity required')
        if not isinstance(spec['branch'], str) or not spec['branch'] or spec['diff'] != 'diffs/' + tid + '.patch':
            raise DomainError('Exact workspace branch/diff association required')
        protocol[spec['diff']] = 'code-diff'
    if {p: f['role'] for p, f in files.items() if f['role'] in ('task-state', 'code-diff') and 'material_id' not in f} != protocol:
        raise DomainError('Protocol snapshot/diff inventory differs')
    proof_ids, config_paths = set(), set()
    for proof in manifest['proofs']:
        exact(proof, {'receipt_id', 'task_id', 'systems'}, 'proof')
        if proof['receipt_id'] in proof_ids or proof['task_id'] not in tasks:
            raise DomainError('Unique selected proof owner required')
        proof_ids.add(proof['receipt_id'])
        exact(proof['systems'], {'poise', 'project'}, 'proof systems')
        for system in proof['systems'].values():
            exact(system, {'commit', 'configs'}, 'proof system')
            if not isinstance(system['commit'], str) or not re.fullmatch('[0-9a-f]{40}|[0-9a-f]{64}', system['commit']) or not system['configs']:
                raise DomainError('Complete proof configuration required')
            for config in system['configs']:
                exact(config, {'path', 'digest', 'captured_path', 'archive_path'}, 'proof config')
                item = files.get(config['archive_path'])
                if (item is None or item['role'] != 'configuration' or item.get('task_id') != proof['task_id']
                        or item['digest'] != config['digest']
                        or (tables is not None and item.get('placement') != relocate_path(
                            config['captured_path'], locations.get(proof['task_id'], {})))
                        or config['archive_path'] in config_paths):
                    raise DomainError('Proof configuration inventory differs')
                config_paths.add(config['archive_path'])
    if {p for p, f in files.items() if f['role'] == 'configuration' and 'material_id' not in f} != config_paths:
        raise DomainError('Undeclared proof configuration file')
    if tables is None:
        return
    if {r['id'] for r in tables['tasks']} != tasks or {r['id'] for r in tables['sprints']} != sprints:
        raise DomainError('Snapshot selected owner inventory differs')
    evidence = {r['id']: r for r in tables['evidence']}
    if set(evidence) != proof_ids:
        raise DomainError('Snapshot proof inventory differs')
    for proof in manifest['proofs']:
        row = evidence[proof['receipt_id']]
        saved = json.loads(row['data'])
        actual = {owner: {'commit': system['commit'], 'configs': [
            {key: config[key] for key in ('path', 'digest', 'captured_path')} for config in system['configs']]}
            for owner, system in proof['systems'].items()}
        if row['task_id'] != proof['task_id'] or actual != saved.get('systems'):
            raise DomainError('Proof differs from captured snapshot provenance')
    eligible = eligible_omissions(manifest, tables)
    placements = {f['placement']: f for f in files.values() if 'placement' in f}
    omitted = set(manifest['omitted_artifacts'])
    artifacts = {r['id']: r for r in tables['artifacts']}
    if not omitted <= artifacts.keys():
        raise DomainError('Omitted artifact lacks immutable snapshot identity')
    for aid, artifact in artifacts.items():
        item = placements.get(artifact['path'])
        if item is not None:
            if aid in omitted or item['digest'] != artifact['digest'] or item.get('task_id') != artifact['owner']:
                raise DomainError('Registered included artifact differs')
            continue
        root = manifest['owners'][artifact['scope']][artifact['owner']].rstrip('/')
        if not artifact['path'].startswith(root + '/'):
            raise DomainError('Artifact outside selected owner')
        path = artifact['path'][len(root) + 1:]
        relative(path)
        if aid not in omitted or aid not in eligible:
            raise DomainError('Missing required artifact; omission needs explicit material classification: ' + artifact['path'])


def eligible_omissions(manifest, tables):
    """Only explicit exclusions and owner-typed regenerable handoff transport."""
    # Internal handoff transport is regenerable owner-typed state, not inferred
    # from a directory name. Authored preserved_artifacts are not exempt.
    generated_handoff_files = set()
    for row in tables['handoffs']:
        saved_handoff = json.loads(row['data'])
        receipt = saved_handoff['receipt']
        if receipt is not None:
            for key in ('receipt_path', 'bundle_path'):
                if receipt.get(key) is not None:
                    generated_handoff_files.add(receipt[key])
            if 'wip_snapshot' in receipt:
                generated_handoff_files.add(receipt['wip_snapshot']['path'])
    eligible = set()
    for artifact in tables['artifacts']:
        if (artifact['scope'] not in ('task', 'sprint')
                or artifact['owner'] not in manifest['owners'][artifact['scope']]):
            raise DomainError('Artifact belongs to an unselected owner')
        root = manifest['owners'][artifact['scope']][artifact['owner']].rstrip('/')
        if not artifact['path'].startswith(root + '/'):
            raise DomainError('Artifact outside selected owner')
        path = artifact['path'][len(root) + 1:]
        relative(path)
        excluded = any(not m['include'] and (path == m['source'] or path.startswith(m['source'] + '/'))
                       for m in manifest['mapping']['materials'])
        if excluded or artifact['path'] in generated_handoff_files:
            eligible.add(artifact['id'])
    return eligible


@dataclass(frozen=True)
class ExportPreparation:
    """An admitted snapshot belongs to one request, not its later live Task."""
    data: dict

    @classmethod
    def parse(cls, value):
        if not isinstance(value, dict) or value.get('phase') not in ('preparing', 'prepared'):
            raise DomainError('Invalid export preparation phase')
        fields = {'phase', 'task_ids', 'sprint_ids', 'fingerprint'}
        if value['phase'] == 'prepared':
            fields.add('manifest')
        exact(value, fields, 'export preparation')
        for key in ('task_ids', 'sprint_ids'):
            ids = value[key]
            if not isinstance(ids, list) or len(ids) != len(set(ids)):
                raise DomainError('Invalid export preparation owners')
            for owner in ids:
                path_identifier(owner)
        if not value['task_ids']:
            raise DomainError('Export preparation needs Task owners')
        if not isinstance(value['fingerprint'], str) or not re.fullmatch('[0-9a-f]{64}', value['fingerprint']):
            raise DomainError('Exact export snapshot fingerprint required')
        if value['phase'] == 'prepared':
            manifest = value['manifest']
            exact(manifest, {'path', 'digest'}, 'prepared export manifest')
            if not isinstance(manifest['path'], str) or not manifest['path'].startswith('/'):
                raise DomainError('Absolute prepared manifest path required')
            if not isinstance(manifest['digest'], str) or not re.fullmatch('[0-9a-f]{64}', manifest['digest']):
                raise DomainError('Exact prepared manifest digest required')
        return cls(deepcopy(value))

    def seal(self, path, digest):
        if self.data['phase'] != 'preparing':
            raise DomainError('Only an unsealed export can be prepared')
        return self.parse({**self.data, 'phase': 'prepared',
                           'manifest': {'path': path, 'digest': digest}})
