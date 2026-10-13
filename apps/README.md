# Application delivery layout

Эта страница определяет структуру семи приложений и контракт корневых точек
запуска. Самостоятельные CLI и конфиги находятся в принадлежащих приложениям
путях из [механических проверок границ](../docs/workflows/application-boundaries.md).
Там описаны точная карта установленных точек входа, конфигов и состояния,
а также конечная статическая проверка; эта страница не подтверждает интеграцию
корневых псевдонимов. В частности, унаследованный маршрут `stride` по-прежнему
указывает `apps/orchestrator/bin/stride`, тогда как самостоятельный исполнитель —
`apps/orchestrator/bin/orchestrator`. Отдельная проверка маршрута T072/T085
остаётся невыполненной. Пустой слот `bin/` означает **не реализовано**, а не
установленного исполнителя или успешную команду. Ниже сохранены исторические
наблюдения T017 о точках запуска и каркасе, а не текущие доказательства работы
исполнителей.

## Seven application owners

| Directory | Public contract | Owned executable location |
| --- | --- | --- |
| `requirements` | [Requirements](requirements/docs/contract-req.md) | `apps/requirements/bin/` |
| `sequencing` | [Sequencing](sequencing/docs/contract-seq.md) | `apps/sequencing/bin/` |
| `execution` | [Execution](execution/docs/contract-exe.md) | `apps/execution/bin/` |
| `orchestrator` | [Orchestrator](orchestrator/docs/contract-orc.md) | `apps/orchestrator/bin/` |
| `bootstrap` | [Bootstrap](bootstrap/docs/contract-bst.md) | `apps/bootstrap/bin/` |
| `stride-uat` | [Stride UAT](stride-uat/docs/contract-uat.md) | `apps/stride-uat/bin/` |
| `poise` | [Poise](poise/docs/contract-poi.md) | `apps/poise/bin/` |

The tracked `.gitkeep` files preserve empty executable directories in a Git-based
delivery. They are not executable providers. There is no eighth Planning/Planner
application: Stride names the system and its direct orchestration alias. There is
no required root `bin/` directory and no mandatory `stack` dispatcher.

## Три корневые shell-точки и Python Bootstrap

| Root launcher | Exact app-owned target | Owner |
| --- | --- | --- |
| `stride` | `apps/orchestrator/bin/stride` | Orchestrator |
| `stride-uat` | `apps/stride-uat/bin/stride-uat` | Stride UAT |
| `poise` | `apps/poise/bin/poise` | Poise |

Три shell-точки из таблицы — исполняемые файлы с Git mode `100755`.
Корневой `bootstrap` сохраняет текущий Python-адаптер обслуживания окружения
из master: его вызывают через выбранный Python. Самостоятельный Bootstrap CLI
спринта доступен по `apps/bootstrap/bin/bootstrap`; его контракт проверяется отдельно.
См. [обслуживание окружения](../docs/operator/bootstrap-catalog-v2.md).
It resolves the delivery directory from its own location in a subshell and uses
`exec` to replace itself with the exact target. It forwards `argv` using `"$@"`,
without splitting, expansion, reordering, option parsing or shell evaluation.
Inherited `stdin`, `stdout` and `stderr` remain raw byte streams. The provider's
exit code and OS process identity are preserved. The caller `cwd` and inherited
environment are unchanged; `CDPATH` is suppressed only during the internal root
lookup, not removed from the provider environment.

A delivery may move intact to another directory, including paths with spaces and
Unicode. Invoke the regular root entrypoint by its path from any caller directory;
no old absolute installation path is embedded. The tested platform is POSIX/Linux
with `/bin/sh`, `dirname`, `cd` and `pwd`. Installing a separate external symlink,
Windows shell emulation and signal-forwarding wrappers are not promised by this
scaffold. No daemon or intermediary process is introduced.

The launcher does not read configuration, initialize state, create a Task, run
migrations or import a monolithic fallback. Configuration and protocol processing
belong to the executable provider. Requirements, Sequencing and Execution have no
extra root aliases; their consumers use explicitly configured provider commands.

## Availability and failure semantics

At T017 the app-owned executable targets are absent. Invoking a root launcher then
returns shell exit **127** and a diagnostic on `stderr`; it must not fabricate a
JSON success on `stdout`. An existing non-executable target returns shell exit
**126**. Once a provider is installed, its status is forwarded unchanged, including
nonzero results. The launcher does not catch an error and pretend another provider
succeeded. Failure to resolve its own root also remains a nonzero shell failure.

T018–T024 deliver the independent app CLI/configuration implementations. Their
integration tests must verify the real installed providers. Neither a placeholder
nor a green launcher fixture test satisfies those implementation Tasks.

## Task-specific verification

The test-owned literal routes in `tests/delivery/test_root_launchers.py` are not
imported from production code. The behavior suite copies the actual launcher
bytes and executable modes into a disposable, relocated delivery. It installs an
explicit temporary **fixture provider** at the expected app-owned path and invokes
the launcher as a real OS process from a different working directory.

The 19 behavior cases cover the three shell routes, empty/quoted/Unicode/metacharacter
arguments, binary pipes, caller environment and `cwd`, provider exit 0/7, `exec`
process replacement, missing/non-executable provider 127/126, and the seven-owner
layout. A separate documentation case checks this exact map and the capability
boundary. These are launcher tests, not fake production app implementations.

```sh
python -B tests/delivery/test_root_launchers.py --suite behavior
python -B tests/delivery/test_root_launchers.py --suite documentation
bash tests/smoke.sh /path/to/explicit/test/python
```

Исторический T017 проверял 25 случаев для четырёх shell-точек. После объединения
с master корневой `bootstrap` проверяют отдельные тесты
`tests/architecture/test_bootstrap.py`; актуальная shell-проверка содержит 19 случаев.
Штатный smoke из шести тестов остаётся отдельной проверкой.

## Requirement and delivery mapping

- GEN-01: the seven owners and app-owned `bin/` locations; no eighth application.
- GEN-02: four direct root aliases with transparent process routing and no `stack`.
- Task DoD: tests prove the launcher process boundary and relocation; this page
  co-delivers the commands, mapping, failures and explicit provider limitations.

## Подготовка рабочих конфигураций

Для новой установки скопируйте `*.example.json` из `apps/*/config/` в рабочий
файл того же каталога, убрав `.example` из имени. Настройте пути и лимиты по
контракту выбранного приложения. Рабочие JSON сохраняются локально и исключены
из Git; под версионным контролем находятся только примеры. В восстановленной
установке рабочие файлы уже присутствуют и сохраняют значения из checkpoint.

Например, подготовка самостоятельного Requirements CLI:

```bash
cp apps/requirements/config/requirements.example.json apps/requirements/config/requirements.json
```
