"""Compact saved-work delivery through the configured workspace-recover pipeline."""
from copy import deepcopy
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sys
import tarfile

from ..common import PoiseError, descendant, digest, encoded, file_digest, worktree_root
from ..modules.transfers.domain import TRANSFER_FORMAT, placement_plan
from .sqlite.transfers import SqliteTransferRepository, completed_external_execution, snapshot_fingerprint
from .sqlite.transfer_records import relocate_path
from .sqlite.database import SCHEMA_VERSION
from .goal_config import atomic_write
from .locking import exclusive_lock
from .task_paths import sprint_root, task_root
from .recovery_flow import WorkspaceRecoveryFlow
from .recovery_delivery import RecoveryDelivery


def _sprint_id(row):
    return json.loads(row['metadata'])['sprint_id']


def execution_policy(cfg):
    value = deepcopy(cfg)
    del value['paths']; del value['git']['repository']; del value['processes']
    del value['runtime_services']['transfer']
    return digest(value)


class RuntimeTransfers:
    def __init__(self, poise):
        self.h = poise
        self.policy = poise.cfg['runtime_services']['transfer']
        self.recovery = self.policy['recovery']
        self.root = descendant(poise.state, self.policy['directory'])
        self.repo = SqliteTransferRepository(poise.store.database)
        self.flow = WorkspaceRecoveryFlow(self.recovery['tool_argv'])

    def _request_dir(self, args):
        return self.root / digest([self.h.session, args['action'], args['request_id']])

    def _locked(self, args):
        return exclusive_lock(self._request_dir(args).with_suffix('.lock'),
                              self.h.cfg['limits']['lock_seconds'], self.h.cfg['limits']['lock_poll_seconds'])

    def export(self, args):
        with self._locked(args):
            return self._export(args)

    def _paths(self, args):
        directory = self._request_dir(args)
        paths = {key: descendant(directory, value) for key, value in self.recovery['paths'].items()}
        paths['package'] = descendant(directory, self.policy['archive'])
        all_paths = [*paths.values()]
        if any(a == b or a.is_relative_to(b) or b.is_relative_to(a)
               for index, a in enumerate(all_paths) for b in all_paths[index + 1:]):
            raise PoiseError('Recovery staging, sessions and package paths must not overlap')
        return directory, paths

    def _external(self, paths, owners):
        for root in owners:
            root = Path(root).resolve()
            for target in paths:
                if target.resolve().is_relative_to(root):
                    raise PoiseError('Recovery output must be outside Task roots and included sources')

    def _export(self, args):
        h, c = self.h, self.policy
        identity = digest(args)
        old = self.repo.request(h.session, args['request_id'], identity)
        if old is not None:
            if old['phase'] == 'complete':
                receipt = old['receipt']; path = Path(receipt['package_path'])
                if not path.is_file() or file_digest(path) != receipt['package_digest']:
                    raise PoiseError('Saved transfer package missing or changed')
                return {**receipt, 'replayed': True}
            if old['phase'] == 'unknown':
                return {**old['receipt'], 'replayed': True}
            raise PoiseError('Prior package preparation needs an explicit recovery decision')
        selected = args['task_ids']
        if args['handoff'] is not None:
            current = h.current_task()
            if current is None:
                raise PoiseError('No current task for combined transfer/handoff')
            if selected is not None and current['id'] not in selected:
                raise PoiseError('Current handoff task is outside selected transfer scope')
            if args['sprint_id'] is not None and current['sprint_id'] != args['sprint_id']:
                raise PoiseError('Current handoff is outside selected sprint')
            if selected is None and args['sprint_id'] is None:
                selected = [current['id']]
            h.handoff_tools.preserve(args['handoff'], portable=True)
        h.result_views.finish()
        tables = self.repo.capture(selected, args['sprint_id'], h.cfg['project'], c['max_tasks'])
        task_ids = [r['id'] for r in tables['tasks']]
        sprint_ids = [r['id'] for r in tables['sprints']]
        directory, paths = self._paths(args)
        owners = {'task': {r['id']: str(task_root(h.state, h.paths, r['id'], _sprint_id(r)))
                           for r in tables['tasks']},
                  'sprint': {sid: str(sprint_root(h.state, h.paths, sid)) for sid in sprint_ids},
                  'worktree': {}}
        self._external(paths.values(), [*owners['task'].values(), *owners['sprint'].values()])
        stage = paths['staging']
        stage.mkdir(parents=True, exist_ok=False)
        files, observed, packaged_paths = [], [], set()
        total = 0

        def add(source, relative, role, **metadata):
            nonlocal total
            source = Path(source)
            if source.is_symlink() or not source.is_file():
                raise PoiseError('Transfer requires regular files, not symlinks')
            size = source.stat().st_size
            total += size
            if size > c['max_file_bytes'] or total > c['max_total_bytes'] or len(files) >= c['max_files']:
                raise PoiseError('Configured transfer file/byte limit reached')
            if any(item['path'] == relative for item in files):
                raise PoiseError('Duplicate packaged file destination')
            target = descendant(stage, relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            if target != source:
                with source.open('rb') as incoming, target.open('wb') as outgoing:
                    while chunk := incoming.read(c['chunk_bytes']):
                        outgoing.write(chunk)
                os.chmod(target, c['file_mode'])
            sha = file_digest(target)
            if source.stat().st_size != size or file_digest(source) != sha:
                raise PoiseError('Source changed while packaging')
            files.append({'path': relative, 'size': size, 'digest': sha, 'role': role, **metadata})
            observed.append((source, size, sha)); packaged_paths.add(str(source))

        # Validate the entire registered source set, including deliberately
        # omitted history. Omission is not permission to export corrupt state.
        for artifact in tables['artifacts']:
            if artifact['scope'] not in ('task', 'sprint') or artifact['owner'] not in owners[artifact['scope']]:
                raise PoiseError('Artifact belongs to an unselected owner')
            root = Path(owners[artifact['scope']][artifact['owner']])
            path = descendant(root, Path(artifact['path']).relative_to(root).as_posix())
            if not path.is_file() or file_digest(path) != artifact['digest']:
                raise PoiseError('Registered artifact changed or disappeared before export')
        snapshot = descendant(stage, c['database'])
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        self.repo.write_snapshot(snapshot, tables)
        add(snapshot, c['database'], 'task-state')
        for tid, root_text in owners['task'].items():
            root = Path(root_text)
            for material in self.recovery['mapping']['materials']:
                if not material['include']:
                    continue
                source = descendant(root, material['source'])
                self._external(paths.values(), [source])
                if not source.exists():
                    continue
                selected_files = sorted(source.rglob('*')) if source.is_dir() else [source]
                for item in selected_files:
                    if item.is_symlink():
                        raise PoiseError('Task material symlink is unsafe')
                    if not item.is_file():
                        continue
                    suffix = item.relative_to(source).as_posix() if source.is_dir() else ''
                    destination = PurePosixPath(material['destination'])
                    if suffix:
                        destination /= suffix
                    relative = (PurePosixPath('materials') / tid / destination).as_posix()
                    add(item, relative, material['role'], task_id=tid, material_id=material['id'],
                        suffix=suffix, placement=str(item))
        execution = {r['task_id']: json.loads(r['data']) for r in tables['task_execution']}
        workspaces = {}
        for tid in task_ids:
            exe = execution.get(tid)
            owners['worktree'][tid] = None if exe is None else exe['worktree']
            if exe is None or exe['worktree'] is None:
                workspaces[tid] = None; continue
            tree = Path(exe['worktree'])
            if not tree.exists() and completed_external_execution(exe['pending']):
                workspaces[tid] = None; continue
            self._external(paths.values(), [tree])
            if h._git(tree, 'symbolic-ref', '--short', 'HEAD') != exe['branch']:
                raise PoiseError('Saved task branch changed')
            if h.plan_actions._read_optional_ref(tree, 'MERGE_HEAD') is not None:
                raise PoiseError('Unfinished merge cannot be transferred')
            head, fingerprint = h._git(tree, 'rev-parse', 'HEAD'), h._tree(tree)
            for run in tables['action_runs']:
                if run['task_id'] == tid and json.loads(run['data'])['status'] != 'complete':
                    raise PoiseError('Resolve the external action before transfer')
            handoffs = [json.loads(row['data']) for row in tables['handoffs']
                        if row['task_id'] == tid and row['state'] == 'released']
            if handoffs and (handoffs[-1]['receipt']['commit'], handoffs[-1]['receipt']['tree']) != (head, fingerprint):
                raise PoiseError('Saved work changed since released handoff')
            relative = 'diffs/' + tid + '.patch'
            patch = descendant(stage, relative); patch.parent.mkdir(parents=True, exist_ok=True)
            data = h._git(tree, 'diff', '--binary', 'HEAD', fingerprint)
            patch.write_bytes((data + '\n').encode() if data else b'')
            add(patch, relative, 'code-diff')
            workspaces[tid] = {'commit': head, 'tree': fingerprint, 'base': exe['base'],
                               'branch': exe['branch'], 'diff': relative}
        proofs = []
        for row in tables['evidence']:
            receipt = json.loads(row['data'])
            systems = receipt.get('systems')
            if not isinstance(systems, dict) or set(systems) != {'poise', 'project'}:
                raise PoiseError('Historical proof requires both complete configuration sets')
            proof = {'receipt_id': receipt['id'], 'task_id': row['task_id'], 'systems': deepcopy(systems)}
            for owner, system in proof['systems'].items():
                if not system.get('commit') or not system.get('configs'):
                    raise PoiseError('Incomplete historical proof configuration')
                for index, config in enumerate(system['configs']):
                    source = Path(config['captured_path'])
                    if not source.is_file() or file_digest(source) != config['digest']:
                        raise PoiseError('Missing or changed historical proof config')
                    relative = (PurePosixPath('proofs') / row['task_id'] / receipt['id'] / owner /
                                (str(index) + '-' + source.name)).as_posix()
                    add(source, relative, 'configuration', task_id=row['task_id'], placement=str(source),
                        configuration_destination=(PurePosixPath('proofs') / receipt['id'] / owner / source.name).as_posix())
                    config['archive_path'] = relative
            proofs.append(proof)
        later = self.repo.capture(task_ids if not sprint_ids else None, sprint_ids[0] if sprint_ids else None,
                                  h.cfg['project'], c['max_tasks'])
        if snapshot_fingerprint(tables) != snapshot_fingerprint(later):
            raise PoiseError('Selected state changed while packaging')
        for path, size, sha in observed:
            if not path.is_file() or path.stat().st_size != size or file_digest(path) != sha:
                raise PoiseError('Selected file changed while packaging')
        manifest = {'format': TRANSFER_FORMAT, 'schema_version': SCHEMA_VERSION, 'project': h.cfg['project'],
                    'execution_policy': execution_policy(h.cfg), 'source_config_hash': h.config_hash,
                    'task_ids': task_ids, 'sprint_ids': sprint_ids, 'owners': owners, 'workspaces': workspaces,
                    'files': files, 'database': c['database'], 'state_fingerprint': snapshot_fingerprint(tables),
                    'mapping': self.recovery['mapping'], 'proofs': proofs,
                    'omitted_artifacts': [r['id'] for r in tables['artifacts'] if r['path'] not in packaged_paths]}
        atomic_write(descendant(stage, c['manifest']), (encoded(manifest) + '\n').encode(), c['file_mode'])
        self.repo.remember(h.session, args['request_id'], identity, {'phase': 'creating'})
        outcome = self.flow.run(self.recovery['create_flow'],
                                {'workspace': str(directory), 'staging': str(stage), 'package': str(paths['package'])},
                                directory / 'create-flow.json', paths['create_session'])
        if outcome['status'] != 'complete':
            receipt = {'status': outcome['status'], 'success': False, 'steps': outcome['steps'],
                       'reason': outcome['reason'], 'replayed': False}
            self.repo.remember(h.session, args['request_id'], identity,
                               {'phase': outcome['status'], 'receipt': receipt})
            return receipt
        if not paths['package'].is_file():
            raise PoiseError('Configured creation flow did not produce the package')
        self._inspect(paths['package'])
        receipt = {'status': 'exported', 'success': True, 'package_path': str(paths['package']),
                   'package_digest': file_digest(paths['package']), 'task_ids': task_ids, 'sprint_ids': sprint_ids,
                   'replayed': False, 'steps': outcome['steps'], 'delivery': 'local_package_only'}
        self.repo.remember(h.session, args['request_id'], identity, {'phase': 'complete', 'receipt': receipt})
        h.store.event(h.session, None, 'transfer.exported', receipt)
        shutil.rmtree(stage); h._cleanup_runtime()
        return receipt

    def _inspect(self, path):
        """Read bounded tar metadata/manifest; extraction belongs to the tool."""
        c = self.policy
        try:
            with tarfile.open(path, 'r:gz') as archive:
                files, names, total = {}, set(), 0
                for item in archive:
                    name = item.name.removeprefix('./').rstrip('/')
                    p = PurePosixPath(name)
                    if (p.is_absolute() or '..' in p.parts or '\\' in name or name in names
                            or not (item.isfile() or item.isdir())):
                        raise PoiseError('Unsafe, duplicate or non-regular transfer entry')
                    names.add(name)
                    if len(names) > c['max_files'] * 16 + 1:
                        raise PoiseError('Transfer entry count limit reached')
                    if item.isfile():
                        total += item.size
                        if item.size > c['max_file_bytes'] or total > c['max_total_bytes'] or len(files) >= c['max_files'] + 1:
                            raise PoiseError('Transfer file/byte limit reached')
                        files[name] = item
                if c['manifest'] not in files:
                    raise PoiseError('Missing configured transfer manifest')
                manifest = json.load(archive.extractfile(files[c['manifest']]))
                required = {'format', 'schema_version', 'project', 'execution_policy', 'source_config_hash',
                            'task_ids', 'sprint_ids', 'owners', 'workspaces', 'files', 'database',
                            'state_fingerprint', 'mapping', 'proofs', 'omitted_artifacts'}
                if not isinstance(manifest, dict) or set(manifest) != required:
                    raise PoiseError('Incomplete transfer manifest')
                if manifest['format'] != TRANSFER_FORMAT or manifest['schema_version'] != SCHEMA_VERSION:
                    raise PoiseError('Incompatible transfer schema; no migration')
                if manifest['project'] != self.h.cfg['project'] or manifest['execution_policy'] != execution_policy(self.h.cfg):
                    raise PoiseError('Destination project execution policy differs')
                inventory = {x['path']: x for x in manifest['files']}
                if len(inventory) != len(manifest['files']) or set(files) != set(inventory) | {c['manifest']}:
                    raise PoiseError('Missing, extra or duplicate package inventory')
                parents = {str(parent) for name in files for parent in PurePosixPath(name).parents}
                if any(name not in files and name not in parents | {''} for name in names):
                    raise PoiseError('Undeclared package directory')
                if manifest['database'] != c['database'] or len(manifest['task_ids']) > c['max_tasks']:
                    raise PoiseError('Explicit snapshot filename or task count differs')
                import hashlib
                for name, record in inventory.items():
                    member = files[name]; sha = hashlib.sha256()
                    if record['size'] != member.size:
                        raise PoiseError('Transfer size mismatch')
                    with archive.extractfile(member) as stream:
                        while chunk := stream.read(c['chunk_bytes']): sha.update(chunk)
                    if sha.hexdigest() != record['digest']:
                        raise PoiseError('Transfer digest/integrity mismatch')
                for proof in manifest['proofs']:
                    if set(proof['systems']) != {'poise', 'project'}:
                        raise PoiseError('Incomplete proof systems')
                    for system in proof['systems'].values():
                        if not system['commit'] or not system['configs']:
                            raise PoiseError('Incomplete proof configuration')
                        for config in system['configs']:
                            if config['archive_path'] not in inventory or inventory[config['archive_path']]['digest'] != config['digest']:
                                raise PoiseError('Missing or changed proof config inventory')
                # SQLite's owning reader requires a file. This bounded read is
                # preflight only; the configured tool still extracts the delivery.
                snapshot = archive.extractfile(files[c['database']]).read(c['max_file_bytes'] + 1)
                return manifest, snapshot
        except (KeyError, TypeError, ValueError, tarfile.TarError) as exc:
            raise PoiseError('Invalid transfer package: ' + str(exc)) from exc

    def restore(self, args):
        with self._locked(args):
            return self._restore(args)

    def _binding(self,manifest,tables,sha):
        h=self.h;locations={};by_task={};trees={}
        task_rows = {row['id']: row for row in tables['tasks']}
        for owner, old in manifest['owners']['task'].items():
            h._identifier(owner)
            locations[old] = str(task_root(
                h.state, h.paths, owner, _sprint_id(task_rows[owner])
            ))
        for owner, old in manifest['owners']['sprint'].items():
            h._identifier(owner)
            locations[old] = str(sprint_root(h.state, h.paths, owner))
        for tid,old in manifest['owners']['worktree'].items():
            h._identifier(tid)
            trees[tid]=None if old is None else str(worktree_root(h.state,h.cfg)/tid)
            if old is not None:locations[old]=trees[tid]
        for row in tables['transfer_locations']:
            for old,previous in json.loads(row['data']).items():
                locations[old]=relocate_path(previous,locations)
        for tid in manifest['task_ids']:by_task[tid]=dict(locations)
        return {'config_hash':h.config_hash,'namespace':'transfer-'+sha+'-',
                'locations':locations,'by_task_locations':by_task,'worktrees':trees}


    def _restore(self, args):
        h, c = self.h, self.policy
        identity = digest(args)
        old = self.repo.request(h.session, args['request_id'], identity)
        path = Path(args['package_path'])
        if path.is_symlink() or not path.is_file() or file_digest(path) != args['package_digest']:
            raise PoiseError('Package digest/integrity mismatch')
        if old is not None and old['phase'] in ('complete', 'unknown', 'failed'):
            return {**old['receipt'], 'replayed': True}
        imported = self.repo.imported(args['package_digest'])
        if imported is not None:
            self.repo.remember(h.session, args['request_id'], identity, {'phase': 'complete', 'receipt': imported})
            return {**imported, 'replayed': True}
        manifest, snapshot = self._inspect(path)
        placements, decision = placement_plan(manifest['mapping'], self.recovery['mapping'],
                                               self.recovery['placement_decisions'])
        if placements is None:
            return {'status': 'placement_decision_required', 'success': False, 'replayed': False}
        directory, paths = self._paths(args)
        directory.mkdir(parents=True, exist_ok=True)
        preflight = directory / 'preflight.sqlite'
        atomic_write(preflight, snapshot, c['file_mode'])
        tables = self.repo.read_snapshot(preflight)
        if snapshot_fingerprint(tables) != manifest['state_fingerprint']:
            raise PoiseError('Snapshot record fingerprint mismatch')
        self.repo.ensure_absent(tables)
        binding = self._binding(manifest, tables, args['package_digest'])
        roots = [binding['locations'][value] for scope in ('task', 'sprint')
                 for value in manifest['owners'][scope].values()]
        self._external(paths.values(), [*roots, *[x for x in binding['worktrees'].values() if x is not None]])
        for root in roots:
            p = Path(root)
            if p.is_symlink():
                raise PoiseError('Destination owner root is a symlink')
            if old is None and p.exists():
                # Check declared paths first, so an escaping link is diagnosed
                # without touching the outside object.
                for destination in placements.values(): descendant(p, destination)
                raise PoiseError('Destination owner directory already exists; no overwrite')
        for item in manifest['files']:
            if 'placement' not in item:
                continue
            tid = item['task_id']
            if tid not in manifest['owners']['task']:
                raise PoiseError('Material has no selected Task owner')
            if item['role'] == 'configuration':
                destination = PurePosixPath(item['configuration_destination'])
            elif item['material_id'] in placements:
                destination = PurePosixPath(placements[item['material_id']])
                if item['suffix']: destination /= item['suffix']
            else:
                raise PoiseError('Material has no selected mapping owner')
            root = Path(binding['locations'][manifest['owners']['task'][tid]])
            binding['locations'][item['placement']] = str(descendant(root, destination.as_posix()))
        for tid in manifest['task_ids']:
            binding['by_task_locations'][tid] = dict(binding['locations'])
        repository = Path(h.cfg['git']['repository']).resolve(strict=True)
        for tid, spec in manifest['workspaces'].items():
            if spec is None: continue
            tree = Path(binding['worktrees'][tid])
            h._git(repository, 'check-ref-format', '--branch', spec['branch'])
            if old is None and (tree.exists() or h.plan_actions._read_optional_ref(repository, 'refs/heads/' + spec['branch']) is not None):
                raise PoiseError('Destination worktree or branch already exists; no overwrite')
        database = h.store.database
        descriptor_path = directory / 'delivery.json'
        variables = {'workspace': str(directory), 'staging': str(paths['staging']), 'package': str(path),
                     'task_root': roots[0] if len(roots) == 1 else None}
        for phase in ('place', 'components', 'validate'):
            variables[phase + '_argv'] = [sys.executable, '-B', '-c',
                'import sys;sys.path.insert(0,sys.argv.pop(1));from poise.recovery_delivery import main;sys.exit(main(sys.argv[1:]))',
                str(Path(__file__).resolve().parents[2]), str(descriptor_path), phase]
        descriptor = {'staging': str(paths['staging']), 'manifest': manifest, 'binding': binding,
                      'policy': c, 'directory': str(directory), 'repository': str(repository),
                      'owner_roots': roots, 'variables': variables,
                      'git_seconds': h.cfg['limits']['git_seconds'],
                      'database': str(database.path), 'database_lock': str(database.lock),
                      'lock_seconds': database.wait, 'lock_poll_seconds': database.poll,
                      'actor': h.session, 'request_id': args['request_id'], 'identity': identity}
        plan = {'phase': 'prepared', 'descriptor_digest': digest(descriptor), 'binding': binding,
                'package_digest': args['package_digest']}
        if old is not None and old != plan:
            raise PoiseError('Prepared import destination/config changed')
        atomic_write(descriptor_path, (encoded(descriptor) + '\n').encode(), c['file_mode'])
        self.repo.remember(h.session, args['request_id'], identity, plan)
        outcome = self.flow.run(self.recovery['restore_flow'], variables,
                                directory / 'restore-flow.json', paths['restore_session'])
        reason = outcome['reason']
        steps = list(outcome['steps'])
        for phase in ('place', 'components', 'validate'):
            phase_path = directory / (phase + '-receipt.json')
            if phase_path.is_file():
                result = json.loads(phase_path.read_text())
                steps.extend(result.get('steps', []))
                if result['status'] == 'unknown': outcome['status'] = 'unknown'
                if result['status'] != 'complete': reason = result.get('reason', reason)
        if outcome['status'] == 'complete':
            # A customized pipeline cannot skip publication invariants.
            delivery = RecoveryDelivery(descriptor)
            delivery.validate()
            tables = delivery.snapshot()
        receipt = {'status': 'imported' if outcome['status'] == 'complete' else outcome['status'],
                   'success': outcome['status'] == 'complete', 'steps': steps, 'reason': reason,
                   'task_ids': manifest['task_ids'], 'sprint_ids': manifest['sprint_ids'],
                   'package_digest': args['package_digest'], 'replayed': False, 'placement_decision': decision,
                   'verification_origin': 'preserved_source_evidence_not_destination_execution',
                   'delivery': {'binding': binding, 'manifest': manifest}}
        if outcome['status'] != 'complete':
            self.repo.remember(h.session, args['request_id'], identity,
                               {'phase': outcome['status'], 'receipt': receipt})
            return receipt
        self.repo.install(tables, binding, h.session, args['request_id'], identity, receipt)
        return receipt
