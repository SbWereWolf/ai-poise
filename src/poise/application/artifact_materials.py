"""Select explicitly authorized physical material without altering logical identity."""
from ..modules.foundation.errors import PoiseError
from ..modules.artifact_factory.domain import RegisteredArtifactMaterial


class ArtifactMaterialResolver:
    def __init__(self, delivery):
        self.delivery = delivery

    def resolve(self, source_task_id, records):
        state = self.delivery.state(source_task_id)
        if state is None or not state.data['retirement_started']:
            return {r['id']: RegisteredArtifactMaterial.active(r) for r in records}
        confirmed = state.data['confirmed']
        outputs = state.data['agreement']['declaration']['outputs']
        result = {}
        for record in records:
            if record['id'] not in state.data['retired_artifacts']:
                result[record['id']] = RegisteredArtifactMaterial.active(record)
                continue
            eligible = [o for o in outputs if o['kind'] in ('file','customer','ignored_configuration')
                        and o['source']==record['path'] and o['digest']==record['digest']
                        and any(c['id']==o['id'] for c in confirmed)]
            if len(eligible)!=1:
                raise PoiseError('Retired artifact has no unique agreed permanent input')
            result[record['id']] = RegisteredArtifactMaterial.delivered(
                record, eligible[0]['destination'], state.data['agreement']['request_id'])
        return result
