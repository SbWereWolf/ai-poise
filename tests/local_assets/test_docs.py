"""Canonical operator instructions must name the executable restore contract."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_local_asset_manual_names_command_and_repair_boundary() -> None:
    manual = ROOT / "docs/configuration/local-assets.md"
    text = manual.read_text(encoding="utf-8")
    assert "python -m poise local-assets --manifest" in text
    assert "start-dev repair" in text
    assert "source_root" in text
    assert "document-intake-policy.json" in text
    assert "content-read.py" in text
    assert "Poise Task DB" in text
