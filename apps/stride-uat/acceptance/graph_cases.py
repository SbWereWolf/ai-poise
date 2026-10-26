"""Application-owned graph cases; generic UAT and Execution retain their owners."""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
import subprocess
from uuid import uuid4

class CaseFailure(Exception):
    """An observed application result contradicts the selected scenario."""


class ExecutionClient:
    def __init__(self, argv, config, observations, actor):
        self.argv = list(argv) + ['--config', str(config)]
        self.observations = Path(observations)
        self.observations.touch()
        self.actor = actor
        self.revision = 0

    def call(self, operation, value, allowed=(0,)):
        packet = {'schema': 'stride-execution/v1', 'operation': operation, 'input': value}
        completed = subprocess.run(self.argv, input=json.dumps(packet) + '\n',
                                   capture_output=True, text=True, check=False)
        record = {'argv': self.argv, 'input': packet, 'exit_code': completed.returncode,
                  'stdout': completed.stdout, 'stderr': completed.stderr}
        with self.observations.open('a') as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + '\n')
        if completed.stderr:
            raise RuntimeError('Execution transport returned stderr; see recorded call')
        response = json.loads(completed.stdout)
        if response.get('schema') != 'stride-execution/v1':
            raise RuntimeError('Execution returned a different public schema')
        if completed.returncode not in allowed:
            raise CaseFailure(f'{operation}: {response}')
        if 'revision' in response:
            self.revision = response['revision']
        return completed.returncode, response

    def query(self, task, kind='execution'):
        _, response = self.call('query', {'queries': [{'id': 'current', 'kind': kind, 'task_id': task}]})
        item = response['results'][0]
        return item['value'] if kind == 'execution' else item['result']

    def common(self, task):
        state = self.query(task)
        return {'request_id': str(uuid4()), 'expected_revision': self.revision,
                'task_id': task, 'expected_generation': state['generation'],
                'expected_contract_revision': state['contract_revision'], 'actor_id': self.actor}

    def lifecycle(self, task, action, **fields):
        _, response = self.call('lifecycle', {**self.common(task), 'action': action, **fields})
        return response

    def process(self, goal, action, **fields):
        value = {'action': action, 'goal_type': goal, **fields}
        if action != 'status':
            value.update(request_id=str(uuid4()), expected_revision=self.revision)
        _, response = self.call('process', value)
        return response


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def require(condition, reason):
    if not condition:
        raise CaseFailure(reason)


