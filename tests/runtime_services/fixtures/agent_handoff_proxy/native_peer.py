"""Invoke existing real owners in an isolated test project, never a live Task."""
import json
from pathlib import Path
import sys

from conftest import WorkPoise
from poise.application.work import WorkTools


config, actor, destination = sys.argv[1:4]
packet = json.load(sys.stdin)
result = WorkTools(WorkPoise(Path(config), actor)).invoke(packet)
path = Path(destination) / "native-response.json"
path.write_text(json.dumps(result, ensure_ascii=False))
print(json.dumps({"status": result["status"], "response_path": str(path)}))
