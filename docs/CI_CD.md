# CI/CD: проверки проекта и публикация этапа

[← README](../README.md) · [Кратко на английском](instructions/ci-and-delivery.md) · [Проверки](instructions/testing.md) · [Результаты](VERIFICATION.md)

Эта инструкция объясняет, что запускает GitHub после отправки изменений,
в каком порядке выполняются проверки и как завершить этап коммитом и push.

**Текущая цель:** после push в `main`, pull request в `main` или ручного
запуска проверить проект в чистом окружении GitHub: зависимости из `uv.lock`,
ошибки кода, форматирование, типы, тесты и локальные ссылки документации.

CD — Continuous Delivery, доставка готового результата. На этапах 01–05 она
не настроена: Docker-образ не собирается, реестр и сервер не используются.
«Публикация этапа» здесь означает commit и push проверенного кода в репозиторий.
Сборка контейнера и описанный путь доставки запланированы на этап 08.

## Подтверждённый запуск

Подтверждённого запуска пока нет. Результат GitHub Actions для коммитов этого
репозитория не наблюдался: при проверках GitHub API возвращал `404`, поэтому
статус удалённого CI — `not_verified`. Фактическое состояние для каждого
коммита фиксируется в [VERIFICATION.md](VERIFICATION.md).

Наличие `.github/workflows/ci.yml` и успешные локальные проверки не означают,
что workflow прошёл на GitHub. Удалённый результат считается подтверждённым
только после просмотра запуска для конкретного коммита.

## 1. Какие слова нужно понимать

| Слово | Значение в этом проекте |
| --- | --- |
| Репозиторий | Папка проекта с историей Git; её копия хранится на GitHub. |
| Commit | Сохранённая версия изменений в локальной истории Git. |
| Push | Отправка локальных коммитов в репозиторий GitHub. |
| Pull request, PR | Предложение включить изменения из другой ветки в `main`. |
| CI | Автоматическая проверка изменений: зависимости, стиль, типы, тесты, ссылки. |
| CD | Доставка проверенного результата; на этапах 01–05 не настроена. |
| Workflow | Сценарий GitHub Actions из файла `.github/workflows/ci.yml`. |
| Job | Отдельное задание внутри workflow; здесь оно одно — `checks`. |
| Step | Шаг внутри задания: например, установить Python или запустить тесты. |
| Runner | Временная машина GitHub, на которой выполняется задание. |
| Action | Готовый шаг из другого репозитория, например `actions/checkout`. |
| Lock-файл | `uv.lock`: точные версии и хеши всех зависимостей. |

**Локальный `git commit` сам по себе не запускает GitHub Actions.**
GitHub увидит изменения только после `git push`. Поэтому «проверять при
коммите в main» здесь означает: **проверять каждый push, который обновляет
`main` на GitHub**.

## 2. Когда запускается pipeline

Pipeline — последовательность автоматических проверок.

| Событие | Запускает проверки |
| --- | --- |
| `push` в `main` | Да |
| Создание или обновление PR в `main` | Да |
| Ручной запуск `Run workflow` | Да |
| `push` в другую ветку без PR в `main` | Нет |
| Локальный `git commit` без `push` | Нет |

В событии `pull_request` фильтр `branches: [main]` относится к **целевой**
ветке PR: PR из `feature/domain` в `main` запускает проверки. Изменения только
в документации тоже запускают pipeline — исключений по путям нет, а проверка
ссылок входит в задание.

Ручной запуск повторяет проверки выбранной ветки. GitHub показывает кнопку
`Run workflow`, когда workflow с `workflow_dispatch` уже находится в ветке
по умолчанию.

## 3. В каком порядке всё выполняется

Задание `checks` называется в интерфейсе **Lint, types, tests and
documentation**. Оно выполняется на `ubuntu-24.04` и ограничено 10 минутами.

| Шаг | Что делает | Что означает ошибка |
| --- | --- | --- |
| Checkout repository | `actions/checkout` v7.0.1 скачивает коммит без сохранения токена в `.git/config` | Коммит недоступен runner |
| Install uv and Python | `astral-sh/setup-uv` v10.3.0 ставит uv 0.12.23 и Python 3.12, кеширует пакеты по `uv.lock` | Не удалось подготовить инструменты |
| Install locked dependencies | `uv sync --locked` | `uv.lock` не соответствует `pyproject.toml` или пакет недоступен |
| Lint | `uv run --locked ruff check .` | Ошибка кода или порядка импортов |
| Check formatting | `uv run --locked ruff format --check .` | Файл оформлен не по правилам; файлы не изменяются |
| Check types | `uv run --locked mypy app tests scripts` | Несогласованные аннотации типов |
| Run tests | `uv run --locked pytest` | Нарушен проверяемый контракт поведения |
| Check documentation links | `uv run --locked python scripts/check_docs.py` | Ссылка Markdown ведёт на отсутствующий файл |

Шаги идут по очереди. Если шаг упал, следующие пропускаются, а весь запуск
получает статус ошибки. Поэтому первая красная строка в логе обычно и есть
причина.

## 4. Зачем нужны `permissions`, `concurrency` и закреплённые SHA

- `permissions: contents: read` — автоматический `GITHUB_TOKEN` может только
  читать содержимое репозитория. Проверкам не нужны права записи.
- `persist-credentials: false` — токен не остаётся в настройках Git на runner,
  поэтому следующие шаги не могут случайно выполнить push.
