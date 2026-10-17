"""Both shipped policy profiles are consumed only after documentation exists."""
import json
import os
from pathlib import Path

from tests.content_reader.helpers import SOURCE, Scenario, sha
from tests.content_reader.installed_consumer import verify_installed


def test_shipped_reader_policy_example_is_executable(project, tmp_path):
    probe = Scenario(project, tmp_path)
    probe.prove()
    from tests.content_reader.helpers import other_project
    proofs = []
    for index, filename in enumerate(['project-file-reading.example.json',
                                      'project-file-reading-session.example.json']):
        example = SOURCE.parent / 'config/examples' / filename
        assert example.is_file(), 'Documentation stage must ship both real profiles'
        s = probe if index == 0 else other_project(project, tmp_path)
        s.prove()
        s.data = s.fill(json.loads(example.read_text(encoding='utf-8')))
        s.save_policy()
        proof = verify_installed(s, tmp_path, label='documented-' + str(index))
        proofs.append({'example':str(example),'sha256':sha(example.read_bytes()),
                       'actual_installed_consumer':proof})
    result = {'examples':proofs,'scope':'Both actual shipped examples through installed CLI and native SDK'}
    (Path(os.environ['POISE_RUN_OUTPUT_DIR'])/'reader.documented-example.json').write_text(
        json.dumps(result,ensure_ascii=False,indent=2)+'\n')
