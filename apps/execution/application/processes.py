"""Existing structural process/edit use cases over explicit application-owned ports."""
import hashlib
import json
import sqlite3
from apps.execution.domain.process.definition import GoalTypeDefinition, canonical
from apps.execution.domain.process.errors import DomainError


class ProcessDefinitions:
    def __init__(self, files):
        self.files = files

    def current(self, goal):
        raw = self.files.read_current(goal)
        definition = GoalTypeDefinition.from_configuration(raw)
        if definition.data['goal_type'] != goal:
            raise DomainError('Selected process goal_type does not match its definition')
        return raw, definition

    def resolve(self, reference):
        _raw, definition = self.current(reference['goal_type'])
        if definition.revision != reference['revision']:
            raise DomainError('Selected process revision does not match current definition')
        return definition.data


class ProcessCommands:
    def __init__(self, unit_of_work, files):
        self.unit_of_work = unit_of_work
        self.files = files
        self.definitions = ProcessDefinitions(files)

    def execute(self, packet):
        value = packet['input']
        goal = value['goal_type']
        self.files.current(goal)
        with self.unit_of_work.open() as repository:
            if value['action'] == 'status':
                _raw, definition = self.definitions.current(goal)
                return {'schema':packet['schema'],'status':'read_only','revision':repository.revision(),
                        'goal_type':goal,'managed_revision':repository.head(goal),
                        'live_revision':definition.revision,'pending_request':repository.pending(goal)}
            request = value['request_id']
            digest = hashlib.sha256(canonical(packet).encode()).hexdigest()
            completed = repository.completed(request)
            if completed:
                if completed['digest'] != digest:
                    raise DomainError('request_reuse_conflict')
                return dict(json.loads(completed['response_json']),replayed=True)
            reserved = repository.reserved(request)
            if reserved:
                if reserved['request_digest'] != digest:
                    raise DomainError('request_reuse_conflict')
                candidate = json.loads(reserved['candidate'])
                response = dict(json.loads(reserved['receipt']),replayed=True)
            else:
                if value['expected_revision'] != repository.revision():
                    raise DomainError('revision_conflict')
                action = value['action']
                previous = repository.head(goal)
                if action == 'create':
                    if previous is not None or self.files.current(goal).exists():
                        raise DomainError('Process already exists')
                    source = value['source']
                    raw = (source['definition'] if source['kind'] == 'definition'
                           else self.files.resolve_template(source['template']['id']))
                    built = GoalTypeDefinition.build_configuration(goal,raw,value['changes'])
                else:
                    raw, current = self.definitions.current(goal)
                    if action == 'edit':
                        if value['expected_process_revision'] != current.revision:
                            raise DomainError('process revision_conflict')
                        built = GoalTypeDefinition.build_configuration(goal,raw,value['changes'])
                    else:
                        if value['expected_managed_revision'] != previous or value['expected_live_revision'] != current.revision:
                            raise DomainError('process reconcile revision_conflict')
                        built = current
                candidate = built.data
                process_revision = GoalTypeDefinition.from_configuration(candidate).revision
                response = {'schema':packet['schema'],'status':'published','revision':repository.revision()+1,
                            'replayed':False,'goal_type':goal,'previous_process_revision':previous,
                            'process_revision':process_revision,'changed':previous != process_revision}
                repository.reserve(request,digest,goal,previous,candidate,response)
            try:
                if value['action'] != 'reconcile':
                    self.files.publish_candidate(goal,candidate)
                repository.finish(request,digest,goal,response)
            except (OSError,sqlite3.Error):
                return {'schema':packet['schema'],'status':'pending','revision':repository.revision(),
                        'intent_id':request,'phase':'process_publication'}
            return response
