"""Configured material observations and confined effects, not Task lifecycle writes."""
import json
import os
import shutil
from pathlib import Path
from .file_publication import BinaryFilePublisher
from .locking import exclusive_lock
from .task_paths import task_root
from ..common import PoiseError, descendant
from ..modules.task_cleanup.domain import CleanupRun


class RuntimeTaskDelivery:
    def __init__(self, runtime):
        self.h = runtime

    def locked(self):
        h = self.h
        return exclusive_lock(descendant(h.state, h.cfg['batch']['artifact_lock']),
                              h.cfg['limits']['lock_seconds'], h.cfg['limits']['lock_poll_seconds'])

    def admission(self, task_id, actor):
        record = self.h.task_queries.record(task_id)
        if record is None:
            raise PoiseError('Task is not registered')
        if record['claimed_by'] not in (None, actor):
            raise PoiseError('Task belongs to another session owner')
        return record

    def _root(self, record):
        return task_root(self.h.state, self.h.paths, record['id'], record['sprint_id'])

    @staticmethod
    def root_identity(root):
        BinaryFilePublisher.without_links(root, 'Task root')
        if not root.is_dir():
            raise PoiseError('Task root is not a directory')
        info = root.lstat()
        return [info.st_dev, info.st_ino]

    def prepare(self, record, args):
        root = self._root(record)
        # A never-started draft may have no material directory yet.
        if not os.path.lexists(root) and record['status'] == 'newborn':
            root.mkdir(parents=True, exist_ok=True)
        root_identity = self.root_identity(root)
        for output in args['declaration']['outputs']:
            if output['kind'] == 'git':
                repository = Path(output['repository'])
                BinaryFilePublisher.without_links(repository, 'Git repository')
                if repository.resolve() != Path(self.h.cfg['git']['repository']).resolve():
                    raise PoiseError('Git delivery repository does not match configured owner')
                self.h._git(repository, 'cat-file', '-e', output['commit'] + '^{commit}')
                self.h._git(repository, 'check-ref-format', output['target_ref'])
                continue
            source, destination = Path(output['source']), Path(output['destination'])
            if not source.is_relative_to(root) or source == root:
                raise PoiseError('Delivery source leaves Task owner')
            BinaryFilePublisher.without_links(source, 'Task source')
            if BinaryFilePublisher.read(source, 'Task source') != output['digest']:
                raise PoiseError('Delivery source digest changed')
            # Reject protected aliases before storing any agreement. Wrong
            # destination types are operational blockers, not loss of intent.
            cursor = Path(destination.anchor)
            for part in destination.parts[1:]:
                cursor /= part
                if cursor.is_symlink():
                    raise PoiseError('Delivery destination contains a symlink')
            protected = [self.h.state, self.h.runtime, self.h.config_path]
            if any(destination == Path(p) or destination.is_relative_to(Path(p)) for p in protected):
                raise PoiseError('Delivery destination is protected owner state')
            if output['kind'] == 'customer':
                ack = Path(output['acknowledgement']['path'])
                BinaryFilePublisher.without_links(ack, 'Acknowledgement')
                if ack.is_relative_to(self.h.state):
                    raise PoiseError('Acknowledgement must be outside Task state')
        epoch = len(record.get('restart_history', []))
        return str(root), root_identity, epoch

    def ensure_resources(self, record, state):
        pending = record.get('pending')
        if record['status'] == 'cancelled' and isinstance(pending, dict) and pending.get('kind') == 'task_cleanup':
            run = CleanupRun.restore(pending)
            if not run.complete and run.intent.commit_disposition is not None:
                self.h.cleanup_tools.apply(run.intent.to_dict(), internal=True)
            return
        if (record['status'] != 'completed' or record.get('duplicate_reuse') is None
                or record.get('pending') is not None or state.data['retirement_started']):
            return
        repository=Path(self.h.cfg['git']['repository'])
        commit=record['last_report']['commit']
        try:
            self.h._git(repository,'merge-base','--is-ancestor',commit,self.h.cfg['git']['base_ref'])
        except PoiseError:
            return
        target=self.h._git(repository,'rev-parse',self.h.cfg['git']['base_ref'])
        self.h.cleanup_tools.apply({'task_id':record['id'],'request_id':'reuse-delivered-resources',
            'authorization':state.data['agreement']['authorization'],
            'commit_disposition':{'kind':'integrated','expected_commit':commit,
                'target_commit':target,'integration_request_id':record['duplicate_reuse']['candidate']['request_id']}},internal=True)

    def preparing_consumer(self, task_id):
        return self.h.transfer_tools.port.repo.preparing_consumers(task_id)

    def consumer_blocker(self, record):
        pending = record.get('pending')
        if isinstance(pending, dict) and pending.get('kind') == 'check_attempt':
            return 'checks_pending'
        with self.h.store.unit_of_work() as uow:
            if any(d['state'] != 'finalized' for d in uow.artifacts.drafts(record['id']).values()):
                return 'artifact_draft_open'
            if uow.handoffs.latest(record['id']) is not None:
                return 'handoff_pending'
            if uow.tasks.pending_artifact_consumers(record['id']):
                return 'artifact_consumer_pending'
        if self.h.transfer_tools.port.repo.pending_consumers(record['id']):
            return 'transfer_pending'
        return None

    def blocker(self, record, state):
        reason = self.consumer_blocker(record)
        if reason:
            return reason
        pending = record.get('pending')
        if record['status'] not in ('completed', 'cancelled'):
            return 'task_not_terminal'
        if isinstance(pending, dict):
            if pending.get('kind') == 'task_cleanup':
                if not CleanupRun.restore(pending).complete:
                    return 'resource_cleanup_pending'
            elif pending.get('kind') == 'result_integration':
                from ..modules.result_integration.domain import IntegrationRun
                if not IntegrationRun.restore(pending).complete:
                    return 'integration_required'
            else:
                return 'external_operation_pending'
        if record.get('worktree') and Path(record['worktree']).exists():
            return 'integration_required' if record['status']=='completed' else 'resource_cleanup_pending'
        return None

    def validate_sources(self, state):
        root = Path(state.data['root'])
        if self.root_identity(root) != state.data['root_identity']:
            raise PoiseError('Task root owner identity changed')
        for output in state.data['agreement']['declaration']['outputs']:
            if output['kind'] == 'git':
                continue
            try:
                if BinaryFilePublisher.read(Path(output['source']), 'Task source') != output['digest']:
                    return 'source_integrity_failed'
            except (OSError, PoiseError):
                return 'source_integrity_failed'
        return None

    def confirm_output(self, output):
        kind = output['kind']
        if kind == 'git':
            try:
                self.h._git(Path(output['repository']), 'merge-base', '--is-ancestor', output['commit'], output['target_ref'])
            except PoiseError:
                return None, 'integration_required'
            return dict(output), None
        source, target = Path(output['source']), Path(output['destination'])
        if kind == 'ignored_configuration':
            repository = Path(self.h.cfg['git']['repository'])
            if not target.is_relative_to(repository):
                return None, 'destination_unavailable'
            name = str(target.relative_to(repository))
            try:
                self.h._git(repository, 'check-ignore', '--', name)
                if self.h._git(repository, 'ls-files', '--', name):
                    return None, 'destination_unavailable'
            except PoiseError:
                return None, 'destination_unavailable'
        if kind == 'customer':
            ack = output['acknowledgement']
            if not os.path.lexists(ack['path']):
                return None, 'customer_acknowledgement_required'
            try:
                BinaryFilePublisher.without_links(Path(ack['path']), 'Acknowledgement')
                received = json.loads(Path(ack['path']).read_text(encoding='utf-8'))
                if received != {'receiver': ack['receiver'], 'digest': output['digest'], 'received_path': str(target)}:
                    return None, 'customer_acknowledgement_invalid'
                if BinaryFilePublisher.read(target, 'Received file') != output['digest']:
                    return None, 'customer_acknowledgement_invalid'
            except (OSError, UnicodeError, ValueError, PoiseError):
                return None, 'customer_acknowledgement_invalid'
        else:
            try:
                BinaryFilePublisher.without_links(target, 'Delivery destination')
                if target.exists() and not target.is_file():
                    return None, 'destination_unavailable'
                if target.exists() and BinaryFilePublisher.read(target, 'Delivery destination') != output['digest']:
                    return None, 'destination_conflict'
                BinaryFilePublisher.publish(source, target, output['digest'], self.h.cfg['batch']['file_mode'])
            except NotADirectoryError:
                return None, 'destination_unavailable'
            except PoiseError as exc:
                if 'parent is not a directory' in str(exc):
                    return None, 'destination_unavailable'
                raise
        return {k: v for k, v in output.items() if k != 'source'}, None

    def retiring_artifacts(self, state):
        root = Path(state.data['root'])
        return [r['id'] for r in self.h.store.artifact_records(state.data['task'])
                if Path(r['path']).is_relative_to(root)]

    def remove_owned_root(self, state):
        root = Path(state.data['root'])
        self.h.result_views.finish()
        BinaryFilePublisher.without_links(root.parent, 'Task root parent')
        if os.path.lexists(root):
            if self.root_identity(root) != state.data['root_identity']:
                raise PoiseError('Task root owner identity changed')
            if not shutil.rmtree.avoids_symlink_attacks:
                raise PoiseError('Confined Task removal unavailable')
            def removal_error(function, path, error):
                # shutil may attach a filename to an errno-less OSError and
                # obscure its original message. Preserve the actual failure.
                raise PoiseError(f'Task removal failed at {path}: {error.args}') from error
            shutil.rmtree(root, onexc=removal_error)
            BinaryFilePublisher.sync_parent(root)
        if os.path.lexists(root):
            raise PoiseError('Task root removal incomplete')
