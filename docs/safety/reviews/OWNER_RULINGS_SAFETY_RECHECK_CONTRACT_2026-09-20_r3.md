# Owner rulings — `safety_recheck` / `CLEARED_BY_RECHECK` product contract (20.09.2026) — immutable record r3 (canonical owner wording)

**DO NOT EDIT — SUPERSEDE WITH A NEW RECORD.**

**Статус:** `OWNER APPROVED — PENDING PHYSICIAN CONFIRMATION`. Record v3.0 (r3) — действующий полный owner contract. `TRANSMISSION GAP IN ACTIVE RECORD: NO`. Этот record содержит **только** тот текст решений 1–5, на который владелец ответил «согласен, утверждаем», и сам ответ — дословно. Всё, что было передано исполнителю сверх этого блока (расширенный state machine, дополнительные acceptance criteria, полный перечень clinical blockers, перечни фраз), **не является дословным owner wording** и живёт в delta / Safety Matrix / Review Pack под грифом `ENGINEERING ELABORATION OF OWNER RULING` / `DERIVED CONTRACT SPECIFICATION` (см. «Разграничение»). Правки в этот файл не вносятся — ошибка или новое слово владельца оформляются новым superseding record.
**Заменяет:** record r1 `docs/safety/reviews/OWNER_RULINGS_SAFETY_RECHECK_CONTRACT_2026-09-20.md` (RECORD SHA-256 `e7b54cc3d9a27c6a067d54b7e725122093ca630f59abced86af7ce23b25a6ff1`, файл `92d05387cbb66f32a2be70193f6a649165f4586aa547dbfa23460e10c0face49`) и record r2 `docs/safety/reviews/OWNER_RULINGS_SAFETY_RECHECK_CONTRACT_2026-09-20_r2.md` (RECORD SHA-256 `0f5324eca27544768739f4eb6d3a24c4a019848dda65ec8d35e16d63596049e8`, файл `97930f59fe7a9b968e24a009682dda44729bbcec03906e4c82bf0800de8a9db2`) — оба `SUPERSEDED BY r3`, не редактируются, не удаляются, остаются byte-identical и **не являются действующим полным owner contract**. Причина: r1 и r2 фиксировали текст из задания исполнителю (передачи T1 / T2) с обрывами в решении 5, без разграничения owner wording / engineering elaboration; r1 к тому же не содержал дословного ответа владельца.
**Дата ответа владельца:** 20.09.2026. **Владелец:** Андрей Тихонов (владелец проекта). **Дата фиксации record:** 20.09.2026.
**Источник текста:** утверждённый блок решений 1–5, переданный владельцем 20.09.2026; решения 3 и 4 взяты целиком из канонического gap fill `docs/r3_gap_fill_canonical_utf8.txt` (sha256 `72436e313de8b1b12c1db4a2dc86fec9e950a0d66b1b5a4f684c1741fb2b3ebf`, сверен перед сборкой) — повреждённые фрагменты передачи **не склеивались**.
**Repository / runtime baseline:** `AndreyDeveloper84/ai-bot-platform`, ветка `dev`; PR #1893 (`feat/s1-g4-question-flow`), squash commit `40b61cb02bcdad98b21987dd10e0106a42923be8` — `QUESTION FLOW IMPLEMENTED: PARTIAL`; `clear_restriction()` намеренно всегда выбрасывает `RecheckNotRegistered`; live-механика clearance отсутствует.
**Природа решений:** регистрация **product-safety контракта** выхода из S1-ограничения (`safety_recheck` → `CLEARED_BY_RECHECK`). Это регистрация контракта, **не** разрешение реализовать runtime сейчас, не physician sign-off, не `CLINICAL APPROVED`, не `SAFE FOR PILOT`. `CONTROLLED PILOT S1 GATE — NOT READY`.
**Связь:** уточняет и не отменяет [OD-BOT §156] (снятие `S1_STOP` только явным `safety_recheck`; источник — `docs/Q1.md`) и [OD-BOT §162] (восемь условий `CLEARED_BY_RECHECK`, outcome `S1_NOT_CURRENT_MEDICAL_FOLLOWUP_REQUIRED`); зарегистрирован в [OD-BOT §166] (решения 1–5) и [OD-BOT §167] (records r1 → r2 → r3); закрывает открытые вопросы DRF-2040 q1 (точка входа), q2 (носитель provenance — на уровне logical contract), q3 (граница с CLARIFY) **на уровне owner-контракта**; runtime — PR #1893 / `40b61cb0`, реализация контракта — второй PR.
**Transmission gaps r1 / r2 / черновика r3 — закрыты.** Обрывы решения 5 в передачах T1 (после «допустимость recheck по каждой группе G1») и T2 (после «…необходимых clinical») относились к тексту задания исполнителю; обрыв T3 на границе решений 3 / 4 в первой передаче утверждённого блока закрыт каноническим gap fill (решения 3 и 4 заменены целиком). В действующем record обрывов нет.
**Рабочий delta-документ (не immutable):** `docs/safety/reviews/AYLA_S1_SAFETY_RECHECK_CONTRACT_DELTA_v0.1_2026-09-20.md`.

