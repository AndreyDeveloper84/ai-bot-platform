# СОСТОЯНИЕ · Дневник питания и диетолог

Формат и правила — `docs/state/README.md`. Проверить всё:
`python tools/state_check.py --file diary`.

Одна строка вывода: дневник живёт **в боте**; в Mini App 08.09 появился первый
настоящий экран, он выложен на пилот, но **входа в него из бота нет ни одного**.
Реактивный диетолог работает на живых людях; проактивная рассылка молчит под
`DRY_RUN`. Выдуманные нормы сняты с обеих сторон провода и накрыты стражами.

---

## Работает

### Ботовый дневник — вход через фразу, а не через слаг мини-приложения
статус:   РАБОТАЕТ
где:      apps/skills/menu/marketplace.py:380 — `DIARY_TAP_TEXT = "дневник питания"`
проверка: git grep -c "DIARY_TAP_TEXT" origin/dev -- apps/skills/menu/marketplace.py
ожидание: >0
снято:    2026-09-08 @ 8c276fa (ai-bot-platform)
задача:   §37 п.5

### Ручка `wellness/today` отдаёт настоящие записи дневника
статус:   РАБОТАЕТ
где:      apps/miniapp_api/views.py:2894 — `payload["entries"] = entries`
проверка: git grep -c "payload\[\"entries\"\] = entries" origin/dev -- apps/miniapp_api/views.py
ожидание: =1
снято:    2026-09-08 @ 8c276fa (ai-bot-platform)
задача:   DRF-1329

### Экран «Сегодня» в Mini App существует и читает живые данные
статус:   РАБОТАЕТ
где:      apps/miniapp/src/screens/FoodScannerDiaryScreen.tsx → `loadDiaryToday()`
проверка: git grep -c "loadDiaryToday" origin/dev -- apps/miniapp/src/screens/FoodScannerDiaryScreen.tsx
ожидание: >0
снято:    2026-09-08 @ 8c276fa (ai-bot-platform)
задача:   DRF-1329

### Выдуманная суточная норма калорий у бота снята: ноль означает «цели нет»
статус:   РАБОТАЕТ
где:      apps/miniapp_api/views.py:2765 — `calories_target = int(summary_res.calories_goal) or None`
проверка: git grep -c "int(summary_res.calories_goal) or None" origin/dev -- apps/miniapp_api/views.py
ожидание: =1
снято:    2026-09-08 @ 8c276fa (ai-bot-platform)
задача:   §65 / OD-NUT-1

### Функция, вычислявшая БЖУ из калорий, УДАЛЕНА, а не обойдена
статус:   РАБОТАЕТ
где:      apps/miniapp/src/lib/food-scanner.ts:397 — на месте функции надгробный комментарий
проверка: git grep -c "export async function fetchDailySummary" origin/dev -- apps/miniapp/src
ожидание: 0
снято:    2026-09-08 @ 8c276fa (ai-bot-platform)
задача:   DRF-1329 / §65

### Со стороны Ayla подстановку норм держит страж без единого исключения
статус:   РАБОТАЕТ
где:      nutrition/tests/test_no_invented_norms.py:85 — `KNOWN_REMAINING: frozenset[str] = frozenset()`
проверка: git grep -c "KNOWN_REMAINING: frozenset\[str\] = frozenset()" origin/dev -- nutrition/tests/test_no_invented_norms.py
ожидание: =1
снято:    2026-09-08 @ ab76e6a1 (djangoproject-catalog)
задача:   §65

### Плоская константа 2000 ккал у Ayla снята — осталась только в объяснении
статус:   РАБОТАЕТ
где:      nutrition/services/nutrition_summary_service.py:141 — упоминание в комментарии, не в коде
проверка: git grep -c "NUTRITION_DEFAULT_CALORIES_GOAL" origin/dev -- nutrition/services/nutrition_summary_service.py
ожидание: =1
снято:    2026-09-08 @ ab76e6a1 (djangoproject-catalog)
задача:   §65

