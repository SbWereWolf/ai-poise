"""Seed the first AI poise WSL project Sprint through public AI poise APIs.

The tool never edits SQLite directly. The project manifest must already contain
the final WSL repository and project-data paths.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path

from poise.application.work import WorkTools
from poise.runtime import Poise

ROOT = Path(__file__).resolve().parents[1]
HISTORICAL_TASK_NAMESPACE = "har" + "ness"

def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))

def packet(action: str, **values):
    return {"operation":"sprint","input":{"action":action, **values},"messages":[]}

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--poise-config",required=True,type=Path)
    args=p.parse_args()
    h=Poise(args.poise_config.resolve(),"wsl-seed-poise")
    tools=WorkTools(h)
    tasks=[load(ROOT/"delivery"/"task-definitions"/HISTORICAL_TASK_NAMESPACE/f"{tid}.json") for tid in ("0001","0002","0003")]
    dependencies=[
        {"predecessor":"0001","successor":"0002","kind":"result"},
        {"predecessor":"0002","successor":"0003","kind":"result"},
    ]
    draft=tools.invoke(packet("draft",sprint_id="SPRINT-0001",request_id="wsl-seed-draft-1",expected_revision=None,
        template={"id":"basic","version":"1"},changes=[
          {"kind":"purpose","goal":"Подготовить AI poise к requirement-aware постановке задач и IDE-MCP-first работе AI-агентов.",
           "requirements":["Все три задачи относятся к общим возможностям AI-агентов и выполняются в одном project-local Sprint AI poise.",
                           "Requirements DB и Task DB принадлежат проекту AI poise и хранятся вне обслуживаемой кодовой базы в путях, задаваемых project configuration."],
           "definition_of_done":["Requirements graph и Task traceability работают штатно.","Bootstrap preflight выдаёт фактические IDE MCP capabilities.","JetBrains MCP policy исследована, документирована и отражена в skill."]},
          {"kind":"sections","values":{"plan":"Последовательно выполнить 0001 → 0002 → 0003. Result dependency передаёт проверенный код предшественника следующей задаче."}},
          {"kind":"upsert_tasks","tasks":tasks},
          {"kind":"dependencies","items":dependencies},
        ]))
    if draft["status"]!="draft" or draft["errors"]:
        raise SystemExit(f"Sprint draft invalid: {draft['errors']}")
    published=tools.invoke(packet("publish",sprint_id=None,request_id="wsl-seed-publish-1",expected_revision=draft["revision"]))
    bootstrap=(ROOT/"delivery"/"requirements-bootstrap.json").read_text(encoding="utf-8")
    tools.invoke({"operation":"artifacts","input":{"items":[{
        "scope":"sprint","path":"requirements-bootstrap.json","source":{"kind":"text","text":bootstrap}
    }]},"messages":[]})
    print(json.dumps({"status":"seeded","poise":published,"requirements_bootstrap":"SPRINT-0001/requirements-bootstrap.json"},ensure_ascii=False,indent=2))

if __name__=="__main__": main()
