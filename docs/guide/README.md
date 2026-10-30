# Учебные материалы AI DevOps Agent

[← README проекта](../../README.md) · [CI/CD подробно](../CI_CD.md)

Подробные руководства — [STAGE_01.md](../STAGE_01.md),
[STAGE_02.md](../STAGE_02.md), [STAGE_03.md](../STAGE_03.md) и
[STAGE_04.md](../STAGE_04.md), [STAGE_05.md](../STAGE_05.md), [STAGE_06.md](../STAGE_06.md),
[STAGE_07.md](../STAGE_07.md), [STAGE_08.md](../STAGE_08.md) и [STAGE_09.md](../STAGE_09.md).
Они объясняют запуск API, настройки, предметную модель, доказательства,
ограниченный ввод, обезличивание, CLI, Docker-лабораторию, сборщик, хранение
расследований, историю деплоев, ограниченного агента LangGraph, метрики
и трассировки, HTTP API, оценки, контейнер, подключение модели Claude, тесты
и типичные ошибки.
План следующих этапов и условия их готовности — в
[дорожной карте](../ROADMAP.md).

Текущая проверка — **30 октября 2026 года**, этап 09: модель Claude за портом
агента, включаемая только явно. Фактические числа
тестов, ссылок и статус удалённого CI хранятся в [verification.json](verification.json)
и [VERIFICATION.md](../VERIFICATION.md); эта страница их не заменяет.

## Какие файлы составляют материалы

| Файл | Назначение |
| --- | --- |
| [STAGE_01.md](../STAGE_01.md) | Подробное руководство этапа 01 на русском |
| [STAGE_02.md](../STAGE_02.md) | Предметная модель и воспроизводимый пример на вымышленных данных |
| [STAGE_03.md](../STAGE_03.md) | Формат входа, ограничения, обезличивание и офлайн-расследование |
| [STAGE_04.md](../STAGE_04.md) | Docker-лаборатория, граница доступа к daemon и сборщик наблюдений |
| [STAGE_05.md](../STAGE_05.md) | SQLite-снимки, хеши, повторный просмотр и история деплоев |
| [STAGE_06.md](../STAGE_06.md) | Граф LangGraph, инструменты чтения, лимиты и проверка ответа агента |
| [STAGE_07.md](../STAGE_07.md) | Метрики, трассировки, пропуски корреляции и лаборатория телеметрии |
| [STAGE_08.md](../STAGE_08.md) | HTTP API с токенами, оценки и поставка контейнера |
| [STAGE_09.md](../STAGE_09.md) | Claude за портом агента: настройки, диалог, ошибки и передача данных |
| [ROADMAP.md](../ROADMAP.md) | Этапы, статусы и условия готовности |
| [CI_CD.md](../CI_CD.md) | Подробное руководство по CI и публикации этапа |
| [instructions/](../instructions/) | Краткие английские инструкции: [запуск](../instructions/quickstart.md), [API](../instructions/usage.md), [проверки](../instructions/testing.md), [CI](../instructions/ci-and-delivery.md), [структура](../instructions/project-structure.md), [лаборатория](../instructions/docker-lab.md), [хранение и Git-история](../instructions/investigation-history.md), [агент](../instructions/bounded-agent.md), [телеметрия](../instructions/telemetry.md), [оценки](../instructions/evaluations.md), [контейнер](../instructions/container.md) |
| [APPROACH.md](../APPROACH.md), [ADR 0001](../adr/0001-staged-read-only-incident-investigation.md), [ADR 0002](../adr/0002-docker-lab-collector-access.md), [ADR 0003](../adr/0003-immutable-investigation-records.md), [ADR 0004](../adr/0004-recorded-lab-deployments.md), [ADR 0005](../adr/0005-bounded-langgraph-agent.md), [ADR 0006](../adr/0006-allowlisted-telemetry.md), [ADR 0007](../adr/0007-token-scoped-api-and-container.md), [ADR 0008](../adr/0008-claude-agent-provider.md) | Принципы, границы и принятые решения |
| [DEVELOPMENT_LOG.md](../DEVELOPMENT_LOG.md) | Что и когда изменилось |
| [VERIFICATION.md](../VERIFICATION.md) | Фактические проверки, история и ограничения |
| [verification.json](verification.json) | Машиночитаемая сводка той же проверки |

## Формат материалов

На этапах 01–09 руководства распространяются в Markdown. PDF-генератор и PDF-файлы
не создаются. При добавлении такого формата потребуется отдельная проверка
содержания и визуального результата; наличие Markdown не означает наличие PDF.

## Как обновлять проверенные сведения

1. Заверши изменения и выполни полный прогон по
   [инструкции тестирования](../instructions/testing.md).
2. Запиши фактические результаты в `verification.json`: дата, исходный коммит,
   число тестов и пропусков, проверки и ограничения. Прежнюю проверку перенеси
   в `previous_checks`, не меняя её данных.
3. Укажи удалённый статус отдельно. `not_verified` означает, что запуск GitHub
   Actions для итогового коммита ещё не подтверждён. Локальный успех не заменяет
   результат Actions.
4. Обнови [VERIFICATION.md](../VERIFICATION.md) и
   [журнал разработки](../DEVELOPMENT_LOG.md) теми же фактами.
5. Закоммить и отправь проверенный этап по [правилам публикации](../CI_CD.md).

В JSON и в документацию не помещают токены, пароли, реальные логи или данные
настоящих инцидентов. Материалы следующих этапов добавляются вместе с их
реализацией.