### Реактивный диетолог: строка наблюдения при открытии дневника
статус:   РАБОТАЕТ (на живых людях)
где:      apps/orchestrator/coach_observation.py
проверка: git ls-tree -r --name-only origin/dev -- apps/orchestrator/coach_observation.py
ожидание: есть
снято:    2026-09-08 @ 8c276fa (ai-bot-platform)
задача:   DRF-1464 / Q-NUTRITION-05

### «Не надоедать» и отписка слиты и лежат в коде
статус:   РАБОТАЕТ
где:      apps/nutrition_proactive/antinag.py, apps/nutrition_proactive/optout.py
проверка: git ls-tree -r --name-only origin/dev -- apps/nutrition_proactive/antinag.py apps/nutrition_proactive/optout.py
ожидание: =2
снято:    2026-09-08 @ 8c276fa (ai-bot-platform)
задача:   DRF-1468

### Проактивная рассылка стоит под `DRY_RUN` — переключатель существует
статус:   РАБОТАЕТ (молчит намеренно)
где:      config/settings/base.py — `NUTRITION_COACH_DRY_RUN`
проверка: git grep -c "NUTRITION_COACH_DRY_RUN" origin/dev -- config/settings/base.py
ожидание: >0
снято:    2026-09-08 @ 8c276fa (ai-bot-platform)
задача:   §59

### Вода у Ayla умеет запись, откат и восстановление
статус:   РАБОТАЕТ
где:      nutrition/urls.py — `internal/water/<uuid:pk>/restore/`
проверка: git grep -c "internal/water/<uuid:pk>/restore/" origin/dev -- nutrition/urls.py
ожидание: =1
снято:    2026-09-08 @ ab76e6a1 (djangoproject-catalog)
задача:   —

### Остаток §33 ПОЧИНЕН: экран называет «не подключено», а не «временно недоступно»
статус:   РАБОТАЕТ
где:      apps/miniapp/src/screens/FoodScannerProcessingScreen.tsx:237 — ветка `isNotWired`
проверка: git grep -c "Пока не подключено" origin/dev -- apps/miniapp/src/screens/FoodScannerProcessingScreen.tsx
ожидание: =1
снято:    2026-09-08 @ 8c276fa (ai-bot-platform)
задача:   §33 (строку в OPEN_DECISIONS следует закрыть)

---

## Сломано или отсутствует

### Коррекция веса порции не сохраняется: ручки update нет ни у кого
статус:   СЛОМАНО
где:      apps/integrations/ayla/nutrition_client.py — `log_meal` есть, `update_meal` нет
проверка: git grep -c "def update_meal" origin/dev -- apps/integrations/ayla/nutrition_client.py
ожидание: 0
снято:    2026-09-08 @ 8c276fa (ai-bot-platform)
задача:   DRF-1579

### У Ayla по еде только create и read — ни PATCH, ни DELETE
статус:   СЛОМАНО
где:      nutrition/urls.py — `food-log/` только POST, `summary/` только GET
проверка: git grep -c "PATCH" origin/dev -- nutrition/urls.py
ожидание: 0
снято:    2026-09-08 @ ab76e6a1 (djangoproject-catalog)
задача:   DRF-1579 / DRF-825

### Код честно признаёт, что коррекцию не имитирует
статус:   СЛОМАНО (по решению владельца, не самодеятельно)
где:      apps/skills/food_correction/skill.py:21
проверка: git ls-tree -r --name-only origin/dev -- apps/skills/food_correction/skill.py
ожидание: есть
снято:    2026-09-08 @ 8c276fa (ai-bot-platform)
задача:   §35 п.15

### Сутки считаются в UTC — приём пищи в 00:00–03:00 MSK уезжает во вчера
статус:   СЛОМАНО
где:      nutrition/services/nutrition_summary_service.py:113 — `tzinfo=timezone.utc`
проверка: git grep -c "tzinfo=timezone.utc" origin/dev -- nutrition/services/nutrition_summary_service.py
ожидание: >0
снято:    2026-09-08 @ ab76e6a1 (djangoproject-catalog)
задача:   DRF-1582

