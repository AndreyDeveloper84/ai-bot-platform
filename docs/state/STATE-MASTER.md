# СОСТОЯНИЕ · Мастерская и салонная поверхность

Формат и правила — `docs/state/README.md`. Проверить всё:
`python tools/state_check.py --file master`.

Замер по этому направлению снят 2026-09-08 ~16:20 MSK; базы чтения —
`ai-bot-platform @ 8c276fa`, `djangoproject-catalog @ ab76e6a1`. Всё, что
касается содержимого боевой базы (сколько мастеров, какие `invite_status`,
сколько услуг размечено), в раздел «Замер не снят»: доступа к прод-БД у этого
слоя нет.

---

## Работает

### Мастерские экраны Mini App существуют
статус:   РАБОТАЕТ
где:      apps/miniapp/src/screens/Master*.tsx
проверка: git ls-tree -r --name-only origin/dev -- apps/miniapp/src/screens/MasterDashboardScreen.tsx apps/miniapp/src/screens/MasterScheduleScreen.tsx
ожидание: =2
снято:    2026-09-08 @ 8c276fa (ai-bot-platform)
задача:   DRF-1503

### Бэкенд мастера — отдельное дерево маршрутов под `/api/v1/master/`
статус:   РАБОТАЕТ
где:      apps/master_api/urls.py
проверка: git grep -c "path(" origin/dev -- apps/master_api/urls.py
ожидание: >20
снято:    2026-09-08 @ 8c276fa (ai-bot-platform)
задача:   DRF-1503

### Мастер видит свои записи через `RemoteBookingProxy`, а не через `BookingRequest`
статус:   РАБОТАЕТ
где:      apps/master_api/services/visit_source.py — дефект «200 с `active_visit: null`» описан там же
проверка: git ls-tree -r --name-only origin/dev -- apps/master_api/services/visit_source.py
ожидание: есть
снято:    2026-09-08 @ 8c276fa (ai-bot-platform)
задача:   DRF-1129

### Мастер может попросить недоступность, владелец одобряет
статус:   РАБОТАЕТ
где:      apps/scheduling/models.py:425 — `ScheduleChangeRequest`
проверка: git grep -c "class ScheduleChangeRequest" origin/dev -- apps/scheduling/models.py
ожидание: =1
снято:    2026-09-08 @ 8c276fa (ai-bot-platform)
задача:   —

### Телефон клиента исполнителю не передаётся: канон записан отдельным модулем
статус:   РАБОТАЕТ
где:      apps/master_api/pii.py:45 — `FORBIDDEN_PII_KEYS`
проверка: git grep -c "FORBIDDEN_PII_KEYS" origin/dev -- apps/master_api/pii.py
ожидание: >0
снято:    2026-09-08 @ 8c276fa (ai-bot-platform)
задача:   DRF-1039

### Свободный текст клиента редактируется, а не маскируется
статус:   РАБОТАЕТ
где:      apps/master_api/pii.py:166 — `redact_contacts()`, плейсхолдеры «[номер скрыт]» / «[почта скрыта]»
проверка: git grep -c "def redact_contacts" origin/dev -- apps/master_api/pii.py
ожидание: =1
снято:    2026-09-08 @ 8c276fa (ai-bot-platform)
задача:   DRF-1039

### Границу PII держит четырёхслойный тест-сторож
статус:   РАБОТАЕТ
где:      apps/master_api/tests/test_pii_boundary.py — живой обход ручек, покрытие маршрутов, AST-скан, паритет со списком в TS
проверка: git ls-tree -r --name-only origin/dev -- apps/master_api/tests/test_pii_boundary.py
ожидание: есть
снято:    2026-09-08 @ 8c276fa (ai-bot-platform)
задача:   DRF-1039 / DRF-1406

### Отдельный салонный бот существует: пульт, а не разговор
статус:   РАБОТАЕТ
где:      apps/channels/max/salon_handler.py — ни LLM, ни диспетчеризации навыков
проверка: git ls-tree -r --name-only origin/dev -- apps/channels/max/salon_handler.py
ожидание: есть
снято:    2026-09-08 @ 8c276fa (ai-bot-platform)
задача:   §69 / DRF-1070

