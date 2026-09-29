# СОСТОЯНИЕ · Клиентская поверхность

Формат и правила — `docs/state/README.md`. Проверить всё:
`python tools/state_check.py --file client`.

Клиентская поверхность живёт в двух местах: бот (`apps/skills`,
`apps/orchestrator`, `apps/channels`) и Mini App (`apps/miniapp`), плюс
рекомендательный резолвер на стороне Ayla (`djangoproject-catalog`).

---

## Работает

### Главное меню собрано из данных, а не из докстринга
статус:   РАБОТАЕТ
где:      apps/skills/menu/marketplace.py — `MAIN_ITEMS`
проверка: git grep -c "MAIN_ITEMS" origin/dev -- apps/skills/menu/marketplace.py
ожидание: >0
снято:    2026-09-08 @ 8c276fa (ai-bot-platform)
задача:   DRF-1547

### Подменю «Ещё» снесено, и это держит страж
статус:   РАБОТАЕТ
где:      apps/channels/tests/test_menu_drf1547.py:464
проверка: git grep -c "assert \"Ещё\" not in labels" origin/dev -- apps/channels/tests/test_menu_drf1547.py
ожидание: =1
снято:    2026-09-08 @ 8c276fa (ai-bot-platform)
задача:   OD-UI-2

### Пункт «Дневник питания» шлёт фразу, а не слаг мини-приложения
статус:   РАБОТАЕТ
где:      apps/skills/menu/marketplace.py:380 — `DIARY_TAP_TEXT`
проверка: git grep -c "DIARY_TAP_TEXT" origin/dev -- apps/skills/menu/marketplace.py
ожидание: >0
снято:    2026-09-08 @ 8c276fa (ai-bot-platform)
задача:   §37 п.5

### Legacy-экраны записи и профиля СНЕСЕНЫ, а не оставлены алиасами
статус:   РАБОТАЕТ
где:      apps/miniapp/src/screens/ — трёх файлов нет в дереве
проверка: git ls-tree -r --name-only origin/dev -- apps/miniapp/src/screens/BookingConfirmScreen.tsx apps/miniapp/src/screens/BookingSuccessScreen.tsx apps/miniapp/src/screens/ProfileScreen.tsx
ожидание: нет
снято:    2026-09-08 @ 8c276fa (ai-bot-platform)
задача:   DRF-1485

### Согласия клиента стоят на настоящих ручках
статус:   РАБОТАЕТ
где:      apps/miniapp_api/urls.py:74 — `me/consents/*` (четыре маршрута)
проверка: git grep -c "me/consents/" origin/dev -- apps/miniapp_api/urls.py
ожидание: =4
снято:    2026-09-08 @ 8c276fa (ai-bot-platform)
задача:   DRF-1520

### Согласие на медданные — отдельная живая ручка
статус:   РАБОТАЕТ
где:      apps/miniapp_api/urls.py — `me/health-consent/`
проверка: git grep -c "me/health-consent/" origin/dev -- apps/miniapp_api/urls.py
ожидание: =1
снято:    2026-09-08 @ 8c276fa (ai-bot-platform)
задача:   DRF-1545

### Транзит рекомендаций к Ayla существует и не выпускает initData наружу
статус:   РАБОТАЕТ
где:      apps/miniapp_api/views.py:2444 — `customer_recommendations`
проверка: git grep -c "def customer_recommendations" origin/dev -- apps/miniapp_api/views.py
ожидание: =1
снято:    2026-09-08 @ 8c276fa (ai-bot-platform)
задача:   DRF-1568

### Гейт владельца «нет displayable WHY → нет блока» держится одной строкой
статус:   РАБОТАЕТ
где:      apps/miniapp/src/lib/customer-booking.ts — `.filter((p) => p.reasons.length > 0)`
проверка: git grep -c "reasons.length > 0" origin/dev -- apps/miniapp/src/lib/customer-booking.ts
ожидание: =1
снято:    2026-09-08 @ 8c276fa (ai-bot-platform)
задача:   §22

### У пустой полки различимые имена, включая «кандидаты, которых полка не умеет»
статус:   РАБОТАЕТ
где:      apps/miniapp/src/lib/customer-booking.ts — `UNRENDERABLE_CANDIDATES`
проверка: git grep -c "UNRENDERABLE_CANDIDATES" origin/dev -- apps/miniapp/src/lib/customer-booking.ts
ожидание: >0
снято:    2026-09-08 @ 8c276fa (ai-bot-platform)
задача:   §76