- `concurrency` с `cancel-in-progress: true` — новый запуск для той же ветки
  или PR отменяет устаревший. Отменённый запуск — не ошибка кода.
- `timeout-minutes: 10` — зависшее задание не занимает runner бесконечно.
- Actions закреплены полным SHA коммита, а версия указана в комментарии.
  Тег можно перенести на другой код, SHA — нельзя. Соответствие SHA
  официальным тегам проверено через `git ls-remote`; результат записан
  в [отчёте](VERIFICATION.md).

## 5. Какие файлы за что отвечают

| Файл | Роль в CI |
| --- | --- |
| `.github/workflows/ci.yml` | События, права, задание и его шаги |
| `pyproject.toml` | Допустимые зависимости и настройки Ruff, mypy и pytest |
| `uv.lock` | Точные версии; `--locked` запрещает его незаметное изменение |
| `.python-version` | Python 3.12 для локального uv |
| `tests/` | Проверки поведения без `.env`, сети, Docker и ключей |
| `scripts/check_docs.py` | Проверка локальных целей ссылок в README, AGENTS и `docs/` |

## 6. Как опубликовать завершённый этап

По требованию пользователя каждый завершённый этап заканчивается коммитом
и push. Команды выполняются из корня проекта.

1. Выполни все [локальные проверки](instructions/testing.md).
2. Обнови инструкцию этапа, затронутые инструкции, [журнал](DEVELOPMENT_LOG.md),
   [отчёт](VERIFICATION.md) и [verification.json](guide/verification.json).
3. Посмотри изменения и убедись, что в них нет `.env`, токенов и реальных логов:

   ```bash
   git status --short
   git diff --check
   ```

4. Подготовь файлы и проверь, что попало в коммит:

   ```bash
   git add -A
   git status --short
   git diff --cached --check
   ```

5. Создай коммит с понятным английским сообщением, например
   `git commit -m "Add incident domain model"`.
6. Перед push проверь состояние удалённой ветки:

   ```bash
   git fetch origin
   git status -sb
   ```

   Если `main` на GitHub ушла вперёд, сначала объедини изменения без потери
   чужой работы, например `git pull --rebase origin main`, и повтори проверки.
   **Не используй `git push --force`.**
7. Отправь коммит и сверь SHA:

   ```bash
   git push origin main
   git rev-parse HEAD
   git ls-remote origin refs/heads/main
   ```

   Оба SHA должны совпасть. Только после этого смотри результат Actions.

## 7. Где смотреть результат

На GitHub открой репозиторий → **Actions** → workflow **CI** → запуск
с SHA своего коммита.

| Статус | Как записать |
| --- | --- |
| Queued, In progress | `pending` — результат ещё не известен |
| Success | Успешно, с указанием SHA коммита и запуска |
| Failure | Ошибка: шаг и сообщение из лога |
| Cancelled | Отменён более новым запуском; смотри следующий запуск |
| Запуск недоступен | `not_verified` с причиной |

Не переноси успешный результат старого коммита на новый: каждый push
проверяется отдельно.

## 8. Как выполнить проверки локально

Те же шаги, что в CI:

```bash
uv sync --locked
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked mypy app tests scripts
uv run --locked pytest
uv run --locked python scripts/check_docs.py
```

Подробности и ручная HTTP-проверка — в
[инструкции тестирования](instructions/testing.md).

## 9. Что делать, если pipeline красный

| Упавший шаг | Что сделать |
| --- | --- |
| Install locked dependencies | Если менялся `pyproject.toml`, намеренно выполни `uv lock`, проверь diff `uv.lock` и закоммить оба файла. Не убирай `--locked` |
| Lint | Запусти `uv run --locked ruff check .`; автоисправление `--fix` применяй только после просмотра |
| Check formatting | Выполни `uv run --locked ruff format .` и просмотри изменения |
| Check types | Запусти `uv run --locked mypy app tests scripts` и исправь аннотации или код |
| Run tests | Запусти `uv run --locked pytest -x -vv`; тест не должен зависеть от личного `.env` |
| Check documentation links | Исправь путь в Markdown или добавь недостающий файл |
| Cancelled | Ничего не исправлять: дождись более нового запуска |

После исправления повтори локальные проверки, создай новый коммит и push.
Не переписывай опубликованную историю, чтобы скрыть ошибку.

## 10. Как pipeline будет развиваться

Новые проверки добавляются вместе с поведением, которое им нужно проверять,
по [дорожной карте](ROADMAP.md). Например, сборка и проверка контейнера
появятся на этапе 08. Пока такие шаги не добавлены, документация не называет
их работающими.

## 11. Официальные источники

- [Синтаксис workflow GitHub Actions](https://docs.github.com/en/actions/using-workflows/workflow-syntax-for-github-actions)
- [События, запускающие workflow](https://docs.github.com/en/actions/using-workflows/events-that-trigger-workflows)
- [Права `GITHUB_TOKEN`](https://docs.github.com/en/actions/security-guides/automatic-token-authentication)
- [Безопасность и закрепление actions по SHA](https://docs.github.com/en/actions/security-guides/security-hardening-for-github-actions)
- [Управление `concurrency`](https://docs.github.com/en/actions/using-jobs/using-concurrency)
- [uv в GitHub Actions](https://docs.astral.sh/uv/guides/integration/github/)
- [actions/checkout](https://github.com/actions/checkout) и
  [astral-sh/setup-uv](https://github.com/astral-sh/setup-uv)
