"""Four actual shipped examples are inputs to the installed real consumer."""
import json
from pathlib import Path

from tests.command_intake.helpers import Intake
from tests.command_intake.installed_consumer import installed


def test_actual_command_intake_examples(project,tmp_path,monkeypatch):
    root=Path(__file__).resolve().parents[2]
    manifest=json.loads((Path(__file__).with_name('fixtures')/'example-paths.json').read_text())
    examples={name:json.loads((root/path).read_text()) for name,path in manifest.items()}
    assert set(examples)=={'policy','profile','definition','settings'}
    i=Intake(project,tmp_path,monkeypatch).enable()
    proof=installed(i,examples=examples,label='documented-command')
    assert proof['examples']==examples
    assert proof['after']['details']['documents'][0]['ranges']==[[1,4]]
