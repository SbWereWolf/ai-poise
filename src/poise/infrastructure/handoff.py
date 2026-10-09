"""Local handoff in the same state store: preserve first, release ownership last.

No migration, remote store import or automatic backup delivery is implied.
"""
from ..modules.actions.domain import CommitMessagePolicy
from copy import deepcopy
from contextlib import contextmanager
import hashlib
import io
import json
import os
import shutil
import stat
import tarfile
import tempfile
from pathlib import Path
from ..common import PoiseError,descendant,digest,file_digest,encoded
from ..application.handoff import HandoffCommands
from ..application.check_attempts import is_check_attempt, validate_preserved_attempt_in, require_quiescence
from .goal_config import atomic_write


class LocalHandoff:
    def __init__(self,poise):
        self.h=poise;self.commands=HandoffCommands(poise.store.unit_of_work)
        self.config=poise.cfg['runtime_services']['handoff']

    def preserve(self,args, *, portable=False):
        h=self.h
        request_id=h._identifier(args['request_id'])
        if not isinstance(args['reason'],str) or not args['reason'].strip():raise PoiseError('Handoff reason required')
        key=digest({'arguments': args, 'portable': True} if portable else args);prior=self.commands.lookup(h.session,request_id)
        if prior is not None:
            if prior['digest']!=key:raise PoiseError('Handoff request reused with different content')
            if prior['state'] in ('released','resumed'):
                return {**prior['receipt'],'replayed':True}
        with h.delivery_effects.locked():
            return self._preserve_material(args, request_id, key, prior, portable=portable)

    def _preserve_material(self, args, request_id, key, prior, *, portable):
        h = self.h
        data=h.current_task()
        if data is None:raise PoiseError('No current task to hand off')
        h.delivery_tools.material_admission(data['id'], 'handoff')
        data=h._task();worktree_free=data['worktree'] is None
        newborn=data['status']=='newborn'
        if newborn and args['result'] is not None:
            raise PoiseError('Newborn handoff requires null result; edit the draft instead of submitting a stage result')
        worktree=h._verification_workspace(data)
        recovery = args.get('uncertain_check_recovery')
        wip_context = None if recovery is not None else self.commands.preservation_context(data['id'], h.session)
        if recovery is not None:
            if (not is_check_attempt(data['pending'])
                    or recovery['attempt_id'] != data['pending']['attempt_id']):
                raise PoiseError('Uncertain recovery attempt identity does not match current pending check')
            with h.store.unit_of_work() as uow:
                validate_preserved_attempt_in(uow, uow.tasks.load(data['id']), h.session, data['pending'])
            h._validate_check_restart(data['pending'])
        elif data['pending'] is not None:
            raise PoiseError('Determine pending execution outcome before handoff')
        action=None if newborn else h.plan_actions.snapshot(data)
        if action is not None and action['status'] not in ('complete',):
            raise PoiseError('Finish or explicitly resolve the external plan before handoff')
        if not worktree_free and h.plan_actions._read_optional_ref(worktree,'MERGE_HEAD') is not None:
            raise PoiseError('Unfinished merge cannot be handed off by the local checkpoint adapter')
        tree=h._current_tree(data)
        if not worktree_free and h._git(worktree,'symbolic-ref','--short','HEAD')!=data['branch']:
            raise PoiseError('Worktree branch changed')
        verified=data['status'] in ('verified','accepted')
        if verified and tree!=data['last_report']['verified_tree']:
            raise PoiseError('Result changed after report: explicit rework before handoff')
        if not isinstance(args['artifact_paths'],list):raise PoiseError('artifact_paths list required')
        h.retention.validate_bundle(data, data['last_report'])
        records=h.validate_artifact_paths(args['artifact_paths'],data)
        report = data['last_report']
        if report is not None and report.get('acceptance_manifests'):
            records += h.validate_artifact_paths([item['path'] for item in h.store.artifact_records(data['id'])
                                                if item['scope'] == 'task'], data)
        payload=args['result']
        if payload is None and not verified and not newborn and recovery is None:
            payload=h.task_queries.current_submission(data['id'])
        if payload is not None:
            if verified:raise PoiseError('Verified handoff does not accept a replacement result')
            h.validate_stage_result(data['id'],payload)
            records+=h.validate_artifact_paths(payload['artifact_paths'],data)
        head=data['base'] if worktree_free else h._git(worktree,'rev-parse','HEAD')
        dirty=False if worktree_free else h._git(worktree,'rev-parse','HEAD^{tree}')!=tree
        msg=args['commit_message']
        if dirty and not portable and recovery is None and wip_context is None and not CommitMessagePolicy(h.cfg['git']['commit_pattern']).matches(msg):
            raise PoiseError('WIP requires explicit valid repository commit message')
        if (not dirty or wip_context is not None) and msg is not None and not CommitMessagePolicy(h.cfg['git']['commit_pattern']).matches(msg):
            raise PoiseError('Invalid supplied handoff commit message')
        directory=descendant(h._roots(data)['task'],self.config['directory'])/digest([h.session,request_id])
        # Every selected runtime artifact is copied to task before cleanup; data in
        # existing submissions remains immutable, receipt carries the mapping.
        directory.mkdir(parents=True,exist_ok=True)
        preserved=[];mapping={}
        for r in records:
            if r['scope']=='runtime':
                target=descendant(directory,self.config['preserved_directory'])/(r['digest']+'-'+Path(r['path']).name)
                target.parent.mkdir(parents=True,exist_ok=True)
                if target.exists() and file_digest(target)!=r['digest']:raise PoiseError('Preserved artifact changed')
                if not target.exists():
                    temp=target.with_name(target.name+'.pending');shutil.copyfile(r['path'],temp)
                    os.chmod(temp,self.config['file_mode']);os.replace(temp,target)
                if file_digest(target)!=r['digest']:raise PoiseError('Artifact changed during preservation')
                mapping[r['path']]=str(target);preserved.append(str(target))
            else:preserved.append(r['path'])
        if payload is not None:
            payload=deepcopy(payload)
            payload['artifact_paths']=[mapping.get(p,p) for p in payload['artifact_paths']]
            h.runner.submit(data['id'],h.session,payload)
            data=h._task()
        plan={'reason':args['reason'],'tree':tree,'head_at_start':head,'directory':str(directory),
              'verified':verified,'commit_message':msg,'preserved_artifacts':list(dict.fromkeys(preserved)),
              'artifact_mapping':mapping}
        if portable: plan['portable'] = True
        if wip_context is not None:
            plan['wip_context'] = wip_context
            plan['wip_snapshot'] = (prior['plan']['wip_snapshot'] if prior is not None
                                    else self._preserve_wip_snapshot(worktree, data, directory))
            if self._validate_wip_snapshot(worktree, plan['wip_snapshot'])['task_id'] != data['id']:
                raise PoiseError('WIP snapshot Task identity changed')
        if recovery is not None:
            plan['uncertain_check_recovery'] = {'intent': deepcopy(recovery), 'attempt': deepcopy(data['pending'])}
            if not worktree_free:
                plan['worktree_state'] = {
                    'index': h._git(worktree, 'write-tree'),
                    'status': h._git(worktree, 'status', '--porcelain=v1'),
                }
        if prior is None:
            prior=self.commands.prepare(h.session,request_id,key,data['id'],data['_version'],plan)
        else:
            plan=prior['plan']
            if tree!=plan['tree'] or data['_version']!=prior['version']:
                raise PoiseError('Preserved inputs changed during unfinished handoff')
        if not portable and recovery is None and wip_context is None and not worktree_free and h._git(worktree,'rev-parse','HEAD^{tree}')!=plan['tree']:
            h._git(worktree,'add','--all')
            if h._git(worktree,'write-tree')!=plan['tree']:raise PoiseError('WIP index no longer matches captured state')
            actor={k:h.cfg['git'][v] for k,v in [('GIT_AUTHOR_NAME','author_name'),('GIT_COMMITTER_NAME','author_name'),
                                                    ('GIT_AUTHOR_EMAIL','author_email'),('GIT_COMMITTER_EMAIL','author_email')]}
            h._git(worktree,'commit','-m',plan['commit_message'],env={**os.environ,**actor})
        sha=plan['head_at_start'] if worktree_free else h._git(worktree,'rev-parse','HEAD')
        if (not worktree_free and
                (h._tree(worktree)!=plan['tree'] or
                 (not portable and recovery is None and wip_context is None and h._git(worktree,'rev-parse','HEAD^{tree}')!=plan['tree']) or
                 (recovery is not None and sha != plan['head_at_start']))):
            h.store.event(h.session,data['id'],'incident.handoff_tree_changed',{'expected_tree':plan['tree'],'commit':sha})
            raise PoiseError('Commit hook changed WIP tree; claim retained')
        directory=Path(plan['directory']);bundle=descendant(directory,self.config['bundle'])
        # Bundle is an offline copy, not a push and not proof of task completion.
        if not portable and not worktree_free and not bundle.exists():
            temp=bundle.with_name(bundle.name+'.pending')
            if temp.exists():temp.unlink()
            h._git(worktree,'bundle','create',str(temp),'HEAD')
            h._git(worktree,'bundle','verify',str(temp));os.replace(temp,bundle)
        if not portable and not worktree_free:
            heads=h._git(worktree,'bundle','list-heads',str(bundle))
            if not any(line.split()[0]==sha for line in heads.splitlines()):raise PoiseError('Bundle does not preserve current commit')
        receipt_path=descendant(directory,self.config['receipt'])
        receipt={'status':'handed_off','task':data['id'],
                 'stage':None if newborn else h._stage(data)['id'],'iteration':data['iteration'],
                 'task_status':data['status'],
                 'verified':plan['verified'],'commit':sha,'tree':plan['tree'],
                 'worktree':None if worktree_free else str(worktree),
                 'receipt_path':str(receipt_path),'bundle_path':None if worktree_free or portable else str(bundle),
                 'bundle_digest':None if worktree_free or portable else file_digest(bundle),
                 'preserved_artifacts':plan['preserved_artifacts'],'artifact_mapping':plan['artifact_mapping'],
                 'reason':plan['reason'],'replayed':False,'transfer_scope':'same_store_local_resume'}
        if 'wip_snapshot' in plan:
            receipt['wip_snapshot'] = plan['wip_snapshot']
        atomic_write(receipt_path,(encoded(receipt)+'\n').encode(),self.config['file_mode'])
        owned=[*plan['preserved_artifacts'],str(receipt_path)]
        if not worktree_free and not portable:owned.append(str(bundle))
        if 'wip_snapshot' in plan:owned.append(plan['wip_snapshot']['path'])
        h.register_artifact_paths(owned,data)
        h.result_views.finish()
        if recovery is not None and not worktree_free:
            self._validate_recovery_worktree(worktree, plan)
        if 'wip_snapshot' in plan:
            self._validate_wip_snapshot(worktree, plan['wip_snapshot'])
        self.commands.release(h.session,request_id,receipt)
        h.store.event(h.session,data['id'],'handoff.released',{'receipt':str(receipt_path),'commit':sha,'verified':verified})
        h._cleanup_runtime()
        return receipt

    def validate_resume(self, data):
        """Check preserved handoff bytes without acquiring or executing the Task."""
        h=self.h;record=self.commands.latest(data['id'])
        if record is None:raise PoiseError('No explicit preserved handoff; cannot adopt unowned state')
        receipt=record['receipt']
        delivery = h.transfer_tools.port.repo.delivery_for_task(data['id'])
        if delivery is not None and not delivery['verification_opened']:
            manifest = delivery['manifest']
            spec = manifest['workspaces'][data['id']]
            if spec is not None:
                tree = Path(data['worktree'])
                if (h._git(tree, 'rev-parse', 'HEAD') != spec['commit'] or h._tree(tree) != spec['tree']
                        or h._git(tree, 'symbolic-ref', '--short', 'HEAD') != spec['branch']):
                    raise PoiseError('Delivered worktree changed before resume')
            for item in manifest['files']:
                if item.get('task_id') != data['id'] or 'placement' not in item:
                    continue
                path = Path(delivery['binding']['locations'][item['placement']])
                if path.is_symlink() or not path.is_file() or file_digest(path) != item['digest']:
                    raise PoiseError('Delivered current Task material missing or changed')
            return record
        if 'wip_snapshot' in receipt:
            if self._validate_wip_snapshot(Path(data['worktree']), receipt['wip_snapshot'])['task_id'] != data['id']:
                raise PoiseError('WIP snapshot Task identity changed before resume')
        recovery = record['plan'].get('uncertain_check_recovery')
        if recovery is not None:
            if data['pending'] != recovery['attempt']:
                raise PoiseError('Preserved uncertain recovery attempt changed before resume')
            require_quiescence(data['pending'])
            if data['worktree'] is not None:
                self._validate_recovery_worktree(Path(data['worktree']), record['plan'])
        if data['worktree'] is None:
            if (receipt['worktree'] is not None or receipt['tree']!=data['entry_tree']
                    or receipt['commit']!=data['base'] or receipt['bundle_path'] is not None
                    or receipt['bundle_digest'] is not None):
                raise PoiseError('Worktree-free handoff facts changed')
        else:
            worktree=Path(data['worktree'])
            if (h._tree(worktree)!=receipt['tree'] or h._git(worktree,'rev-parse','HEAD')!=receipt['commit']
                or h._git(worktree,'symbolic-ref','--short','HEAD')!=data['branch']):
                raise PoiseError('Worktree changed since handoff; do not adopt external changes silently')
            if not record['plan'].get('portable') and (not Path(receipt['bundle_path']).is_file() or file_digest(Path(receipt['bundle_path']))!=receipt['bundle_digest']):
                raise PoiseError('Preserved source bundle missing or changed')
        h.retention.validate_bundle(data, data['last_report'])
        return record

    def _validate_recovery_worktree(self, worktree, plan):
        h = self.h
        if (h._git(worktree, 'rev-parse', 'HEAD') != plan['head_at_start']
                or h._tree(worktree) != plan['tree']
                or h._git(worktree, 'write-tree') != plan['worktree_state']['index']
                or h._git(worktree, 'status', '--porcelain=v1') != plan['worktree_state']['status']):
            raise PoiseError('Uncertain recovery worktree HEAD/index/WIP changed')

    def resume(self,data, *, force_duplicate_start=False):
        h = self.h
        record = self.validate_resume(data)
        receipt = record['receipt']
        self.commands.resume(data['id'],h.session,record['actor'],record['request_id'],force_duplicate_start=force_duplicate_start)
        h.store.event(h.session,data['id'],'handoff.resumed',{'from_session':record['actor'],'commit':receipt['commit']})

    @staticmethod
    def _snapshot_path(name):
        path = Path(name)
        if not isinstance(name, str) or not name or path.is_absolute() or '..' in path.parts or str(path) != name:
            raise PoiseError('Unsafe WIP snapshot path')
        return path

    @staticmethod
    @contextmanager
    def _working_parent(worktree, relative):
        """Anchor leaf operations to directories opened without symlink traversal."""
        directory = os.open(worktree, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            for component in relative.parts[:-1]:
                child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                dir_fd=directory)
                os.close(directory)
                directory = child
            yield directory
        finally:
            os.close(directory)

    @staticmethod
    def _read_working_file(directory, relative):
        descriptor = os.open(relative.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        with os.fdopen(descriptor, 'rb') as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise PoiseError(f'WIP file type changed: {relative}')
            return stream.read()

    def _index_material(self, worktree, temporary):
        h = self.h
        if h._git(worktree, 'rev-parse', '--shared-index-path'):
            raise PoiseError('WIP snapshot does not support split-index dependencies; claims retained')
        index = Path(h._git(worktree, 'rev-parse', '--git-path', 'index'))
        if not index.is_absolute():
            index = worktree / index
        if not stat.S_ISREG(index.lstat().st_mode):
            raise PoiseError('WIP snapshot requires a regular index')
        raw = index.read_bytes()
        copy = temporary / 'index'
        copy.write_bytes(raw)
        environment = {**os.environ, 'GIT_INDEX_FILE': str(copy)}
        entries = []
        for row in h._git(worktree, 'ls-files', '--stage', '-z', env=environment).split('\0'):
            if not row:
                continue
            metadata, name = row.split('\t', 1)
            mode, oid, stage = metadata.split()
            self._snapshot_path(name)
            if stage != '0' or mode not in ('100644', '100755', '120000'):
                raise PoiseError(f'Unsupported staged snapshot entry {name}; claims retained')
            entries.append({'path': name, 'mode': mode, 'oid': oid})
        tree = h._git(worktree, 'write-tree', env=environment)
        return raw, stat.S_IMODE(index.lstat().st_mode), entries, tree

    def _observe_wip(self, worktree, task_id):
        h = self.h
        names = set()
        for arguments in (('ls-tree', '-r', '-z', '--name-only', 'HEAD'),
                          ('ls-files', '-z', '--cached', '--others', '--exclude-standard')):
            names.update(name for name in h._git(worktree, *arguments).split('\0') if name)
        files = {}
        payloads = {}
        for name in sorted(names):
            relative = self._snapshot_path(name)
            # Record ancestor modes; leaf metadata and reads are anchored to
            # no-follow directory descriptors below, not these observations.
            for parent in reversed(relative.parents):
                if str(parent) == '.':
                    continue
                location = worktree / parent
                try:
                    mode = location.lstat().st_mode
                except FileNotFoundError:
                    continue
                if not stat.S_ISDIR(mode):
                    raise PoiseError(f'Unsafe symlink/special ancestor {parent}; claims and WIP retained')
                files[str(parent)] = {'path': str(parent), 'kind': 'directory',
                                      'mode': stat.S_IMODE(mode), 'payload': None}
            try:
                with self._working_parent(worktree, relative) as directory:
                    mode = os.stat(relative.name, dir_fd=directory, follow_symlinks=False).st_mode
                    if stat.S_ISLNK(mode):
                        content = os.fsencode(os.readlink(relative.name, dir_fd=directory))
                        kind = 'symlink'
                    elif stat.S_ISREG(mode):
                        content = self._read_working_file(directory, relative)
                        kind = 'file'
                    else:
                        raise PoiseError(f'Unsupported WIP snapshot file {name}; claims retained')
            except FileNotFoundError:
                files[name] = {'path': name, 'kind': 'absent', 'mode': None, 'payload': None}
                continue
            checksum = hashlib.sha256(content).hexdigest()
            payload = 'payloads/' + checksum
            payloads[payload] = content
            files[name] = {'path': name, 'kind': kind, 'mode': stat.S_IMODE(mode),
                           'payload': payload, 'sha256': checksum}
        with tempfile.TemporaryDirectory(dir=h.runtime.parent, prefix='handoff-index-') as directory:
            raw, mode, entries, staged_tree = self._index_material(worktree, Path(directory))
        payloads['index'] = raw
        return {'schema': 'poise-handoff-wip-1', 'task_id': task_id,
                'head': h._git(worktree, 'rev-parse', 'HEAD'),
                'branch': h._git(worktree, 'symbolic-ref', '--short', 'HEAD'),
                'tree': h._tree(worktree),
                'index': {'path': 'index', 'sha256': hashlib.sha256(raw).hexdigest(), 'mode': mode},
                'staged_tree': {'path': 'staged-tree.tar', 'tree': staged_tree, 'entries': entries},
                'files': [files[name] for name in sorted(files)]}, payloads

    def _validate_staged_archive(self, worktree, content, expected):
        entries = {item['path']: item for item in expected}
        if len(entries) != len(expected):
            raise PoiseError('Duplicate staged snapshot inventory')
        with tarfile.open(fileobj=io.BytesIO(content)) as archive:
            members = archive.getmembers()
            seen = set()
            leaves = {}
            ancestors = {str(parent) for name in entries for parent in Path(name).parents if str(parent) != '.'}
            for member in members:
                name = member.name.rstrip('/') if member.isdir() else member.name
                self._snapshot_path(name)
                if name in seen:
                    raise PoiseError(f'Duplicate staged archive entry {name}; claims retained')
                seen.add(name)
                if member.isdir():
                    if name not in ancestors:
                        raise PoiseError(f'Unexpected staged archive directory {name}')
                else:
                    leaves[name] = member
            missing = entries.keys() - leaves.keys()
            extra = leaves.keys() - entries.keys()
            if missing or extra:
                raise PoiseError(f'Incomplete staged archive: missing {sorted(missing)}, unexpected {sorted(extra)}; claims retained')
            with tempfile.TemporaryDirectory(dir=self.h.runtime.parent, prefix='handoff-blobs-') as directory:
                for number, (name, entry) in enumerate(entries.items()):
                    member = leaves[name]
                    if entry['mode'] == '120000' and member.issym():
                        raw = os.fsencode(member.linkname)
                    elif entry['mode'] in ('100644', '100755') and member.isfile():
                        if bool(member.mode & 0o111) != (entry['mode'] == '100755'):
                            raise PoiseError(f'Staged archive mode mismatch {name}')
                        raw = archive.extractfile(member).read()
                    else:
                        raise PoiseError(f'Staged archive type mismatch {name}')
                    blob = Path(directory) / str(number)
                    blob.write_bytes(raw)
                    oid = self.h._git(worktree, 'hash-object', '--no-filters', '--', str(blob))
                    if oid != entry['oid']:
                        raise PoiseError(f'Staged archive blob mismatch {name}; claims retained')

    def _validate_wip_snapshot(self, worktree, descriptor):
        try:
            if set(descriptor) != {'schema', 'path', 'digest'} or descriptor['schema'] != 'poise-handoff-wip-1':
                raise PoiseError('Unsupported WIP snapshot descriptor')
            path = Path(descriptor['path'])
            if not stat.S_ISREG(path.lstat().st_mode):
                raise PoiseError('WIP snapshot missing or changed')
            raw = path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != descriptor['digest']:
                raise PoiseError('WIP snapshot missing or changed')
            with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
                members = archive.getmembers()
                if len({item.name for item in members}) != len(members):
                    raise PoiseError('Duplicate WIP snapshot payload')
                for member in members:
                    self._snapshot_path(member.name)
                    if not member.isfile():
                        raise PoiseError('WIP snapshot payload must be regular data')
                payloads = {member.name: archive.extractfile(member).read() for member in members}
            manifest = json.loads(payloads.pop('manifest.json'))
            if set(manifest) != {'schema', 'task_id', 'head', 'branch', 'tree', 'index', 'staged_tree', 'files'}:
                raise PoiseError('Invalid WIP snapshot manifest schema')
            if manifest['schema'] != descriptor['schema']:
                raise PoiseError('WIP snapshot schema mismatch')
            live, live_payloads = self._observe_wip(worktree, manifest['task_id'])
            declared_staged = manifest['staged_tree']
            if set(declared_staged) != {'path', 'tree', 'entries', 'sha256'}:
                raise PoiseError('Invalid staged archive manifest')
            observed = deepcopy(manifest)
            observed['staged_tree'].pop('sha256')
            if live != observed:
                raise PoiseError('WIP snapshot material/index/branch changed; claims retained')
            staged = payloads.pop(declared_staged['path'])
            if hashlib.sha256(staged).hexdigest() != declared_staged['sha256'] or payloads != live_payloads:
                raise PoiseError('WIP snapshot payload changed')
            self._validate_staged_archive(worktree, staged, declared_staged['entries'])
            return manifest
        except (PoiseError, OSError, ValueError, KeyError, TypeError, tarfile.TarError) as exc:
            raise PoiseError(f'Invalid WIP snapshot; claims retained: {exc}') from exc

    def _preserve_wip_snapshot(self, worktree, data, directory):
        target = descendant(directory, self.config['preserved_directory']) / 'wip-snapshot.tar'
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            descriptor = {'schema': 'poise-handoff-wip-1', 'path': str(target), 'digest': file_digest(target)}
            return descriptor
        manifest, payloads = self._observe_wip(worktree, data['id'])
        with tempfile.TemporaryDirectory(dir=self.h.runtime.parent, prefix='handoff-archive-') as temporary:
            archive = Path(temporary) / 'staged-tree.tar'
            self.h._git(worktree, 'archive', '--output=' + str(archive), manifest['staged_tree']['tree'])
            staged = archive.read_bytes()
        self._validate_staged_archive(worktree, staged, manifest['staged_tree']['entries'])
        manifest['staged_tree']['sha256'] = hashlib.sha256(staged).hexdigest()
        payloads['staged-tree.tar'] = staged
        payloads['manifest.json'] = (encoded(manifest) + '\n').encode()
        content = io.BytesIO()
        with tarfile.open(fileobj=content, mode='w') as archive:
            for name, raw in sorted(payloads.items()):
                member = tarfile.TarInfo(name)
                member.size = len(raw)
                archive.addfile(member, io.BytesIO(raw))
        atomic_write(target, content.getvalue(), self.config['file_mode'])
        descriptor = {'schema': 'poise-handoff-wip-1', 'path': str(target), 'digest': file_digest(target)}
        return descriptor
