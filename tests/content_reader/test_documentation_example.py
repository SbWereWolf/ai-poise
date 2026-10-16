"""The actual shipped operator example becomes runnable at documentation."""
import json

from tests.content_reader.helpers import SOURCE, Scenario


def test_shipped_reader_policy_example_is_executable(project, tmp_path):
    s = Scenario(project, tmp_path)
    s.prove()
    shipped = SOURCE.parent / 'config/examples/project-file-reading.example.json'
    assert shipped.is_file(), 'Documentation producer must ship its actual policy'
    s.data = s.fill(json.loads(shipped.read_text(encoding='utf-8')))
    s.save_policy(); s.ready()
    assert s.read()['items'][0]['status'] == 'content'
    assert s.read()['items'][0]['status'] == 'unchanged'
    s.reset()
    assert s.read()['items'][0]['classification'] == 'new_context_generation'
    s.reset('example-text', operation='clear')
    code, out, err = s.raw(s.request(), 'text')
    assert code == 0 and err == '' and 'βeta\nlast' in out
