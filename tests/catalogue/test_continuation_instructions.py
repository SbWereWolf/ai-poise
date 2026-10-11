"""Reference instructions keep verified work within the authorized progression."""
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
PACKS = sorted(
    [*ROOT.glob("config/catalogue/processes/*.json"),
     *ROOT.glob("config/catalogue/process-templates/*.json"),
     *ROOT.glob("config/examples/ai-poise-processes/*.example.json")]
)


@pytest.mark.parametrize("path", PACKS, ids=lambda path: str(path.relative_to(ROOT)))
def test_stage_instructions_continue_authorized_work(path):
    stages = json.loads(path.read_text(encoding="utf-8"))["stages"]
    assert stages
    for stage in stages:
        instruction = stage["instruction"]
        assert "Work on this stage only." not in instruction, stage["id"]
        assert "report and stop" not in instruction, stage["id"]
        if "Use exact registered checks;" in instruction:
            assert "submit one batch to verify." in instruction, stage["id"]
            assert "public advance" in instruction, stage["id"]
            assert "role handoff" in instruction, stage["id"]
            assert "real blockers" in instruction, stage["id"]
            assert "user authorization" in instruction, stage["id"]
