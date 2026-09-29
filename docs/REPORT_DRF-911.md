# ОТЧЁТ окна DRF-911 → главное окно

**Задача:** клиент не может посмотреть свои записи (глобальный путь / LLM-консьерж).
**Бриф:** `IMPL_BRIEF_DRF-911.md`. Окно ведёт отчёт правками (новые секции — снизу по ходу работы).

---

## Старт окна (2026-08-12)

- Протокол прочитан: `WINDOW_PROTOCOL.md` (VERIFIED).
- REPLY прочитан: секция №0, ответов нет (VERIFIED).
- Монитор на `REPLY_DRF-911.md` поставлен (md5, 30 с); доставка уведомлений проверена пробой на scratch-файле — механизм срабатывает (VERIFIED).
- Живая бронь владельца (14 августа, «УЗ-кавитация») — только чтение, принято.

Дальше: сверка базы `origin/dev`, ветка `fix/drf911-booking-lookup-global`, изучение кода, решение по §3.

---

## База и ветка (2026-08-12)

- `git fetch origin`: `origin/dev` = `1ea47b915090cedb56698e42e1db122473965392` — совпал с брифом (VERIFIED).
- Ветка `fix/drf911-booking-lookup-global` создана от `origin/dev`, HEAD = `1ea47b9` (VERIFIED).
- Код изучен: `apps/skills/booking/lookup.py` (детектор), `apps/skills/booking/tools.py` (`show_my_bookings`, `_show_my_bookings_ayla`, `_format_bookings_text`), `apps/skills/booking/skill.py` (`matches`/`handle`, fast-path :502), `apps/orchestrator/handoff.py` (DRF-1015: `matches_human_handoff_request`, `route_global_human_handoff`, `_latest_tenant_conversation`, `global_handoff_muted`), `apps/channels/max/handler.py` (`_handle_global_max_event_inner`, порядок веток), `apps/channels/max/global_onboarding.py` (`needs_onboarding`), `apps/skills/booking/provider.py` (`get_booking_provider`).

---

## Решение по §3 (продуктовая развилка) — АГРЕГАЦИЯ по всем тенантам

**Решение:** показывать записи по ВСЕМ тенантам, где у обратившегося есть CONFIRMED-брони, сгруппированные по салонам с подписями. Эскалация не требуется — решение совпадает с позицией главного окна («полнота выдачи важнее простоты реализации»), аргументы ниже.

**Обоснование:**

1. **Худший исход — частичный список, который выглядит полным** (пропущенный визит). Правило «последний активный тенантный диалог» из DRF-1015 для просмотра записей воспроизводит именно его: человек, записанный в два салона, видит один и принимает список за полный. Для эскалации к человеку адресность уместна (обращение адресно), для чтения своих данных — нет.
2. **Вопрос «записи какого салона?» — лишний ход, который ничего не даёт:** при одном салоне (подавляющий кейс пилота) спрашивать нечего, при нескольких агрегированная выдача отвечает на вопрос сама.
3. **Цена агрегации ограничена:** число тенантов на пользователя — единицы (пилот: 1–2); на каждый тенант — те же два индексированных запроса (`BookingRequest` + `RemoteBookingProxy`), что и на тенантном пути. Новых интеграций и сетевых вызовов нет.
4. **Изоляция сохраняется:** каждый тенант читается в своём `tenant_scope(T)`, выдача скоупится тем же `(channel, channel_user_id)` — мост тот же, что в DRF-1015/DRF-988, без создания новых идентичностей (BotUser берётся из существующей строки брони).

**Гарантия «не выглядеть полным, не будучи полным»:** если загрузка по какому-то из тенантов падает (напр. `schedule_unavailable`), его секция помечается явно («не удалось загрузить записи — попробуйте позже»), а список не маскируется под полный. При единственном тенанте ошибка отдаётся как есть (тот же retry-текст, что на тенантном пути).

**§4 п.6 (прошлые записи):** поведение не меняется — только будущие CONFIRMED, как сейчас в `show_my_bookings`. Вопрос «когда я записан?» — про предстоящие визиты; расширение до истории — отдельная продуктовая тема, в бриф не входит.

---

## Порядок веток в глобальном обработчике (обоснование)

Новая ветка `is_personal_booking_lookup` → `route_global_booking_lookup` ставится **после human-handoff (DRF-1015), перед onboarding**:

1. Заглушка при активной эскалации (`global_handoff_muted`) — бот молчит, ветка lookup под ней тоже (требование брифа выполняется конструктивно).
2. Safety pre-check (#1053) — кризис важнее любых данных.
3. Human handoff (DRF-1015) — экстренный выход к человеку важнее чтения данных.
4. **Booking lookup (DRF-911, новая)** — детерминированный ответ данными до LLM.
5. Onboarding (#1046, флаг) — прецедент уже есть в `needs_onboarding`: «booking handoff wins over onboarding, unconditionally»; вопрос о своих записях — тот же класс, welcome-гритинг не должен его проглатывать (когорта существующих глобальных BotUser с `welcomed_at IS NULL` — в т.ч. владелец пилота).
6. `cb:discover:book:*`, `cb:book:*` — колбэки, с текстовым lookup не пересекаются.
7. Memory / concierge LLM — общий путь, куда lookup-реплики раньше проваливались.

Детектор **импортируется** из `apps/skills/booking/lookup.py` (`is_personal_booking_lookup`), не дублируется. Пустой текст берётся из `_format_bookings_text([])` (тот же источник, что у тенантного пути, по образцу импорта `_HANDOFF_KEYWORDS`/`_HANDOFF_REPLY` в DRF-1015).

Дальше: красные тесты по §4 → реализация → зелёные тесты → PR.

---

## Реализация и тесты (2026-08-12)

**Изменения (3 файла, +~190 строк):**

- `apps/channels/max/handler.py` — новая ветка 0.7 в `_handle_global_max_event_inner`: `is_personal_booking_lookup(event.text)` → `route_global_booking_lookup(...)`, `assistant_action_type="booking_lookup"`. Детектор импортирован из `apps/skills/booking/lookup.py` (не продублирован). Место ветки — после human-handoff, перед onboarding (обоснование в секции «Порядок веток» выше). Под заглушкой `global_handoff_muted` — молчит конструктивно.
- `apps/orchestrator/handoff.py` — блок «Global-path personal booking lookup (DRF-911)»: `_booking_lookup_scopes` (тенанты из CONFIRMED `BookingRequest` по `(channel, channel_user_id)`, BotUser берётся из строки брони — идентичности не создаются), `_lookup_in_tenant` (тот же `show_my_bookings` внутри `tenant_scope(T)`; на Ayla-пути клиент не конструируется — инструмент его не трогает, read-only lookup не должен умирать от конфигурации сетевого клиента), `_compose_multi_tenant_text` (секции по салонам; упавшая секция помечается «не удалось загрузить записи — попробуйте позже»), `route_global_booking_lookup`. Пустой текст — `_format_bookings_text([])` из tools.py (тот же источник, что у тенантного пути). Событие `marketplace.booking_lookup.routed`.
- `apps/channels/tests/test_global_booking_lookup.py` — 13 тестов.

**Красный до / зелёный после (VERIFIED, локальный прогон):** до фикса красными были 9 приёмочных (выдача, мультизаписи, пустой список, агрегация, изоляция, отмена/перенос/прошедшие, мьют); 4 сторожевых (детектор: FAQ/мутация/вебинар; отсутствие мутаций) были зелёными и остались зелёными. После фикса — 13/13 зелёные.

**Регресс (VERIFIED, локально):** `apps/channels/tests` + `apps/skills/booking/tests` + `apps/skills/faq` — зелёные; `apps/skills` + `apps/booking` — зелёные; `apps/orchestrator` + `apps/identity` — в прогоне. `ruff check` — чисто; `ruff format` — применён; `mypy` на изменённых файлах — чисто.

**Покрытие критериев §4:** реальные записи ✔, пустой список текстом ✔, чужие не видны ✔, отменённая/перенесённая/прошедшая не показывается ✔, молчание при активной эскалации ✔, «как записаться?»/«перенеси мою запись»/«запись вебинара» не в ветке ✔, регресс тенантного пути ✔, несколько записей — все в списке ✔, ноль мутаций данных ✔.

Дальше: коммит, push, PR в `dev`, CI.

---

## Регресс-прогоны: статус и локальные аномалии (2026-08-12)

**Зелёные (VERIFIED локально):**
- `apps/channels/tests`, `apps/skills` (включая booking + faq), `apps/booking` — полностью.
- `apps/handoff`, `apps/conversations`, `apps/marketplace`, `apps/tenancy`, `apps/identity` — полностью.
- `apps/orchestrator/tests -k "handoff or lookup or booking"` — точечно зелёный.
- CI-гейты: `ruff check`, `ruff format --check`, `tools/lint/red_zone_guard.py`, `tools/lint/import_boundaries.py`, `manage.py check` — чисто. `mypy` на изменённых файлах — чисто.

**Локальные аномалии окружения (НЕ от дифа):**
1. `manage.py makemigrations --check` падает с `InconsistentMigrationHistory` (`internal_chat.0002…` применена раньше `tenancy.0010_tenant_city`) — состояние локальной dev-БД, к дифу отношения нет (модели не тронуты) (INFERRED — природа ошибки чисто БД-шная).
2. `tests/smoke/test_ayla_import.py::test_package_sha_pinned` падает: локальный venv ставит ayla-ai-core из `file:///C:/Users/user/PycharmProjects/ayla-ai-core` (видно в boot-логе), git-SHA гейт неприменим к local-path установке. CI ставит через `uv sync --frozen` из git (VERIFIED по коду теста и boot-логу).
3. Полный прогон `apps/orchestrator` на этой Windows-машине идёт >30 мин и показывает кластер падений в районе ~28% (первый — `test_faq_latency.py::test_p95_under_4000ms_over_50_runs`, перформанс-гейт `@pytest.mark.slow`, в CI не входит — CI гоняет smoke/contract/eventbus, а не полный apps-suite). Идёт идентификация остальных падений + сравнение с базой `origin/dev`. Мой диф трогает только `handoff.py` (аддитивно) и глобальный обработчик MAX; целевые тесты этой области зелёные.

Дальше: идентификация кластера падений, коммит, PR.

---

## Локализация кластера падений apps/orchestrator — A/B с базой (2026-08-12)

Падения локализованы: `test_pipeline.py`, `test_pipeline_ai_metric_emission.py`, `test_pipeline_latency.py`, `test_shadow_short_circuit.py` (тенантный pipeline, латентность/аутбаунд/аудит). A/B-проверка: на файлах из `origin/dev` (мой диф убран) те же 4 файла падают в том же паттерне на этой машине (VERIFIED — прогон на базе). **Вывод: падения предсуществующие, окружение Windows-дева (таймауты/латентность/отсутствие живых сервисов), к дифу отношения не имеют.** В CI эти тесты не входят (CI: smoke + contract + eventbus + ruff/mypy/линты).

Диф восстановлен после A/B, 13/13 тестов DRF-911 снова зелёные (VERIFIED).

---

## PR и CI (2026-08-12)

- Коммит `9c45385` (pre-commit хуки зелёные: ruff, ruff-format, detect-secrets, red-zone, import-boundary), push в `fix/drf911-booking-lookup-global` (VERIFIED).
- PR: https://github.com/AndreyDeveloper84/ai-bot-platform/pull/1169 в `dev` (VERIFIED).
- CI запущен, жду зелёный. Учту ловушку DRF-1027 (docs-skip прогоны могут выглядеть fail — сверю с `gh run list --commit 9c45385`).

**Кандидаты в Linear (→ в Linear):**
1. Прошлые записи («история посещений») на глобальном пути — сейчас показываются только будущие CONFIRMED (зафиксировано как осознанное поведение, §4 п.6 брифа). Если продукт захочет историю — отдельная задача.
2. Локальный дев-регресс `apps/orchestrator/tests` (pipeline-латентность/DLQ/shadow) падает на Windows-деве и на базе `origin/dev` — в CI не входит; кандидат на стабилизацию локального прогона или перенос в CI-гейт.

---

## Merge в dev (2026-08-12)

- CI зелёный (VERIFIED): `gh pr checks 1169` — `pytest + ruff + mypy` pass (2m21s и 2m7s, push + pull_request), `replay fixtures` pass; сверка по DRF-1027 через `gh run list --commit 9c453850` — все три прогона `completed success`, отменённых docs-skip-ложных fail нет.
- **Merge выполнен** (обычный merge, не squash — как предразрешено; эскалация по §3 не потребовалась): PR #1169 → `dev`, `origin/dev` = `02a61a9d9efa1bf9159e1660b6240fe9c9223912` (VERIFIED).
- Эскалаций нет. **Жду «GO НА ФАЗУ C» в REPLY** — деплой по рецепту §5 (bundle `1ea47b9..02a61a9` → scp → checkout → пересборка → `up -d` ayla-bot-staging, rollback-точка `1ea47b9`, контроль диска, health на 127.0.0.1:8014, smoke read-only на синтетике; бронь владельца — только чтение).

---

## Follow-up по REPLY №1–№4: тест на частичный отказ (2026-08-13)

REPLY прочитан целиком (секции №1–№4). Ветка `test/drf911-lookup-partial-failure` от свежего `origin/dev` @ `b0849ed` (после merge DRF-1029) (VERIFIED). Правится только `apps/channels/tests/test_global_booking_lookup.py`, код не тронут (диф `handoff.py` после красной пробы — пустой, VERIFIED `git diff`).

**Тест:** `apps/channels/tests/test_global_booking_lookup.py::TestMultiTenantAggregation::test_partial_failure_shows_ok_salon_and_marks_failed`

Сценарий: у пользователя записи в двух салонах (`salon-ok`, `salon-broken`); загрузка `salon-broken` падает (`show_my_bookings` для него возвращает `error="schedule_unavailable"` через monkeypatch — на Ayla-пути чистой БД-ошибки не бывает, отказ инжектирован на границе инструмента). Оба факта в одном тесте:
- записи здорового салона в ответе есть (`SALON-OK`, «УЗ-кавитация»);
- упавший салон помечен явно (`SALON-BROKEN` + «не удалось загрузить записи»);
- запись упавшего салона не протекает в список как живая («Солярий» отсутствует).

**Доказательство недекоративности (красный до / зелёный после, VERIFIED локально):**
- Красный: временно убрана строка `lines.append(_LOOKUP_SECTION_UNAVAILABLE)` в `_compose_multi_tenant_text` → тест падает чистым assertion failure на `assert "не удалось загрузить записи" in text` (не ошибкой — пометка снята, секция-пустышка остаётся).
- Зелёный: строка возвращена, `git diff` на `handoff.py` пуст, файл тестов 14/14 зелёный.

**Регресс:** `apps/channels/tests` целиком — зелёный (VERIFIED).

Дальше: коммит, push, PR, зелёный CI, перечитаю REPLY перед merge (гонка №2/merge учтена), merge обычный.

**Инцидент общего рабочего каталога (для протокола):** между моим `git checkout -b test/drf911-lookup-partial-failure` и первым коммитом чекаут в этом же каталоге был переключён на `feat/drf1029-notify-zero-queries` (окно DRF-1029 работает в той же рабочей копии). Мой коммит `51ef519` на минуту оказался на их локальной ветке. Восстановлено: коммит перенесён cherry-pick'ом на мою ветку (`cae3398`, родитель `b0849ed` = origin/dev), их ветка возвращена на `origin/feat/drf1029-notify-zero-queries` (`3097126`, уже запушен — потерь нет), worktree был чист. Урон: нулевой. Рекомендация главному окну: развести окна по git-worktree — два активных окна в одном чекауте будут сталкиваться снова (→ в Linear как инфра-кандидат).

**PR:** https://github.com/AndreyDeveloper84/ai-bot-platform/pull/1172 (коммит `cae3398`, тест-only). Жду CI.

**Merge follow-up (VERIFIED):** CI зелёный — `gh run list --commit cae3398`: ci (2m48s) и replay (55s), оба `completed success`, ложных docs-skip fail нет. REPLY перед merge перечитан (верхняя секция №4, условия не изменились). PR #1172 смержен обычным merge: `origin/dev` = `4373bb5`. Follow-up окна DRF-1029 (`565af20`, PR #1171) там же — условие владельца «оба follow-up в dev» выполнено с моей стороны.

**Статус: жду «GO НА ФАЗУ C».** Деплой одним заходом: bundle `1ea47b9..4373bb5` → scp → checkout → пересборка → `up -d` ayla-bot-staging; rollback-точка `1ea47b9` (откат предразрешён); контроль `df -h /` до/после в REPORT; health `/healthz/` на 127.0.0.1:8014 + web-контейнер healthy + логи worker'а 5 мин; smoke read-only на синтетике; бронь владельца — только чтение; тенанту `b32a057a-…` записей не создавать.