class ScenarioInstallation:
    def __init__(self, context, plan):
        self.context, self.plan = context, plan
        self.workspace = Path(context['workspace'])
        self.instance = self.workspace / plan['instance_directory']
        self.instance.mkdir()
        self.repository = self.instance / 'repository'
        self.repository.mkdir()
        self.setup_log = []
        settings = deepcopy(plan['execution_config'])
        settings['verification']['repository'] = str(self.repository)
        settings['verification']['output_root'] = str(self.instance / 'attempts')
        self.config = self.instance / 'execution.json'
        write(self.config, settings)
        self.settings = settings
        self.client = ExecutionClient(context['public_argv'], self.config,
                                      self.workspace / plan['observation_file'], plan['actor_id'])
        self.git('init', '-b', settings['verification']['base_ref'])
        self.git('-c', 'user.name=Stride UAT fixture', '-c', 'user.email=uat@example.invalid',
                 'commit', '--allow-empty', '-m', 'Initialize graph acceptance fixture')
        self.source = {'commit': self.git('rev-parse', 'HEAD').strip(),
                       'tree': self.git('rev-parse', 'HEAD^{tree}').strip()}
        write(self.workspace / 'setup.json', {'git': self.setup_log, 'source': self.source})

    def git(self, *args):
        argv = ['git', '-C', str(self.repository), *args]
        result = subprocess.run(argv, capture_output=True, text=True, check=False)
        self.setup_log.append({'argv': argv, 'exit_code': result.returncode,
                               'stdout': result.stdout, 'stderr': result.stderr})
        write(self.workspace / 'setup.json', {'git': self.setup_log})
        if result.returncode:
            raise RuntimeError('Own fixture Git preparation failed; see setup.json')
        return result.stdout

    def publish(self, graph, *, template=False):
        if template:
            raw = self.settings['process']['templates'][self.plan['template_id']]['path']
            state = self.instance / self.settings['paths']['state']
            path = Path(raw) if Path(raw).is_absolute() else state / raw
            write(path, graph)
            source = {'kind': 'template', 'template': {'id': self.plan['template_id']}}
        else:
            source = {'kind': 'definition', 'definition': graph}
        response = self.client.process(graph['goal_type'], 'create', source=source, changes=[])
        require(response['status'] == 'published', 'Selected graph was not published')
        return response

    def setup(self, task, process):
        contract = deepcopy(self.plan['contracts'][task])
        contract['process_ref'] = {'goal_type': process['goal_type'], 'revision': process['process_revision']}
        _, result = self.client.call('apply', {
            'request_id': str(uuid4()), 'expected_revision': self.client.revision,
            'operations': [{'id': 'setup-' + task, 'kind': 'setup_execution', 'contract': contract}]})
        require(result['status'] == 'applied', 'Selected execution was not set up')
        self.client.lifecycle(task, 'freeze', requirements_snapshot=self.plan['snapshots'][task])
        self.client.lifecycle(task, 'enter', entry_tree=self.source['tree'])
        return contract

    def gate(self, task, graph, stage):
        state = self.client.query(task)
        context, candidate = str(uuid4()), str(uuid4())
        common = {'task_id': task, 'generation': state['generation'], 'context_id': context}
        self.client.call('gate', {**common, 'action': 'declare', 'request_id': str(uuid4()),
            'expected_revision': self.client.revision, 'contract_revision': state['contract_revision'],
            'requirement_ids': [], 'task_content': {'sections': [], 'routes': [], 'requirements': []},
            'stage_contracts': [{'stage_id': item['id'], 'allowed_paths': item['allowed_paths'],
                                 'entry_requirements': [], 'exit_requirements': [],
                                 'criteria': {'requirements': [], 'definition_of_ready': [],
                                              'definition_of_done': []}} for item in graph['stages']]})
        _, accepted = self.client.call('gate', {**common, 'action': 'evaluate',
            'request_id': str(uuid4()), 'expected_revision': self.client.revision,
            'candidate_id': candidate, 'stage_id': stage['id'], 'iteration': state['iteration'],
            'sections': {name: 'Проверен выбранный этап «' + stage['id'] + '».' for name in stage['sections']},
            'trace': {}, 'files': []})
        require(accepted['accepted'], 'Actual selected stage Gate did not accept its observation')
        return context, candidate

    def execute_route(self, task, graph, contract, expected):
        for index, stage_id in enumerate(expected):
            state = self.client.query(task)
            require(state['current_stage'] == stage_id,
                    f'Actual current_stage {state["current_stage"]!r} differs from selected {stage_id!r}')
            stage = next(item for item in graph['stages'] if item['id'] == stage_id)
            target = expected[index + 1] if index + 1 < len(expected) else None
            outcomes = [key for key, value in stage['transitions'].items() if value == target]
            require(len(outcomes) == 1, 'Scenario must uniquely select its declared transition outcome')
            context, candidate = self.gate(task, graph, stage)
            attempt_id = str(uuid4())
            methods = next(item['methods'] for item in contract['stage_expectations']
                           if item['stage_id'] == stage_id)
            _, started = self.client.call('attempt', {**self.client.common(task), 'action': 'begin',
                'attempt_id': attempt_id, 'context_id': context, 'candidate_id': candidate,
                'source': self.source, 'method_ids': methods})
            for run in started['attempt']['runs']:
                self.client.call('attempt', {**self.client.common(task), 'action': 'run',
                                             'attempt_id': attempt_id, 'run_id': run['run_id']})
            _, finished = self.client.call('attempt', {**self.client.common(task), 'action': 'finish',
                'attempt_id': attempt_id, 'evidence_work': {'phase': 'prepare', 'arguments': [], 'decisions': []}})
            self.client.lifecycle(task, 'present', context_id=context, candidate_id=candidate)
            _, advanced = self.client.call('advance', {**self.client.common(task), 'action': 'next',
                'source': self.source, 'entry_tree': self.source['tree'], 'proof': {
                    'context_id': context, 'candidate_id': candidate, 'outcome': outcomes[0],
                    'evidence_id': finished['attempt']['evidence_id']}})
            require(advanced['status'] == ('route_end' if target is None else 'advanced'),
                    'Public advance did not perform the declared transition')
            actual = self.client.query(task)
            require(actual['current_stage'] == (stage_id if target is None else target),
                    'Public next-stage observation differs from the declared transition')
        observed = self.client.query(task, 'progression')
        require([item['stage_id'] for item in observed['accepted_visits']] == expected,
                'Actual accepted route differs from the selected scenario')