### Клиент отказывается рисовать кандидата без `mapping_status = VERIFIED`
статус:   РАБОТАЕТ (опережающий гейт: серверного поля ещё нет)
где:      apps/miniapp/src/lib/api.ts:521 — нарушение контракта, а не тихий пропуск
проверка: git grep -c "mapping_status" origin/dev -- apps/miniapp/src/lib/api.ts
ожидание: >0
снято:    2026-09-08 @ 8c276fa (ai-bot-platform)
задача:   §76

### Пустой каталог называет причину пустоты, а не молчит
статус:   РАБОТАЕТ
где:      apps/miniapp/src/lib/customer-catalog-empty.ts — `resolveCatalogEmpty`
проверка: git grep -c "resolveCatalogEmpty" origin/dev -- apps/miniapp/src/lib/customer-catalog-empty.ts
ожидание: >0
снято:    2026-09-08 @ 8c276fa (ai-bot-platform)
задача:   DRF-1482

### Флаг честности пилота `STUB_SURFACES_ENABLED` не имеет ни одного потребителя
статус:   РАБОТАЕТ (записанное состояние, а не забытая уборка)
где:      apps/miniapp/src/lib/feature-flags.ts:43 — объявление; импортов нет
проверка: git grep -c "import { STUB_SURFACES_ENABLED" origin/dev -- apps/miniapp/src
ожидание: 0
снято:    2026-09-08 @ 8c276fa (ai-bot-platform)
задача:   DRF-1546

### Клавиатура у повтора отказа ВОССТАНОВЛЕНА — прежний вердикт устарел
статус:   РАБОТАЕТ
где:      apps/orchestrator/concierge.py:1687 — `action_data=rendered.action_data` рядом с `text=rendered.text`
проверка: git grep -c "action_data=rendered.action_data" origin/dev -- apps/orchestrator/concierge.py
ожидание: >0
снято:    2026-09-08 @ 8c276fa (ai-bot-platform)
задача:   DRF-1576

---

## Сломано или отсутствует

### Блок «Ayla подобрала» рисует УСЛУГИ, а решено — МАСТЕРОВ
статус:   СЛОМАНО
где:      apps/miniapp/src/screens/CustomerCatalogScreen.tsx:203 — `ServiceCard` внутри блока
проверка: git grep -c "ServiceCard" origin/dev -- apps/miniapp/src/screens/CustomerCatalogScreen.tsx
ожидание: >0
снято:    2026-09-08 @ 8c276fa (ai-bot-platform)
задача:   §81 / T6

### Источника кандидатов-услуг в Ayla не существует — виды по разные стороны провода
статус:   СЛОМАНО (дефект спрятан, пока полка пуста)
где:      users/recommendation_source.py:209 — единственный источник даёт `CandidateKind.PROVIDER`
проверка: git grep -c "CandidateKind.SERVICE" origin/dev -- recommendation users ai
ожидание: 0
снято:    2026-09-08 @ ab76e6a1 (djangoproject-catalog)
задача:   §81 / T6

### Согласие сканера еды оседает в браузере, а не в базе
статус:   СЛОМАНО (152-ФЗ-класс)
где:      apps/miniapp/src/lib/food-scanner.ts:444 — `CONSENT_STORAGE_KEY` в `localStorage`
проверка: git grep -c "CONSENT_STORAGE_KEY" origin/dev -- apps/miniapp/src/lib/food-scanner.ts
ожидание: >0
снято:    2026-09-08 @ 8c276fa (ai-bot-platform)
задача:   DRF-1564

### Ручки, пишущей `BotUser.food_scanner_consent_at`, в `miniapp_api` нет
статус:   СЛОМАНО
где:      apps/miniapp_api/urls.py — перечень маршрутов не содержит согласия сканера
проверка: git grep -c "food_scanner_consent_at" origin/dev -- apps/miniapp_api/urls.py
ожидание: 0
снято:    2026-09-08 @ 8c276fa (ai-bot-platform)
задача:   DRF-1564

### Часовой пояс клиента никто не спрашивает и не шлёт
статус:   СЛОМАНО
где:      профиль и настройки уведомлений — ни одного упоминания `timezone`
проверка: git grep -c "timezone" origin/dev -- apps/miniapp/src/screens/CustomerProfileScreen.tsx apps/miniapp/src/lib/customer-profile.ts apps/miniapp/src/screens/CustomerNotificationSettingsScreen.tsx
ожидание: 0
снято:    2026-09-08 @ 8c276fa (ai-bot-platform)
задача:   DRF-1477

### Всё, что не спросили о поясе, приезжает в Москву
статус:   СЛОМАНО
где:      apps/nutrition_proactive/prefs.py:203 — `resolve_timezone` падает в `Europe/Moscow`
проверка: git grep -c "Europe/Moscow" origin/dev -- apps/nutrition_proactive/prefs.py
ожидание: >0
снято:    2026-09-08 @ 8c276fa (ai-bot-platform)
задача:   DRF-1477

