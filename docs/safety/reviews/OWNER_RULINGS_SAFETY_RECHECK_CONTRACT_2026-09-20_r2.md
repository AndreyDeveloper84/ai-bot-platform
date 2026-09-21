# Owner rulings — `safety_recheck` / `CLEARED_BY_RECHECK` product contract (20.09.2026) — immutable record r2

**DO NOT EDIT — SUPERSEDE WITH A NEW RECORD.**

**Статус:** `OWNER APPROVED — PENDING PHYSICIAN CONFIRMATION`. Record v2.0 (r2). Тексты решений и ответ владельца зафиксированы дословно; правки в этот файл не вносятся — ошибка или новое слово владельца оформляются новым superseding record. SHA-256 record — в конце файла (см. «Хеш record»).
**Заменяет:** record r1 `docs/safety/reviews/OWNER_RULINGS_SAFETY_RECHECK_CONTRACT_2026-09-20.md` (RECORD SHA-256 `e7b54cc3d9a27c6a067d54b7e725122093ca630f59abced86af7ce23b25a6ff1`, sha256 файла `92d05387cbb66f32a2be70193f6a649165f4586aa547dbfa23460e10c0face49`). Причина: r1 зафиксирован по первой передаче сообщения владельца (T1), в которой дословный ответ владельца отсутствовал — был передан только пересказ «Ответ относится ко всем пяти решениям ниже»; во второй передаче (T2) ответ владельца передан дословно. r1 **не редактируется** (immutable), остаётся в репозитории со статусом `SUPERSEDED BY r2`; его зарегистрированные SHA не меняются. Решения 1–5 в T1 и T2 совпадают текстуально (см. «Передачи и обрывы»).
**Дата ответа владельца:** 20.09.2026. **Владелец:** Андрей Тихонов (владелец проекта). **Дата фиксации record:** 20.09.2026.
**Repository / base HEAD:** `AndreyDeveloper84/ai-bot-platform`, ветка `dev`, HEAD `44a6400a` (fresh fetch на момент создания r2). **Runtime baseline:** PR #1893 (`feat/s1-g4-question-flow`), squash commit `40b61cb02bcdad98b21987dd10e0106a42923be8` в `dev` — `QUESTION FLOW IMPLEMENTED: PARTIAL`; `clear_restriction()` намеренно всегда выбрасывает `RecheckNotRegistered`; live-механика clearance отсутствует.
**Природа решений:** регистрация **product-safety контракта** выхода из S1-ограничения (`safety_recheck` → `CLEARED_BY_RECHECK`): точка входа, формула допуска, граница с CLARIFY, provenance contract, порядок реализации. Это регистрация контракта, **не** разрешение реализовать runtime сейчас, не physician sign-off, не `CLINICAL APPROVED`, не `SAFE FOR PILOT`. `CONTROLLED PILOT S1 GATE — NOT READY`.
**Связь:** уточняет и не отменяет [OD-BOT §156] (снятие `S1_STOP` только явным `safety_recheck`; источник — `docs/Q1.md`) и [OD-BOT §162] (граница `CLEARED_BY_RECHECK`, восемь условий, outcome `S1_NOT_CURRENT_MEDICAL_FOLLOWUP_REQUIRED`); закрывает открытые вопросы DRF-2040 q1 (точка входа), q2 (носитель provenance — на уровне logical contract), q3 (граница с CLARIFY) **на уровне owner-контракта**; runtime-состояние — PR #1893 / `40b61cb0` (см. выше), runtime-реализация контракта — Пакет B (отдельный PR).
**Реестр:** [OD-BOT §166] (решения 1–5) и [OD-BOT §167] (регистрация record r1 → r2) в `docs/OPEN_DECISIONS.md`.
**Рабочий delta-документ (не immutable):** `docs/safety/reviews/AYLA_S1_SAFETY_RECHECK_CONTRACT_DELTA_v0.1_2026-09-20.md`.

## Scope

