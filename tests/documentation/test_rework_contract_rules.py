import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RUSSIAN_HEADING = "## Неизменность путей, rework и граф этапов"
ENGLISH_HEADING = "## Task path, rework, and stage graph invariants"
CANONICAL_LINK = (
    "[\u043d\u0435\u0438\u0437\u043c\u0435\u043d\u043d\u043e\u0441\u0442\u044c \u043f\u0443\u0442\u0435\u0439, rework \u0438 \u0433\u0440\u0430\u0444 \u044d\u0442\u0430\u043f\u043e\u0432]"
    "(../governance/development-rules.md#\u043d\u0435\u0438\u0437\u043c\u0435\u043d\u043d\u043e\u0441\u0442\u044c-\u043f\u0443\u0442\u0435\u0439-rework-\u0438-\u0433\u0440\u0430\u0444-\u044d\u0442\u0430\u043f\u043e\u0432)"
)
DRAFT_HEADING = "## Редактируемые приёмочные черновики"
DRAFT_LINK = (
    "[редактируемый приёмочный черновик]"
    "(../workflows/batch-work.md#редактируемые-приёмочные-черновики)"
)
AGENT_DRAFT_LINK = (
    "[editable acceptance draft contract]"
    "(docs/workflows/batch-work.md#редактируемые-приёмочные-черновики)"
)
RUSSIAN_SOURCE_TIMING = (
    "Проверка противоречий учитывает `method_inputs.future_outputs` и активные post-обязательства\n"
    "`stage_output` как планируемые записи. Артефакт без `source` и артефакт `preexisting`\n"
    "замораживают путь до Task, `declared_arrival` — при входе в `arrival_stage`, а\n"
    "`stage_output` — после выхода из `producer_stage`. Противоречие определяется пересечением\n"
    "языков path-pattern, а не только равенством строк. Предикат с `maximum: 0` требует\n"
    "отсутствия артефакта и не замораживает соответствующий путь."
)
ENGLISH_SOURCE_TIMING = (
    "Contradiction validation treats `method_inputs.future_outputs` and active post\n"
    "`stage_output` obligations as planned writes. A source-less or `preexisting` artifact\n"
    "freezes its path before the Task, `declared_arrival` at `arrival_stage` entry, and\n"
    "`stage_output` after `producer_stage` exits. Conflicts use path-pattern language\n"
    "intersection rather than string equality alone. A predicate with `maximum: 0` requires\n"
    "absence and does not freeze its path."
)
PLANNING_SOURCE_TIMING = (
    "При этой проверке `future_outputs` и активные post-обязательства `stage_output` считаются\n"
    "планируемыми записями; момент заморозки определяется видом source, а пересечение путей —\n"
    "пересечением языков path-pattern. Предикат с `maximum: 0` означает отсутствие и путь не\n"
    "замораживает."
)
RULES = {
    "TASK-PATH-01": {
        "ru": "При создании Task запрещено объявлять путь неизменным, если последующий обычный этап или rework должен изменить либо создать его; до `ready` каждый планируемый путь записи должен входить в `allowed_paths` соответствующего этапа.",
        "en": "Task creation must not declare a path immutable when a later normal or rework stage must modify or create it; every planned write path belongs in that stage's `allowed_paths` before `ready`.",
    },
    "TASK-PATH-02": {
        "ru": "Последующий этап или план rework не может требовать запись в путь, замороженный разрешённым контрактом Task; до допуска этапа авторизованное планирование или `restart` должно устранить противоречие.",
        "en": "A later stage or rework plan must not require a write to a path frozen by the resolved Task contract; authorized planning or `restart` must remove the contradiction before stage admission.",
    },
    "TASK-PATH-03": {
        "ru": "Rework не снимает неизменность: он использует точные `allowed_paths` целевого этапа, а изменение вне этого scope отклоняется до сохранения Task, артефакта или результата проверки.",
        "en": "Rework does not lift immutability: it uses the target stage's exact `allowed_paths`, and an out-of-scope change is rejected before Task, artifact, or check-result persistence.",
    },
    "TASK-PATH-04": {
        "ru": "Обычные зарегистрированные неизменяемые артефакты не перезаписываются; `artifact_drafts` сохраняет один рабочий путь и identity только для заранее объявленного черновика или явно разрешённого восстановления преждевременно замороженного нетерминального файла, а каждый review создаёт отдельный неизменяемый снимок.",
        "en": "Ordinary registered immutable artifacts are not overwritten; `artifact_drafts` preserves one working path and identity only for a predeclared draft or explicitly authorized recovery of a prematurely frozen nonterminal file, while every review creates a separate immutable snapshot.",
    },
    "TASK-PATH-05": {
        "ru": "Некорректный граф этапов отклоняется, если цель перехода или rework отсутствует, этап недостижим от entry либо из достижимого этапа нет пути к положительному завершению; цикл допустим только при наличии такого выхода.",
        "en": "An invalid stage graph is rejected when a transition or rework target is missing, a stage is unreachable from the entry, or a reachable stage has no path to positive termination; a cycle is valid only when it retains such an exit.",
    },
}
RUSSIAN_RULES = {identifier: text["ru"] for identifier, text in RULES.items()}
ENGLISH_RULES = {identifier: text["en"] for identifier, text in RULES.items()}


