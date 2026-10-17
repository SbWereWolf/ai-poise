"""Shared session operations; template publication is distinct from admission."""
from ..modules.document_intake.ports import TemplateStore


class IntakeSessions:
    def __init__(self, configuration, resources, template: TemplateStore):
        self.configuration, self.resources, self.template = configuration, resources, template

    def prepare_template(self):
        return self.template.prepare()

    def context(self):
        r = self.resources
        generation, summary = r.registry.context(r.scope)
        return dict(caller_key=r.scope.caller_key, checkout_key=r.scope.checkout_key,
                    generation=generation, admission_mode='native_hook', native_episode=r.scope.episode), summary
