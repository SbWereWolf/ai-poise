"""Batch input through one port; no project-specific commands or storage access."""
from ..modules.projects.ports import ProjectSetupPort, ProjectAvailabilityPort, ProjectPreflightPort
from ..modules.verification.domain import exact_keys
from ..modules.foundation.errors import DomainError, PoiseError


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
    def __init__(self, port: ProjectSetupPort, availability: ProjectAvailabilityPort | None = None,
                 preflight: ProjectPreflightPort | None = None):
        self.port = port
        self.availability = availability
        self.preflight = preflight

    def input_failure(self, raw, reason):
        from ..modules.projects.preflight import ProjectPreflightReport
        if self.preflight is None:
            raise DomainError('No read-only project preflight port configured')
        return ProjectPreflightReport(self.preflight.settings_path,
                                      self.preflight.request_context(raw)).input_failure(reason)

    def check(self, raw):
        from ..modules.actions.domain import CommitMessagePolicy
        from ..modules.projects.preflight import ProjectPreflightReport, ProjectPreflightRequest
        if self.preflight is None:
            raise DomainError('No read-only project preflight port configured')
        try:
            request = ProjectPreflightRequest.parse(raw)
        except PoiseError as exc:
            return self.input_failure(raw, str(exc))
        report = ProjectPreflightReport(self.preflight.settings_path,
                                        self.preflight.request_context(raw))
        component = 'configuration'
        try:
            observation = self.preflight.observe_configuration(request)
            report.context = observation.context
            if observation.rejection is not None:
                report.record(component, 'rejected', observation.rejection)
                return report.finish()
            cfg = observation.configuration
            report.record(component, 'passed')
            component = 'git'
            if request.probe_repository:
                self.preflight.probe_git(cfg)
                report.record(component, 'passed')
            component = 'commit_policy'
            if request.commit_message is not None:
                CommitMessagePolicy(cfg['git']['commit_pattern']).require(request.commit_message)
                report.record(component, 'passed')
            component = 'task_lookup'
            if request.task_id is not None:
                self.preflight.require_task(request.config_path, cfg, request.task_id)
                report.record(component, 'passed')
        except PoiseError as exc:
            report.record(component, 'rejected', str(exc))
        except Exception as exc:
            report.record(component, 'unknown', f'{type(exc).__name__}: {exc}')
        return report.finish()

    def apply(self,request):
        validate_request(request)
        return self.port.apply(request)

    def list(self):
        return self.port.list()

    def next(self, project: str | None = None) -> dict:
        if project is not None and (not isinstance(project, str) or not project):
            raise DomainError('An exact nonempty project ID is required')
        if self.availability is None:
            raise DomainError('No read-only project availability port configured')
        registry = self.port.list()
        projects = registry['projects']
        errors = list(registry['errors'])
        if project is not None:
            projects = [p for p in projects if p['project'] == project]
            errors = [e for e in errors if e['project'] == project]
            if not projects and not errors:
                errors.append({'project': project, 'status': 'unknown',
                               'reason': 'Project is not in the configured-project registry'})
        tasks = []
        for entry in projects:
            try:
                tasks.extend(self.availability.startable(entry))
            except PoiseError as exc:
                errors.append({**entry, 'status': 'unavailable', 'reason': str(exc)})
        return {'status': 'listed_with_errors' if errors else 'listed',
                'tasks': sorted(tasks, key=lambda t: (t['project'], t['task'])),
                'errors': sorted(errors, key=lambda e: e['project'])}

    def questionnaire(self,request):
        from ..modules.projects.domain import Survey
        validate_request(request)
        if request['edits']:raise DomainError('Questionnaire starts with explicit template, not hidden prefilled edits')
        return Survey(self.port.template(request['template']))
