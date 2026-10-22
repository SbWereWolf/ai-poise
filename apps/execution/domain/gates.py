"""Existing content rules composed into an own, pure result acceptance decision."""
from dataclasses import dataclass
import json

from .process.content import ContentState, SectionBook, SectionRule, SectionValue
from .process.content_policy import ContentPolicy, ContentSnapshot, TraceValue, count_artifacts
from .process.definition import GoalTypeDefinition
from .process.errors import DomainError
from .process.stage_contracts import TaskStageContracts
from .process.workflow import RouteDefinition


@dataclass(frozen=True)
class GateContext:
    process: dict
    policy: ContentPolicy
    contracts: TaskStageContracts

    @classmethod
    def parse(cls, process, contract, declaration):
        GoalTypeDefinition.parse(process)
        stages = tuple(stage['id'] for stage in process['stages'])
        methods = contract['methods']
        if any(not isinstance(m, dict) or not isinstance(m.get('id'), str)
               or not m['id'].strip() for m in methods):
            raise DomainError('Saved methods require explicit nonempty method IDs')
        policy = ContentPolicy.from_layers(
            process['content_contract'], declaration['task_content'], stages,
            tuple(declaration['requirement_ids']), tuple(m['id'] for m in methods),
            tuple(sorted({name for stage in process['stages'] for name in stage['sections']})))
        contracts = TaskStageContracts.parse(
            declaration['stage_contracts'], RouteDefinition.from_workflow(process), policy)
        return cls(process, policy, contracts)

    def stage(self, stage_id):
        self.contracts.stage(stage_id)
        return next(stage for stage in self.process['stages'] if stage['id'] == stage_id)


def snapshot_from_value(value):
    if value is None:
        return ContentSnapshot((), ())
    return ContentSnapshot(
        tuple(SectionValue(v['name'], v['content'], ContentState(v['state']))
              for v in value['sections']),
        tuple(TraceValue(route, point, json.dumps(data, ensure_ascii=False, sort_keys=True))
              for route, points in value['trace'].items() for point, data in points.items()))


def snapshot_value(snapshot):
    return {'sections': [{'name': value.name, 'content': value.content, 'state': value.state.value}
                         for value in sorted(snapshot.sections, key=lambda value: value.name)],
            'trace': snapshot.trace_view()}


def prepare_candidate(context, previous_snapshot, stage_id, sections, trace):
    stage = context.stage(stage_id)
    standard = {name: text for name, text in sections.items() if name in stage['sections']}
    extra = {name: text for name, text in sections.items() if name not in stage['sections']}
    rules = tuple(SectionRule(name, template, stage['normalization'], name in stage['required_sections'])
                  for name, template in stage['sections'].items())
    prepared = SectionBook(rules).prepare(standard)
    return context.policy.apply(previous_snapshot.with_sections(prepared), stage_id, extra, trace)


def assess_candidate(context, stage_id, snapshot, facts, file_errors, preparation_error):
    if preparation_error is not None:
        return {'prepared': False, 'preparation_error': preparation_error,
                'file_errors': [], 'artifact_checks': [], 'pre': None, 'post': None, 'passed': False}
    stage = context.stage(stage_id)
    checks = []
    for rule in stage['artifact_requirements']:
        actual = count_artifacts(facts, rule['scope'], rule['pattern'])
        checks.append({**rule, 'actual': actual,
                       'passed': rule['minimum'] <= actual <= rule['maximum']})
    bindings = context.contracts.stage(stage_id)
    pre = context.policy.evaluate(stage_id, 'pre', snapshot, facts, bindings.entry_requirements).to_dict()
    post = context.policy.evaluate(stage_id, 'post', snapshot, facts, bindings.exit_requirements).to_dict()
    return {'prepared': True, 'preparation_error': None, 'file_errors': file_errors,
            'artifact_checks': checks, 'pre': pre, 'post': post,
            'passed': not file_errors and all(check['passed'] for check in checks)
                      and pre['passed'] and post['passed']}