- Механика выхода из активного S1-ограничения (`open` — ambiguous-сигнал ждёт зарегистрированного вопроса; `stop` — S1 STOP) на уровне product policy: единственная точка входа, формула допуска `CLEARED_BY_RECHECK`, граница с CLARIFY, logical provenance / audit contract, разделение на docs-пакет и runtime-пакет.
- Не меняет: семь групп S1 (OD-SAF-11…22), четыре `SafetyState` (`NORMAL | CAUTION | CLARIFY | STOP`), W1-06 = C, W1-07 = A, текст медицинской эскалации v2 ([§163]), question contracts ([§164]), recent-resolved ([§161]).
- Runtime этим record не меняется. Clinical blockers (CQ-CTX-01…03 и остальные пункты physician queue) остаются открытыми.

## Ответ владельца — дословно

Получено 20.09.2026 (передача T2), дословно:

> Владелец Андрей Тихонов 20.09.2026 ответил:
>
> > «согласен, утверждаем»
>
> Ответ относится ко всем пяти решениям ниже.

Дословный ответ владельца: **«согласен, утверждаем»** — относится к решениям 1–5 ниже. Полный текст передачи T2 — Приложение A.

## Передачи сообщения владельца и обрывы

| Передача | Где зафиксирована дословно | Обрыв в переданном тексте |
|---|---|---|
| T1 (первая) | record r1, Приложение A | решение 5: после строки «* допустимость recheck по каждой группе G1» текст продолжается сразу «> STOP_PERSISTS» (начало блока state machine) |
| T2 (вторая; этот record) | Приложение A ниже | решение 5: после «…закрытия необходимых clinical» текст продолжается сразу «learance;» (хвост строки CQ‑CTX‑03 из physician queue) |