### Реестр ботов маршрутизирует по вебхук-секрету и падает на кривой конфигурации
статус:   РАБОТАЕТ
где:      apps/channels/bot_registry.py; настройка `MAX_BOTS` в `config/settings/base.py`
проверка: git grep -c "MAX_BOTS" origin/dev -- config/settings/base.py
ожидание: >0
снято:    2026-09-08 @ 8c276fa (ai-bot-platform)
задача:   §69

### Закрывать визиты через Django-админку запрещено механически
статус:   РАБОТАЕТ
где:      apps/booking/admin.py:73 — `readonly_fields = tuple(...)` по всем полям, включая `status`
проверка: git grep -c "readonly_fields = tuple" origin/dev -- apps/booking/admin.py
ожидание: >0
снято:    2026-09-08 @ 8c276fa (ai-bot-platform)
задача:   DRF-1498

### «Закрыл ли живой человек» — отдельный предикат с default-deny
статус:   РАБОТАЕТ
где:      apps/booking/completion.py:95 — `confirmed_by_human()`
проверка: git grep -c "def confirmed_by_human" origin/dev -- apps/booking/completion.py
ожидание: =1
снято:    2026-09-08 @ 8c276fa (ai-bot-platform)
задача:   п.36

### Автозакрытие визита штампует `completed_by = system` и откатывает штамп при провале эмита
статус:   РАБОТАЕТ
где:      apps/bookings/tasks.py:484
проверка: git grep -c "SYSTEM_ACTOR" origin/dev -- apps/bookings/tasks.py
ожидание: >0
снято:    2026-09-08 @ 8c276fa (ai-bot-platform)
задача:   п.36

### 429 от Ayla больше не проваливается в общую 4xx-ветку
статус:   РАБОТАЕТ
где:      apps/catalog/services/http_client.py:245 — `CatalogThrottledError` НЕ подкласс `CatalogClientError`
проверка: git grep -c "class CatalogThrottledError" origin/dev -- apps/catalog/services/http_client.py
ожидание: =1
снято:    2026-09-08 @ 8c276fa (ai-bot-platform)
задача:   DRF-1595

### Каталог тянется страницами по 100, а не по дефолтным 20
статус:   РАБОТАЕТ
где:      apps/catalog/services/http_client.py:296 — `_PAGE_SIZE = 100` во всех трёх вызовах
проверка: git grep -c "_PAGE_SIZE = 100" origin/dev -- apps/catalog/services/http_client.py
ожидание: =1
снято:    2026-09-08 @ 8c276fa (ai-bot-platform)
задача:   DRF-1595

### У ожидания throttle есть бюджет на весь прогон
статус:   РАБОТАЕТ
где:      apps/catalog/services/throttle.py:55 — `DEFAULT_WAIT_BUDGET_SECONDS`
проверка: git grep -c "DEFAULT_WAIT_BUDGET_SECONDS" origin/dev -- apps/catalog/services/throttle.py
ожидание: >0
снято:    2026-09-08 @ 8c276fa (ai-bot-platform)
задача:   DRF-1595

### Живут две салонные админ-поверхности: мост из 5 вкладок и пилот из 3
статус:   РАБОТАЕТ (сознательно, обе)
где:      apps/miniapp/src/lib/admin-tabs.ts и apps/miniapp/src/lib/salon-pilot.ts
проверка: git ls-tree -r --name-only origin/dev -- apps/miniapp/src/lib/admin-tabs.ts apps/miniapp/src/lib/salon-pilot.ts
ожидание: =2
снято:    2026-09-08 @ 8c276fa (ai-bot-platform)
задача:   DRF-1235

---

## Сломано или отсутствует

### Разметки связей (`mapping_status`) в коде НЕТ — колонки не существует
статус:   СЛОМАНО
где:      apps/catalog/models.py — ни `mapping_status`, ни `verification_status`
проверка: git grep -c "mapping_status" origin/dev -- apps/catalog/models.py
ожидание: 0
снято:    2026-09-08 @ 8c276fa (ai-bot-platform)
задача:   §76

### Слов `VERIFIED / REVIEW_REQUIRED / UNMAPPED` нет и в логике каталога
статус:   СЛОМАНО
где:      apps/catalog — ноль совпадений `review_required`
проверка: git grep -c "review_required" origin/dev -- apps/catalog
ожидание: 0
снято:    2026-09-08 @ 8c276fa (ai-bot-platform)
задача:   §76

