"""A compact complete terminal result stays visible at the CLI output boundary."""
import io
import json
from pathlib import Path

from conftest import WorkPoise
from poise.interfaces.work import write_result
from result_integration.helpers import prepare_completed_task, source_change


def test_compact_terminal_result_keeps_all_fields_in_visible_output(project):
    prepare_completed_task(project, source_change)
    runtime = WorkPoise(project['config_path'], 'compact-terminal-reader')
    runtime.cfg['limits']['output_chars'] = 2000
    fixture = Path(__file__).parent / 'fixtures/compact_terminal_response.json'
    payload = json.loads(fixture.read_text())
    output = io.StringIO()
    write_result(runtime, payload, output)
    raw = output.getvalue()
    brief = json.loads(raw)
    full = json.loads(Path(brief['response_path']).read_text())
    assert len(raw) <= 2000
    for value in (brief, full):
        assert value['status'] == 'read_only'
        assert value['task'] == 'T1'
        assert value['summary'] == 'Historical check passed'
        assert value['history'] == {
            'method': 'SAMPLE', 'actual_exit_code': 0, 'passed': True,
        }