## Ответ владельца — дословно

> «согласен, утверждаем»

Ответ дан 20.09.2026 на полный блок решений 1–5, приведённый в следующем разделе.

## Утверждённый текст решений 1–5 — дословно, без сокращений

Ниже приводится полный блок, на который владелец Андрей Тихонов 20.09.2026 ответил:

> «согласен, утверждаем»

### Решение 1. Точка входа

`safety_recheck` запускается только явным действием пользователя при активном ограничении:

* кнопка/действие `safety_recheck.start`;
* Telegram, MAX и Mini App используют один backend-контракт;
* обычный текст, новая сессия, новый intent, TTL и повторная попытка записи recheck не запускают;
* рабочий кандидат текста кнопки: «Повторно проверить безопасность».

### Решение 2. Формула допуска

`CLEARED_BY_RECHECK` возможен, когда выполняется:

* доказано хотя бы одно основание:

  * исходный сигнал был ложным срабатыванием;
  * это цитата, гипотеза или переносное выражение;
  * симптом относился к другому человеку;
  * исправлена доказуемая опечатка или полярность;
  * ambiguous-сигнал после зарегистрированного вопроса однозначно оказался вне S1;
* одновременно соблюдены все три обязательных запрета:

  * нет `UNKNOWN`;
  * нет нового S1-сигнала;
  * нет подтверждённого recent-resolved события, для которого исчезновение симптома не снимает срочность.

То есть пункты 1–5 §162 — альтернативные доказуемые основания, а пункты 6–8 — обязательные общие guards. Это нужно зарегистрировать явно, чтобы исполнитель не попытался потребовать логически невозможное одновременное выполнение всех восьми оснований.

### Решение 3. Граница с CLARIFY

- Ограничение `open`, созданное ambiguous-сигналом, разрешается только зарегистрированным конкретным вопросом и однозначным результатом `OUTSIDE_S1`.
- Простое «нет», молчание, уклонение или новая тема не являются достаточным ответом.
- Ограничение `stop` нельзя понизить обычным CLARIFY-вопросом — только отдельный полный `safety_recheck`.
- Для текущего G4-flow #1893 поведение пока не меняется: вопрос повторяется, ограничение сохраняется.

### Решение 4. Provenance

Успешный и неуспешный recheck должен оставлять запись без сырого медицинского текста:

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
evidence_refs[]          # message/event ID, не текст
started_at
completed_at
result:
  CLEARED_BY_RECHECK
  STOP_PERSISTS
  S1_NOT_CURRENT_MEDICAL_FOLLOWUP_REQUIRED