### Настраиваемого расписания нет ни у мастера, ни у салона
статус:   СЛОМАНО
где:      apps/master_api/urls.py — ни одной ручки записи `working_hours`
проверка: git grep -c "working_hours" origin/dev -- apps/master_api/urls.py
ожидание: 0
снято:    2026-09-08 @ 8c276fa (ai-bot-platform)
задача:   «график делать настраиваемым» (сказано владельцем дважды)

### Единственный редактор расписания — Django-админка, то есть доступ к серверу
статус:   СЛОМАНО
где:      apps/scheduling/admin.py — полный CRUD по `WorkingHours`
проверка: git grep -c "WorkingHours" origin/dev -- apps/scheduling/admin.py
ожидание: >0
снято:    2026-09-08 @ 8c276fa (ai-bot-platform)
задача:   DRF-1521

### Мастер не может завершить визит — слова `complete` в его маршрутах нет
статус:   СЛОМАНО
где:      apps/master_api/urls.py
проверка: git grep -c "complete" origin/dev -- apps/master_api/urls.py
ожидание: 0
снято:    2026-09-08 @ 8c276fa (ai-bot-platform)
задача:   —

### Закрытие визита живёт только под ролью владельца/админа
статус:   СЛОМАНО (следствие предыдущего)
где:      apps/admin_api/urls.py — единственный маршрут закрытия
проверка: git grep -c "complete/" origin/dev -- apps/admin_api/urls.py
ожидание: =1
снято:    2026-09-08 @ 8c276fa (ai-bot-platform)
задача:   DRF-1232

### Экраны переписки мастера с клиентом решено удалить — они на месте
статус:   СЛОМАНО (решение §28 п.4 не исполнено)
где:      apps/miniapp/src/App.tsx — маршруты `/master/conversations` и `/master/conversations/:id`
проверка: git grep -c "master/conversations" origin/dev -- apps/miniapp/src/App.tsx
ожидание: >0
снято:    2026-09-08 @ 8c276fa (ai-bot-platform)
задача:   DRF-1255

### Ранжирование мастеров в боте — подстрочная заглушка
статус:   СЛОМАНО
где:      apps/skills/booking/tools.py:689 — `_relevance_score()`: 1.0 / 0.5 / 0.2 по вхождению подстроки
проверка: git grep -c "def _relevance_score" origin/dev -- apps/skills/booking/tools.py
ожидание: =1
снято:    2026-09-08 @ 8c276fa (ai-bot-platform)
задача:   §81

### Путь записи через Ayla выключен по умолчанию — по умолчанию YClients
статус:   СЛОМАНО (значение на пилоте не замерено, см. ниже)
где:      config/settings/base.py:771 — `BOOKING_VIA_AYLA_REST`, дефолт `"false"`
проверка: git grep -c "BOOKING_VIA_AYLA_REST" origin/dev -- config/settings/base.py
ожидание: =1
снято:    2026-09-08 @ 8c276fa (ai-bot-platform)
задача:   —

### Две трети пилотной вкладки «Сегодня» — пустые рамки, и экран это признаёт
статус:   СЛОМАНО
где:      apps/miniapp/src/screens/admin/SalonPilotTodayScreen.tsx — `BACKEND-BLOCKED`
проверка: git grep -c "BACKEND-BLOCKED" origin/dev -- apps/miniapp/src/screens/admin/SalonPilotTodayScreen.tsx
ожидание: >0
снято:    2026-09-08 @ 8c276fa (ai-bot-platform)
задача:   DRF-1236

### Прод-выкладка не прогонялась с 19 мая — машинерия ожила только 08.09
статус:   СЛОМАНО
где:      workflow `deploy.yml`, последний прогон 26072780032
проверка: gh run list --workflow deploy.yml --limit 1
ожидание: 2026-05-19
снято:    2026-09-08 @ 8c276fa (ai-bot-platform)
задача:   DRF-1578

---

## В работе

### Сторож дрейфа выкладки — черновой PR #1486
статус:   В РАБОТЕ
где:      ветка `ci/drf1605-deploy-guards`
проверка: gh pr view 1486 --json state
ожидание: OPEN
снято:    2026-09-08 @ 8c276fa (ai-bot-platform)
задача:   DRF-1605

### Половина §76 на стороне Ayla — PR #302, не слит
статус:   В РАБОТЕ
где:      `beautygo_backend`, ветка `feat/drf1593-no-verified-candidates`
проверка: git grep -c "NO_VERIFIED_CANDIDATES" origin/dev -- recommendation
ожидание: 0
снято:    2026-09-08 @ ab76e6a1 (djangoproject-catalog)
задача:   DRF-1593 / §76

