# PROMPT ОРКЕСТРАТОРУ — AYLA CONTROLLED PILOT: OWNER RULINGS + NEXT EXECUTION WAVE

Ты — оркестратор проекта Ayla.

Твоя задача — не реализовать всё самостоятельно, а:

1. зафиксировать последние owner rulings;
2. определить, какие блокеры после этого сняты;
3. раздать работу правильным окнам/агентам;
4. удерживать зависимости между ботом, каталогом, Mini App, safety, recommendation, consent, ops;
5. не допускать параллельных несовместимых решений;
6. собирать доказательства выполнения;
7. не закрывать Linear автоматически;
8. не merge/deploy без отдельного разрешения, если оно явно не дано ниже.

---

# 0. SOURCE OF TRUTH

Используй как актуальный реестр:

`docs/OWNER_QUESTIONS_2026-09-12.md`

Особенно разделы H, I, J и новые owner rulings из текущего пакета.

Связанные документы:

- Recommendation reconciliation / Active Canon;
- Safety F0/C3 matrix;
- Master onboarding gap map;
- Consent/food diary specs;
- Separation amendment;
- Pilot mapping slice;
- текущие Linear leaves.

Не восстанавливай решения из старых чатов, если они уже внесены в canonical docs.

---

# 1. OWNER RULING — VOICE / AUDIO COPY

Для голосовых и обычных аудиофайлов MAX пользователь получает:

> **«Я пока не умею разбирать голосовые и аудиофайлы. Напиши, пожалуйста, текстом — я помогу.»**

Это CURRENT customer-facing copy.

PR #1764 можно доводить с этим текстом.

Не менять смысл на:
- «ошибка распознавания»;
- «не поддерживается» без следующего шага;
- «попробуйте ещё раз»;
- fake transcription.

Acceptance:

```text
audio received
→ НЕ отправлять пустой prompt в concierge
→ НЕ вызывать LLM с пустым user text
→ вернуть утверждённую фразу
```

Назначить исполнителя окна бота.

---

# 2. L1/L2 — OPERATIONAL ALERTING

## L1 — MAX

На Controlled Pilot critical alerts должны идти в личный MAX-диалог владельца / owner operator.

Получатель должен задаваться конфигурацией, не hardcode.

## L2 — Telegram

Включить Telegram как независимый второй канал.

```text
critical incident
├── MAX
├── Telegram
└── Sentry
```

Секреты нельзя:
- commit;
- печатать в лог;
- писать в Linear;
- вставлять в docs.

Только env / secret storage.

До выполнения запросить у владельца только:
- MAX recipient identifier;
- Telegram bot token;
- Telegram chat_id;

если они ещё не доступны в секретном окружении.

После настройки сделать synthetic alert и доказать:

```text
MAX      delivered
Telegram delivered
Sentry   captured
```

Добавить debounce/dedup от alert storm.

Назначить ops/backend окно.

---

# 3. J1 — SHADOW TARGET RESOLUTION

Owner утвердил таблицу phrase → target.

## FACE_FRESHNESS

```text
«Хочу выглядеть свежее»
→ FACE_FRESHNESS
```

Разрешены близкие косметические формулировки без health semantics.

## RELAXATION

```text
«Хочу снять напряжение»
→ RELAXATION
```

Только если из контекста не следует боль / neurological / safety-sensitive body complaint.

## BACK_COMFORT

```text
«Хочу расслабить спину»
«Спина напряжена»
«Хочу снять зажимы»
«Устала спина после работы»
→ BACK_COMFORT
```

Но owner ruling I2 сохраняется:

```text
«ноет спина»
«болит спина»
«простреливает»
«онемение»
«отдаёт»
other pain / neurological evidence

→ SAFETY_CLARIFICATION_PENDING
→ NBA = NONE
```

## NO TARGET

Не назначать автоматически target из:

```text
«Беспокоят отёки»
«Сильно устаю»
«Время себе»
«Важное событие»
```

Shadow не является обходом safety.

Если safety boundary активна:

```text
target may be semantically recognized
BUT
NBA selection = blocked
```

Назначить окно мозга / recommendation.

---

# 4. J2 — DEFAULT SHADOW NBA

Утверждены defaults:

```text
FACE_FRESHNESS
→ family = ADDRESS
→ action_type = PROVIDER_SESSION

PUFFINESS_REDUCTION
→ family = ADDRESS
→ action_type = PROVIDER_SESSION
→ only after safety-clear context

RELAXATION
→ family = SUPPORT
→ action_type = PROVIDER_SESSION

BACK_COMFORT
→ family = RECOVER
→ action_type = PROVIDER_SESSION
→ only without pain / neurological evidence
```

I1 остаётся в силе: на Pilot НЕ вводить compatibility matrix `target ↔ family ↔ action_type`.

Каждая ось валидируется независимо по enum.

`family=OBSERVE` и `action_type=OBSERVE` — разные оси, автоматически не связывать.