### Ручек `customer/food/*` не существует ни одной
статус:   СЛОМАНО
где:      apps/miniapp_api/urls.py — ни `food/log`, ни `food/diary`, ни `food/consent`
проверка: git grep -c "food/" origin/dev -- apps/miniapp_api/urls.py
ожидание: 0
снято:    2026-09-08 @ 8c276fa (ai-bot-platform)
задача:   DRF-1329

### Комментарий в настройках описывает оператору три несуществующие ручки
статус:   СЛОМАНО (протухший текст в коде)
где:      config/settings/base.py:861 — «miniapp_api `/customer/food/{log,diary,consent}` endpoints»
проверка: git grep -c "customer/food" origin/dev -- config/settings/base.py
ожидание: =1
снято:    2026-09-08 @ 8c276fa (ai-bot-platform)
задача:   —

### Экраны сканера живы маршрутами, но в прод-сборке бросают `StubNotWiredError`
статус:   СЛОМАНО
где:      apps/miniapp/src/lib/food-scanner.ts — единственный файл с `guardProd(`
проверка: git grep -cl "guardProd(" origin/dev -- apps/miniapp/src
ожидание: =1
снято:    2026-09-08 @ 8c276fa (ai-bot-platform)
задача:   DRF-1417

### Различимость подбора как число не измеряется нигде
статус:   СЛОМАНО
где:      apps/orchestrator — метрик `separation` нет вне тестов
проверка: git grep -c "separation" origin/dev -- apps/orchestrator
ожидание: =1
снято:    2026-09-08 @ 8c276fa (ai-bot-platform)
задача:   DRF-1533

### Недельного отчёта по питанию нет во всём репозитории
статус:   СЛОМАНО
где:      весь `origin/dev`
проверка: git grep -c "DRF-1465" origin/dev
ожидание: 0
снято:    2026-09-08 @ 8c276fa (ai-bot-platform)
задача:   DRF-1465

### Запрос про чужой город всё равно отдаёт наших мастеров
статус:   СЛОМАНО
где:      apps/marketplace/discovery.py:512 — `_known_cities()`
проверка: git grep -c "def _known_cities" origin/dev -- apps/marketplace/discovery.py
ожидание: =1
снято:    2026-09-08 @ 8c276fa (ai-bot-platform)
задача:   DRF-1296

### `PHOTO_BIOMETRIC` объявлено и нигде не запрашивается
статус:   СЛОМАНО
где:      apps/consent/models.py — единственные упоминания вне тестов
проверка: git grep -c "PHOTO_BIOMETRIC" origin/dev -- apps
ожидание: =2
снято:    2026-09-08 @ 8c276fa (ai-bot-platform)
задача:   DRF-1011

### Докстринг экрана каталога протух — библиотеку под ним переписали
статус:   СЛОМАНО (протухший текст в коде)
где:      apps/miniapp/src/screens/CustomerCatalogScreen.tsx:11 — «the scorer sends `{service_id, score}` only»
проверка: git grep -c "the scorer sends" origin/dev -- apps/miniapp/src/screens/CustomerCatalogScreen.tsx
ожидание: =1
снято:    2026-09-08 @ 8c276fa (ai-bot-platform)
задача:   —

### Докстринг флага пилота протух — обе секции профиля давно в рендере
статус:   СЛОМАНО (протухший текст в коде)
где:      apps/miniapp/src/lib/feature-flags.ts:29 — «убраны из рендера до DRF-1520», а DRF-1520 закрыта
проверка: git grep -c "до DRF-1520" origin/dev -- apps/miniapp/src/lib/feature-flags.ts
ожидание: =1
снято:    2026-09-08 @ 8c276fa (ai-bot-platform)
задача:   DRF-1520

---

## В работе

### Две UX-формулировки застоялись открытыми PR с 07.09
статус:   В РАБОТЕ
где:      PR #1418 `fix/clarify-question-wording` (§35 п.12)
проверка: gh pr view 1418 --json state
ожидание: OPEN
снято:    2026-09-08 @ 8c276fa (ai-bot-platform)
задача:   §35 п.12

### «Готово! Записала…» не несёт следующего шага — PR открыт с 07.09
статус:   В РАБОТЕ
где:      PR #1419 `fix/booking-confirm-cta` (§25 п.3)
проверка: gh pr view 1419 --json state
ожидание: OPEN
снято:    2026-09-08 @ 8c276fa (ai-bot-platform)
задача:   §25 п.3