class GraphCase:
    def __init__(self, context, plan, graph):
        self.context, self.plan, self.graph = context, plan, graph
        self.installation = ScenarioInstallation(context, plan)
        self.checks = []

    def check(self, name, status):
        self.checks.append({'id': name, 'status': status,
                            'evidence_paths': [self.plan['observation_file'], 'setup.json']})

    def execute(self, scenario):
        installation, graph = self.installation, self.graph
        client = installation.client
        if scenario == 'invalid':
            baseline = installation.publish(self.plan['baseline'])
            before = client.process(baseline['goal_type'], 'status')
            code, refusal = client.call('process', {
                'action': 'edit', 'goal_type': graph['goal_type'], 'request_id': str(uuid4()),
                'expected_revision': client.revision,
                'expected_process_revision': baseline['process_revision'],
                'changes': [{'op': 'put_stage', 'value': item} for item in graph['stages']]}, allowed=(0, 2))
            require(code == 2 and refusal['status'] == 'rejected' and
                    self.plan['expected_rejection_token'] in refusal['reason'],
                    'Selected invalid graph did not receive its concrete structural refusal')
            after = client.process(baseline['goal_type'], 'status')
            require(after == before, 'Invalid graph changed the prior valid public process')
            self.check('prior-valid-state', 'PASS')
            self.check('graph-validity', 'FAIL')
            return 'FAIL', 'Selected graph was rejected by Execution'
        if scenario == 'edited':
            before = installation.publish(self.plan['baseline'])
            old_task, new_task = self.plan['expected_routes']
            old_contract = installation.setup(old_task, before)
            selected = client.process(graph['goal_type'], 'edit',
                                      expected_process_revision=before['process_revision'],
                                      changes=self.plan['changes'])
            require(selected['status'] == 'published' and selected['changed'],
                    'Named graph edit was not published')
            new_contract = installation.setup(new_task, selected)
            installation.execute_route(old_task, self.plan['baseline'], old_contract,
                                       self.plan['expected_routes'][old_task])
            self.check('prior-frozen-route', 'PASS')
            installation.execute_route(new_task, graph, new_contract,
                                       self.plan['expected_routes'][new_task])
        else:
            selected = installation.publish(graph, template=scenario == 'template')
            task, = self.plan['expected_routes']
            contract = installation.setup(task, selected)
            installation.execute_route(task, graph, contract, self.plan['expected_routes'][task])
        self.check('selected-public-route', 'PASS')
        return 'PASS', None

    def run(self, scenario):
        try:
            status, reason = self.execute(scenario)
        except CaseFailure as exc:
            self.check('observed-state', 'FAIL')
            status, reason = 'FAIL', str(exc)
        except (OSError, ValueError, KeyError, RuntimeError) as exc:
            self.check('case-preparation', 'BLOCKED')
            status, reason = 'BLOCKED', str(exc)
        return {'schema': 'stride-uat-case-result/v1',
                **{key: self.context[key] for key in ['run_id', 'step_id', 'application_id']},
                'status': status, 'reason': reason,
                'graph': {'id': self.context['graph']['id'], 'sha256': self.context['graph']['sha256'],
                          'checks': self.checks}}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--context', required=True)
    parser.add_argument('--scenario', choices=['full', 'template', 'edited', 'invalid'], required=True)
    parser.add_argument('--plan', required=True)
    args = parser.parse_args()
    context = read(args.context)
    try:
        case = GraphCase(context, read(args.plan), read(context['graph']['path']))
        result = case.run(args.scenario)
    except (OSError, ValueError, KeyError, RuntimeError) as exc:
        write(Path(context['workspace']) / 'preparation.json', {'reason': str(exc)})
        result = {'schema': 'stride-uat-case-result/v1',
                  **{key: context[key] for key in ['run_id', 'step_id', 'application_id']},
                  'status': 'BLOCKED', 'reason': str(exc),
                  'graph': {'id': context['graph']['id'], 'sha256': context['graph']['sha256'],
                            'checks': [{'id': 'case-preparation', 'status': 'BLOCKED',
                                        'evidence_paths': ['preparation.json']}]}}
    write(context['result_path'], result)
    return {'PASS': 0, 'FAIL': 1, 'BLOCKED': 3}[result['status']]


if __name__ == '__main__':
    raise SystemExit(main())