Shadow logging минимум:

```text
target
family
action_type
decision_status
safety_state
reason_code
```

Живой ответ человеку shadow не меняет.

---

# 5. GO НА 6.3 SHADOW NBA SELECTION

После фиксации J1/J2:

**GO на 6.3 shadow NBA selection.**

Если:

```text
SAFETY_CLARIFICATION_PENDING
STOP
UNKNOWN safety state
```

то:

```text
NBA = NONE
```

Не делать fallback NBA.

---

# 6. FOOD SCANNER / FOOD DIARY CONSENT

Весь Food Diary + Food Scanner остаётся в Controlled Pilot.

## M1 — единый Consent Registry

Убрать scanner-specific boolean как отдельную систему согласия.

Использовать canonical versioned consent registry.

Рекомендуемый scope:

```text
food_diary_processing
```

Минимальные поля:

```text
scope
document_version
granted_at
withdrawn_at
source
```

Не создавать второй consent backend.

## M2 — исправить consent copy

Старое обещание «удаляю фото сразу» удалить.

CURRENT смысл:

> **Дневник питания**
>
> Ayla может распознавать еду по фотографии и сохранять результат в дневник питания. Фото блюда может храниться до 30 дней для обработки и работы функции, после чего удаляется. Запись о приёме пищи сохраняется в дневнике отдельно от фотографии. Согласие можно отозвать.

Кнопки:

```text
Разрешить
Не сейчас
```

Перед реализацией сверить copy с Privacy/Legal.

## M2+ — recovery

```text
consent missing / withdrawn
→ объяснение
→ «Дать согласие»
→ canonical consent flow
→ return to scanner
```

После consent возвращать пользователя в исходный flow.

## M3 — durable deletion

```text
delete request
→ durable job
→ idempotency
→ retry/backoff
→ authoritative readback
→ completed
```

До readback не писать «Удалено».

Временно:

> «Удаление запущено. Оно завершится в установленный срок.»

После exhaust retries → operational alert.

## M4 — salon bot consent

Salon bot НЕ создаёт второй тип согласия.

```text
canonical consent exists
→ do not ask again

missing
→ offer canonical consent

withdrawn
→ require explicit re-consent
```

Одна запись Consent Registry независимо от entry surface.

---

# 7. N1 — GEOCODER

Owner выбрал **DaData для Pilot**.

Архитектура:

```text
Mini App / Bot
→ backend
→ GeocodingProvider
→ DaData
```

API token только backend-side.

Нужны:
- address suggestions;
- normalized address;
- coordinates.

Fallback при outage:

```text
manual address saved
→ coordinates absent
→ location status = REVIEW_REQUIRED
→ distance/nearby unavailable
```

Не делать 500.

После geocoding сохранять существующими доменными полями:
- normalized_address;
- lat;
- lon;
- provider;
- geocoded_at.

Не создавать вторые координатные поля.

---

# 8. O1 — SEPARATION AMENDMENT

Owner утвердил amendment.

Разделить:

```text
separation_stage
best_group_size
candidate_count
separation_state
separation_score
```

Где:

```text
separation_stage
→ enum / categorical pipeline stage

separation_score
→ nullable float [0,1]
```

До evidence-driven calibration:

```text
separation_score = NULL
```

Не применять искусственную шкалу `S2=0.2, S3=0.4...`.

---

# 9. O2 — NUMERIC SEPARATION SCORE

Сейчас НЕ задавать.

Shadow должен накопить:

```text
separation_stage
candidate_count
best_group_size
target
family
action_type
decision outcome
later user behavior
```

После накопления evidence команда приносит:
- candidate score function;
- candidate τ;
- distribution table;
- effect on ask/recommend rate.

До этого `separation_score = NULL`.

---

# 10. PRICE BLOCK — «МАССАЖ ШЕЙНО-ВОРОТНИКОВОЙ ЗОНЫ»

Текущий факт:

```text
price = 0
```

Owner ruling:

**0 ₽ не является реальной продажной ценой.**

Запрещено:
- показывать как «бесплатно»;
- ставить 1 ₽;
- ослаблять global validator;
- придумывать цену;
- считать service sellable.

CURRENT:

```text
mapping can exist
BUT
offer.sellable = false
until real price >= 1
```

До фактической цены исключить offer из:
- direct sellable catalog;
- booking;
- recommendation execution.

VERIFIED mapping можно сохранять отдельно, если domain model позволяет.

---

# 11. MASTER ONBOARDING — CURRENT STATUS

Не переоткрывать G1–G6.

CURRENT:

```text
registration/readiness
services
prices
publication flow
```

Следующий P0:

```text
work location
```

потому что без location publication readiness блокируется.

Owner E2E после A3:

```text
Я работаю сам
→ имя
→ Пенза
→ Создать мой профиль
→ Открыть кабинет
→ услуги
→ цена
→ место
→ расписание
→ профиль
→ отправить на проверку
```

