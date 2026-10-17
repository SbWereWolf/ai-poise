# Публикации, исходы и выполнение требований

Обновлено: **2026-10-18**.

## Запуск и рабочий пакет

Это реализованный профиль самостоятельного Requirements. Подготовьте выбранный
Python 3.13, checkout и конфигурацию по [инструкции CLI](shell-req.md).
Для последовательности ниже используйте новое пустое хранилище и конфигурацию
из раздела [создания и повторного открытия](shell-req.md#создание-и-повторное-открытие):
выдача IDs начинается с 1, ширина 6, шаг 1, `max_items:64`.
Сохраните очередной JSON-пакет в `$requirements_work/request.json` и выполните:

```sh
"$requirements_python" -B "$requirements_checkout/apps/requirements/bin/requirements" \
  --config "$requirements_work/config/requirements.json" \
  < "$requirements_work/request.json"
```

Каждый пакет ниже выполняется отдельно, в показанном порядке с той же конфигурацией.
Пакет содержит `operation/input`. `apply.input` содержит `request_id`,
`expected_revision`, `operations`. Успешное изменение возвращает
`status:"applied"`, новую `revision`, `replayed:false`; точный повтор запроса —
сохранённую ревизию и `replayed:true`. `query` возвращает `status:"read_only"`,
общую `revision` и упорядоченный `results:[{id,value}]`. Описанный отказ CLI имеет
`status:"rejected"`, содержательный `reason` и exit 2. Изменение сохраняется целиком
одной транзакцией. Для очередного изменения берите актуальную общую ревизию.

## Поля команд и чтений

Все три команды передаются элементами `apply.input.operations`.

| Команда | Поля помимо `kind` | Результат в хранилище |
| --- | --- | --- |
| `publish_task` | `task_id`, `statement_revision` | Полный неизменяемый snapshot текущей постановки с полным содержательным основанием. Повтор той же пары сохраняет публикацию. |
| `record_outcome` | `outcome` | Один адресованный факт исхода и, при первом текущем positive без предыдущего решения, подтверждение указанных ключей. |
| `select_confirmation` | `task_id`, `snapshot_revision`, `key`, `outcome_id` | Явный выбор подходящего положительного факта либо `null` для отсутствия подтверждения. |

`outcome` содержит ровно `outcome_id`, `task_id`, `snapshot_revision`, `kind`,
`satisfied_task_requirements`. Передавайте `positive`, `negative` или `cancelled`;
для последних двух список ключей пуст. Ключи — уникальное подмножество ключей
выбранного снимка. Сохраняйте неизменяемый ID и полное содержание факта при повторе.
Для выбора укажите положительный факт той же Task/публикации, содержащий этот key.

Чтения передаются элементами `query.input.queries` с уникальным `id`.

| `kind` | Поля помимо `id/kind` | Полное `value` |
| --- | --- | --- |
| `snapshot` | `task_id`, `snapshot_revision` | Заголовки публикации, полная `statement`, выбранные `requirements` и обе группы `links`. |
| `current_satisfaction` | — | `task_requirements`, `applications`, `systems` для всех текущих постановок и всего реестра родителей. |
| `outcome_state` | `task_id`, `snapshot_revision` | `task_id`, `snapshot_revision`, `statement_revision`, все `outcomes`, текущие `confirmations`, вся последовательность `decisions` этой публикации. |

Строка текущего TR содержит `task_id`, `statement_revision`, `snapshot_revision`
либо `null`, `key`, `applicable`, `satisfied`, `evidence_outcome_ids`.
Строка AR/SR содержит `id`, полный текущий `children`, `applicable`, `satisfied`.
Дети AR имеют `task_id/statement_revision/key`, дети SR — IDs Application.
TR упорядочены по Task/key, родители по ID; дочерние наборы тоже упорядочены.
Содержательный current родитель выполнен при непустом полном наборе выполненных
детей. Current AR без TR остаётся невыполненным ребёнком SR. Future/obsolete
родители имеют `applicable:false`, `satisfied:false`, текущие дети пусты.

`outcome_state.outcomes` упорядочены по `outcome_id`, `confirmations` — по key.
Решения содержат `sequence/key/outcome_id`, sequence начинается с 1 внутри
Task/публикации. Отсутствие выбора отображается как `outcome_id:null`; история
отличает его от явного решения с `null`. Первый явный `null` создаёт решение,
поэтому следующий positive сохраняется и ждёт явного выбора. Negative/cancelled
сами решения не создают: последующий первый positive допускается обычным порядком.

## 1. Подготовить основание и две постановки

Обе Task используют local key `R1`, но это разные обязательства. APP-X требует
обе Task, APP-Y — только первую. APP-X относится к SYS-A и SYS-B, APP-Y — к SYS-A.
Первые четыре пакета дают общие ревизии 1, 2, 3 и 4.

**Сохранить верхние требования и связи**

```json
{
  "operation": "apply",
  "input": {
    "request_id": "S05-explicit-choice-foundation",
    "expected_revision": 0,
    "operations": [
      {
        "kind": "put_requirement",
        "requirement": {
          "id": "SYS-A",
          "level": "system",
          "status": "current",
          "text": "Предоставить общий комплект."
        }
      },
      {
        "kind": "put_requirement",
        "requirement": {
          "id": "SYS-B",
          "level": "system",
          "status": "current",
          "text": "Предоставить полный результат."
        }
      },
      {
        "kind": "put_requirement",
        "requirement": {
          "id": "APP-X",
          "level": "application",
          "status": "current",
          "text": "Предоставить полный результат."
        }
      },
      {
        "kind": "put_requirement",
        "requirement": {
          "id": "APP-Y",
          "level": "application",
          "status": "current",
          "text": "Предоставить основную часть."
        }
      },
      {
        "kind": "link",
        "system": "SYS-A",
        "application": "APP-X"
      },
      {
        "kind": "link",
        "system": "SYS-A",
        "application": "APP-Y"
      },
      {
        "kind": "link",
        "system": "SYS-B",
        "application": "APP-X"
      }
    ]
  }
}
```

**Зарегистрировать первую Task**

```json
{
  "operation": "create_task",
  "input": {
    "request_id": "S05-explicit-choice-create-000001",
    "goal": "Подготовить основную часть."
  }
}
```

**Зарегистрировать вторую Task**

```json
{
  "operation": "create_task",
  "input": {
    "request_id": "S05-explicit-choice-create-000002",
    "goal": "Подготовить приложение."
  }
}
```

**Сохранить текущие постановки**

```json
{
  "operation": "apply",
  "input": {
    "request_id": "S05-explicit-choice-statements",
    "expected_revision": 3,
    "operations": [
      {
        "kind": "revise_task",
        "task_id": "000001",
        "expected_statement_revision": 1,
        "statement": {
          "goal": "Подготовить основную часть.",
          "task_requirements": [
            {
              "key": "R1",
              "status": "current",
              "text": "Сформировать основную часть.",
              "applications": [
                "APP-X",
                "APP-Y"
              ]
            }
          ],
          "definition_of_done": [
            "Результат доступен."
          ]
        }
      },
      {
        "kind": "revise_task",
        "task_id": "000002",
        "expected_statement_revision": 1,
        "statement": {
          "goal": "Подготовить приложение.",
          "task_requirements": [
            {
              "key": "R1",
              "status": "current",
              "text": "Сформировать приложение.",
              "applications": [
                "APP-X"
              ]
            }
          ],
          "definition_of_done": [
            "Результат доступен."
          ]
        }
      }
    ]
  }
}
```

## 2. Опубликовать текущее основание

Публикация обеих Task фиксируется общей ревизией 5. Чтение возвращает полные
снимки с собственными `statement_revision:2`, `snapshot_revision:1`.
Публикация содержит основание только своей Task; глобальная полнота читает всех
текущих детей из собственного графа.

**Опубликовать обе постановки**

```json
{
  "operation": "apply",
  "input": {
    "request_id": "S05-explicit-choice-publications",
    "expected_revision": 4,
    "operations": [
      {
        "kind": "publish_task",
        "task_id": "000001",
        "statement_revision": 2
      },
      {
        "kind": "publish_task",
        "task_id": "000002",
        "statement_revision": 2
      }
    ]
  }
}
```

**Прочитать полные снимки**

```json
{
  "operation": "query",
  "input": {
    "queries": [
      {
        "id": "snapshot-0",
        "kind": "snapshot",
        "task_id": "000001",
        "snapshot_revision": 1
      },
      {
        "id": "snapshot-1",
        "kind": "snapshot",
        "task_id": "000002",
        "snapshot_revision": 1
      }
    ]
  }
}
```

## 3. Передать положительные исходы

Первый исход A подтверждает только A.R1: APP-Y выполнен, APP-X ждёт B.R1.
Второй исход даёт B.R1; после него оба AR и оба SR выполнены. Общая ревизия 7.
Запрос текущей оценки показывает полные дочерние наборы и выбранные IDs.

**Принять исход A**

```json
{
  "operation": "apply",
  "input": {
    "request_id": "S05-explicit-choice-accept-a",
    "expected_revision": 5,
    "operations": [
      {
        "kind": "record_outcome",
        "outcome": {
          "outcome_id": "o-a",
          "task_id": "000001",
          "snapshot_revision": 1,
          "kind": "positive",
          "satisfied_task_requirements": [
            "R1"
          ]
        }
      }
    ]
  }
}
```

**Принять исход B**

```json
{
  "operation": "apply",
  "input": {
    "request_id": "S05-explicit-choice-accept-b",
    "expected_revision": 6,
    "operations": [
      {
        "kind": "record_outcome",
        "outcome": {
          "outcome_id": "o-b",
          "task_id": "000002",
          "snapshot_revision": 1,
          "kind": "positive",
          "satisfied_task_requirements": [
            "R1"
          ]
        }
      }
    ]
  }
}
```

**Прочитать полную текущую оценку**

```json
{
  "operation": "query",
  "input": {
    "queries": [
      {
        "id": "current",
        "kind": "current_satisfaction"
      }
    ]
  }
}
```

## 4. Сохранить альтернативу и выбрать её явно

Новый положительный факт A сохраняется при ревизии 8, прежний выбор остаётся.
История показывает оба факта. Явный выбор альтернативы получает ревизию 9 и
добавляет следующее решение; выполнение родителей сохраняется.

**Сохранить альтернативный исход A**

```json
{
  "operation": "apply",
  "input": {
    "request_id": "S05-explicit-choice-alternative",
    "expected_revision": 7,
    "operations": [
      {
        "kind": "record_outcome",
        "outcome": {
          "outcome_id": "o-a-alt",
          "task_id": "000001",
          "snapshot_revision": 1,
          "kind": "positive",
          "satisfied_task_requirements": [
            "R1"
          ]
        }
      }
    ]
  }
}
```

**Прочитать прежнее подтверждение**

```json
{
  "operation": "query",
  "input": {
    "queries": [
      {
        "id": "current",
        "kind": "current_satisfaction"
      }
    ]
  }
}
```

**Прочитать факты и историю A**

```json
{
  "operation": "query",
  "input": {
    "queries": [
      {
        "id": "history",
        "kind": "outcome_state",
        "task_id": "000001",
        "snapshot_revision": 1
      }
    ]
  }
}
```

**Явно выбрать альтернативу**

```json
{
  "operation": "apply",
  "input": {
    "request_id": "S05-explicit-choice-choose-alternative",
    "expected_revision": 8,
    "operations": [
      {
        "kind": "select_confirmation",
        "task_id": "000001",
        "snapshot_revision": 1,
        "key": "R1",
        "outcome_id": "o-a-alt"
      }
    ]
  }
}
```

**Прочитать подтверждённые требования**

```json
{
  "operation": "query",
  "input": {
    "queries": [
      {
        "id": "current",
        "kind": "current_satisfaction"
      }
    ]
  }
}
```

## 5. Зафиксировать отсутствие подтверждения

Явный `null` даёт ревизию 10. A.R1 становится невыполненным, B.R1 остаётся
выполненным, оба AR/SR ждут A. Повтор прежнего факта в новом внешнем запросе
даёт ревизию 11, сохраняя один факт и явное отсутствие. Новая альтернатива даёт
ревизию 12 и тоже ждёт явного выбора. История сохраняет все три факта A.

**Явно выбрать отсутствие подтверждения**

```json
{
  "operation": "apply",
  "input": {
    "request_id": "S05-explicit-choice-choose-absence",
    "expected_revision": 9,
    "operations": [
      {
        "kind": "select_confirmation",
        "task_id": "000001",
        "snapshot_revision": 1,
        "key": "R1",
        "outcome_id": null
      }
    ]
  }
}
```

**Прочитать неполный текущий результат**

```json
{
  "operation": "query",
  "input": {
    "queries": [
      {
        "id": "current",
        "kind": "current_satisfaction"
      }
    ]
  }
}
```

**Повторить прежний факт новым запросом**

```json
{
  "operation": "apply",
  "input": {
    "request_id": "S05-explicit-choice-old-fact-again",
    "expected_revision": 10,
    "operations": [
      {
        "kind": "record_outcome",
        "outcome": {
          "outcome_id": "o-a",
          "task_id": "000001",
          "snapshot_revision": 1,
          "kind": "positive",
          "satisfied_task_requirements": [
            "R1"
          ]
        }
      }
    ]
  }
}
```

**Прочитать сохранённый выбор**

```json
{
  "operation": "query",
  "input": {
    "queries": [
      {
        "id": "current",
        "kind": "current_satisfaction"
      }
    ]
  }
}
```

**Сохранить новый альтернативный факт**

```json
{
  "operation": "apply",
  "input": {
    "request_id": "S05-explicit-choice-later-alternative",
    "expected_revision": 11,
    "operations": [
      {
        "kind": "record_outcome",
        "outcome": {
          "outcome_id": "o-a-later",
          "task_id": "000001",
          "snapshot_revision": 1,
          "kind": "positive",
          "satisfied_task_requirements": [
            "R1"
          ]
        }
      }
    ]
  }
}
```

**Прочитать сохранённое отсутствие**

```json
{
  "operation": "query",
  "input": {
    "queries": [
      {
        "id": "current",
        "kind": "current_satisfaction"
      }
    ]
  }
}
```

**Прочитать полную историю A**

```json
{
  "operation": "query",
  "input": {
    "queries": [
      {
        "id": "history",
        "kind": "outcome_state",
        "task_id": "000001",
        "snapshot_revision": 1
      }
    ]
  }
}
```

## 6. Повторить запрос и восстановить явное подтверждение

Точный повтор первоначального запроса A возвращает сохранённую ревизию 6
с `replayed:true`; текущая общая ревизия остаётся 12 и выбор остаётся `null`.
Явный выбор последнего факта A даёт ревизию 13 и завершает обоих родителей
каждого уровня. Новый запрос того же выбора даёт ревизию 14 без нового решения.
Итоговая история A содержит решения: o-a, o-a-alt, null, o-a-later.

**Повторить первоначальный внешний запрос A**

```json
{
  "operation": "apply",
  "input": {
    "request_id": "S05-explicit-choice-accept-a",
    "expected_revision": 5,
    "operations": [
      {
        "kind": "record_outcome",
        "outcome": {
          "outcome_id": "o-a",
          "task_id": "000001",
          "snapshot_revision": 1,
          "kind": "positive",
          "satisfied_task_requirements": [
            "R1"
          ]
        }
      }
    ]
  }
}
```

**Прочитать актуальную общую ревизию**

```json
{
  "operation": "query",
  "input": {
    "queries": [
      {
        "id": "current",
        "kind": "current_satisfaction"
      }
    ]
  }
}
```

**Явно выбрать последний факт A**

```json
{
  "operation": "apply",
  "input": {
    "request_id": "S05-explicit-choice-choose-later",
    "expected_revision": 12,
    "operations": [
      {
        "kind": "select_confirmation",
        "task_id": "000001",
        "snapshot_revision": 1,
        "key": "R1",
        "outcome_id": "o-a-later"
      }
    ]
  }
}
```

**Прочитать выполненный каскад**

```json
{
  "operation": "query",
  "input": {
    "queries": [
      {
        "id": "current",
        "kind": "current_satisfaction"
      }
    ]
  }
}
```

**Повторить текущий выбор новым запросом**

```json
{
  "operation": "apply",
  "input": {
    "request_id": "S05-explicit-choice-unchanged-choice",
    "expected_revision": 13,
    "operations": [
      {
        "kind": "select_confirmation",
        "task_id": "000001",
        "snapshot_revision": 1,
        "key": "R1",
        "outcome_id": "o-a-later"
      }
    ]
  }
}
```

**Прочитать итоговую историю A**

```json
{
  "operation": "query",
  "input": {
    "queries": [
      {
        "id": "history",
        "kind": "outcome_state",
        "task_id": "000001",
        "snapshot_revision": 1
      }
    ]
  }
}
```

## Новая постановка и изменение родителей

Для новой постановки используйте `revise_task` из [руководства графа](graph-req.md).
Её current требования уже входят в глобальный набор детей и ожидают собственной
публикации и положительного подтверждения. Опубликуйте новую `statement_revision`
и адресуйте новый исход возвращаемой паре Task/snapshot. Подтверждения остальных
Task сохраняются. Старый snapshot и `outcome_state` остаются историей.

`get_task.snapshot_revision` показывает последнюю историческую публикацию;
для текущего выполнения используйте `current_satisfaction`, где публикация
подобрана по текущей постановке. Изменение связей учитывает полные текущие наборы
прежних и новых AR/SR, включая опустевшего current родителя с `satisfied:false`.

Negative/cancelled сохраняют факт с пустыми ключами и оставляют прежний выбранный
positive. Для изменения подтверждения используйте явный `select_confirmation`.
Повтор исполнения с прежней постановкой и публикацией сохраняет это основание;
данные попыток и generation исполнения остаются у Execution. Перенос адресованных
фактов выполняет вызывающая сторона через публичный интерфейс Requirements.

Предметные переходы и независимые примеры описаны в
[политике пересчёта](outcome-recalculation.md); формат строк и транзакция —
в [описании хранения](storage-req.md#публикации-исходы-и-решения).
