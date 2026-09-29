# План: второй проход окна архитектуры общения

**Документ плана:** `C:\Users\user\PycharmProjects\Ayla\docs\PLAN_CONVERSATION_ARCH.md` (каноническая копия; обновить после утверждения этой редакции).

**Основание:** `docs/REPLY_CONVERSATION_ARCH.md` (Ответы 1–3): OD-1 (гибрид: чипы + равноправный свободный текст), OD-2 (корпуса нет, классификатор не строим, `goal_text` дословно, склонность к уточнению), OD-4 (территории), DRF-1190 (C01 — три намерения), Ответ 3 (экран — проекция понимания; контракт документа состояния — сейчас; CI лежит).

**Режимы:** backend — merge в `dev` = боевая выкладка; PR не открываю и не мержу; работа аддитивная; миграции отдельным PR от кода. Живой контур не меряю. **CI репозитория не работает (лимит минут исчерчен, джобы не стартуют — замер главного окна): проверки гоняю локально и перечисляю в отчёте; зелёный CI не жду.**

## Главная инструкция Ответа 3: экран — тупой отрисовщик

Экран не принимает ни одного решения: получил документ «что известно / чего не хватает / что предложить» — отрисовал. Когда появится Decision Orchestrator, меняется только сервер, экран не трогаем. Свойство контракта: **экран не должен уметь вычислить содержимое документа самостоятельно.**

`ClientGoal` — durable-факт (шесть полей, принято без изменений); документ состояния — эфемерная проекция. В одну таблицу не смешивать.

## Названный выбор (требование Ответа 1)

**Полный экран выбора цели, не промежуточный редирект** — в рамке проекции: экран отображает серверный документ состояния; редирект не закрывает DRF-1190 и рамку Ответа 3 не реализует.

## Территории и узлы координации

- Моё (backend `beautygo_backend-conv`): новое приложение `goals/`, маппинг в `services/`, миграции к ним.
- Моё (conv): `apps/miniapp/src/lib/max-sdk.ts`, `apps/miniapp/src/screens/` (кроме `admin/`).
- **Не моё, требует запроса:** `apps/miniapp/src/App.tsx` (главное окно) — регистрация маршрута; `analytics/event_catalogue.py` (не роздано) — +2 события; `apps/skills/welcome/skill.py` (салонный бот; Ответ 1 п.4 передал докдолг мне — правлю с пометкой в отчёте).
- Не трогать: `users/permissions.py`, `users/middleware.py`, `tenants/appointments_api.py`.

## Этап 0. Подготовка деревьев