policy_refs              # §156, §162; contract version
```

Активное ограничение и журнал результата — разные сущности: после успешного recheck блокировка снимается, но доказательство решения не уничтожается.

### Решение 5. Порядок реализации

Первый PR — только документы и исполнимый контракт:

* зарегистрировать owner ruling новым параграфом `[OD-BOT]`;
* создать immutable record;
* обновить Safety Matrix, Review Pack и recheck fixtures;
* определить exact API/state machine/provenance schema;
* подготовить вопросы врачу CQ‑CTX‑01…03;
* runtime, merge и deploy не выполнять.

Второй PR — runtime только после отдельной проверки зарегистрированного контракта. Live-разблокировку нельзя включать, пока не определены допустимость recheck по G1–G7 и точные question contracts. Наше точечное клиническое ревью остаётся подготовительным и не заменяет заключение лицензированного врача.

## Разграничение: owner wording vs engineering elaboration

Владелец утвердил **только** блок выше. Следующие элементы были переданы исполнителю в задании (передачи T1 / T2, зафиксированы в r1 / r2) и **не входят** в утверждённый блок; они сохраняются в delta / Safety Matrix / Review Pack как `ENGINEERING ELABORATION OF OWNER RULING` (или `DERIVED CONTRACT SPECIFICATION`) и не могут цитироваться как слово владельца:

| Элемент | Где живёт | Статус |
|---|---|---|
| «единый action ID `safety_recheck.start`»; «действие показывается только при активном S1 restriction»; «регистрация контракта, а не разрешение сейчас реализовать кнопку» | delta §3 | `ENGINEERING ELABORATION` — owner wording: «кнопка/действие `safety_recheck.start`», «только явным действием пользователя при активном ограничении», «рабочий кандидат текста кнопки» |
| Именованная формула `eligible_basis = ANY(1..5)`, `mandatory_guards = ALL(6..8)`, `CLEARED_BY_RECHECK = eligible_basis AND mandatory_guards`; перечень фраз («мне лучше», «всё прошло», «сейчас нормально», detector silence, TTL, новая сессия, новый intent, желание записаться) | delta §2, matrix §6.1 | `DERIVED CONTRACT SPECIFICATION` — семантика ANY/ALL утверждена владельцем словами «пункты 1–5 §162 — альтернативные доказуемые основания, а пункты 6–8 — обязательные общие guards»; идентификаторы формулы и перечень фраз — инженерные |
| «пока не утверждён детерминированный negative/safe answer contract, существующий G4-вопрос сам по себе не создаёт `CLEARED_BY_RECHECK`» | delta §4, §9 | `ENGINEERING ELABORATION` — owner wording решения 3: «для текущего G4-flow #1893 поведение пока не меняется: вопрос повторяется, ограничение сохраняется» |
| «provenance не содержит сырой пользовательский медицинский текст» как отдельное требование; «не создавать новый `SafetyState`»; «`CLEARED_BY_RECHECK` не означает `NORMAL` / medical clearance»; «`S1_NOT_CURRENT_MEDICAL_FOLLOWUP_REQUIRED` не разблокирует booking автоматически»; `IMPLEMENTATION CARRIER: OPEN`; выбор носителя | delta §5 | `DERIVED CONTRACT SPECIFICATION` — owner wording решения 4: запись без сырого медицинского текста, схема полей выше, «активное ограничение и журнал результата — разные сущности» |
| State machine Пакета B (`RECHECK_IN_PROGRESS → CLEARED_BY_RECHECK / STOP_PERSISTS / S1_NOT_CURRENT_…`, `clinical contract permits clearance`); «последний outcome не открывает booking/recommendation»; формулировки physician queue CQ‑CTX‑01…03 и пункты про кнопку и «ни один результат не звучит как разрешение» | delta §4, §6–§7; Review Pack §7D | `ENGINEERING ELABORATION` — owner wording решения 5: «определить exact API/state machine/provenance schema», «подготовить вопросы врачу CQ‑CTX‑01…03», «пока не определены допустимость recheck по G1–G7 и точные question contracts», «наше точечное клиническое ревью остаётся подготовительным и не заменяет заключение лицензированного врача» |

## Что это НЕ означает

- Не physician sign-off, не `CLINICAL APPROVED`, не `PHYSICIAN PASS`, не `SAFE FOR PILOT`.
- `CLEARED_BY_RECHECK` ≠ `NORMAL`, ≠ medical clearance, ≠ разрешение врача — снятие **продуктового** блокиратора по зарегистрированному контракту.
- Не разрешение включить live-разблокировку: по решению 5 она не включается, пока не определены допустимость recheck по G1–G7 и точные question contracts; `clear_restriction()` в runtime остаётся отказным.
- Owner approval («согласен, утверждаем») не заменяет заключение лицензированного врача (решение 5, последний абзац) и не закрывает вопросы CQ‑CTX‑01…03.

## Хеш record

Файл не может содержать собственный хеш. Зарегистрированный `RECORD SHA-256` = sha256 (UTF-8, LF) всего содержимого **выше** строки `---- RECORD HASH BOUNDARY ----`; sha256 полного файла (с хвостом) — в [OD-BOT §167], `docs/safety/README.md` и описании PR. Оба зафиксированы при создании и не меняются.

---- RECORD HASH BOUNDARY ----
RECORD SHA-256 (content above the boundary): 4935e344689632cf41ce6622198d70ebbfd87fd4ccb740a8930c5df31e3c3aaf
