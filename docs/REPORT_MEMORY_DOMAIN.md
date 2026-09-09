# REPORT — окно памяти и диалога

Пишет окно памяти (исполнитель). Новое сверху.

---

## Запись 4 — 2026-08-23. PR #1239 — CI зелёный, по факту прогона

Run 32629318982: **pytest + ruff + mypy — success (30m15s)**, miniapp — pass, replay fixtures — pass. Отменённых docs-skip в этом PR не было. PR #1239 (DRF-1261 + DRF-1290) готов к ревью/мержу — за главным окном. После выкладки на пилот главным окном — живое доказательство четырьмя шагами (харнес готов, код менять не нужно).

---

## Запись 3 — 2026-08-23. DRF-1261 + DRF-1290 построены: PR #1239, четыре шага доказаны локально

Ветка `feat/drf1261-memory-extraction-bridge` (worktree `ai-bot-platform-drf1261`), PR **#1239** против `dev`. Пять коммитов + merge origin/dev (влиты соседи #1233 DRF-1283 и #1234 DRF-1284 — конфликтов нет, их правки не тронуты).

### Что построено

**Извлечение** — `apps/persona/memory_extract.py` (переписан). Пять ключей, правило «не фабриковать» соблюдено жёстко:
- `diet` — только названные типы (`vegan/vegetarian/keto/halal/kosher`). «я веган» → `diet_type=vegan` БЕЗ развёрнутых `excluded_foods`.
- Пищевые исключения («не ем мясо/свинину», «не пью молоко») — **drop с логом** `diet_exclusion_dropped`. Приёмник (Ayla `diet_type`, fixed choices) не принимает их без искажения; «не ем мясо → vegetarian» — фабрикация, удалена (третья регулярка, о которой писал бриф).
- `preferred_time_slots` («мне удобно после 18:00» → evening; слоты контракта), `preferred_districts` (verbatim, с инфлексией — см. ограничения), `price_range` (min/max/«не готова платить больше N», валюта только RUB), `favorite_masters` (только явные: «мой мастер — Анна», «записываюсь только к Анне»; «хожу к Анне» без «только/всегда» — НЕ факт, по ruling).
- Session context ≠ memory: маркеры «сегодня/сейчас/завтра» гасят извлечение; у времени/района/бюджета/мастера обязателен долговременный якорь.
- Ретракция («я теперь снова ем мясо», «я больше не веган») → кандидат `value=none` — драйвер supersession.

**DRF-1290** — аллергические формулировки: клауза с маркером (`аллерг`, `непереносимост`) вырезается до извлечения, drop логируется WARNING'ом `allergy_clause_dropped` (без содержания фразы в логе). Чистые клаузы того же сообщения извлекаются. «Не люблю орехи» — не аллергия и не consumption-исключение: под пятью ключами ей места нет, не сохраняется (без drop-лога — это не sensitive-событие).

**Политика** — `memory_key_policy.py`: `diet`/`price_range` — single; `preferred_districts`/`preferred_time_slots`/`favorite_masters` — multi. `memory_writer.supersede_entries` — write-side supersession: `status=superseded`, `superseded_by`, `reason=changed`, `updated_at`; tombstone НЕ ставится (история хранится, 152-ФЗ экспорт видит строку).

**Мост** — `apps/orchestrator/memory/ayla_bridge.py` (новый) + вызов из `record_explicit_green_facts`. Маппинг: слоты/районы — union-merge (GET+PATCH), `price_range` → `price_range_min/max`, `diet` → `diet_type`, ретракция → `diet_type=""`. Мосту предлагаются ВСЕ кандидаты хода (не только свежезаписанные) — идемпотентный LWW лечит транзиентный сбой при повторе фразы.

**Циклы** — `memory_commands.py` + `memory_surface.py` + `handler.py` (передан `bot_user`):
- «покажи» = локальный conflict-resolved вид + Ayla declared prefs (ask-flow W5 писал их мимо локальной памяти — человек их не видел), без дублей мостовых копий. Формат прежний («Помню, что ты …»), новые рендеры для пяти ключей.
- «забудь всё про моё питание» — доменное забвение: negative lookahead отделяет от forget-all; tombstone'ятся ВСЕ живые строки домена (включая superseded), история не стирается, другие домены не тронуты; Ayla-поле очищается тем же дыханием (`clear_declared_fields`). «забудь, что я веган» — по-прежнему точечно. «удалить» после «забудь всё» — forget-all + очистка всех мостовых полей.

### Consent — как решён (Ответ 4)