def _section(text: str, heading: str) -> str | None:
    marker = f"{heading}\n"
    if text.count(marker) != 1:
        return None
    return text.split(marker, 1)[1].split("\n## ", 1)[0]


def _rules(text: str, heading: str) -> dict[str, str] | None:
    section = _section(text, heading)
    if section is None:
        return None
    found = {}
    for identifier, statement in re.findall(
        r"^- \*\*(TASK-PATH-\d+)\.\*\* (.+)$", section, re.MULTILINE
    ):
        if identifier in found:
            return None
        found[identifier] = statement
    return found


def policy_rules_present(files: dict[str, str] | None = None) -> bool:
    if files is None:
        files = {
            "development": (ROOT / "docs/governance/development-rules.md").read_text(),
            "planning": (ROOT / "docs/workflows/task-planning.md").read_text(),
            "batch": (ROOT / "docs/workflows/batch-work.md").read_text(),
            "agents": (ROOT / "AGENTS.md").read_text(),
        }
    russian = _rules(files["development"], RUSSIAN_HEADING)
    english = _rules(files["agents"], ENGLISH_HEADING)
    return (
        russian == RUSSIAN_RULES
        and english == ENGLISH_RULES
        and CANONICAL_LINK in files["planning"]
        and CANONICAL_LINK in files["batch"]
        and DRAFT_LINK in files["planning"]
        and DRAFT_HEADING in files["batch"]
        and AGENT_DRAFT_LINK in files["agents"]
        and RUSSIAN_SOURCE_TIMING in files["development"]
        and ENGLISH_SOURCE_TIMING in files["agents"]
        and PLANNING_SOURCE_TIMING in files["planning"]
        and set(russian or ()) == set(english or ())
        and all(russian[key] != english[key] for key in RULES)
    )


def _valid_fixture() -> dict[str, str]:
    russian = "\n".join(f"- **{key}.** {value}" for key, value in RUSSIAN_RULES.items())
    english = "\n".join(f"- **{key}.** {value}" for key, value in ENGLISH_RULES.items())
    return {
        "development": f"{RUSSIAN_HEADING}\n\n{russian}\n{RUSSIAN_SOURCE_TIMING}\n",
        "planning": f"{CANONICAL_LINK}\n{DRAFT_LINK}\n{PLANNING_SOURCE_TIMING}",
        "batch": f"{CANONICAL_LINK}\n{DRAFT_HEADING}\n",
        "agents": (
            f"{ENGLISH_HEADING}\n\n{english}\n{ENGLISH_SOURCE_TIMING}\n"
            f"{AGENT_DRAFT_LINK}\n"
        ),
    }


def test_rework_and_task_creation_path_rules_are_canonical() -> None:
    assert policy_rules_present()


def test_disconnected_negated_and_contradictory_rule_fixtures_are_rejected() -> None:
    fixtures = json.loads(
        (ROOT / "fixtures/rework-contract-rules/invalid.json").read_text()
    )
    for fixture in fixtures:
        files = _valid_fixture()
        files[fixture["file"]] = files[fixture["file"]].replace(
            fixture["from"], fixture["to"], 1
        )
        assert not policy_rules_present(files), fixture["id"]


def test_editable_draft_contract_links_are_required() -> None:
    for file, marker in (
        ("planning", DRAFT_LINK),
        ("batch", DRAFT_HEADING),
        ("agents", AGENT_DRAFT_LINK),
    ):
        files = _valid_fixture()
        files[file] = files[file].replace(marker, "", 1)
        assert not policy_rules_present(files), file


def test_source_timing_and_pattern_overlap_rules_are_required() -> None:
    for file, marker in (
        ("development", RUSSIAN_SOURCE_TIMING),
        ("planning", PLANNING_SOURCE_TIMING),
        ("agents", ENGLISH_SOURCE_TIMING),
    ):
        files = _valid_fixture()
        files[file] = files[file].replace(marker, "", 1)
        assert not policy_rules_present(files), file
