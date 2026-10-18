"""Artifact identity follows its logical owner rather than its physical directory."""

def test_artifact_id_does_not_depend_on_physical_root(tmp_path):
    from poise.artifacts import inspect_paths
    a = tmp_path / 'a'
    b = tmp_path / 'b'
    a.mkdir()
    b.mkdir()
    (a / 'note.txt').write_text('same')
    (b / 'note.txt').write_text('same')
    one = inspect_paths([str(a / 'note.txt')], {'task': a}, {'task': 'T'})
    two = inspect_paths([str(b / 'note.txt')], {'task': b}, {'task': 'T'})
    assert one[0]['id'] == two[0]['id']