Молчаливое запоминание по общему согласию онбординга, БЕЗ per-write подтверждения — как велит Ответ 4. Механика: два существующих гейта — локальная запись под PERSONAL_DATA (`can_store_green_memory`, ADR-0011 §11), провод в Ayla под `memory_green` (`has_memory_consent`, MEMORY_CONSENT_SPEC) — гейт ДО провода, оба fail-closed. Шаг «получить право на persistent write» из цикла убран; его место занимают «покажи полный список» и «забудь по-настоящему» — им дан полный вес (см. циклы выше). Session context отсекается извлекателем (маркеры + якоря), поведение в факт не превращается вообще (поведенческого пути в коде нет).

### Contract gaps (для заведения задач)

1. **`excluded_foods` / `user_note` — нет полей в приёмнике.** Предложение расширения (чужая выкладка, НЕ делаю): `UserPersonalContext.excluded_foods JSONField(default=list)` (свободные токены, как `skin_sensitivities`) + `user_note CharField/TextField blank`; в контракт — аддитивные поля каталога (bump версии документа). После этого извлечение исключений включается одной строкой: drop-правило заменяется кандидатом `{diet_type: None, excluded_foods: [...]}` — код-точка помечена в `memory_extract.py`.
2. **`favorite_masters` несовместим по смыслу:** контракт = UUID `SpecialistProfile` («rebooked ≥3»), ruling = явно названный мастер (имя). Кросс-тенантный резолв имени → UUID невозможен (то же ограничение задокументировано в `memory_ask`). Варианты: отдельное поле `favorite_master_names list[str]`, либо резолв через поиск мастеров с подтверждением. Сейчас: имя хранится локально, показывается/забывается; в Ayla не пишется (лог `favorite_master_unbridgeable`).
3. **Очистка `price_range_min/max` невозможна по контракту:** `value` JSONField отвергает null (проверено на DRF стенде), `""` ломает Decimal. «Забудь бюджет» чистит локальную память, но Ayla-поля останутся — лог `clear_skipped`. Предложение: разрешить `null` в `_UpdateItemSerializer.value` (`allow_null=True`) как явное «очистить поле» — аддитивно, обратно совместимо.
4. `skin_sensitivities` — поле есть, ruling запрещает green-активацию (DRF-1290): не пишем никогда. Периметр — отдельная задача.

### Аллергии — что бот ГОВОРИТ (открытый вопрос из DRF-1290, поднимаю)

Сейчас бот молчит: фраза «у меня аллергия на орехи» уходит в консьерж как обычная, ничего не сохраняется, человек решит, что его услышали и запомнили. Это худший неявный исход. Вариант без продукта: консьерж-подсказка в промпт «если человек упоминает аллергию — честно скажи, что это ты пока не запоминаешь» — но это формулировка, которую должен утвердить владелец. Жду решения; drop-лог даёт наблюдаемость (`allergy_clause_dropped` считается в логах).

### Доказательство — четыре шага

