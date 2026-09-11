"""Scoped SQLite/files/Git handoff across stores; explicit transport, no migrations."""
from copy import deepcopy
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import uuid
import zipfile
from ..common import PoiseError, descendant, digest, encoded, file_digest, read_json
from ..modules.transfers.domain import TRANSFER_FORMAT
from .sqlite.transfers import SqliteTransferRepository, snapshot_fingerprint
from .sqlite.transfer_records import relocate_path, relocate_receipt
from .sqlite.database import SCHEMA_VERSION
from .goal_config import atomic_write
from .locking import exclusive_lock


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
            receipt=old['receipt'];p=Path(receipt['package_path'])
            if not p.is_file() or file_digest(p)!=receipt['package_digest']:
                raise PoiseError('Saved transfer package missing or changed; no false successful replay')
            return {**receipt,'replayed':True}
        selected=args['task_ids']
        if args['handoff'] is not None:
            current=h.current_task()
            if current is None:raise PoiseError('No current task for combined transfer/handoff')
            if selected is not None and current['id'] not in selected:
                raise PoiseError('Current handoff task is outside selected transfer scope')
            if args['sprint_id'] is not None and current['sprint_id']!=args['sprint_id']:
                raise PoiseError('Current handoff is outside selected sprint')
            if selected is None and args['sprint_id'] is None:selected=[current['id']]
            h.handoff(args['handoff'])
        h.result_views.finish()
        tables=self.repo.capture(selected,args['sprint_id'],h.cfg['project'],c['max_tasks'])
        task_ids=[r['id'] for r in tables['tasks']];sprint_ids=[r['id'] for r in tables['sprints']]
        directory=self._request_dir(args);stage=directory/'preparing'
        if stage.exists():shutil.rmtree(stage)
        stage.mkdir(parents=True)
        owners={'task':{tid:str(descendant(h.state,h.paths['tasks'])/tid) for tid in task_ids},
                'sprint':{sid:str(descendant(h.state,h.paths['sprints'])/sid) for sid in sprint_ids},
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
                for path in sorted(root.rglob('*')):
                    if path.is_symlink():raise PoiseError('Task/sprint artifact symlink is not transferable')
                    if path.is_dir():continue
                    rel=(PurePosixPath(c['files_directory'])/scope/owner/path.relative_to(root).as_posix()).as_posix()
                    add(path,rel)
        execution={r['task_id']:json.loads(r['data']) for r in tables['task_execution']}
        for tid in task_ids:
            exe=execution.get(tid)
            if exe is None:
                owners['worktree'][tid]=None;workspaces[tid]=None;continue
            tree=Path(exe['worktree']);owners['worktree'][tid]=str(tree)
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
        package=descendant(directory,c['archive']);package.parent.mkdir(parents=True,exist_ok=True)
        pending=package.with_name(package.name+'.pending')
        with zipfile.ZipFile(pending,'w',compression=zipfile.ZIP_DEFLATED) as archive:
            archive.write(manifest_path,c['manifest'])
            for item in files:archive.write(descendant(stage,item['path']),item['path'])
        os.chmod(pending,c['file_mode']);os.replace(pending,package)
        receipt={'status':'exported','package_path':str(package),'package_digest':file_digest(package),
                 'task_ids':task_ids,'sprint_ids':sprint_ids,'replayed':False,
                 'delivery':'local_package_only','next_work':'Copy this package; import with explicit destination project configuration'}
        self.repo.remember(h.session,args['request_id'],identity,{'phase':'complete','receipt':receipt})
        h.store.event(h.session,None,'transfer.exported',receipt)
        shutil.rmtree(stage);h._cleanup_runtime()
        return receipt

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
                expected={'format','schema_version','project','execution_policy','source_config_hash',
                          'task_ids','sprint_ids','owners','workspaces','files','database','state_fingerprint'}
                if not isinstance(manifest,dict) or set(manifest)!=expected:
                    raise PoiseError('Incomplete transfer manifest')
                if manifest['format']!=TRANSFER_FORMAT or manifest['schema_version']!=SCHEMA_VERSION:
                    raise PoiseError('Incompatible transfer schema; no migration')
                if manifest['project']!=self.h.cfg['project'] or manifest['execution_policy']!=execution_policy(self.h.cfg):
                    raise PoiseError('Destination project execution policy differs; no implicit changes')
                inventory={x['path']:x for x in manifest['files']}
                if len(inventory)!=len(manifest['files']) or set(names)!=set(inventory)|{c['manifest']}:
                    raise PoiseError('Missing, extra or duplicate packaged files')
                if manifest['database']!=c['database']:raise PoiseError('Explicit snapshot filename differs')
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
        for scope in ('task','sprint'):
            for owner,old in manifest['owners'][scope].items():
                h._identifier(owner)
                folder=h.paths['tasks'] if scope=='task' else h.paths['sprints']
                locations[old]=str(descendant(h.state,folder)/owner)
        for tid,old in manifest['owners']['worktree'].items():
            h._identifier(tid)
            trees[tid]=None if old is None else str(descendant(h.state,h.paths['worktrees'])/tid)
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