- `beautygo_backend-conv` — detached HEAD (merge PR #229): `git fetch origin`, ветка `feat/goal-layer` от `origin/dev`.
- `ai-bot-platform-conv` — ветка `feat/goal-select-entry`.

## Этап 1. Backend PR-1 «структура» (только модели + миграции)

1. Новое приложение `goals/` (`apps.py`, `models.py`, `migrations/`, `tests/`). Модель — `ClientGoal` (в `nutrition/models.py:377` есть `Goal(TextChoices)` — избегаем коллизии имён).
2. `goals/models.py` — `ClientGoal`:
   - `id` UUID PK; `client` = `ForeignKey(settings.AUTH_USER_MODEL, on_delete=PROTECT, related_name="client_goals")` (образец `appointments/models.py:38-42`);
   - `goal_key` SlugField(NULL, blank), `goal_text` TextField(NULL, blank);
   - `selected_at` DateTimeField(default=timezone.now); `source_channel` CharField(choices: `bot`/`miniapp`); `is_active` BooleanField(default=True);
   - `CheckConstraint`: хотя бы одно из `goal_key`/`goal_text` не пустое;
   - Partial `UniqueConstraint(fields=["client"], condition=Q(is_active=True))` — одна активная цель на клиента.
3. `services/models.py` — аддитивно, по конвенциям `ServiceTemplate` (русские docstring/help_text, Meta ordering+indexes, `__str__`):
   - `GoalOption` — курируемая подсказка: `key` SlugField(unique), `label` CharField(100), `sort_order`, `is_active`;
   - `GoalOptionCategory` — маппинг: `goal_option` FK(CASCADE, related_name="category_links"), `category` FK(`ServiceCategory`, PROTECT), `sort_order`; `unique_together(goal_option, category)`.
4. `djangoProject/settings/base.py` — `INSTALLED_APPS += "goals"`.
5. `makemigrations goals services` — только структура (CLAUDE.md:95), никаких данных в миграциях.

## Этап 2. Backend PR-2 «код, использующий схему»

1. **Сид:** `services/seeds/goal_options_2026-08.json` + `services/management/commands/seed_goal_options.py` — по образцу `seed_canonical_catalog.py` (идемпотентность, `--dry-run`, `transaction.atomic`). ~~Стартовый набор — из канона~~ **Отклонено владельцем 2026-08-19: словарь канона (recovery/appearance/wellness) — предметные области, не человеческие цели. Набор — 5–8 целей словами людей; сид НЕ запускать до подтверждённого владельцем списка.**
2. **Админка:** `services/admin.py` — `GoalOptionAdmin` + `GoalOptionCategoryInline` (образец `ServiceTemplateInline`).
3. **Контракт документа состояния (минимальный `Unified DecisionContext`)** — сердце прохода. `goals/decision_context.py` — чистая функция `build_decision_context(client) -> dict`:
   - `known`: активная цель (`goal_key`/`goal_text`, `selected_at`, `source_channel`) или отсутствие;
   - `missing`: список недостающего — на этом проходе один возможный элемент: `goal` (цель не установлена) или `goal_clarification` (есть `goal_text` без ключа — уверенность низкая, OD-1);
   - `suggestions`: подсказки под текущее состояние — чипы из `GoalOption` (активные, по `sort_order`); при `goal_clarification` — первый уточняющий вопрос как часть документа (статический текст элемента `missing`, не логика экрана);
   - `intents`: три намерения DRF-1190 как данные (`choose_suggested` / `formulate_own` / `need_guidance`) — чтобы экран не решал даже их состав.
   - Экран по этому документу не может вычислить ничего, кроме отображения, — свойство проверяется тестом: документ не содержит полей, из которых выводится другое содержимое.
4. **Внутренний API** (`/api/v1/internal/`, auth `IsBotServiceWithVerifiedClient`, образец `users/catalog_recommendations_api.py:373-377`):
   - `GET .../decision-context/` → документ состояния;
   - `POST .../goals/select/` — тело: `goal_key` XOR `goal_text` XOR `intent=need_guidance`; закрывает прежнюю активную цель; `goal_text` хранит дословно (OD-2); эмитит `goal_selected` (кроме чистого `need_guidance`); **ответ — обновлённый документ состояния** (цикл проекции: уточнение — не тупик, а перерисовка);
   - регистрация в `djangoProject/urls.py` (не из запретного списка — пометить в отчёте).
   - «Не понимаю, чего хочу» — **не выход**: сервер переводит клиента в состояние `need_guidance` и возвращает документ с первым ведущим вопросом в `missing`; ведущий сценарий дальше первого вопроса в этом проходе не строим (Ответ 3 разрешает), но человек остаётся на поверхности.
5. **Резолвер** `goals/resolution.py`: активная цель → `category_id`s через `GoalOptionCategory`; `goal_text` — только точное совпадение по `label` (casefold), иначе `None` = уточнить (принято Ответом 3 как есть). Подключение к вызывающим — за флагом `GOAL_RESOLUTION_ENABLED` (default false); движок не меняется.
6. **Аналитика:** `GOAL_SELECTED`, `RECOMMENDATION_SHOWN` +2 строки в `analytics/event_catalogue.py` (без миграции; вне розданных территорий — пометка на ревью). Эмиссия `goal_selected` — в select-эндпоинте. `recommendation_shown` не подключается (точка — существующая ручка, поведение менять нельзя) — в отчёт.
7. **Тесты** `goals/tests/`: constraint'ы модели; одна активная цель; auth fail-closed; select-флоу (key / text / замена / need_guidance); документ состояния (пустой клиент / с ключом / с текстом без ключа → `goal_clarification` + вопрос в `missing`); резолвер (совпадение / отказ маппить); свойство «экран не вычисляет» (документ сериализуется без производных полей).

## Этап 3. Conv PR-3 «вход и поверхность цели»

1. `apps/miniapp/src/lib/max-sdk.ts` — +4 записи в `_ROUTE_MAP`:
   - `open_food_scan` → `/customer/food-scanner/capture` (экран есть, `App.tsx:1036-1039`);
   - `open_water_add_250` → `/customer/wellness`;
   - `open_goal_select` → `/customer/goal-select`;
   - `open_home` → `/customer/main` (`App.tsx:984-987`: home = «Мои записи», решение оркестратора phase 3.2 — свежее спеки).
   - Auto-log «+250 мл» дашборд не умеет — отдельной задачей, в отчёт.
2. `apps/skills/welcome/skill.py` (передано Ответом 1 п.4):
   - docstring `:618-625` — убрать несуществующую маршрутизацию, указать `_ROUTE_MAP` как источник истины;
   - link-ветка `:649-658` — пути привести к тем же маршрутам, что и слаги.
3. `apps/miniapp_api/` — прокси (образец `customer_recommendations`, `views.py:1917-1942`):
   - `GET /api/v1/customer/decision-context` → backend `decision-context/`;
   - `POST /api/v1/customer/goals/select` → backend `goals/select/` (ответ — документ, проксируется как есть);
   - +2 строки в `apps/miniapp_api/urls.py`.
4. **Поверхность** `apps/miniapp/src/screens/GoalSelectScreen.tsx` — **тупой отрисовщик**:
   - загрузка документа (`ScreenLayout` + `loading/ok/error`, образец `CustomerCatalogScreen.tsx`);
   - рендер секций строго по документу: известное (активная цель), недостающее (включая уточняющий/ведущий вопрос как текст из документа), подсказки (чипы: `.chip`/`.chip--active`, `globals.css:668-690`, a11y `role="radiogroup"` — образец `FoodScannerManualScreen.tsx:156-170`);
   - единое поле свободного ввода на той же поверхности (textarea-паттерн `CustomerBookingConfirmScreen.tsx:462-489`) — «сформулирую свою» не отдельная ветка, а тот же ввод;
   - любое действие = POST на сервер → перерисовка по вернувшемуся документу. Никаких локальных вычислений «что показать дальше»;
   - «не понимаю, чего хочу» — кнопка из `intents` документа, отправляет `need_guidance`; экран остаётся, отрисовывает ведущий вопрос. `closeApp()` здесь **не используется** (прямое указание Ответа 3);
   - тексты — русские литералы; тест `GoalSelectScreen.test.tsx`: рендер по моковому документу, отсутствие локальной логики выбора (контроль: смена документа → смена отображения без смены кода).
5. Классификатор не строим (OD-2).

## Этап 4. Координация (не код, в отчёт главному окну)

- **Блокирующий запрос:** маршрут `/customer/goal-select` в `CustomerRoutes` (`App.tsx:966-1065`) — территория главного окна; без него поверхность недостижима по deeplink.
- Пометки на ревью: `analytics/event_catalogue.py`, `djangoProject/urls.py`, `apps/skills/welcome/skill.py` — вне/на грани розданного.
- Следующие шаги вне прохода: эмиссия `recommendation_shown`, auto-log воды, ведущий сценарий после первого вопроса, наполнение чипов владельцем, подключение резолвера к `RecommendationQuery` под флагом.

## Порядок и верификация

- Порядок мержа (мержит главное окно после GO владельца; сейчас — когда поднимут лимит CI): PR-1 → PR-2 → PR-3. Работа над PR-2/PR-3 параллельна, экран гоняется против мокового документа до мержа PR-2.
- Локальные проверки (CI лежит — перечислить в отчёте фактически прогнанное): `pytest goals services` (backend); `npm run typecheck && npm run test` (miniapp); `pytest apps/miniapp_api` (conv).
- Отчёт о проходе — обновить `docs/REPORT_CONVERSATION_ARCH.md`; копию плана — `docs/PLAN_CONVERSATION_ARCH.md`.
