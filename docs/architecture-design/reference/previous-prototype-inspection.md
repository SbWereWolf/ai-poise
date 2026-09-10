# Что прочитано в существующем прототипе

Создано: **2026-09-06T15:39:15+05:00**.

Статус: **предлагаемая декомпозиция для реализации; код Harness и исходные маршруты не изменены**.

Источник: `harness-happy-path_2026-09-06T14-30-44+05-00`. Только просмотр исходников; тесты не запускались. Это не новый обзор дефектов.

| Файл | Строка | Существующий элемент |
|---|---:|---|
| `src/harness/runtime.py` | 17 | `class Harness:` |
| `src/harness/runtime.py` | 76 | `def _stage(self, task: dict) -> dict:` |
| `src/harness/runtime.py` | 51 | `def _tree(self, worktree: Path) -> str:` |
| `src/harness/runtime.py` | 223 | `def _select_checks(self, data: dict, changed: list[str]) -> list[dict]:` |
| `src/harness/runtime.py` | 242 | `def _candidate_artifacts(self, data: dict, submitted: list[str], roots: dict[str,Path]):` |
| `src/harness/runtime.py` | 137 | `def bootstrap(self, task_file: Path | str | None = None, decision: str | None = None,` |
| `src/harness/runtime.py` | 258 | `def verify(self) -> dict:` |
| `src/harness/runtime.py` | 357 | `def _publish(self, data: dict, publication: dict) -> dict:` |
| `src/harness/execution.py` | 10 | `def run_command(argv: list[str], cwd: Path, env: dict[str,str], timeout: float,` |
| `src/harness/execution.py` | 46 | `def preview(path: Path, chars: int) -> str:` |
| `src/harness/storage.py` | 12 | `class Store:` |
| `src/harness/artifacts.py` | 8 | `def inspect_paths(paths: list[str], roots: dict[str, Path], owners: dict[str,str]):` |
| `src/harness/artifacts.py` | 35 | `def check_counts(records: list[dict], rules: list[dict]) -> None:` |

Исходные файлы находились в отдельном неизменённом комплекте. Контрольные суммы кода, tests, configs и examples до/после приведены в `prototype-unchanged.json`. Нового кода семи обработчиков в этом комплекте нет.
