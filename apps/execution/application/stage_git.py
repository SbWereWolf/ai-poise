"""Current Stage Git admission, own observations and protected commit use cases."""
import json
import re

from apps.execution.application.gates import internal_record_id
from apps.execution.domain.records import ExecutionRecord, RecordsError
from apps.execution.domain.process.errors import PoiseError
from apps.execution.errors import ExecutionError
from apps.execution.gate_protocol import commit_input


class StageGit:
    def __init__(self, unit_of_work, contexts, settings, config, workspace_factory):
        self.unit_of_work = unit_of_work
        self.contexts = contexts
        self.settings = settings
        self.config = config
        self.workspace_factory = workspace_factory

    def _context(self, repository, value):
        context, contract, revision = self.contexts.context(repository, value)
        policy = context.file_policy
        if policy is None:
            raise RecordsError('Declare file_policy before Stage Git operations')
        key = policy.root_key(value['root'])
        if key not in policy.roots:
            raise RecordsError('Select an existing declared file_policy root')
        return context, contract, revision, policy.roots[key]

    def _workspace(self, root):
        if self.settings is None:
            raise ExecutionError('Configure explicit git.seconds and git.observations before Stage Git operations')
        return self.workspace_factory(root, self.settings['seconds'], self.settings['observations'])

    @staticmethod
    def _admission(context, value, observation):
        decisions = context.file_policy.decide_diff(
            value['stage_id'], value['root'], observation['stage_paths'], observation['commit_paths'])
        return {**observation, 'root': value['root'],
                'role': context.stage(value['stage_id'])['role'],
                'decisions': decisions, 'allowed': all(item['allowed'] for item in decisions)}

    @staticmethod
    def _replay(repository, value, digest):
        prior = repository.get_request(value['request_id'])
        if prior is None:
            return None
        if prior['digest'] != digest:
            raise RecordsError('Stage Git request has different input; use the original packet or a new request_id')
        return dict(json.loads(prior['response_json']), replayed=True)

    @staticmethod
    def _revision(repository, value):
        revision = repository.get_revision()
        if revision != value['expected_revision']:
            raise RecordsError(f'Stage Git revision conflict: expected {value["expected_revision"]}, actual {revision}')
        return revision

    def diff(self, value):
        digest = self.contexts.request_digest('files', value)
        with self.unit_of_work(write=False) as repository:
            replay = self._replay(repository, value, digest)
            if replay is not None:
                return replay
            self._revision(repository, value)
            context, contract, contract_revision, root = self._context(repository, value)
        observed = self._workspace(root).observe(value['baseline_ref'])
        admission = self._admission(context, value, observed)
        with self.unit_of_work(write=True) as repository:
            replay = self._replay(repository, value, digest)
            if replay is not None:
                return replay
            revision = self._revision(repository, value)
            data = {key: value[key] for key in ('context_id', 'request_id', 'stage_id', 'baseline_ref')}
            data.update(admission, contract_revision=contract_revision,
                        process_ref=contract['process_ref'], workspace_id=context.file_policy.workspace_id)
            record = ExecutionRecord('file_diff_decision',
                internal_record_id('file_diff_decision', value['generation'], value['context_id'], value['request_id']),
                value['task_id'], value['generation'], 1, data)
            repository.insert(record)
            response = {'schema': 'stride-execution/v1', 'status': 'diff_checked',
                        'revision': revision + 1, 'replayed': False,
                        **{key: value[key] for key in ('task_id', 'generation', 'context_id', 'stage_id')},
                        **admission, 'result_id': record.record_id}
            repository.set_revision(revision + 1)
            repository.save_request(value['request_id'], digest, response)
            return response

    def prepare_stage_commit(self, value):
        try:
            commit_input(value, self.config)
            for key in ('author_name', 'author_email', 'message', 'commit_pattern'):
                if type(value[key]) is not str or not value[key].strip():
                    raise ExecutionError(f'Provide an explicit nonempty {key}')
            try:
                matches = re.fullmatch(value['commit_pattern'], value['message'])
            except re.error as error:
                raise ExecutionError('Provide a valid commit_pattern: ' + str(error)) from error
            if matches is None:
                raise ExecutionError('Commit message does not match the explicit commit_pattern')
            with self.unit_of_work(write=False) as repository:
                context, _, _, root = self._context(repository, value)
            workspace = self._workspace(root)
            admission = self._admission(context, value, workspace.observe(value['baseline_ref']))
            before = {'status': 'commit_denied', 'created': False,
                      'commit': admission['head_commit'], 'tree': admission['head_tree'],
                      'admission': admission}
            if not admission['allowed']:
                return before
            if admission['candidate_tree'] != value['candidate_tree']:
                raise ExecutionError('Working candidate changed; observe and select the intended candidate again')
            if not admission['commit_paths']:
                return dict(before, status='committed')
            if admission['head_commit'] != value['expected_head']:
                raise ExecutionError('HEAD changed; observe and select the intended HEAD again')
            commit, tree = workspace.commit(value['candidate_tree'], value['message'], value)
            return dict(before, status='committed', created=True, commit=commit, tree=tree)
        except (PoiseError, RecordsError) as error:
            raise ExecutionError(str(error)) from error
