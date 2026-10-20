"""Read projection for immutable historical receipts at confirmed locations."""
from ..modules.artifact_factory.locations import relocate_path

def relocate_receipt(value,locations):
    """Only mechanical path fields. Never rewrite section text, argv or an argument."""
    if value is None:return None
    if isinstance(value,list):return [relocate_receipt(x,locations) for x in value]
    if not isinstance(value,dict):return value
    result={}
    for key,item in value.items():
        if key in ('path','worktree','directory','manifest','stdout','stderr','bundle_path','receipt_path','captured_path'):
            result[key]=relocate_path(item,locations)
        elif key in ('artifact_paths','preserved_artifacts'):
            result[key]=[relocate_path(x,locations) for x in item]
        elif key=='artifact_mapping':
            result[key]={relocate_path(k,locations):relocate_path(v,locations) for k,v in item.items()}
        elif key in ('sections','content','text','argv','environment','facts','assumptions','argument','conclusion'):
            result[key]=item
        else:result[key]=relocate_receipt(item,locations)
    return result
