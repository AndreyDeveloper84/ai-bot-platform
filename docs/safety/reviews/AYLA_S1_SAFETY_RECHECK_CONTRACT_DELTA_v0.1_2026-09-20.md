# AYLA — `safety_recheck` / `CLEARED_BY_RECHECK` contract delta v0.1 (20.09.2026)

**Статус:** `WORKING DOCUMENT` — engineering rendering зарегистрированного owner-контракта; не immutable, не physician sign-off. Источник истины — действующий immutable record **r4** `docs/safety/reviews/OWNER_RULINGS_SAFETY_RECHECK_CONTRACT_2026-09-20_r4.md` (RECORD SHA-256 `2245924e6f551cb5dd84a4e67f50093a1ec2a64646408881449281f1d64a7ef0`; дословный ответ владельца «согласен, утверждаем», 20.09.2026; полный утверждённый текст решений 1–5 без обрывов; r3 — `SUPERSEDED BY r4 — provenance-only correction`; r1 и r2 — superseded transmission drafts), реестр [OD-BOT §166] / [§167]. При расхождении — record и реестр.
**Статус содержимого этого документа:** `ENGINEERING ELABORATION OF OWNER RULING` / `DERIVED CONTRACT SPECIFICATION`. Дословное слово владельца — только в r4; всё, что здесь детальнее утверждённого блока (именованная формула `eligible_basis` / `mandatory_guards`, перечни фраз, расширенная state machine Пакета B, `IMPLEMENTATION CARRIER: OPEN`, acceptance criteria, полный перечень clinical blockers), — инженерное расширение и не цитируется как owner wording.
**Версия:** v0.1.4 (2026-09-21) — §10 clinical applicability 21.09 ([OD-BOT §168–§169]; record `OWNER_RULINGS_S1_RECHECK_CLINICAL_BOUNDARIES_2026-09-21.md`); §1–§9 без изменений (предыдущая версия v0.1.3 — sha256 `820e248432c163eb4bd4b9975204c59f7c3a97959086b2faf338b57b4f67a3dd`). v0.1.3 (2026-09-21) — источник переведён на record r4 (provenance-only correction r3); технический текст без изменений (предыдущая версия v0.1.2 — sha256 `45f69d3180033322b7c24438eb0848ae9ac9bee09f5e67670761e717934d4abe`). v0.1.2 (2026-09-20) — источник переведён на record r3 (canonical owner wording), содержимое помечено как engineering elaboration; технический текст без изменений (предыдущая версия v0.1.1 — sha256 `671ccb7a0fd3779237716c319776ac48dac3979f9029df60f8f54fae4179676e`). v0.1.1 (2026-09-20) — источник переведён на record r2, runtime baseline PR #1893 squash `40b61cb0`; содержательно без изменений (предыдущая версия v0.1 — sha256 `afc973ce458ee11a10fc9c0dca43a1ce7d47f26f627f8f4dd679b911f3cefa67`). **Пакет:** A (docs-first). **Runtime:** не меняется; `IMPLEMENTATION CARRIER: OPEN`.
**Не является:** `CLINICAL APPROVED`, `PHYSICIAN PASS`, `SAFE FOR PILOT`; разрешением включить live-clearance.

## 1. Что регистрируется (решения 1–5 → контракт)

| Решение | Контракт | Где отражено |
|---|---|---|
| 1. Точка входа | единственный явный action `safety_recheck.start`; один backend-контракт для Telegram / MAX / Mini App; показывается только при активном S1 restriction; обычная реплика, текстовое намерение, новая сессия, новый intent, TTL, повторная попытка записи — **не** запускают | §3 (API), fixtures REC-DENY-14 |
| 2. Формула допуска | `CLEARED_BY_RECHECK = ANY(1..5) AND ALL(6..8)`; 1–5 — альтернативные основания, 6–8 — обязательные guards; «мне лучше / всё прошло / сейчас нормально / detector silence / TTL / новая сессия / новый intent / желание записаться» — не основание | §2, fixtures REC-ALLOW-01…08, REC-DENY-01…13 |
| 3. Граница с CLARIFY | `open` разрешается только зарегистрированным вопросом + однозначным `OUTSIDE_S1`; «нет» / молчание / уклонение / свободное отрицание / новая тема — недостаточны; `stop` не понижается CLARIFY-вопросом — только полный `safety_recheck`; G4-вопрос #1893 сам по себе не создаёт clearance | §4, fixtures REC-ALLOW-08, REC-DENY-04 |
| 4. Provenance contract | logical schema (§5); active restriction ≠ audit record; успешный recheck снимает блокиратор, историю не уничтожает; без сырого медицинского текста; без нового `SafetyState`; `CLEARED_BY_RECHECK` ≠ `NORMAL`; `S1_NOT_CURRENT_MEDICAL_FOLLOWUP_REQUIRED` не разблокирует | §5, fixtures REC-DENY-15, REC-DENY-16 |
| 5. Порядок реализации | Пакет A — только docs (этот PR); Пакет B — runtime после отдельной проверки контракта и закрытия clinical blockers; live-clearance выключен до того | §6, §7 |