### T6 («подбор показывает людей») — кода нет ни в `dev`, ни в открытом PR
статус:   В РАБОТЕ (назначено, не начато)
где:      apps/miniapp/src/screens/CustomerCatalogScreen.tsx — `MasterCard` есть только в секции «Мастера»
проверка: git grep -c "MasterCard" origin/dev -- apps/miniapp/src/screens/CustomerCatalogScreen.tsx
ожидание: =2
снято:    2026-09-08 @ 8c276fa (ai-bot-platform)
задача:   §81 / T6, ~5 SP

---

## Ждёт владельца

### Формулировка частичного отзыва согласия не написана — константа `null`
статус:   ЖДЁТ ВЛАДЕЛЬЦА
где:      apps/miniapp/src/lib/customer-profile.ts:216 — `DATA_STORAGE_PARTIAL_PROCESSING_NOTE = null`
проверка: git grep -c "DATA_STORAGE_PARTIAL_PROCESSING_NOTE: string | null = null" origin/dev -- apps/miniapp/src/lib/customer-profile.ts
ожидание: =1
снято:    2026-09-08 @ 8c276fa (ai-bot-platform)
задача:   Q-CLIENT-03 / §64

### Решение владельца §81 в репозиторий бота НЕ доехало
статус:   ЖДЁТ ВЛАДЕЛЬЦА (решение принято, следа в репозитории нет)
где:      docs/OPEN_DECISIONS.md на `origin/dev` кончается на §76; §77–§81 живут только в рабочей копии `Ayla/docs/OPEN_DECISIONS.md`
проверка: git grep -c "§81" origin/dev -- docs/OPEN_DECISIONS.md
ожидание: 0
снято:    2026-09-08 @ 8c276fa (ai-bot-platform)
задача:   §81

### `set_proactive_hints` согласия не проверяет — вариант владельцем не выбран
статус:   ЖДЁТ ВЛАДЕЛЬЦА
где:      в коде помечено `TODO(Q-CLIENT-04)`
проверка: git grep -c "Q-CLIENT-04" origin/dev -- apps
ожидание: >0
снято:    2026-09-08 @ 8c276fa (ai-bot-platform)
задача:   Q-CLIENT-04 / §46

### Что видит человек без анкеты питания — вариант не выбран
статус:   ЖДЁТ ВЛАДЕЛЬЦА
проверка: нечем
почему:   OD-NUT-1 стоит открытым в `OPEN_DECISIONS.md`, но нужная редакция файла (§77+) в репозиторий не доехала, а «решение не принято» вообще не имеет представления в коде. Косвенный след — что цифровая половина экрана дневника не построена.
снято:    2026-09-08 @ 8c276fa (ai-bot-platform)
задача:   OD-NUT-1 / §65

---

## Замер не снят

### Флаги питания и подбора на боевом пилоте
статус:   НЕ ЗАМЕРЕНО
где:      контейнер `ayla-bot-staging-web-1`
проверка: нечем
почему:   значения переменных окружения на бою `git` не видит. Последний набор (`NUTRITION_ENABLED=True`, `FOOD_PHOTO_SCAN_ENABLED=False` и т.д.) снят 08.09 11:11 чужим окном; у боевых замеров есть срок годности, и `NUTRITION_PROACTIVE_*` в том наборе не назван вовсе.
снято:    2026-09-08 11:11 (замер чужой)
задача:   §59

### Числа разметки связей: `verified 0 / review_required 206 / unmapped 59`
статус:   НЕ ЗАМЕРЕНО
где:      Ayla, связи услуга ↔ шаблон
проверка: нечем
почему:   это ПРОГНОЗ миграции из текста §76, а не замер колонки: колонки `mapping_status` в `apps/catalog/models.py` не существует (см. STATE-MASTER). Число, которого нельзя снять, фактом называть нельзя.
снято:    2026-09-08 (прогноз, не замер)
задача:   §76

### Достижимость мастеров подбором на пилоте («31 мастер, достижимы 3»)
статус:   НЕ ЗАМЕРЕНО
где:      боевая база
проверка: нечем
почему:   цифра из §75 (стр. 5792), снята чужим окном и названа там отдельным дефектом отбора. Из репозитория не воспроизводится; лечить ослаблением `VERIFIED` запрещено §76.
снято:    2026-09-08 (замер чужой)
задача:   §75 / §76

### Состояния клиентских задач в Linear и их оценки в SP
статус:   НЕ ЗАМЕРЕНО
где:      Linear, DRF-1477 / DRF-1564 / DRF-1533 / DRF-1349 и др.
проверка: нечем
почему:   Linear не читается командой из белого списка. 08.09 рабочее пространство упёрлось в лимит 2500 запросов в час, и ни один статус не был снят; по правилу «пустое место лучше выдуманного» цифр здесь нет.
снято:    2026-09-08 (не снято)
задача:   —
