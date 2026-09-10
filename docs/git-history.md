# Восстановление Git-истории

Обновлено: **2026-09-07T14:31:46+05:00**.

Поставка содержит исходники без рабочего `.git`, чтобы временные runtime/тестовые данные не
выдавались за репозиторий. История среза имеет базу `tasks/HARNESS-DDD-09`; все необходимые
более ранние bundles сохранены в полном комплекте.

Из корня распакованного комплекта, с новым каталогом назначения:

```bash
BUNDLE_ROOT="$PWD"
CHECKOUT=/absolute/new/harness-checkout

git clone --branch tasks/HARNESS-DDD-07C \
  "$BUNDLE_ROOT/evidence/ddd-step07c/history.bundle" "$CHECKOUT"
git -C "$CHECKOUT" fetch "$BUNDLE_ROOT/evidence/ddd-step08/history.bundle" \
  refs/heads/tasks/HARNESS-DDD-08:refs/heads/tasks/HARNESS-DDD-08
git -C "$CHECKOUT" fetch "$BUNDLE_ROOT/evidence/ddd-step09/history.bundle" \
  refs/heads/tasks/HARNESS-DDD-09:refs/heads/tasks/HARNESS-DDD-09
git -C "$CHECKOUT" fetch "$BUNDLE_ROOT/evidence/pilot01/history.bundle" \
  refs/heads/tasks/HARNESS-PILOT-01:refs/heads/tasks/HARNESS-PILOT-01
git -C "$CHECKOUT" switch tasks/HARNESS-PILOT-01
```

Оператор явно задаёт CHECKOUT; существующий каталог не перезаписывается. Bundle remote не
является сетевым backup и не доказывает доступность пользовательского Git-сервера. Для
обычного запуска можно использовать распакованные исходники; для примера self-pilot нужен
именно Git checkout актуального ref с `tests/projects`.

Отчёты испытаний поставки лежат в `evidence/pilot01`; не все производные runtime-evidence
закоммичены в собственную историю (bundle не включает сам себя).

## HARNESS-PILOT-02

Новый incremental bundle: `evidence/pilot02/history.bundle`, ветка
`tasks/HARNESS-PILOT-02`. Prerequisite: `e24a8c550300ecc59d10fb90cd7d9cee545f5d12`
(PILOT-01). Прежняя цепочка сохранена. При восстановлении нескольких bundles использовать
отдельные refs для каждого снимка; не переписывать общий HEAD первым произвольным ref
из `git bundle list-heads`. Код закоммичен локально; внешнего remote Harness не предоставлено.