**На пилоте невозможно:** там `789e82b` (PR #1237), моей ветки нет, выкладка за главным окном — зафиксировано как ограничение. Доказано максимумом локально: живой Postgres 16 (контейнер `pg-drf1261`), реальный write path, реальные команды, Ayla wire — записывающий stub:

1. «мне удобно после 18:00» / «я веган» / «ориентируюсь на бюджет до 3500» → 3 MemoryEntry с `provenance=user_stated`, status=active + три PATCH в Ayla (`preferred_time_slots=[evening]`, `diet_type=vegan`, `price_range_max=3500.00`).
2. «покажи, что знаешь обо мне» → «Помню, что ты предпочитает время: по вечерам; придерживается веганского питания; ориентируется на бюджет до 3 500 ₽.»
3. «я на кето» → vegan-строка `status=superseded, reason=changed, superseded_by=<keto>`; показ: «…придерживается кето-диеты.»
4. «забудь всё про моё питание» → «Готово — забыла всё, что знала: питание.»; живых diet-строк нет, история (4 строки) цела, время и бюджет живы, Ayla `diet_type=""`.
5. Бонус: «у меня аллергия на орехи» → 0 записей + WARNING `allergy_clause_dropped`.

Харнес — `scratchpad/drf1261_proof.py` (в git не коммичен, одноразовый).

### CI / гейты

Локально (Postgres-стенд): `apps/persona`, `apps/identity/services`, `apps/orchestrator` (memory: bridge/block/ask/personal_context_write/ayla_link), `apps/consent`, `apps/integrations/ayla`, `apps/channels`, `tests/smoke` — зелёные. ruff + ruff format + mypy по изменённым файлам — чисто. detect-secrets (pre-commit, uvx) — чисто. CI PR #1239 — вердикт по факту прогона (см. коммит-пуш; `gh run view --json conclusion`, cancelled ≠ fail).

### Найдено рядом, не тронуто

- `memory_ask.py` (W5 ask-flow) пишет в Ayla с `source=conversational` мимо локальной MemoryEntry — расхождение предсуществует; «покажи» теперь его закрывает домешиванием. Унификация — отдельная задача.
- DRF-1284 (`nutrition_context.py`, handler ~851) и DRF-1283 (~`looks_like_booking_request`) — влиты через origin/dev, не пересеклись.
- `.venv` основного дерева пуст; venv тестов держит .pth на СВОЁ дерево — голый `python script.py` из worktree резолвит `apps` мимо (pytest не подвержен: prepend rootdir). Ловушка для будущих харнесов.
- DRF-1292 (обнаружимость списка памяти): дешёвое естественное место — после ПЕРВОЙ молчаливой записи мягкое «я запомню это; посмотреть, что я помню, можно словами „покажи, что знаешь обо мне“» — один раз на пользователя, не чаще. Не реализовано (вне объёма), записано сюда.

### Коммиты

`71aa975` извлечение · `908fd4e` политика+supersession · `0d70d9e` мост · `bb3dc93` циклы покажи/исправь/забудь · `3593fad` тесты · `e7c9d7d` merge origin/dev.

---

## Запись 2 — 2026-08-23. PR #1230 — CI зелёный, по факту прогона

Ответ 2 обработан полностью:
- `types-requests` добавлен в dev extra (pyproject.toml + uv.lock, коммит `725db95`) — каталог из mypy не исключён.
- Вторичный эффект типизации: mypy нашёл `apps/integrations/yclients/tests/test_client.py:441` — `BaseAdapter.max_retries` (есть только у HTTPAdapter). Исправлено isinstance-гардом (коммит `19ae481`), таргетированный mypy-прогон чист.
- `tests/acceptance/README.md` — что это, почему не в CI, как запускать вручную.

CI (run 32623406585, по факту): **pytest + ruff + mypy — success (28m22s)**, replay fixtures — pass, miniapp — pass. Три «fail» в `gh pr checks` — cancelled docs-skip (проверено через `gh run view --json conclusion`), как и было описано в ответе 2.

PR #1230 готов к ревью/мержу (за главным окном).

---

## Запись 1 — 2026-08-23. Приёмка брифа: монитор, замер, раскладка

**Монитор:** поставлен (cron `a1756f68`, каждые 19 минут, читаю `docs/REPLY_MEMORY.md`). Оба ответа прочитаны; PR #1230 починен по ответу 2 (types-requests в dev extra; isinstance-гард в `yclients/tests/test_client.py:441` — вторичный эффект типизации requests; `tests/acceptance/README.md` добавлен). CI пуша `19ae481` — в фоне, вердикт по факту.

**Замер воспроизведён полностью:**
- `apps/persona/memory_extract.py:47-62` — ровно три регулярки, все про диету. Находка: третья (`я не ем мяс` → vegetarian) уже сегодня фабрикует факт — прямое нарушение запрета нормализации DRF-1260.
- `apps/identity/services/memory_key_policy.py:52-54` — один разрешённый ключ `diet` (single).
- `djangoproject/users/models.py:412-537` — ровно 12 персонализируемых полей `UserPersonalContext`, все пустые (писателя нет).

**Раскладка:** извлечение 5 ключей (structured, no-fabrication, allergy → не сохранять) → key policy (кардинальности) → мост (`apps/integrations/ayla/` → internal personal context API, provenance=user_stated) → цикл покажи/исправь/забудь на DRF-1262/1263 → живое доказательство 4 шагами на пилоте (без выкладки — выкладка за главным окном).

**Риски (поднимаю, не додумываю):**
1. **Consent boundary**: DRF-1260 требует persistent write через утверждённый consent/memory flow, но вопрос/момент не определён. Вариант по умолчанию не выбираю молча — предложу минимальный: write только после явного подтверждения ботом («Запомнить?»), либо эскалация.
2. **Схема `diet` шире поля Ayla**: целевой домен (`diet_type` + `excluded_foods` + `user_note`) не ложится в `diet_type` (fixed choices). Проверю `PERSONAL_CONTEXT_INTERNAL_API_CONTRACT.md`; если поля нет — это gap контракта, подниму.
3. Соседи в `handler.py`: DRF-1284 (~:820, питание в промпт) и DRF-1283 (~:791, booking-ветка) — работаю от свежего origin/dev, вливаю перед пушем.