## 2. Формула допуска — без двусмысленности

```text
eligible_basis   = ANY(1..5)        # альтернативные основания: ложное срабатывание | цитата / гипотеза / фигура речи |
                                    # другой человек | доказуемая опечатка / полярность | ambiguous → OUTSIDE_S1 после зарегистрированного вопроса
mandatory_guards = ALL(6..8)        # нет UNKNOWN ∧ нет нового S1 ∧ нет disqualifying recent-resolved
CLEARED_BY_RECHECK = eligible_basis AND mandatory_guards AND clinical_contract_permits_clearance(group)
```

`clinical_contract_permits_clearance(group)` — третий множитель из state-machine владельца: до ответа врача на CQ-CTX-01…03 он `UNKNOWN` для всех G1–G7, поэтому **ни один** recheck сегодня не может завершиться `CLEARED_BY_RECHECK` даже при доказанном основании и пройденных guards. Это не engineering-ограничение, а clinical blocker.

Не-основания (registered): «мне лучше», «всё прошло», «сейчас нормально», detector silence, TTL, новая сессия, новый intent, желание продолжить запись, простое «нет», молчание, уклонение, новая тема. Context / evidence correction (основания 2–4) ≠ выздоровление.

## 3. Entry point / API contract (logical)

```text
action_id      = "safety_recheck.start"
trigger        = explicit_user_action            # единственный
preconditions  = active S1 restriction on the identity (status open | stop)
surfaces       = Telegram, MAX, Mini App          # один backend-контракт; кнопка/действие показываются только при активном restriction
visible label  = «Повторно проверить безопасность» (owner-approved CANDIDATE; production wording — physician + Legal, OPEN)
NOT a trigger  = ordinary next reply · recognised textual intent («хочу перепроверить») · new session · new intent · TTL · retry of booking
idempotency    = one recheck in progress per restriction_ref; a second start while in progress → the same recheck
```

Что здесь **не** решено (Пакет B / architecture review): транспорт action на каждой поверхности, где хранится `RECHECK_IN_PROGRESS`, таймаут незавершённого recheck (→ `STOP_PERSISTS`, не clearance), rate-limit повторных запусков.

## 4. State machine (engineering rendering решений 1–4; фрагмент владельца — в record, с отмеченным transmission gap)

```text
ACTIVE_RESTRICTION(open|stop)
  -- ordinary reply / new intent / new session / TTL / booking retry ---> ACTIVE_RESTRICTION (unchanged; open: registered question repeated)
  -- registered question answered, unambiguous OUTSIDE_S1 (open only) ---> resolution path per §164 contract (Пакет B; not by «нет»)
  -- safety_recheck.start (explicit) ---------------------------------> RECHECK_IN_PROGRESS

RECHECK_IN_PROGRESS
  -> UNKNOWN answer / evasion / abandonment                             -> STOP_PERSISTS   (restriction unchanged; audit record written)
  -> new S1 (same or other group)                                       -> STOP_PERSISTS   (restriction may be re-attributed / escalated; audit written)
  -> disqualifying recent-resolved event ([§161] G3 / G4 / G6 …)        -> STOP_PERSISTS
  -> ANY qualifying basis true AND ALL mandatory guards true
     AND clinical contract permits clearance                            -> CLEARED_BY_RECHECK        (product blocker lifted; history kept)
  -> symptom not current, but medical follow-up still required          -> S1_NOT_CURRENT_MEDICAL_FOLLOWUP_REQUIRED
                                                                           (NOT NORMAL; health-sensitive recommendation / booking stay blocked)
```

