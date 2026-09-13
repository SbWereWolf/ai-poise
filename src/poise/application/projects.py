"""Batch input through one port; no project-specific commands or storage access."""
from ..modules.projects.ports import ProjectSetupPort
from ..modules.verification.domain import exact_keys
from ..modules.foundation.errors import DomainError


def validate_request(request):
    exact_keys(request,{'schema','request_id','destination','template','edits','probe_repository'},'project setup')
    if request['schema']!='project-setup-1':raise DomainError('Unsupported project request schema; no migration')
    for key in ('request_id','destination'):
        if not isinstance(request[key],str) or not request[key].strip():raise DomainError(f'Explicit {key} required')
    exact_keys(request['template'],{'id','version','digest'},'project template selection')
    if any(not isinstance(v,str) or not v for v in request['template'].values()):
        raise DomainError('Template selection must be explicit')
    if type(request['probe_repository']) is not bool:raise DomainError('Explicit probe_repository required')
    if not isinstance(request['edits'],list):raise DomainError('Expected edit batch')


class ProjectCommands:
    def __init__(self,port:ProjectSetupPort):self.port=port

    def apply(self,request):
        validate_request(request)
        return self.port.apply(request)

    def list(self):
        return self.port.list()

    def questionnaire(self,request):
        from ..modules.projects.domain import Survey
        validate_request(request)
        if request['edits']:raise DomainError('Questionnaire starts with explicit template, not hidden prefilled edits')
        return Survey(self.port.template(request['template']))