### Неделя собирается семью последовательными запросами
статус:   СЛОМАНО
где:      apps/nutrition_coach/history.py:78 — `week_picture`, цикл по семи дням
проверка: git grep -c "def week_picture" origin/dev -- apps/nutrition_coach/history.py
ожидание: =1
снято:    2026-09-08 @ 8c276fa (ai-bot-platform)
задача:   DRF-1582

### Колонка согласия сканера есть, а производственных писателей — ноль
статус:   СЛОМАНО
где:      apps/identity/models.py:213 — `food_scanner_consent_at`; пишут только тесты
проверка: git grep -c "food_scanner_consent_at" origin/dev -- apps/identity/models.py
ожидание: =1
снято:    2026-09-08 @ 8c276fa (ai-bot-platform)
задача:   DRF-1564

### Слаг `open_food_diary` объявлен, но его никто не шлёт
статус:   СЛОМАНО (выложенная поверхность без входа)
где:      apps/skills/welcome/skill.py:187 — единственное объявление на стороне бота
проверка: git grep -c "open_food_diary" origin/dev -- apps/skills apps/channels
ожидание: =1
снято:    2026-09-08 @ 8c276fa (ai-bot-platform)
задача:   FOOD_DIARY_MINIAPP_PLAN §1.5 (1 SP)

### Сканер еды закрыт флагом, и второе из трёх условий открытия не выполнено
статус:   СЛОМАНО
где:      config/settings/base.py:867 — `FOOD_PHOTO_SCAN_ENABLED` по умолчанию `False`
проверка: git grep -c "FOOD_PHOTO_SCAN_ENABLED" origin/dev -- config/settings/base.py
ожидание: >0
снято:    2026-09-08 @ 8c276fa (ai-bot-platform)
задача:   DRF-1564 (условие 2 из 3)

### Плановый файл дневника в `dev` протух: строки 1.1–1.4 стоят открытыми, а они слиты
статус:   СЛОМАНО (протухший документ в репозитории)
где:      docs/specs/FOOD_DIARY_MINIAPP_PLAN.md §0.2 — «Записи дневника уже приезжают — и выбрасываются»
проверка: git grep -c "Записи дневника уже приезжают" origin/dev -- docs/specs/FOOD_DIARY_MINIAPP_PLAN.md
ожидание: =1
снято:    2026-09-08 @ 8c276fa (ai-bot-platform)
задача:   DRF-1329

### Сухой прогон диетолога нечем прочитать: события нет, только лог
статус:   СЛОМАНО
где:      apps/nutrition_coach — метрики срабатываний исходящего фильтра не заводятся
проверка: git grep -c "dry_run" origin/dev -- apps/nutrition_coach
ожидание: >0
снято:    2026-09-08 @ 8c276fa (ai-bot-platform)
задача:   DRF-1586 / §50

---

## В работе

### DRF-1564 больше не «ветка без коммитов»: открыт PR #1487
статус:   В РАБОТЕ
где:      PR #1487 `feat/drf1564-scanner-consent`, создан 2026-09-08T13:44:29Z
проверка: gh pr view 1487 --json state
ожидание: OPEN
снято:    2026-09-08 @ 8c276fa (ai-bot-platform)
задача:   DRF-1564

### Кода PR #1487 в `origin/dev` ещё нет — ручка согласия не слита
статус:   В РАБОТЕ
где:      apps/miniapp_api/urls.py — согласия сканера в маршрутах нет
проверка: git grep -c "food_scanner_consent_at" origin/dev -- apps/miniapp_api/urls.py
ожидание: 0
снято:    2026-09-08 @ 8c276fa (ai-bot-platform)
задача:   DRF-1564

