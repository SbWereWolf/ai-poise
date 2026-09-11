"""Files contain independent explicit templates; managed packs go through the editor."""
from pathlib import Path
from ..common import descendant,exact_keys
from ..modules.foundation.errors import PoiseError,VersionConflict
from ..modules.goal_config.domain import fingerprint
from ..modules.catalogue.domain import TaskBlueprint
from .goal_config import EditorSettings,FileTemplates,read_document


class FileCatalogue:
    def __init__(self,path):
        self.path=Path(path).resolve();self.raw=read_document(self.path)
        exact_keys(self.raw,{'schema','editor','task_templates','max_items'},'catalogue settings')
        if self.raw['schema']!='process-catalogue-1':raise PoiseError('Unsupported catalogue schema')
        self.editor=EditorSettings((self.path.parent/self.raw['editor']).resolve())
        self.root=self.editor.root
        if type(self.raw['max_items']) is not int or self.raw['max_items']<=0:raise PoiseError('Explicit max_items required')
        if not isinstance(self.raw['task_templates'],dict):raise PoiseError('Explicit task templates required')
        for ident,entry in self.raw['task_templates'].items():
            exact_keys(entry,{'path','version','digest','goal_type'},'task template entry')
            if any(not isinstance(v,str) or not v for v in entry.values()):raise PoiseError('Incomplete template selection')
            descendant(self.root,entry['path'])

    def process_selection(self,goal_type):
        # IDs are explicit, not inferred from filename or goal prefix.
        matches=[{'id':ident,'version':v['version'],'digest':v['digest']}
                 for ident,v in self.editor.raw['templates'].items()
                 if read_document(descendant(self.root,v['path'])).get('goal_type')==goal_type]
        if len(matches)!=1:raise PoiseError('Exactly one independent template must be explicitly registered for goal')
        self.editor.target(goal_type)
        return matches[0]

    def process_template(self,selection):return FileTemplates(self.editor).resolve(selection)

    def configured_process(self,goal_type):return read_document(self.editor.target(goal_type))

    def task_blueprint(self,selection):
        exact_keys(selection,{'id','version','digest'},'task template selection')
        if selection['id'] not in self.raw['task_templates']:raise PoiseError('Unregistered task template')
        entry=self.raw['task_templates'][selection['id']]
        if (entry['version'],entry['digest'])!=(selection['version'],selection['digest']):
            raise VersionConflict('Task template revision mismatch')
        data=read_document(descendant(self.root,entry['path']))
        if fingerprint(data)!=entry['digest']:raise VersionConflict('Task template content changed')
        template=TaskBlueprint.parse(data)
        if template.goal_type!=entry['goal_type']:raise PoiseError('Task template identity mismatch')
        return template