Решения 1–4 и решение 5 до строки «Runtime разрешён только после…» в T1 и T2 совпадают дословно (в r1 Приложение A блоки ```` ```text ```` показаны отступом из-за вложения в code block; текст тот же). Объединение T1 и T2 **не даёт** полного текста решения 5: в обеих передачах отсутствуют (a) продолжение списка «Live-clearance нельзя включать, пока не определены:» после «G1», (b) начало блока state machine до строки «> STOP_PERSISTS»; в T2 отсутствует также начало physician queue (есть в T1). Недостающий текст **не восстанавливался и не додумывался**; ниже приведены только полученные фрагменты с указанием передачи. Владельцу — подтвердить или дополнить superseding record (r3).

## Решения владельца — дословно (T2)

### Решение 1. Точка входа

`safety_recheck` запускается только явным действием пользователя при активном ограничении:

* единый action ID: `safety_recheck.start`;
* Telegram, MAX и Mini App используют один backend-контракт;
* обычная следующая реплика, распознанное текстовое намерение, новая сессия, новый intent, TTL и повторная попытка записи не запускают recheck;
* owner-approved кандидат текста кнопки: «Повторно проверить безопасность»;
* действие показывается только при активном S1 restriction.

Это регистрация контракта, а не разрешение сейчас реализовать кнопку.

### Решение 2. Формула допуска

`CLEARED_BY_RECHECK` допустим, только когда доказано хотя бы одно квалифицирующее основание:

1. исходный personal S1 был ложным срабатыванием;
2. сообщение было цитатой, гипотезой или переносным выражением;
3. сообщение относилось к другому человеку;
4. исправлена доказуемая опечатка или полярность;
5. ambiguous-сигнал после зарегистрированного конкретного вопроса однозначно оказался вне S1;

и одновременно пройдены все три обязательных guard:

6. нет `UNKNOWN`;
7. нет нового S1;
8. нет подтверждённого recent-resolved события, для которого исчезновение симптома не снимает срочность.

Зафиксируй формулу явно:

```text
eligible_basis = ANY(1..5)
mandatory_guards = ALL(6..8)
CLEARED_BY_RECHECK = eligible_basis AND mandatory_guards
```

Не интерпретируй §162 как требование одновременно выполнить все пункты 1–5: это логически взаимоисключающие альтернативные основания.

Context/evidence correction не равна выздоровлению.

Фразы «мне лучше», «всё прошло», «сейчас нормально», detector silence, TTL, новая сессия, новый intent или желание записаться не являются квалифицирующим основанием.

### Решение 3. Граница с CLARIFY

* Ограничение `open`, созданное ambiguous-сигналом, может разрешаться только зарегистрированным конкретным вопросом и однозначным результатом `OUTSIDE_S1`.
* Простое «нет», молчание, уклонение, свободная отрицательная фраза или новая тема недостаточны.
* Ограничение `stop` нельзя понизить обычным CLARIFY-вопросом.
* Для `stop` требуется отдельный полный `safety_recheck`.
* Текущее поведение G4 из #1893 не меняется: вопрос повторяется, а ограничение сохраняется.
* Пока не утверждён детерминированный negative/safe answer contract, существующий G4-вопрос сам по себе не создаёт `CLEARED_BY_RECHECK`.

### Решение 4. Provenance contract

Успешный или неуспешный recheck должен оставить audit/provenance record без сырого медицинского текста.

Минимальная схема:

```text
schema_version
recheck_id
mechanism = safety_recheck
trigger = explicit_user_action
restriction_ref
original_group
original_status
question_contract_version
qualifying_basis[]
guard_results:
  unknown_absent
  new_s1_absent
  disqualifying_recent_resolved_absent
detector_result
evidence_refs[]          # message/event IDs, не сырой текст
started_at
completed_at
result:
  CLEARED_BY_RECHECK
  STOP_PERSISTS
  S1_NOT_CURRENT_MEDICAL_FOLLOWUP_REQUIRED
policy_refs              # OD-BOT §156, §162 и новый параграф
```

Требования:

* active restriction и audit record — разные сущности;
* успешный recheck снимает активный продуктовый блокиратор, но не уничтожает историю решения;
* provenance не содержит сырой пользовательский медицинский текст;
* не создавать новый `SafetyState`;
* `CLEARED_BY_RECHECK` не означает `NORMAL`, medical clearance или разрешение врача;
* `S1_NOT_CURRENT_MEDICAL_FOLLOWUP_REQUIRED` не разблокирует health-sensitive recommendation/booking автоматически.

На docs-этапе опиши logical contract. Не выбирай новый runtime storage или таблицу без отдельного architecture review. Если канонического audit carrier ещё нет, пометь `IMPLEMENTATION CARRIER: OPEN`, а не проектируй параллельную систему.

### Решение 5. Порядок реализации

Работа делится на два пакета.

#### Пакет A — текущий PR

Только docs-first регистрация:

* новый `[OD-BOT §…]`;
* новый immutable owner record;
* обновление Safety Matrix;
* обновление Review Pack/delta;
* обновление recheck fixtures и индекса;
* state-machine/API/provenance contract;
* physician review queue CQ‑CTX‑01…03;
* никаких runtime-изменений.

#### Пакет B — отдельный будущий PR

Runtime разрешён только после отдельной проверки зарегистрированного контракта и закрытия необходимых clinical

**[TRANSMISSION GAP — T2]** — в T2 строка получена как «…закрытия необходимых clinicallearance;», после чего идут два последних пункта physician queue и строка «Owner approval не закрывает эти пункты.» (см. Приложение A). Продолжение решения 5, полученное только в T1 (record r1, Приложение A) — дословно:

> *(T1)* Runtime разрешён только после отдельной проверки зарегистрированного контракта и закрытия необходимых clinical blockers.

Live-clearance нельзя включать, пока не определены:

* допустимость recheck по каждой группе G1

**[TRANSMISSION GAP — T1]** — в T1 после «…по каждой группе G1» текст продолжается сразу «> STOP_PERSISTS». Полученное продолжение (T1) — дословно:

```text
> STOP_PERSISTS

RECHECK_IN_PROGRESS
  -> ANY qualifying basis true
     AND all mandatory guards true
     AND clinical contract permits clearance
  -> CLEARED_BY_RECHECK

RECHECK_IN_PROGRESS
  -> symptom not current, but medical follow-up still required
  -> S1_NOT_CURRENT_MEDICAL_FOLLOWUP_REQUIRED
```

Последний outcome не должен автоматически открывать booking/recommendation.

## Physician queue — дословно

Полный текст — T1 (record r1, Приложение A); T2 после обрыва содержит хвост этого же раздела — пункты «проверка формулировок…», «подтверждение…» и строку «Owner approval не закрывает эти пункты.» дословно совпадают.

Не отвечай за врача. Зафиксируй как `PENDING_CLINICAL_EXPERT`:

* CQ‑CTX‑01: для каких G1–G7 product recheck вообще допустим;
* CQ‑CTX‑02: минимальные вопросы для каждой допустимой группы;
* CQ‑CTX‑03: какие ответы достаточны и какие recent-resolved случаи исключают clearance;
* проверка формулировок кнопки и recheck-вопросов;
* подтверждение того, что никакой результат не звучит как медицинское разрешение.

Owner approval не закрывает эти пункты.

## Что это НЕ означает

- Не physician sign-off, не `CLINICAL APPROVED`, не `PHYSICIAN PASS`, не `SAFE FOR PILOT`.
- `CLEARED_BY_RECHECK` ≠ `NORMAL`, ≠ medical clearance, ≠ разрешение врача — снятие **продуктового** блокиратора по зарегистрированному контракту.
- Не разрешение включить live-clearance: `clear_restriction()` в runtime остаётся отказным до Пакета B и закрытия clinical blockers.
- Новый `SafetyState` не вводится; текст медицинской эскалации v2 не меняется; семь групп S1 не меняются.
- Owner approval («согласен, утверждаем») не закрывает ни один пункт physician queue.

## Приложение A — полный текст передачи T2 (дословно, без правок)

Раздел «Новое слово владельца» сообщения от 20.09.2026 — от заголовка до строки «Owner approval не закрывает эти пункты.». Далее в сообщении следовали инструкции по fixtures, версионности, git-процессу, acceptance criteria и форме отчёта — процессные указания исполнителю, не owner-решения по policy; они исполняются в PR и не входят в record.

~~~~text
## Новое слово владельца

Владелец Андрей Тихонов 20.09.2026 ответил:

> «согласен, утверждаем»

Ответ относится ко всем пяти решениям ниже. В новом immutable record сохрани:

* полный текст решений 1–5;
* дословный ответ владельца;
* дату;
* связь с `[OD-BOT §156]`, `[§162]`, DRF‑2040 и PR #1893;
* SHA-256 итогового immutable-файла.

### Решение 1. Точка входа

`safety_recheck` запускается только явным действием пользователя при активном ограничении:

* единый action ID: `safety_recheck.start`;
* Telegram, MAX и Mini App используют один backend-контракт;
* обычная следующая реплика, распознанное текстовое намерение, новая сессия, новый intent, TTL и повторная попытка записи не запускают recheck;
* owner-approved кандидат текста кнопки: «Повторно проверить безопасность»;
* действие показывается только при активном S1 restriction.

Это регистрация контракта, а не разрешение сейчас реализовать кнопку.

### Решение 2. Формула допуска

`CLEARED_BY_RECHECK` допустим, только когда доказано хотя бы одно квалифицирующее основание:

1. исходный personal S1 был ложным срабатыванием;
2. сообщение было цитатой, гипотезой или переносным выражением;
3. сообщение относилось к другому человеку;
4. исправлена доказуемая опечатка или полярность;
5. ambiguous-сигнал после зарегистрированного конкретного вопроса однозначно оказался вне S1;

и одновременно пройдены все три обязательных guard:

6. нет `UNKNOWN`;
7. нет нового S1;
8. нет подтверждённого recent-resolved события, для которого исчезновение симптома не снимает срочность.

Зафиксируй формулу явно:

```text
eligible_basis = ANY(1..5)
mandatory_guards = ALL(6..8)
CLEARED_BY_RECHECK = eligible_basis AND mandatory_guards
```

Не интерпретируй §162 как требование одновременно выполнить все пункты 1–5: это логически взаимоисключающие альтернативные основания.

Context/evidence correction не равна выздоровлению.

Фразы «мне лучше», «всё прошло», «сейчас нормально», detector silence, TTL, новая сессия, новый intent или желание записаться не являются квалифицирующим основанием.

### Решение 3. Граница с CLARIFY

* Ограничение `open`, созданное ambiguous-сигналом, может разрешаться только зарегистрированным конкретным вопросом и однозначным результатом `OUTSIDE_S1`.
* Простое «нет», молчание, уклонение, свободная отрицательная фраза или новая тема недостаточны.
* Ограничение `stop` нельзя понизить обычным CLARIFY-вопросом.
* Для `stop` требуется отдельный полный `safety_recheck`.
* Текущее поведение G4 из #1893 не меняется: вопрос повторяется, а ограничение сохраняется.
* Пока не утверждён детерминированный negative/safe answer contract, существующий G4-вопрос сам по себе не создаёт `CLEARED_BY_RECHECK`.

### Решение 4. Provenance contract

Успешный или неуспешный recheck должен оставить audit/provenance record без сырого медицинского текста.

Минимальная схема:

```text
schema_version
recheck_id
mechanism = safety_recheck
trigger = explicit_user_action
restriction_ref
original_group
original_status
question_contract_version
qualifying_basis[]
guard_results:
  unknown_absent
  new_s1_absent
  disqualifying_recent_resolved_absent
detector_result
evidence_refs[]          # message/event IDs, не сырой текст
started_at
completed_at
result:
  CLEARED_BY_RECHECK
  STOP_PERSISTS
  S1_NOT_CURRENT_MEDICAL_FOLLOWUP_REQUIRED
policy_refs              # OD-BOT §156, §162 и новый параграф
```

Требования:

* active restriction и audit record — разные сущности;
* успешный recheck снимает активный продуктовый блокиратор, но не уничтожает историю решения;
* provenance не содержит сырой пользовательский медицинский текст;
* не создавать новый `SafetyState`;
* `CLEARED_BY_RECHECK` не означает `NORMAL`, medical clearance или разрешение врача;
* `S1_NOT_CURRENT_MEDICAL_FOLLOWUP_REQUIRED` не разблокирует health-sensitive recommendation/booking автоматически.

На docs-этапе опиши logical contract. Не выбирай новый runtime storage или таблицу без отдельного architecture review. Если канонического audit carrier ещё нет, пометь `IMPLEMENTATION CARRIER: OPEN`, а не проектируй параллельную систему.

### Решение 5. Порядок реализации

Работа делится на два пакета.

#### Пакет A — текущий PR

Только docs-first регистрация:

* новый `[OD-BOT §…]`;
* новый immutable owner record;
* обновление Safety Matrix;
* обновление Review Pack/delta;
* обновление recheck fixtures и индекса;
* state-machine/API/provenance contract;
* physician review queue CQ‑CTX‑01…03;
* никаких runtime-изменений.

#### Пакет B — отдельный будущий PR

Runtime разрешён только после отдельной проверки зарегистрированного контракта и закрытия необходимых clinicallearance;
* проверка формулировок кнопки и recheck-вопросов;
* подтверждение того, что никакой результат не звучит как медицинское разрешение.

Owner approval не закрывает эти пункты.
~~~~

## Приложение B — передача T1

Полный текст T1 не воспроизводится: он зафиксирован дословно в record r1 (`docs/safety/reviews/OWNER_RULINGS_SAFETY_RECHECK_CONTRACT_2026-09-20.md`, Приложение A; RECORD SHA-256 `e7b54cc3d9a27c6a067d54b7e725122093ca630f59abced86af7ce23b25a6ff1`). Из T1 в этот record перенесены только фрагменты, отсутствующие в T2 (продолжение решения 5 и начало physician queue), с пометкой *(T1)*.

## Хеш record

Файл не может содержать собственный хеш. Зарегистрированный `RECORD SHA-256` = sha256 (UTF-8, LF) всего содержимого **выше** строки `---- RECORD HASH BOUNDARY ----`; sha256 полного файла (с хвостом) — в [OD-BOT §167], `docs/safety/README.md` и описании PR. Оба зафиксированы при создании и не меняются.

---- RECORD HASH BOUNDARY ----
RECORD SHA-256 (content above the boundary): 0f5324eca27544768739f4eb6d3a24c4a019848dda65ec8d35e16d63596049e8