### DRF-1579 — план на 9 SP составлен, кода не тронуто
статус:   В РАБОТЕ (план, не код)
где:      `bus/DIARY-brief-drf1579.md`; в репозитории следа работы нет
проверка: git grep -c "DRF-1579" origin/dev -- apps
ожидание: 0
снято:    2026-09-08 @ 8c276fa (ai-bot-platform)
задача:   DRF-1579, 9 SP (+0–1)

---

## Ждёт владельца

### Что видит человек без анкеты питания — OD-NUT-1 открыт
статус:   ЖДЁТ ВЛАДЕЛЬЦА
проверка: нечем
почему:   блокирует ЦИФРОВУЮ половину экрана (цель, шкала, процент). «Решение не принято» не имеет представления в коде; косвенный след — что `calories_target` уходит отсутствием ключа, а шкалу рисовать не из чего.
снято:    2026-09-08 @ 8c276fa (ai-bot-platform)
задача:   OD-NUT-1 / §65

### Откуда берётся норма у того, у кого она есть — вопрос НЕ ПОСТАВЛЕН нигде
статус:   ЖДЁТ ВЛАДЕЛЬЦА
проверка: нечем
почему:   это дыра в самом реестре решений, а не в коде: OD-NUT-1 спрашивает, что видит человек БЕЗ нормы, и не спрашивает, откуда норма берётся у остальных (анкета? BMR от веса, запрещённого §35 п.10? цифра владельца?). Отсутствие вопроса командой не докажешь.
снято:    2026-09-08 @ 8c276fa (ai-bot-platform)
задача:   §65 / OD-NUT-1

### Снимать ли `DRY_RUN` с непрошеной еженедельной рассылки
статус:   ЖДЁТ ВЛАДЕЛЬЦА
где:      условие владельца — «не раньше, чем прочитаны логи `nutrition_coach.*.dry_run`»
проверка: git grep -c "NUTRITION_COACH_DRY_RUN" origin/dev -- apps
ожидание: >0
снято:    2026-09-08 @ 8c276fa (ai-bot-platform)
задача:   §59 / DRF-1586

---

## Замер не снят

### Состояние `NUTRITION_PROACTIVE_ENABLED` / `NUTRITION_PROACTIVE_DRY_RUN` на пилоте
статус:   НЕ ЗАМЕРЕНО
где:      контейнер `ayla-bot-staging-web-1`
проверка: нечем
почему:   в наборе флагов, снятом 08.09 11:11, эти две переменные НЕ НАЗВАНЫ вовсе. Состояние дневного отчёта и водных напоминаний на пилоте не замерено никем — это дыра замера, а не факт «выключено».
снято:    (не снималось)
задача:   §59

### Сколько раз сработал исходящий фильтр диетолога на бою
статус:   НЕ ЗАМЕРЕНО
где:      stdout контейнера
проверка: нечем
почему:   логи `nutrition_coach.*.dry_run` собираются только логом, без события; считать нечем иначе как чтением stdout, который плохо переживает рестарт. Это ровно то, что чинит DRF-1586.
снято:    (не снималось)
задача:   DRF-1586

### Номер DRF-1546 в Linear и в коде означает разное
статус:   НЕ ЗАМЕРЕНО
где:      Linear vs `apps/miniapp_api/views.py:2656`
проверка: нечем
почему:   в коде DRF-1546 стоит на домашнем экране «Главной»; по чужому чтению Linear тот же номер — «Разбор: прогресс цели». Разрешается сверкой в Linear, которую белый список команд не делает; 08.09 Linear упёрся в лимит.
снято:    2026-09-08 (сверка не проведена)
задача:   DRF-1546

### Оценки в SP и статусы задач направления в Linear
статус:   НЕ ЗАМЕРЕНО
где:      Linear, DRF-1329 / DRF-1547 / DRF-825 / DRF-1402 / DRF-1476 / DRF-1491 и др.
проверка: нечем
почему:   из двенадцати задач направления 08.09 прочитаны три; веерный поиск отменён из-за лимита 2500 запросов в час. Общая сумма SP по направлению не снята и здесь не подставлена.
снято:    2026-09-08 (снято 3 из 12)
задача:   —