Инварианты: `stop` никогда не понижается CLARIFY-вопросом; `S1_NOT_CURRENT_MEDICAL_FOLLOWUP_REQUIRED` не открывает booking / recommendation автоматически; ни один outcome не является medical clearance; новый `SafetyState` не вводится (outcomes — resolution-level, как в [§162]).

## 5. Provenance / audit contract (logical) — `IMPLEMENTATION CARRIER: OPEN`

```text
schema_version               : "recheck-provenance/1"
recheck_id                   : opaque id
mechanism                    : "safety_recheck"
trigger                      : "explicit_user_action"           # никакого другого значения
restriction_ref              : ссылка на активное ограничение (не копия его содержимого)
original_group               : G1..G7 | null (unattributed)
original_status              : open | stop
question_contract_version    : версия зарегистрированного вопроса ([§164] / будущие recheck-вопросы)
qualifying_basis[]           : подмножество {1,2,3,4,5}; пустое при STOP_PERSISTS
guard_results                : {unknown_absent, new_s1_absent, disqualifying_recent_resolved_absent} — каждый true|false|not_evaluated
detector_result              : результат повторного прохода детектора (класс, не текст)
evidence_refs[]              : message / event IDs — НИКОГДА сырой текст
started_at / completed_at    : ISO-8601
result                       : CLEARED_BY_RECHECK | STOP_PERSISTS | S1_NOT_CURRENT_MEDICAL_FOLLOWUP_REQUIRED
policy_refs                  : ["OD-BOT §156", "OD-BOT §162", "OD-BOT §166"]
```

Требования (registered): active restriction и audit record — **разные сущности**; успешный recheck снимает активный продуктовый блокиратор и **не уничтожает** audit record; provenance без сырого пользовательского медицинского текста; без нового `SafetyState`. Обязательные поля — все перечисленные; record без любого из них невалиден (fixture REC-DENY-15); очистка restriction без audit record запрещена (REC-DENY-16).

