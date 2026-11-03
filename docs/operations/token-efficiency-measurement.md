# Оценка токенов законченного рабочего маршрута

## Назначение и границы

Методика внешнего анализа использует первичные метрики
[существующего механизма записи](../configuration/agent-metrics.md#назначение).
Она связывает расход с доставленным результатом и обязательными проверками.
Нового сборщика, runtime-политики или автоматической оценки она не вводит.

Единица измерения — заранее ограниченное поручение от начала работы до доставки
результата, включая подготовку, инструменты, продолжения и обязательные проверки.
Сначала фиксируют источник и полноту, проверяют факты, определяют расчёт
и независимо проверяют результат. Один завершённый turn подходит, когда в нём
закончено именно заявленное поручение. Предложенный в ответе будущий эксперимент
не считается исполненным результатом.

## Правила расчёта

Записи отбирают по действительным thread/turn/response ID, сохраняя область
каждого счётчика. Идентичный повтор response учитывают один раз; конфликт
значений одного ID требует разрешения расхождения. Размер входа отдельного
обращения, сумма входов, выходы, кеш, reasoning и время остаются разными величинами.

Суммарный native расход — сумма input_tokens + output_tokens уникальных
response. cached_input_tokens входит во входные, reasoning_output_tokens —
в выходные. cache_write_input_tokens сохраняют отдельно с исходным значением.
Детализацию и дублирующие области response/turn/thread к total не прибавляют.

Независимая сверка сравнивает response с last_token_usage, накопленные суммы —
с turn/thread и total_token_usage; отдельный путь суммирует приращения
накопительного счётчика. Начало накопления устанавливают по исходным данным.
В данном первом turn нового журнала первый response совпадает с первыми
turn/thread/cumulative значениями. Отсутствие предшествующей записи само
по себе не является измеренным нулевым расходом.

Продолжения объединяют по явному членству в поручении и уникальным response ID.
Для нескольких поручений задают отдельные границы; общую стоимость контекста
нельзя автоматически разложить по ним. Отсутствующий компонент остаётся
неизвестным, измеренный ноль — нулём. Уменьшение накопительного счётчика
означает сброс или неоднозначную границу и не подтверждает полный итог.

Проверенные искусственные примеры: вход 10, кеш 4, выход 3 и reasoning 1
дают total **13**; идентичный повтор сохраняет 13, конфликт ID отклоняется.
Явное продолжение с входом 20 и выходом 4 даёт общий total **37**.
Накопительные 13, 13, 37 также дают 37. Продолжение без членства, отсутствие
обязательного компонента и сброс не дают подтверждённого полного итога.
Это проверки метода, а не native расходы.

## Измеренный законченный маршрут

Первый законченный turn чата 01a19ea0-de1c-7313-ae36-add58c616ad4 выполнял
просьбу дать четыре профессиональных ответа и переписать запрос о диагностике
эффективности. Такой текст доставлен. Наблюдаются проверка WSL и обращение
к первичной официальной документации. Продуктовых тестов поручение не требовало;
независимая приёмка исходного ответа неизвестна. Будущая диагностика
и контролируемый эксперимент здесь не выполнялись.

Выборка — все native строки 2–45, от первого task_started до соответствующего
task_complete; turn 01a19ea0-eb38-7632-b3a1-21a49c388ee1. Зафиксирован префикс
19 490 247 байт с приведённым ниже SHA-256; поздние дописанные записи исключены.
Модель источника — gpt-6.1-sol, effort — medium.

| Компонент | Измерение |
| --- | ---: |
| Уникальные ответы модели | 4 |
| Входные токены всех обращений | 133 492 |
| Кешированные входные, внутри входных | 82 432 |
| Отдельное поле cache-write | 0 |
| Выходные токены всех обращений | 1 317 |
| Reasoning, внутри выходных | 44 |
| Native total всего маршрута | 134 809 |

Входы: **27 651 → 31 164 → 35 259 → 39 418**, крайняя разница — **11 767**.
Это размеры входов запросов, а не измерение занятого окна. Native capacity
**475 000** тоже не измеряет фактическую заполненность.
Последний ответ содержит **1 018** выходных токенов; его размер не представляет
полный output и расход работы. Его сокращение само по себе не доказывает экономии.

Предметная граница для этой текстовой задачи выбрана явно: первый поиск
официальной документации, call_b2406676c5ad4ed6bed5beeb2b655bc3, строка 31.
До него — один response: input **27 651**, output **210**, total **27 861**,
внутри них cached **11 904** и reasoning **44**. Это классификация обращений
до выбранного действия. Подготовительные результаты инструментов могут входить
в последующие запросы; полной причинной стоимости подготовки она не определяет.

Ручная проекция операций подтверждена по ограниченному исходнику: три внешних
exec, три буквальных чтения разных файлов — сведения об ОС и два навыка,
повторных чтений этих путей **0**, вызовов ожидания **0**.
Полный inventory типов 44 записей даёт событий сжатия **0**.
Инструкции и контекст открытой страницы отделены от одного содержательного
поручения; дополнительных turn/продолжений в границах нет.
Команда ниже пересчитывает числа из опубликованных проекций, но не выводит
ручную классификацию заново из исключённых тел сообщений. Неизвестная работа
интерфейса или скрытые автоматические чтения этим числам не приписываются.

Native duration_ms — **48 312**, time_to_first_token_ms — **8 772**.
Отдельный интервал между source timestamps 2026-11-02T23:38:29.126Z
и 2026-11-02T23:39:20.045Z — **50 919 ms**.
Начало в Asia/Yekaterinburg — 3 ноября 2026 года, 04:38:29.126.
Монотонная семантика native duration не установлена; эти поля не подменяются
интервалом wall timestamps.

## Первичные проекции для воспроизведения

Ниже — точные выбранные native usage, области счётчиков, временные поля
и безопасные метаданные событий. manual_source_inventory — ручная проверенная
классификация. Роли read targets сохраняют равенство исходных буквальных
путей в выборке; частные абсолютные пути установки исключены.

event_columns задаёт порядок столбцов events. Строки — 1-based, смещения —
байтовые от начала файла; length включает исходный перевод строки,
sha256 относится к полным исходным байтам. Хеши фиксируют происхождение,
а не обещают вечную доступность полного журнала. Достаточные первичные поля
сохранены здесь и не зависят от временных материалов Task.

```json
{
  "source": {"native_basename":"rollout-2026-11-03T04-38-25-01a19ea0-de1c-7313-ae36-add58c616ad4.jsonl","prefix_bytes":19490247,"prefix_sha256":"7a5c0330d01237b30f0143c60d4d413eecbedd4d0910ba4326d4c98cd18761b9","session_id":"01a19ea0-de1c-7313-ae36-add58c616ad4","turn_id":"01a19ea0-eb38-7632-b3a1-21a49c388ee1","selection":"first task_started through matching task_complete; native lines 2-45"},
  "usage": [
    {"source":{"line":24,"offset":233880,"length":870,"sha256":"fb8f9652772e7175cd4635de45f441e2fee1564ca2340a2ddd3633b491fddfaf"},"ordinal":23,"payload":{"thread_id":"01a19ea0-de1c-7313-ae36-add58c616ad4","turn_id":"01a19ea0-eb38-7632-b3a1-21a49c388ee1","session_id":"01a19ea0-de1c-7313-ae36-add58c616ad4","root_turn_id":"01a19ea0-eb38-7632-b3a1-21a49c388ee1","response_id":"resp_095e47fc5761bf91016ae91efd2ed081918ef1120adf4ec85c","usage":{"input_tokens":27651,"cached_input_tokens":11904,"cache_write_input_tokens":0,"output_tokens":210,"reasoning_output_tokens":44,"total_tokens":27861},"turn_token_usage":{"input_tokens":27651,"cached_input_tokens":11904,"cache_write_input_tokens":0,"output_tokens":210,"reasoning_output_tokens":44,"total_tokens":27861},"thread_token_usage":{"input_tokens":27651,"cached_input_tokens":11904,"cache_write_input_tokens":0,"output_tokens":210,"reasoning_output_tokens":44,"total_tokens":27861}}},
    {"source":{"line":32,"offset":267515,"length":868,"sha256":"5ff32aa58a726d82c5a4e6e3b633e17f53c7424e6501cb04adc10d7dd8701d6e"},"ordinal":31,"payload":{"thread_id":"01a19ea0-de1c-7313-ae36-add58c616ad4","turn_id":"01a19ea0-eb38-7632-b3a1-21a49c388ee1","session_id":"01a19ea0-de1c-7313-ae36-add58c616ad4","root_turn_id":"01a19ea0-eb38-7632-b3a1-21a49c388ee1","response_id":"resp_095e47fc5761bf91016ae91f066cf08191a316124afe65abab","usage":{"input_tokens":31164,"cached_input_tokens":27648,"cache_write_input_tokens":0,"output_tokens":44,"reasoning_output_tokens":0,"total_tokens":31208},"turn_token_usage":{"input_tokens":58815,"cached_input_tokens":39552,"cache_write_input_tokens":0,"output_tokens":254,"reasoning_output_tokens":44,"total_tokens":59069},"thread_token_usage":{"input_tokens":58815,"cached_input_tokens":39552,"cache_write_input_tokens":0,"output_tokens":254,"reasoning_output_tokens":44,"total_tokens":59069}}},
    {"source":{"line":37,"offset":299924,"length":868,"sha256":"5617ba6900c82efcf7ddc4bdde4a12dabf588055cf0d95b99d614f7934922927"},"ordinal":36,"payload":{"thread_id":"01a19ea0-de1c-7313-ae36-add58c616ad4","turn_id":"01a19ea0-eb38-7632-b3a1-21a49c388ee1","session_id":"01a19ea0-de1c-7313-ae36-add58c616ad4","root_turn_id":"01a19ea0-eb38-7632-b3a1-21a49c388ee1","response_id":"resp_095e47fc5761bf91016ae91f0bfbf08191b7d2c68234d0147e","usage":{"input_tokens":35259,"cached_input_tokens":30976,"cache_write_input_tokens":0,"output_tokens":45,"reasoning_output_tokens":0,"total_tokens":35304},"turn_token_usage":{"input_tokens":94074,"cached_input_tokens":70528,"cache_write_input_tokens":0,"output_tokens":299,"reasoning_output_tokens":44,"total_tokens":94373},"thread_token_usage":{"input_tokens":94074,"cached_input_tokens":70528,"cache_write_input_tokens":0,"output_tokens":299,"reasoning_output_tokens":44,"total_tokens":94373}}},
    {"source":{"line":43,"offset":332980,"length":876,"sha256":"7aab282bb2436f68180270cf11a67d5bb9a5fa9440ed502b2c61f75f54256e0f"},"ordinal":42,"payload":{"thread_id":"01a19ea0-de1c-7313-ae36-add58c616ad4","turn_id":"01a19ea0-eb38-7632-b3a1-21a49c388ee1","session_id":"01a19ea0-de1c-7313-ae36-add58c616ad4","root_turn_id":"01a19ea0-eb38-7632-b3a1-21a49c388ee1","response_id":"resp_095e47fc5761bf91016ae91f0fa51c81918c520158e35f33cf","usage":{"input_tokens":39418,"cached_input_tokens":11904,"cache_write_input_tokens":0,"output_tokens":1018,"reasoning_output_tokens":0,"total_tokens":40436},"turn_token_usage":{"input_tokens":133492,"cached_input_tokens":82432,"cache_write_input_tokens":0,"output_tokens":1317,"reasoning_output_tokens":44,"total_tokens":134809},"thread_token_usage":{"input_tokens":133492,"cached_input_tokens":82432,"cache_write_input_tokens":0,"output_tokens":1317,"reasoning_output_tokens":44,"total_tokens":134809}}}
  ],
  "token_count": [
    {"source":{"line":30,"offset":266161,"length":812,"sha256":"28cc06f2d91a7dacaca5a70f1d54387fadd0df2a364d529a1067d0fab5c4a8ff"},"info":{"total_token_usage":{"input_tokens":27651,"cached_input_tokens":11904,"cache_write_input_tokens":0,"output_tokens":210,"reasoning_output_tokens":44,"total_tokens":27861},"last_token_usage":{"input_tokens":27651,"cached_input_tokens":11904,"cache_write_input_tokens":0,"output_tokens":210,"reasoning_output_tokens":44,"total_tokens":27861},"model_context_window":475000}},
    {"source":{"line":35,"offset":298577,"length":810,"sha256":"0ed9c9337953d1a05df6c3c05595091d5a9db4aa0b49fd57183ebd6a32f253b5"},"info":{"total_token_usage":{"input_tokens":58815,"cached_input_tokens":39552,"cache_write_input_tokens":0,"output_tokens":254,"reasoning_output_tokens":44,"total_tokens":59069},"last_token_usage":{"input_tokens":31164,"cached_input_tokens":27648,"cache_write_input_tokens":0,"output_tokens":44,"reasoning_output_tokens":0,"total_tokens":31208},"model_context_window":475000}},
    {"source":{"line":40,"offset":315729,"length":810,"sha256":"26419d81546012ee9aa14a28fcdc178944a1152273877948227e39a365868584"},"info":{"total_token_usage":{"input_tokens":94074,"cached_input_tokens":70528,"cache_write_input_tokens":0,"output_tokens":299,"reasoning_output_tokens":44,"total_tokens":94373},"last_token_usage":{"input_tokens":35259,"cached_input_tokens":30976,"cache_write_input_tokens":0,"output_tokens":45,"reasoning_output_tokens":0,"total_tokens":35304},"model_context_window":475000}},
    {"source":{"line":44,"offset":333856,"length":815,"sha256":"23bdc34d6131b7204a1b9e15c5772a92169691f649c5cf776b7cf74f5a6ce4ef"},"info":{"total_token_usage":{"input_tokens":133492,"cached_input_tokens":82432,"cache_write_input_tokens":0,"output_tokens":1317,"reasoning_output_tokens":44,"total_tokens":134809},"last_token_usage":{"input_tokens":39418,"cached_input_tokens":11904,"cache_write_input_tokens":0,"output_tokens":1018,"reasoning_output_tokens":0,"total_tokens":40436},"model_context_window":475000}}
  ],
  "context": {"source":{"line":10,"offset":210931,"length":2060,"sha256":"daf019c677e6b76e823c3ae6cff46c3df81615defa732bf1bf0d83454b5d7306"},"model":"gpt-6.1-sol","effort":"medium"},
  "start": {"source":{"line":2,"offset":22748,"length":500,"sha256":"a6a0eb85b8411567311a1ee2e0f0dade727b592d9b8562d99858c38f8c3d1cb6"},"timestamp":"2026-11-02T23:38:29.126Z","type":"task_started","turn_attribution":{"turn_id":"01a19ea0-eb38-7632-b3a1-21a49c388ee1","turn_trigger":"composer","parent_turn_id":null,"initiating_agent_path":null,"root_turn_id":"01a19ea0-eb38-7632-b3a1-21a49c388ee1"},"turn_id":"01a19ea0-eb38-7632-b3a1-21a49c388ee1","root_turn_id":"01a19ea0-eb38-7632-b3a1-21a49c388ee1","started_at":1793662709,"model_context_window":475000,"collaboration_mode_kind":"default"},
  "completion": {"source":{"line":45,"offset":334671,"length":7959,"sha256":"1ccfccc306de8ae134fee741cb985fab4bb2131609c7316059d688f47fffbc83"},"timestamp":"2026-11-02T23:39:20.045Z","type":"task_complete","root_turn_id":"01a19ea0-eb38-7632-b3a1-21a49c388ee1","turn_id":"01a19ea0-eb38-7632-b3a1-21a49c388ee1","started_at":1793662709,"completed_at":1793662760,"duration_ms":48312,"time_to_first_token_ms":8772},
  "event_columns": [
    "line",
    "record_type",
    "payload_type",
    "role",
    "call_id",
    "offset",
    "length",
    "sha256"
  ],
  "events": [
    [2,"event_msg","task_started",null,null,22748,500,"a6a0eb85b8411567311a1ee2e0f0dade727b592d9b8562d99858c38f8c3d1cb6"],
    [3,"response_item","additional_tools","developer",null,23248,37887,"7a99d0ee74bd3922a4c1f03a685a9439f9dc076a35854665fa01b64e8aa30313"],
    [4,"response_item","message","developer",null,61135,22248,"47776830ac6b09e5971ee29dd5b44e4f0110c05a8c90df5df64e43a0ca2df1d9"],
    [5,"response_item","message","developer",null,83383,31114,"48114bfaf184c1e7b859bfcd2a88edc3eb0548e5e1edfd6982e5875d087c6501"],
    [6,"response_item","message","developer",null,114497,2921,"f3438c04abdc93309a718b5907ae46311990e61b07ee7b88061dda391b933aa6"],
    [7,"response_item","message","developer",null,117418,756,"05fa3f833f88619e796d4fb60f6d60f3cbba9b9024a629afba1eed62e2671a27"],
    [8,"response_item","message","user",null,118174,35603,"c4fb7b71fc9c8f8274f2981f390fa4475c6a0ab1f29a732d1ab0bdc34f17d2c4"],
    [9,"world_state",null,null,null,153777,57154,"e2bddaf493da1860785c589790deaafefad8c88ac67f060e08f678b3270b302e"],
    [10,"turn_context",null,null,null,210931,2060,"daf019c677e6b76e823c3ae6cff46c3df81615defa732bf1bf0d83454b5d7306"],
    [11,"event_msg","thread_settings_applied",null,null,212991,2213,"e24c3475165511008f3e5f0d8aec2cadf2b1eb022dd26ef0d6909a20e9ffcef7"],
    [12,"response_item","message","developer",null,215204,1755,"09d01cc2745d9d41894c960953440b4d95d2f8470d1878b65118bffc55e207ce"],
    [13,"response_item","message","developer",null,216959,772,"facfffbfa7bbbaf46c3de485d7a48e7750e9ad2b845b7ecae795d2c3313d499d"],
    [14,"response_item","message","user",null,217731,560,"8268cf7252b4132292e2ab221777c3eced45ef8d8eefe39194254193d7d836cc"],
    [15,"response_item","message","developer",null,218291,963,"fa39aad1a11ae26145d99d283e685f6394b710bc9d2971a618c25d6eea27f898"],
    [16,"response_item","message","user",null,219254,4060,"690b40e205221c8fc3e6fd88a366fb67cdf576573e9817fe9063ba60da6e6910"],
    [17,"event_msg","item_completed",null,null,223314,3765,"a0cd5dba1dc1dcc8f18035b239cb4e655db168e5d79423ab400f5981e67e976b"],
    [18,"response_item","message","developer",null,227079,1332,"6fc8bf51440eff46dc1173a1fbf2cade10f6ec9cc1013644f9b2a0944a7b6355"],
    [19,"event_msg","item_completed",null,null,228411,395,"4cdc67ea2470823f1d72e4244a11b4ca92409c5ae2c1475cf404786609ed02a0"],
    [20,"response_item","reasoning",null,null,228806,2425,"27939dbfe561b815d5b0e305d79a748dea23dbac3599f379aef578043ba946b2"],
    [21,"event_msg","item_completed",null,null,231231,785,"8233f6a565e5ba2b79c3729ac9b2f3b477c30d6a155b81c49cc436be80b62c85"],
    [22,"response_item","message","assistant",null,232016,1127,"00e30226eb998313646d3da913c5d1ff389c572c8daeb1d4383692ad89f989a6"],
    [23,"response_item","custom_tool_call",null,"call_523546b7ae6b4132bb2a7c3cdd017a79",233143,737,"ce22811cdc6206bb64f29c1709afd12040686eb8e601c821b4ae050a35195883"],
    [24,"token_usage_record",null,null,null,233880,870,"fb8f9652772e7175cd4635de45f441e2fee1564ca2340a2ddd3633b491fddfaf"],
    [25,"event_msg","item_completed",null,null,234750,799,"042c15a94b97fc72ec753e2e0b8253d50e152779b6512850e2bf43d887e882a3"],
    [26,"response_item","message","developer",null,235549,505,"67dd338ff1652ec0fd9a7c264d746ac4bfdeae1cebc2f5dd39f00e8ca1a258b8"],
    [27,"event_msg","item_completed",null,null,236054,14707,"e54e212c7c121e3c64f505a25bab0ee9c82e4a567e55d5546c2e63e393467c78"],
    [28,"response_item","message","developer",null,250761,507,"b987a17dc88f42deb7ef524cc9ceb5d872823b6f5392869a8c3ee351c33b0077"],
    [29,"response_item","custom_tool_call_output",null,"call_523546b7ae6b4132bb2a7c3cdd017a79",251268,14893,"83d8327ee25d771b7584dccfb60cee208313158dc8508c42aff472e0b20842cf"],
    [30,"event_msg","token_count",null,null,266161,812,"28cc06f2d91a7dacaca5a70f1d54387fadd0df2a364d529a1067d0fab5c4a8ff"],
    [31,"response_item","custom_tool_call",null,"call_b2406676c5ad4ed6bed5beeb2b655bc3",266973,542,"5d8d2f9c84a1ad1562ab68ff3af019c6b9db315ebe3bfd1653b40ff6102d8773"],
    [32,"token_usage_record",null,null,null,267515,868,"5ff32aa58a726d82c5a4e6e3b633e17f53c7424e6501cb04adc10d7dd8701d6e"],
    [33,"event_msg","item_completed",null,null,268383,11431,"10cc361becb158f3d901c8743d582461d3d5deb1e2aaeb85164cbe5b0de307e3"],
    [34,"response_item","custom_tool_call_output",null,"call_b2406676c5ad4ed6bed5beeb2b655bc3",279814,18763,"17ca94882169dc49818861e90e560dd12537f08fad4906e00a3ea4bedc1a9c06"],
    [35,"event_msg","token_count",null,null,298577,810,"0ed9c9337953d1a05df6c3c05595091d5a9db4aa0b49fd57183ebd6a32f253b5"],
    [36,"response_item","custom_tool_call",null,"call_99ff19f77f7f4c72a850fdb636a6cd71",299387,537,"41da1751a51e48308a1bcd298039e6a7b1968c1795dff68b4254bcf58a3cfad9"],
    [37,"token_usage_record",null,null,null,299924,868,"5617ba6900c82efcf7ddc4bdde4a12dabf588055cf0d95b99d614f7934922927"],
    [38,"event_msg","item_completed",null,null,300792,913,"60ae373525e1195b849f1b9217ed72ef62c2c9f2f5da83ab936b0dad04553d29"],
    [39,"response_item","custom_tool_call_output",null,"call_99ff19f77f7f4c72a850fdb636a6cd71",301705,14024,"27afa9b874481205229e2b2f30d5ec501ea0de5d8b204f96b85aa5591edcb459"],
    [40,"event_msg","token_count",null,null,315729,810,"26419d81546012ee9aa14a28fcdc178944a1152273877948227e39a365868584"],
    [41,"event_msg","item_completed",null,null,316539,8049,"b0faf3367fdc0cf49f80b49bfd865638c38a90ddd5bfdbd4d858741873c8052f"],
    [42,"response_item","message","assistant",null,324588,8392,"70e8fb57e1c98d86d95397c2a784f87074b1619ab14329481c58649afc9588d8"],
    [43,"token_usage_record",null,null,null,332980,876,"7aab282bb2436f68180270cf11a67d5bb9a5fa9440ed502b2c61f75f54256e0f"],
    [44,"event_msg","token_count",null,null,333856,815,"23bdc34d6131b7204a1b9e15c5772a92169691f649c5cf776b7cf74f5a6ce4ef"],
    [45,"event_msg","task_complete",null,null,334671,7959,"1ccfccc306de8ae134fee741cb985fab4bb2131609c7316059d688f47fffbc83"]
  ],
  "manual_source_inventory": {"meaning":"Source-bounded human classification; full prompts/commands are not retained.","user_messages":[{"source":{"line":8,"offset":118174,"length":35603,"sha256":"c4fb7b71fc9c8f8274f2981f390fa4475c6a0ab1f29a732d1ab0bdc34f17d2c4"},"classification":"supplied_instructions_environment"},{"source":{"line":14,"offset":217731,"length":560,"sha256":"8268cf7252b4132292e2ab221777c3eced45ef8d8eefe39194254193d7d836cc"},"classification":"open_page_context"},{"source":{"line":16,"offset":219254,"length":4060,"sha256":"690b40e205221c8fc3e6fd88a366fb67cdf576573e9817fe9063ba60da6e6910"},"classification":"substantive_assignment"}],"result":{"source":{"line":42,"offset":324588,"length":8392,"sha256":"70e8fb57e1c98d86d95397c2a784f87074b1619ab14329481c58649afc9588d8"},"kind":"four professional-framing answers and rewritten prompt","independent_original_acceptance":null},"outer_calls":[{"source":{"line":23,"offset":233143,"length":737,"sha256":"ce22811cdc6206bb64f29c1709afd12040686eb8e601c821b4ae050a35195883"},"call_id":"call_523546b7ae6b4132bb2a7c3cdd017a79","name":"exec","operations":[{"kind":"host_check","tool":"exec_command","reads":["osrelease"]},{"kind":"instruction_read","tool":"exec_command","reads":["openai-docs-skill","adhd-skill"]}]},{"source":{"line":31,"offset":266973,"length":542,"sha256":"5d8d2f9c84a1ad1562ab68ff3af019c6b9db315ebe3bfd1653b40ff6102d8773"},"call_id":"call_b2406676c5ad4ed6bed5beeb2b655bc3","name":"exec","operations":[{"kind":"subject_search","tool":"web__run","reads":[]}]},{"source":{"line":36,"offset":299387,"length":537,"sha256":"41da1751a51e48308a1bcd298039e6a7b1968c1795dff68b4254bcf58a3cfad9"},"call_id":"call_99ff19f77f7f4c72a850fdb636a6cd71","name":"exec","operations":[{"kind":"subject_open","tool":"web__run","reads":[]}]}],"tool_results":[{"source":{"line":29,"offset":251268,"length":14893,"sha256":"83d8327ee25d771b7584dccfb60cee208313158dc8508c42aff472e0b20842cf"},"call_id":"call_523546b7ae6b4132bb2a7c3cdd017a79"},{"source":{"line":34,"offset":279814,"length":18763,"sha256":"17ca94882169dc49818861e90e560dd12537f08fad4906e00a3ea4bedc1a9c06"},"call_id":"call_b2406676c5ad4ed6bed5beeb2b655bc3"},{"source":{"line":39,"offset":301705,"length":14024,"sha256":"27afa9b874481205229e2b2f30d5ec501ea0de5d8b204f96b85aa5591edcb459"},"call_id":"call_99ff19f77f7f4c72a850fdb636a6cd71"}],"observed_checks":[{"source_line":29,"kind":"WSL_identity","terminal_exit_code":0},{"source_line":29,"kind":"skill_reads","terminal_exit_code":0},{"source_line":34,"kind":"official_documentation_search"},{"source_line":39,"kind":"official_documentation_open","url":"https://developers.openai.com/api/docs/guides/compaction"}],"substantive_boundary_call":"call_b2406676c5ad4ed6bed5beeb2b655bc3","read_target_roles":{"osrelease":"OS identity /proc/sys/kernel/osrelease","openai-docs-skill":"openai-docs/SKILL.md; installation path omitted","adhd-skill":".agents/skills/i-have-adhd/SKILL.md"}}
}
```

## Воспроизведение расчёта

Из корня checkout с Python 3.10 или новее выполните:

```bash
python3 - <<'PY'
import json
from collections import Counter
from datetime import datetime
from pathlib import Path

text = Path("docs/operations/token-efficiency-measurement.md").read_text()
fence = chr(96) * 3
d = json.loads(text.split(fence + "json\n", 1)[1].split("\n" + fence, 1)[0])
fields = ("input_tokens", "cached_input_tokens", "cache_write_input_tokens",
          "output_tokens", "reasoning_output_tokens", "total_tokens")
responses = [item["payload"] for item in d["usage"]]
assert len({(r["thread_id"], r["response_id"]) for r in responses}) == len(responses)
assert all(r["turn_id"] == d["source"]["turn_id"] for r in responses)
origin = responses[0]
assert origin["usage"] == origin["turn_token_usage"] == origin["thread_token_usage"]
assert origin["usage"] == d["token_count"][0]["info"]["total_token_usage"]
running = dict.fromkeys(fields, 0)
delta_sum = dict(d["token_count"][0]["info"]["total_token_usage"])
previous = None
for r, c in zip(responses, d["token_count"], strict=True):
    u, info = r["usage"], c["info"]
    assert all(type(u.get(k)) is int and u[k] >= 0 for k in fields)
    assert u["total_tokens"] == u["input_tokens"] + u["output_tokens"]
    assert u["cached_input_tokens"] <= u["input_tokens"]
    assert u["reasoning_output_tokens"] <= u["output_tokens"]
    assert u == info["last_token_usage"]
    for k in fields:
        running[k] += u[k]
        if previous is not None:
            assert info["total_token_usage"][k] >= previous[k]
            delta_sum[k] += info["total_token_usage"][k] - previous[k]
    assert running == r["turn_token_usage"] == r["thread_token_usage"] == info["total_token_usage"]
    previous = info["total_token_usage"]
assert running == delta_sum
events = [dict(zip(d["event_columns"], row, strict=True)) for row in d["events"]]
assert [e["line"] for e in events] == list(range(2, 46))
assert all(a["offset"] + a["length"] == b["offset"] for a, b in zip(events, events[1:]))
inventory = d["manual_source_inventory"]
calls, outputs = inventory["outer_calls"], inventory["tool_results"]
assert {c["call_id"] for c in calls} == {o["call_id"] for o in outputs}
assert {c["call_id"] for c in calls} == {e["call_id"] for e in events if e["payload_type"] == "custom_tool_call"}
reads = Counter(target for c in calls for op in c["operations"] for target in op["reads"])
boundary = next(c["source"]["line"] for c in calls if c["call_id"] == inventory["substantive_boundary_call"])
prep = [r for r in d["usage"] if r["source"]["line"] < boundary]
start = datetime.fromisoformat(d["start"]["timestamp"].replace("Z", "+00:00"))
end = datetime.fromisoformat(d["completion"]["timestamp"].replace("Z", "+00:00"))
result = {"totals": running, "response_count": len(responses),
          "request_inputs": [r["usage"]["input_tokens"] for r in responses],
          "last_minus_first_input": responses[-1]["usage"]["input_tokens"] - origin["usage"]["input_tokens"],
          "last_response_output": responses[-1]["usage"]["output_tokens"],
          "pre_subject_usage": {k: sum(r["payload"]["usage"][k] for r in prep) for k in fields},
          "read_target_counts": dict(reads),
          "observed_turns": sum(e["payload_type"] == "task_started" for e in events),
          "model_context_capacity": d["start"]["model_context_window"],
          "pre_subject_responses": len(prep),
          "pre_subject_total": sum(r["payload"]["usage"]["total_tokens"] for r in prep),
          "outer_calls": len(calls), "literal_reads": sum(reads.values()),
          "repeated_literal_reads": sum(n - 1 for n in reads.values()),
          "wait_operations": sum(op["kind"] == "wait" for c in calls for op in c["operations"]),
          "compaction_events": sum(e["record_type"] == "compacted" for e in events),
          "substantive_assignments": sum(m["classification"] == "substantive_assignment" for m in inventory["user_messages"]),
          "native_duration_ms": d["completion"]["duration_ms"],
          "native_time_to_first_token_ms": d["completion"]["time_to_first_token_ms"],
          "wall_timestamp_interval_ms": round((end - start).total_seconds() * 1000)}
assert result["totals"]["total_tokens"] == 134809
print(json.dumps(result, ensure_ascii=False, indent=2))
PY
```

## Сопоставление и допустимые выводы

Для сравнения фиксируют одинаковые поручение, результат, обязательные проверки,
исходный код при его наличии, модель, настройки, окружение и границы данных.
Изменившиеся условия показывают явно. Историческая разница при несопоставимых
условиях позволяет сформулировать гипотезу, но не устанавливает причину
и величину экономии.

Контролируемого второго маршрута здесь нет. Измерена стоимость одного
законченного текстового поручения с его наблюдаемыми проверками.
Причинное влияние Poise, экономия всей работы, счёт провайдера, стоимость
конкретного инструмента и фактическое заполнение контекста не установлены.

Исходные границы и расчёт независимо проверены. Правила кеша, reasoning,
продолжений, нескольких поручений, дубликатов и неполных данных проверены
искусственными примерами, отдельно от native измерения.
Для новой работы результат и достаточные обязательные проверки определяют
заново; локальное сокращение ответа не заменяет сравнение законченной работы.
