"""Coordinate one admitted relocation through the existing material owners."""
from ..modules.foundation.errors import PoiseError


class ProjectStateRelocation:
    def __init__(self, effects):
        self.effects = effects

    def run(self):
        effects = self.effects
        effects.admit()
        effects.copy()
        effects.publish_materials()
        effects.publish_hooks()
        effects.publish_configuration()
        effects.confirm_consumers()
        effects.retire_source()
        return effects.result()
