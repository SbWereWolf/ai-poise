"""Pure newline-preserving text delta, without Task content selection."""
from difflib import SequenceMatcher


def line_delta(before,after):
    a,b=before.splitlines(keepends=True),after.splitlines(keepends=True)
    added,removed=[],[]
    for operation,i,j,k,l in SequenceMatcher(None,a,b,autojunk=False).get_opcodes():
        if operation in ('replace','delete'):removed.extend(a[i:j])
        if operation in ('replace','insert'):added.extend(b[k:l])
    add,remove=''.join(added),''.join(removed)
    return {'added_lines':len(added),'removed_lines':len(removed),'added_bytes':len(add.encode()),
            'removed_bytes':len(remove.encode()),'added_text':add,'removed_text':remove}
