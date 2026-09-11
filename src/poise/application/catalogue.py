"""Batch administration reuses GoalConfigCommands and the Task creation contract."""
from copy import deepcopy
from .goal_config import validate_batch_request
from ..modules.foundation.errors import DomainError
from ..modules.verification.domain import exact_keys
from ..modules.goal_config.domain import GoalTypeDefinition


class CatalogueCommands:
    def __init__(self,repository,editor,max_items):
        if type(max_items) is not int or max_items<=0:raise DomainError('Explicit catalogue item limit required')
        self.repository,self.editor,self.max_items=repository,editor,max_items

    def _batch(self,items):
        if not isinstance(items,list) or not items or len(items)>self.max_items:
            raise DomainError('Nonempty bounded catalogue batch required')

    def install(self,items):
        self._batch(items);prepared=[];goals=set()
        # Validate every request/template before publishing the first file.
        for item in items:
            exact_keys(item,{'goal_type','request_id','mode','expected_revision','template','changes'},'catalogue install item')
            request={'schema':'goal-config-batch-1',**deepcopy(item)}
            validate_batch_request(request,self.editor.max_changes)
            goal=item['goal_type']
            if not isinstance(goal,str) or goal in goals:raise DomainError('Distinct explicit goal types required')
            goals.add(goal)
            selection=self.repository.process_selection(goal)
            if item['template']!=selection and item['mode']=='create':
                raise DomainError('Select the exact registered independent process template')
            source=(self.repository.process_template(selection) if item['mode']=='create'
                    else self.repository.configured_process(goal))
            GoalTypeDefinition.build(goal,source,item['changes'])
            prepared.append(request)
        # Each file has its own durable receipt. The batch is NOT a filesystem transaction.
        results=[]
        for request in prepared:results.append(self.editor.apply_batch(request))
        return {'status':'installed','results':results,'count':len(results)}

    def tasks(self,items,processes,automatic_checks):
        self._batch(items);tasks=[];ids=set()
        for item in items:
            exact_keys(item,{'template','parameters'},'catalogue task item')
            blueprint=self.repository.task_blueprint(item['template'])
            if blueprint.goal_type not in processes:raise DomainError('Goal is not in the selected project')
            contract=blueprint.instantiate(item['parameters'],processes[blueprint.goal_type],automatic_checks)
            if contract['id'] in ids:raise DomainError('Duplicate task identity in batch')
            ids.add(contract['id']);tasks.append(contract)
        return {'status':'prepared','tasks':tasks,'count':len(tasks)}