**Carrier:** канонического audit carrier в runtime нет. Кандидаты (существующие носители, **не выбираются здесь**): `apps/events` (Event с `payload`), `apps/observability` audit, `BotUser.context` (сегодня носитель самого restriction в #1893 — но active restriction ≠ audit record). Выбор — отдельный architecture review в Пакете B. Параллельная система не проектируется.

## 6. Пакет A (этот PR) — сделано

- [OD-BOT §166] (решения 1–5 дословно, формула) и [OD-BOT §167] (регистрация record) — `docs/OPEN_DECISIONS.md`;
- immutable records r1 и r2 (superseded transmission drafts), r3 (`SUPERSEDED BY r4 — provenance-only correction`) и действующий record r4 `OWNER_RULINGS_SAFETY_RECHECK_CONTRACT_2026-09-20_r4.md` (canonical owner wording, полный утверждённый текст решений 1–5; единственный источник истины);
- Safety Matrix v0.12-reviewfix6: §6.1 «Owner amendments 20.09 — recheck contract», §14.2 OD-F0C3-09, §16;
- Review Pack v0.1-reviewfix6: §7D physician queue (CQ-CTX-01…03 переформулированы под контракт + кнопка / recheck-вопросы / «ни один результат не звучит как разрешение»);
- fixtures v0.2.6 (sha256 `ae9ea59e9f8e619d3059b4c527b43e53962a1ca4b22403ddf59f11efca9b05fc`; 24 contract fixtures добавлены в v0.2.2, 32 `T-S1-RCB-*` — в v0.2.6): `T-S1-REC-ALLOW-01…08`, `T-S1-REC-DENY-01…16`; индекс и counts;
- этот delta-документ; README индекс.
- Runtime, тесты, code fixtures — не тронуты.

## 7. Пакет B (будущий PR) — условия входа

Live-clearance нельзя включать, пока не определены (по владельцу; transmission gap в record — список ниже восстановлен **только** из physician queue и решений 1–4, не из потерянного фрагмента):

- допустимость recheck по каждой группе G1–G7 (CQ-CTX-01);
- минимальные recheck-вопросы для каждой допустимой группы (CQ-CTX-02);
- достаточные ответы и исключающие recent-resolved случаи (CQ-CTX-03);
- детерминированный negative / safe answer contract для зарегистрированного G4-вопроса ([§164] boundary → `OUTSIDE_S1`);
- production wording кнопки и recheck-вопросов (physician + Legal);
- подтверждение, что ни один результат не звучит как медицинское разрешение;
- architecture review носителя audit record (§5) и транспорта `safety_recheck.start`;
- отдельная проверка соответствия runtime зарегистрированному контракту (fixtures REC-*).

До закрытия: `clear_restriction()` в runtime остаётся отказным (`RecheckNotRegistered`), `QUESTION FLOW IMPLEMENTED: PARTIAL` (#1893).

## 8. Fixture delta (v0.2.1 → v0.2.2)

Добавлены 24 contract fixtures (dimension «Recheck contract»), каждая с пятью полями: `expected_product_outcome`, `restriction_state`, `capability_gate`, `provenance_expectation`, `physician_status`. Старые 69 фикстур и их clinical verdicts — без изменений (не переведены в `PASS`). Technical status новых фикстур — `NOT_IMPLEMENTED` (Пакет B); clinical — `PENDING_CLINICAL_EXPERT`.

| Класс | Fixtures |
|---|---|
| допускающие основания (при ALL guards и clinical permit) | REC-ALLOW-01 false positive · 02 quotation · 03 hypothetical · 04 figurative · 05 third party · 06 typo correction · 07 polarity correction · 08 registered ambiguous → `OUTSIDE_S1` |
| запрещающие | REC-DENY-01 «мне лучше» · 02 «всё прошло» · 03 «сейчас нормально» · 04 простое «нет» · 05 `UNKNOWN` · 06 новый S1 той же группы · 07 новый S1 другой группы · 08 disqualifying recent-resolved · 09 новая сессия · 10 TTL · 11 новый intent · 12 detector silence · 13 желание продолжить booking · 14 вызов без явного user action · 15 provenance без обязательного поля · 16 очистка restriction без audit record |

## 9. Трассировка к PR #1893 (runtime, unchanged by this PR)

Baseline: PR #1893 squash-merged в `dev` как `40b61cb02bcdad98b21987dd10e0106a42923be8`; `origin/dev` на момент r2 — `44a6400a`.

| Контракт | #1893 сегодня | Расхождений нет? |
|---|---|---|
| «нет» / UNKNOWN / новый intent / новая сессия / TTL → ограничение сохраняется | `route_g4_reply` → `restriction_persists`; restriction на `BotUser.context`, без TTL; новая сессия проверена на реальном lifecycle | да |
| `stop` не понижается CLARIFY-вопросом | durable `stop` → на каждом ходу STOP-ответ | да |
| G4-вопрос сам по себе не создаёт clearance | нет negative resolution в runtime | да |
| `CLEARED_BY_RECHECK` только через `safety_recheck` | `clear_restriction()` отказывает всем | да (до Пакета B) |
| active restriction ≠ audit record | audit record не существует (`IMPLEMENTATION CARRIER: OPEN`) | открыто — Пакет B |

## 10. Clinical applicability 21.09 — `ENGINEERING ELABORATION OF OWNER RULING`

Источник — immutable record `docs/safety/reviews/OWNER_RULINGS_S1_RECHECK_CLINICAL_BOUNDARIES_2026-09-21.md` (RECORD SHA-256 `cf0263dd5d890217e534817ab6fc7ece15af3cbbddfceb827c557c52f15af094`; ответ владельца `утвержда.`); [OD-BOT §168–§169]. Формула §2 **не меняется**; множитель `clinical_contract_permits_clearance(group)` получает owner-policy значения (ниже), подтверждение врачом — `PENDING`.

| recheck_type | Где допустим `CLEARED_BY_RECHECK` |
|---|---|
| `EVIDENCE_CORRECTION` | G1–G7 — основания 2–4 r4 (цитата / гипотеза / метафора, другой человек, опечатка / полярность) при ALL(6..8) и пяти элементах реш. 3 |
| `SYMPTOM_UPDATE` | только G1 (прошедшая реакция на нагрузку без тяжёлых признаков, question contract G1), G2 (фактической потери сознания не было), G5 (все условия небольшого поверхностного остановившегося кровотечения), G6 (локальная сыпь / зуд без системных признаков → `OUTSIDE_S1`, `CAUTION` может остаться); G3, G4, G7 — нет |

| Группа | Clearance допустим | Clearance запрещён | Outcome при запрете / blocker |
|---|---|---|---|
| G1 дыхание | однозначная коррекция контекста; либо question contract G1 [§164] устанавливает полностью прошедшую реакцию на нагрузку без тяжёлой одышки | реальный эпизод удушья / тяжёлой одышки в покое, невозможности говорить полными фразами, посинения, спутанности, другого тяжёлого признака | `S1_NOT_CURRENT_MEDICAL_FOLLOWUP_REQUIRED` (не NORMAL) |
| G2 потеря сознания | установлено: фактической потери сознания не было, текущих симптомов и опасных признаков нет | фактический обморок; текущий симптом, неполное восстановление, речь / движение, боль в груди, опасный ритм, травма, судороги, обморок при нагрузке или лёжа | обморок → минимум `MEDICAL_REVIEW_REQUIRED`; опасные признаки → `STOP` |
| G3 грудной симптом | только однозначная коррекция доказательства (метафора, цитата, третье лицо, гипотетика, ошибка текста, не личный симптом) | любой реальный недавний эпизод боли / давления / тяжести / сжатия — даже если прошёл | `STOP` (18.09 RES-G3 не понижается); прошедший реальный — медицинская маршрутизация, не NORMAL |
| G4 очаговая неврология | только доказанное отсутствие личного очагового эпизода или однозначная контекстная ошибка | достоверный внезапный эпизод односторонней слабости / онемения, перекоса лица, речи, зрения, равновесия — даже если прошёл; один «нет» на составной вопрос | экстренная маршрутизация (`STOP`); одиночный «нет» → `UNKNOWN` |
| G5 кровотечение | одновременно: небольшая поверхностная рана, полностью остановлено, не возобновляется, нет пульсирующей / трудно контролируемой крови, не глубокая и не большая, нет инородного тела, нет нарушения чувствительности / движения, нет нового S1 | продолжающееся, усиливающееся, возобновляющееся, трудноостанавливаемое, глубокое или осложнённое | `STOP`; **blocker:** вопрос G5 [§164] не выясняет глубину, инородное тело, чувствительность |
| G6 анафилаксия | локальная сыпь / зуд при явном отсутствии отёка губ / рта / языка / горла, затруднения дыхания / глотания, выраженного головокружения, обморока, других системных признаков | любой реальный системный эпизод — даже если стало лучше | `STOP`; вне S1 может сохраниться `CAUTION` |
| G7 тяжёлое системное ухудшение | только однозначная `EVIDENCE_CORRECTION` (до регистрации G7 question contract) | clearance по изменению самочувствия или простому ответу; `UNKNOWN`; подтверждённый тяжёлый признак | restriction сохраняется / `STOP`; **blocker:** G7 question contract не зарегистрирован |

Пять элементов реш. 3 (все обязательны; любой недостающий → `UNKNOWN`): attribution · actuality · temporality · question-contract exclusion · no new S1. Односложные / generic ответы («нет», «уже прошло», «стало лучше», «сейчас нормально», «всё хорошо») не устанавливают ни одного элемента.

Blockers (Пакет A их не закрывает): (1) G7 question contract и group-specific матрица recent-resolved G7 не зарегистрированы — до их регистрации для G7 допустима только `EVIDENCE_CORRECTION` (реш. 2 G7). (2) Вопрос G5 [§164] не выясняет глубину / размер раны, инородное тело и нарушение чувствительности / движения — через зарегистрированные вопросы `SYMPTOM_UPDATE`-clearance для G5 достижим только если пользователь сам сообщает все условия; иначе `UNKNOWN` (реш. 3 запрещает расширять вопрос). (3) Составной вопрос G4 [§164] допускает одиночный ответ «нет», который по реш. 2 G4 не отрицает все части — нужен owner-зарегистрированный способ получить поэлементный ответ; до этого одиночный «нет» → `UNKNOWN`. (4) Независимый врачебный sign-off CQ-CTX-01…03 (+ CQ-CTX-08, CQ-CTX-09) — `PENDING`. (5) Пакет B (транспорт `safety_recheck.start`, runtime state machine, provenance carrier, live-clearance) не реализован; `clear_restriction()` закрыт.

Fixtures v0.2.5 → v0.2.6: +32 `T-S1-RCB-*` (ALLOW 11 / DENY 12 / UNKNOWN 9); итог 125; technical `NOT_IMPLEMENTED`; clinical `PENDING_INDEPENDENT_PHYSICIAN_SIGNOFF`.
