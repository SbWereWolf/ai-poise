"""Scoped SQLite/files/Git handoff across stores; explicit transport, no migrations."""
from copy import deepcopy
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import uuid
import zipfile
from ..common import PoiseError, descendant, digest, encoded, file_digest, read_json, worktree_root
from ..modules.transfers.domain import TRANSFER_FORMAT, ExportPreparation
from .sqlite.transfers import (SqliteTransferRepository,completed_external_execution,
                               snapshot_fingerprint)
from .sqlite.transfer_records import relocate_path, relocate_receipt
from .sqlite.database import SCHEMA_VERSION
from .goal_config import atomic_write
from .locking import exclusive_lock
from .file_publication import BinaryFilePublisher
from .task_paths import sprint_root, task_root


def _sprint_id(row):
    return json.loads(row['metadata'])['sprint_id']


def execution_policy(cfg):
    """Only physical repository/store locations may differ for this transfer slice."""
    value=deepcopy(cfg)
    del value['paths'];del value['git']['repository'];del value['processes']
    del value['runtime_services']['transfer']
    return digest(value)


class RuntimeTransfers:
    def __init__(self,poise):
        self.h=poise
        self.policy=poise.cfg['runtime_services']['transfer']
        self.root=descendant(poise.state,self.policy['directory'])
        self.repo=SqliteTransferRepository(poise.store.database)

    def _request_dir(self,args):
        return self.root / digest([self.h.session,args['action'],args['request_id']])

    def _locked(self,args):
        h=self.h
        return exclusive_lock(self._request_dir(args).with_suffix('.lock'),
                              h.cfg['limits']['lock_seconds'],h.cfg['limits']['lock_poll_seconds'])

    def export(self,args):
        with self._locked(args):return self._export(args)

    def _export(self,args):
        h=self.h;c=self.policy;identity=digest(args)
        old=self.repo.request(h.session,args['request_id'],identity)
        if old is not None and old['phase']=='complete':
            return self._finish_export(args, old['receipt'], replayed=True)
        selected=args['task_ids']
        if old is None and args['handoff'] is not None:
            current=h.current_task()
            if current is None:raise PoiseError('No current task for combined transfer/handoff')
            if selected is not None and current['id'] not in selected:
                raise PoiseError('Current handoff task is outside selected transfer scope')
            if args['sprint_id'] is not None and current['sprint_id']!=args['sprint_id']:
                raise PoiseError('Current handoff is outside selected sprint')
            if selected is None and args['sprint_id'] is None:selected=[current['id']]
            h.handoff(args['handoff'])
        if old is not None and old['phase'] == 'prepared':
            prepared = ExportPreparation.parse(old)
        else:
            with h.delivery_effects.locked():
                prepared = self._prepare_export(args, selected, old)
        return self._publish_prepared(args, prepared)

    def _prepare_export(self, args, selected, old):
        h, c, identity = self.h, self.policy, digest(args)
        h.result_views.finish()
        tables=self.repo.capture(selected,args['sprint_id'],h.cfg['project'],c['max_tasks'])
        task_ids=[r['id'] for r in tables['tasks']];sprint_ids=[r['id'] for r in tables['sprints']]
        preparation = ExportPreparation.parse({'phase': 'preparing', 'task_ids': task_ids,
            'sprint_ids': sprint_ids, 'fingerprint': snapshot_fingerprint(tables)})
        if old is not None and ExportPreparation.parse(old).data != preparation.data:
            raise PoiseError('Original export inputs changed before preparation completed')
        for task_id in task_ids:
            h.delivery_tools.material_admission(task_id, 'export')
        if old is None:
            self.repo.remember_export(h.session, args['request_id'], identity,
                                      preparation.data, expected_phase=None)
        directory=self._request_dir(args);stage=directory/'preparing'
        BinaryFilePublisher.without_links(stage, 'Export staging')
        if stage.exists():shutil.rmtree(stage)
        stage.mkdir(parents=True)
        task_rows = {row['id']: row for row in tables['tasks']}
        owners={'task':{tid:str(task_root(h.state, h.paths, tid, _sprint_id(task_rows[tid]))) for tid in task_ids},
                'sprint':{sid:str(sprint_root(h.state, h.paths, sid)) for sid in sprint_ids},
                'worktree':{}}
        files=[];workspaces={};observed_sources=[];total=0
        for artifact in tables['artifacts']:
            if artifact['scope'] not in ('task','sprint') or artifact['owner'] not in owners[artifact['scope']]:
                raise PoiseError('Artifact belongs to an unselected owner')
            path=Path(artifact['path']);root=Path(owners[artifact['scope']][artifact['owner']])
            if (path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root.resolve())
                    or file_digest(path)!=artifact['digest']):
                raise PoiseError('Registered artifact changed or disappeared before export')

        def add(source,relative):
            nonlocal total
            if source.is_symlink() or not source.is_file():raise PoiseError('Transfer accepts regular files, not symlinks')
            size=source.stat().st_size;total+=size
            if size>c['max_file_bytes'] or total>c['max_total_bytes'] or len(files)>=c['max_files']:
                raise PoiseError('Configured transfer file/byte limit reached')
            target=descendant(stage,relative);target.parent.mkdir(parents=True,exist_ok=True)
            if target!=source:
                with source.open('rb') as incoming,target.open('wb') as out:
                    while chunk:=incoming.read(c['chunk_bytes']):out.write(chunk)
                    out.flush();os.fsync(out.fileno())
                os.chmod(target,c['file_mode'])
            sha=file_digest(target)
            if source.stat().st_size!=size or file_digest(source)!=sha:raise PoiseError('Source changed while packaging')
            files.append({'path':relative,'size':size,'digest':sha})
            observed_sources.append((source,size,sha))

        snapshot=descendant(stage,c['database']);snapshot.parent.mkdir(parents=True,exist_ok=True)
        self.repo.write_snapshot(snapshot,tables);add(snapshot,c['database'])
        for scope in ('task','sprint'):
            for owner,root_text in owners[scope].items():
                root=Path(root_text)
                if root.is_symlink():raise PoiseError('Owner root is a symlink')
                if not root.exists():continue
                nested_task_roots = (
                    [Path(value).resolve() for value in owners['task'].values()
                     if Path(value).resolve().is_relative_to(root.resolve())]
                    if scope == 'sprint' else []
                )
                for path in sorted(root.rglob('*')):
                    if path.is_symlink():raise PoiseError('Task/sprint artifact symlink is not transferable')
                    if path.is_dir():continue
                    if any(path.resolve().is_relative_to(task) for task in nested_task_roots):continue
                    rel=(PurePosixPath(c['files_directory'])/scope/owner/path.relative_to(root).as_posix()).as_posix()
                    add(path,rel)
        execution={r['task_id']:json.loads(r['data']) for r in tables['task_execution']}
        for tid in task_ids:
            exe=execution.get(tid)
            if exe is None or exe['worktree'] is None:
                owners['worktree'][tid]=None;workspaces[tid]=None;continue
            tree=Path(exe['worktree']);owners['worktree'][tid]=str(tree)
            if not tree.exists() and completed_external_execution(exe['pending']):
                workspaces[tid]=None;continue
            if h._git(tree,'symbolic-ref','--short','HEAD')!=exe['branch']:
                raise PoiseError('Saved task branch changed')
            head=h._git(tree,'rev-parse','HEAD');fingerprint=h._tree(tree)
            if h._git(tree,'rev-parse','HEAD^{tree}')!=fingerprint:
                raise PoiseError('Dirty worktree must be handed off before export')
            if h.plan_actions._read_optional_ref(tree,'MERGE_HEAD') is not None:
                raise PoiseError('Unfinished merge cannot be transferred')
            for run in tables['action_runs']:
                if run['task_id']==tid and json.loads(run['data'])['status']!='complete':
                    raise PoiseError('Resolve the external action before transfer')
            handoffs=[json.loads(r['data']) for r in tables['handoffs'] if r['task_id']==tid and r['state']=='released']
            if handoffs:
                receipt=handoffs[-1]['receipt']
                if (receipt['commit'],receipt['tree'])!=(head,fingerprint):
                    raise PoiseError('Saved work changed since released handoff')
            bundle_rel=(PurePosixPath(c['bundles_directory'])/(tid+'.bundle')).as_posix()
            bundle=descendant(stage,bundle_rel);bundle.parent.mkdir(parents=True,exist_ok=True)
            h._git(tree,'bundle','create',str(bundle),'HEAD')
            h._git(tree,'bundle','verify',str(bundle));add(bundle,bundle_rel)
            workspaces[tid]={'commit':head,'tree':fingerprint,'base':exe['base'],'branch':exe['branch'],'bundle':bundle_rel}
        # Reconcile the finite selected snapshot, not unrelated work in the same store.
        later=self.repo.capture(task_ids if not sprint_ids else None,sprint_ids[0] if sprint_ids else None,
                                h.cfg['project'],c['max_tasks'])
        stable=snapshot_fingerprint
        if stable(tables)!=stable(later):raise PoiseError('Selected state changed while packaging')
        for path,size,sha in observed_sources:
            if not path.is_file() or path.stat().st_size!=size or file_digest(path)!=sha:
                raise PoiseError('Selected file changed while packaging')
        manifest={'format':TRANSFER_FORMAT,'schema_version':SCHEMA_VERSION,'project':h.cfg['project'],
                  'execution_policy':execution_policy(h.cfg),'source_config_hash':h.config_hash,
                  'task_ids':task_ids,'sprint_ids':sprint_ids,'owners':owners,'workspaces':workspaces,
                  'files':files,'database':c['database'], 'state_fingerprint':stable(tables)}
        manifest_path=descendant(stage,c['manifest'])
        atomic_write(manifest_path,(encoded(manifest)+'\n').encode(),c['file_mode'])
        # Persist a sealed, validated snapshot before any archive effect.
        prepared = preparation.seal(str(manifest_path), file_digest(manifest_path))
        self._validate_prepared(args, prepared)
        for item in [manifest_path, *(descendant(stage, f['path']) for f in files)]:
            fd = os.open(item, os.O_RDONLY | os.O_NOFOLLOW)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
            BinaryFilePublisher.sync_parent(item)
        BinaryFilePublisher.sync_parent(stage)
        self.repo.remember_export(h.session, args['request_id'], identity,
                                  prepared.data, expected_phase='preparing')
        return prepared

    def _validate_manifest(self, manifest):
        c = self.policy
        expected = {'format', 'schema_version', 'project', 'execution_policy', 'source_config_hash',
                    'task_ids', 'sprint_ids', 'owners', 'workspaces', 'files', 'database', 'state_fingerprint'}
        if not isinstance(manifest, dict) or set(manifest) != expected:
            raise PoiseError('Incomplete transfer manifest')
        if manifest['format'] != TRANSFER_FORMAT or manifest['schema_version'] != SCHEMA_VERSION:
            raise PoiseError('Incompatible transfer schema; no migration')
        if manifest['project'] != self.h.cfg['project'] or manifest['execution_policy'] != execution_policy(self.h.cfg):
            raise PoiseError('Destination project execution policy differs; no implicit changes')
        if manifest['database'] != c['database']:
            raise PoiseError('Explicit snapshot filename differs')
        inventory = {x['path']: x for x in manifest['files']}
        if len(inventory) != len(manifest['files']) or c['manifest'] in inventory:
            raise PoiseError('Duplicate transfer file identity')
        total = 0
        if len(inventory) > c['max_files']:
            raise PoiseError('Transfer entry count invalid')
        for name, item in inventory.items():
            relative = PurePosixPath(name)
            if relative.is_absolute() or '..' in relative.parts or str(relative) != name or '\\' in name:
                raise PoiseError('Unsafe transfer file path')
            if type(item['size']) is not int or not 0 <= item['size'] <= c['max_file_bytes']:
                raise PoiseError('Transfer file exceeds configured limit')
            total += item['size']
        if total > c['max_total_bytes']:
            raise PoiseError('Transfer exceeds configured byte limit')
        return inventory

    def _validate_prepared(self, args, prepared):
        data, c = prepared.data, self.policy
        stage = self._request_dir(args) / 'preparing'
        manifest_path = descendant(stage, c['manifest'])
        if data['phase'] != 'prepared' or data['manifest']['path'] != str(manifest_path):
            raise PoiseError('Prepared export staging owner changed')
        if BinaryFilePublisher.read(manifest_path, 'Prepared export manifest') != data['manifest']['digest']:
            raise PoiseError('Prepared export manifest integrity mismatch')
        manifest = read_json(manifest_path)
        inventory = self._validate_manifest(manifest)
        if (manifest['task_ids'], manifest['sprint_ids'], manifest['state_fingerprint']) != (
                data['task_ids'], data['sprint_ids'], data['fingerprint']):
            raise PoiseError('Prepared export selection changed')
        for name, item in inventory.items():
            path = descendant(stage, name)
            if BinaryFilePublisher.read(path, 'Prepared export file') != item['digest'] or path.stat().st_size != item['size']:
                raise PoiseError('Prepared export file integrity mismatch')
        tables = self.repo.read_snapshot(descendant(stage, c['database']))
        if snapshot_fingerprint(tables) != data['fingerprint']:
            raise PoiseError('Prepared export snapshot integrity mismatch')
        if sorted(row['id'] for row in tables['tasks']) != sorted(data['task_ids']):
            raise PoiseError('Prepared export snapshot owner changed')
        for workspace in manifest['workspaces'].values():
            if workspace is not None:
                self.h._git(Path(self.h.cfg['git']['repository']), 'bundle', 'verify',
                            str(descendant(stage, workspace['bundle'])))
        return manifest

    def _publish_prepared(self, args, prepared):
        h, c, identity = self.h, self.policy, digest(args)
        manifest = self._validate_prepared(args, prepared)
        directory = self._request_dir(args)
        stage = directory / 'preparing'
        manifest_path = descendant(stage, c['manifest'])
        package = descendant(directory, c['archive'])
        pending = package.with_name(package.name + '.pending')
        BinaryFilePublisher.without_links(pending, 'Export publication')
        with zipfile.ZipFile(pending, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            archive.write(manifest_path, c['manifest'])
            for item in manifest['files']:
                archive.write(descendant(stage, item['path']), item['path'])
        self._validate_prepared(args, prepared)
        BinaryFilePublisher.publish(pending, package, file_digest(pending), c['file_mode'])
        pending.unlink()
        receipt = {'status': 'exported', 'package_path': str(package), 'package_digest': file_digest(package),
                   'task_ids': prepared.data['task_ids'], 'sprint_ids': prepared.data['sprint_ids'],
                   'replayed': False, 'delivery': 'local_package_only',
                   'next_work': 'Copy this package; import with explicit destination project configuration'}
        self.repo.remember_export(h.session, args['request_id'], identity,
                                  {'phase': 'complete', 'receipt': receipt}, expected_phase='prepared')
        return self._finish_export(args, receipt, replayed=False)

    def _finish_export(self, args, receipt, *, replayed):
        """Finish only this completed request's remaining preparation cleanup."""
        package = Path(receipt['package_path'])
        if not package.is_file() or file_digest(package) != receipt['package_digest']:
            raise PoiseError('Saved transfer package missing or changed; no false successful replay')
        if not replayed:
            self.h.store.event(self.h.session, None, 'transfer.exported', receipt)
        stage = self._request_dir(args) / 'preparing'
        BinaryFilePublisher.without_links(stage, 'Export staging')
        if os.path.lexists(stage):
            if not stage.is_dir():
                raise PoiseError('Export staging is not a directory')
            shutil.rmtree(stage)
            BinaryFilePublisher.sync_parent(stage)
        if os.path.lexists(stage):
            raise PoiseError('Export preparation removal incomplete')
        self.h._cleanup_runtime()
        return {**receipt, 'replayed': replayed}

    def restore(self,args):
        with self._locked(args):return self._restore(args)

    def _unpack(self,path,target):
        c=self.policy
        if target.exists():shutil.rmtree(target)
        target.mkdir(parents=True)
        try:
            with zipfile.ZipFile(path) as archive:
                entries=archive.infolist();names=[x.filename for x in entries]
                if len(entries)>c['max_files']+1 or len(names)!=len(set(names)):
                    raise PoiseError('Transfer entry count/identity invalid')
                total=0
                for entry in entries:
                    p=PurePosixPath(entry.filename)
                    mode=stat.S_IFMT(entry.external_attr>>16)
                    if (entry.is_dir() or p.is_absolute() or '..' in p.parts or str(p)!=entry.filename
                        or '\\' in entry.filename or mode not in (0,stat.S_IFREG)):
                        raise PoiseError('Unsafe/non-regular transfer entry')
                    if entry.file_size>c['max_file_bytes']:raise PoiseError('Transfer file exceeds configured limit')
                    total+=entry.file_size
                if total>c['max_total_bytes']:raise PoiseError('Transfer exceeds configured byte limit')
                if c['manifest'] not in names:raise PoiseError('Missing configured transfer manifest')
                manifest=json.loads(archive.read(c['manifest']))
                inventory = self._validate_manifest(manifest)
                if set(names) != set(inventory) | {c['manifest']}:
                    raise PoiseError('Missing, extra or duplicate packaged files')
                for entry in entries:
                    destination=descendant(target,entry.filename);destination.parent.mkdir(parents=True,exist_ok=True)
                    count=0
                    with archive.open(entry) as source,destination.open('wb') as out:
                        while chunk:=source.read(c['chunk_bytes']):
                            count+=len(chunk)
                            if count>entry.file_size:raise PoiseError('Expanded file exceeds declared size')
                            out.write(chunk)
                    os.chmod(destination,c['file_mode'])
                    if entry.filename in inventory:
                        record=inventory[entry.filename]
                        if count!=record['size'] or file_digest(destination)!=record['digest']:
                            raise PoiseError('Transfer file digest/integrity mismatch')
                return manifest
        except (KeyError,TypeError,ValueError,zipfile.BadZipFile) as exc:
            raise PoiseError(f'Invalid transfer package: {exc}') from exc

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

    def _restore(self,args):
        h=self.h;c=self.policy;identity=digest(args)
        old=self.repo.request(h.session,args['request_id'],identity)
        path=Path(args['package_path'])
        if not path.is_file() or path.is_symlink() or file_digest(path)!=args['package_digest']:
            raise PoiseError('Package digest/integrity mismatch')
        if old is not None and old['phase']=='complete':return {**old['receipt'],'replayed':True}
        imported=self.repo.imported(args['package_digest'])
        if imported is not None:
            self.repo.remember(h.session,args['request_id'],identity,{'phase':'complete','receipt':imported})
            return {**imported,'replayed':True}
        directory=self._request_dir(args);stage=directory/'validated'
        manifest=self._unpack(path,stage)
        tables=self.repo.read_snapshot(descendant(stage,c['database']))
        if [r['id'] for r in tables['tasks']]!=manifest['task_ids'] or [r['id'] for r in tables['sprints']]!=manifest['sprint_ids']:
            raise PoiseError('Transfer owner inventory differs from snapshot')
        if len(manifest['task_ids'])>c['max_tasks']:raise PoiseError('Transfer task count limit reached')
        if snapshot_fingerprint(tables)!=manifest['state_fingerprint']:
            raise PoiseError('Snapshot record fingerprint mismatch')
        binding=self._binding(manifest,tables,args['package_digest'])
        self.repo.ensure_absent(tables)
        repository=Path(h.cfg['git']['repository']).resolve(strict=True)
        owner_dirs=[]
        for scope in ('task','sprint'):
            for owner,source in manifest['owners'][scope].items():
                target=Path(binding['locations'][source]);owner_dirs.append(target)
                if target.is_symlink():raise PoiseError('Destination owner root is a symlink')
                if old is None and target.exists():raise PoiseError('Destination owner directory already exists')
        for tid,spec in manifest['workspaces'].items():
            if spec is None:continue
            tree=Path(binding['worktrees'][tid]);branch=spec['branch']
            bundle=descendant(stage,spec['bundle'])
            if not bundle.is_file():raise PoiseError('Missing source Git bundle')
            h._git(repository,'check-ref-format','--branch',branch)
            h._git(repository,'bundle','verify',str(bundle))
            heads=h._git(repository,'bundle','list-heads',str(bundle))
            if not any(line.split()[0]==spec['commit'] for line in heads.splitlines()):
                raise PoiseError('Bundle does not contain declared saved result')
            if old is None and (tree.exists() or h.plan_actions._read_optional_ref(repository,'refs/heads/'+branch) is not None):
                raise PoiseError('Destination worktree or branch already exists; no overwrite')
        plan={'phase':'prepared','package_digest':args['package_digest'],
              'binding':binding,'task_ids':manifest['task_ids'],'sprint_ids':manifest['sprint_ids']}
        if old is not None and old!=plan:raise PoiseError('Prepared import destination/config changed')
        self.repo.remember(h.session,args['request_id'],identity,plan)
        # Immutable file preparation precedes the short business publication transaction.
        allowed_files=set()
        for item in manifest['files']:
            rel=PurePosixPath(item['path']);prefix=PurePosixPath(c['files_directory'])
            if not rel.is_relative_to(prefix):continue
            parts=rel.relative_to(prefix).parts
            if len(parts)<3 or parts[0] not in ('task','sprint') or parts[1] not in manifest['owners'][parts[0]]:
                raise PoiseError('File has no selected task/sprint owner')
            source_root=manifest['owners'][parts[0]][parts[1]]
            target=descendant(Path(binding['locations'][source_root]),PurePosixPath(*parts[2:]).as_posix())
            allowed_files.add(target)
            if target.exists():
                if target.is_symlink() or not target.is_file() or file_digest(target)!=item['digest']:
                    raise PoiseError('Prepared destination file changed; not overwriting it')
            else:
                target.parent.mkdir(parents=True,exist_ok=True)
                temp=target.with_name(target.name+'.pending')
                shutil.copyfile(descendant(stage,item['path']),temp);os.chmod(temp,c['file_mode']);os.replace(temp,target)
        for root in owner_dirs:
            if not root.exists():root.mkdir(parents=True)
            for item in root.rglob('*'):
                if item.is_symlink() or (item.is_file() and item not in allowed_files):
                    raise PoiseError('Unexpected file in prepared import owner root')
        for tid,spec in manifest['workspaces'].items():
            if spec is None:continue
            tree=Path(binding['worktrees'][tid]);bundle=descendant(stage,spec['bundle'])
            if not tree.exists():
                h._git(repository,'fetch','--no-tags',str(bundle),'HEAD')
                tree.parent.mkdir(parents=True,exist_ok=True)
                h._git(repository,'worktree','add','-b',spec['branch'],str(tree),spec['commit'])
            if (h._git(tree,'rev-parse','HEAD')!=spec['commit'] or h._tree(tree)!=spec['tree']
                or h._git(tree,'symbolic-ref','--short','HEAD')!=spec['branch']):
                raise PoiseError('Prepared imported worktree changed; do not reset it')
            h._git(tree,'cat-file','-e',spec['base']+'^{commit}')
        receipt={'status':'imported','task_ids':manifest['task_ids'],'sprint_ids':manifest['sprint_ids'],
                 'package_digest':args['package_digest'],'replayed':False,
                 'verification_origin':'preserved_source_evidence_not_destination_execution',
                 'next_work':'Bootstrap a selected task/sprint to resume; existing work was not switched'}
        self.repo.install(tables,binding,h.session,args['request_id'],identity,receipt)
        shutil.rmtree(stage)
        return receipt