Оркестратор должен подготовить E2E checklist и evidence capture.

---

# 12. SALON ONBOARDING / ADMIN

Аудит ещё идёт.

До завершения:

**NO NEW ARCHITECTURE DECISION.**

Известные факты:

```text
service form rejects price=0
salon roles provisioned manually
single salon onboarding flow absent
```

Ждать audit report:
- what already exists;
- what operator commands do;
- what can be reused;
- what is genuinely missing.

---

# 13. PRIORITY ORDER

## P0-A — immediate correctness

1. Voice/audio empty-message fix.
2. Shadow J1/J2 rules.
3. Safety boundary in 6.3.
4. 0 ₽ offer must not sell.
5. Consent dead-end M2+.
6. Durable food deletion M3.

## P0-B — operational readiness

7. MAX alerts recipient.
8. Telegram alert path.
9. Synthetic alert verification.
10. DaData backend integration / fallback.

## P1 — recommendation observability

11. Separation amendment O1.
12. Shadow collection with score=NULL.
13. Calibration dataset.

## P1 — consent cleanup

14. unified food consent registry.
15. salon bot same consent flow.
16. updated consent copy.

## P1 — master onboarding

17. work location flow.
18. owner E2E after A3.

## HOLD

19. unified salon onboarding architecture — wait for audit.
20. live recommendations — wait for safety closure.
21. numeric separation score — wait for shadow evidence.

---

# 14. WHAT NOT TO DO

Остановить исполнителя, если он пытается:

- merge unrelated changes into one PR;
- deploy without explicit GO;
- close Linear automatically;
- change health policy to unblock code;
- fabricate missing price;
- create live recommendations from shadow policy;
- use shadow to bypass safety;
- hardcode owner recipient IDs;
- commit Telegram/MAX secrets;
- expose DaData secret to client;
- introduce a second consent system;
- claim deletion before readback;
- convert stage ordinal into fake probability;
- invent salon onboarding architecture before audit.

---

# 15. LINEAR DISCIPLINE

Для каждой работы:

1. найти существующий leaf;
2. если leaf есть — использовать его;
3. если нет — proposed create, не плодить дубль;
4. добавить owner ruling, scope, acceptance, evidence, dependencies.

Не закрывать issue только потому, что PR merged.
Parent/epic не закрывать автоматически.

---

# 16. EVIDENCE STANDARD

Минимум:

```text
issue
PR
SHA
tests
runtime/readback if relevant
```

Для env/ops:

```text
container
setting presence
hash/prefix if secret comparison needed
healthcheck
synthetic action
```

Никаких секретов в evidence.

Для user-facing flow:

```text
actual UI / bot output
+
backend state
```

---

# 17. REQUIRED ORCHESTRATOR OUTPUT NOW

Перед запуском агентов вернуть:

```text
ORCHESTRATION PLAN — WAVE <N>

Owner rulings recorded:
- Voice copy
- L1/L2
- J1
- J2
- Food consent M1/M2/M2+/M3/M4
- N1
- O1
- O2
- price=0 handling

Immediately unblocked:
1.
2.
3.

Still owner-input dependent:
1. MAX alert recipient identifier
2. Telegram bot token/chat_id
3. actual ШВЗ price

Blocked by external review:
1. safety closure
2. Privacy/Legal copy if applicable
3. salon onboarding audit

Agents/windows:
- Bot:
- Brain:
- Catalog:
- Privacy/Consent:
- Ops:
- Master surface:

Execution order:
...

No-go list:
...
```

После этого запускай окна.

---

# 18. PER-WINDOW HANDOFF FORMAT

Каждому агенту передавать только его scope:

```text
TASK
SOURCE OF TRUTH
OWNER RULING
CURRENT FACTS
DO
DO NOT
DEPENDENCIES
ACCEPTANCE
RETURN FORMAT
```

Оркестратор отвечает за context reduction.

---

# 19. FINAL WAVE REPORT

Когда wave закончена, вернуть владельцу:

```text
CONTROLLED PILOT — ORCHESTRATOR REPORT

Completed:
- ...

PRs:
- ...

Deployed:
- ...

Verified on runtime:
- ...

Still blocked:
- ...

Owner input needed:
- ...

Safety state:
- ...

Recommendation shadow state:
- ...

Food/consent state:
- ...

Master onboarding state:
- ...

Salon onboarding audit:
- ...

Top 3 next actions:
1.
2.
3.
```

---

# 20. ГЛАВНЫЙ ПРИНЦИП

Сейчас цель — не «добавить больше Ayla».

Цель:

> довести ограниченный Controlled Pilot до состояния, где каждое показанное пользователю действие реально работает, каждое решение имеет источник, safety не обходится, а операционные сбои видны владельцу.

Если новая работа расширяет продукт, но не повышает correctness, safety, observability или E2E readiness — она не входит в эту wave.