### По мастерской и салонной поверхности открытых PR в боте нет
статус:   В РАБОТЕ (пусто)
где:      AndreyDeveloper84/ai-bot-platform
проверка: gh pr list --state open --limit 30
ожидание: feat/planning-rules-intake
снято:    2026-09-08 @ 8c276fa (ai-bot-platform)
задача:   —

---

## Ждёт владельца

### Где жить признаку «расписание подтверждено человеком»
статус:   ЖДЁТ ВЛАДЕЛЬЦА
где:      apps/catalog/master_state.py:397 — «признака нет ни на `WorkingHours`, ни на `CatalogMaster`»
проверка: git grep -c "выразить нечем" origin/dev -- apps/catalog/master_state.py
ожидание: =1
снято:    2026-09-08 @ 8c276fa (ai-bot-platform)
задача:   DRF-1521 п.6

### Авторитетен ли `completed_by = system`
статус:   ЖДЁТ ВЛАДЕЛЬЦА
где:      apps/booking/completion.py:58 — `SYSTEM_COMPLETION_ACTORS` существует, решение о его весе — нет
проверка: git grep -c "SYSTEM_COMPLETION_ACTORS" origin/dev -- apps/booking/completion.py
ожидание: >0
снято:    2026-09-08 @ 8c276fa (ai-bot-platform)
задача:   п.36

### Какие салонные сценарии отдать ресепшн
статус:   ЖДЁТ ВЛАДЕЛЬЦА
где:      apps/miniapp/src/lib/salon-pilot.ts — ресепшн пилот не видит вовсе; открыть ей его значило бы ответить молча
проверка: git ls-tree -r --name-only origin/dev -- apps/miniapp/src/lib/salon-pilot.ts
ожидание: есть
снято:    2026-09-08 @ 8c276fa (ai-bot-platform)
задача:   §69

### Снимать ли пятивкладочный мост после пилота
статус:   ЖДЁТ ВЛАДЕЛЬЦА
проверка: нечем
почему:   «владелец не сказал её выключать» — отсутствие решения. Обе поверхности живут одновременно, и это состояние доказывается наличием обоих файлов (блок выше), а не наличием решения.
снято:    2026-09-08 @ 8c276fa (ai-bot-platform)
задача:   DRF-1235

---

## Замер не снят

### Есть ли у Ayla способ человеку закрыть визит и что она кладёт в `completed_by`
статус:   НЕ ЗАМЕРЕНО
где:      сторона Ayla, контракт `complete_appointment`
проверка: нечем
почему:   вопрос стороне Ayla (§42), ответа нет. Замер 07.09: из 30 `RemoteBookingProxy` — `completed` НОЛЬ; ни один визит на пилоте никогда не доходил до «завершён». Это чужой замер боевой базы, отсюда не воспроизводится.
снято:    2026-09-07 (замер чужой)
задача:   §42

### Фактическое значение `BOOKING_VIA_AYLA_REST` на пилоте
статус:   НЕ ЗАМЕРЕНО
где:      контейнер `ayla-bot-staging-web-1`
проверка: нечем
почему:   в коде виден только дефолт (`"false"`); прод-окружения этот слой не видит. Дефолт в `base.py` не доказывает значение переменной на бою.
снято:    (не снималось)
задача:   —

### Сколько мастеров на пилоте, какие у них `invite_status`, сколько услуг размечено
статус:   НЕ ЗАМЕРЕНО
где:      боевая база
проверка: нечем
почему:   доступа к прод-БД нет. Все числа по пилоту в отчётах — чужие датированные замеры; у боевых замеров есть срок годности.
снято:    (не снималось)
задача:   —

### Статусы и оценки задач мастерского направления в Linear
статус:   НЕ ЗАМЕРЕНО
где:      Linear, DRF-1039 / DRF-1521 / DRF-1540 / DRF-1571 / DRF-1573 / DRF-1595
проверка: нечем
почему:   08.09 рабочее пространство упёрлось в лимит 2500 запросов в час; ни одна карточка не прочитана. Оценки SP, встречающиеся в файлах шины (DRF-1521 = 8, DRF-1255 = 5, DRF-1503 = 22, DRF-1560 = 61), — чужой пересказ 06–07.09, сверке не подвергался.
снято:    2026-09-08 (не снято)
задача:   —
