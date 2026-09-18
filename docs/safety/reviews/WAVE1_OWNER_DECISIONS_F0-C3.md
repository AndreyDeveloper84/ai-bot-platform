# Wave 1 Owner Decision Pack — F0 / C3 Safety Matrix

**Статус:** `OWNER DECISION PACK — WAVE 1 — PARTIALLY RESOLVED`. W1-06 = C ([OD-BOT §154]) и W1-07 = A for Controlled Pilot ([OD-BOT §155]) — приняты владельцем 17.09; W1-01 / 02 / 03 / 04 / 05 / 08 — OPEN, ни один их вариант **не принят**; «Recommended option» — рекомендация оркестратора, не ruling. Baseline `docs/safety/F0-C3-safety-matrix.md` v0.12-reviewfix1 этими решениями **не редактировался** (только cross-reference, §14.4).
**Дата:** 2026-09-17. **Составитель:** Safety Reconciliation Orchestrator.
**Источники:** `docs/safety/reviews/REVIEW_RECONCILIATION_F0-C3_v0.12_2026-09-12.md` (CF-01…08, §9 architecture packet, §17 O-01…O-25, REVIEW-CONFLICT-01); `safety-review-001.md`, `legal-review-001.md`, `privacy-review-001.md`, `clinical-review-001.md`, `architecture-review-001.md`; FINAL FREEZE 2026-09-09 (V1–V8, M1–M14, §1, §5, §6, §8); свод 11.09 §3 / §4; DRE v1.0 §3.1, §8, §11, §11.4, §13.5, §22; пакет 2 (B6, B13, C2, C3); `[OD-BOT §72 / §98 / §126 / §127 / §128]` (`ai-bot-platform/docs/OPEN_DECISIONS.md`); Consent Scope Registry §4; **решения владельца после review** — `docs/OWNER_DECISIONS_2026-09-15_PACKAGE3.md` (п. 5–10, 16), `docs/OWNER_DECISIONS_RECOMMENDED_2026-09-15.md` §F0 / D7.
**Статус решений на 17.09:** **W1-06 = C** — [OD-BOT §154]; **W1-07 = A for Controlled Pilot** — [OD-BOT §155] (зарегистрированы в `ai-bot-platform/docs/OPEN_DECISIONS.md`, ветка `docs/od-bot-safety-owner-rulings-2026-09-17`, ожидает commit / merge главным окном); разделы W1-06 / W1-07 ниже — `RESOLVED — historical options retained for traceability`. W1-01 = OPEN, W1-02 = OPEN, W1-03 = OPEN, W1-04 = OPEN, W1-05 = OPEN, W1-08 = OPEN.

**Правило пакета:** OD-SAF-11…22 не переоткрываются (все пять reviewers: owner semantics PASS). Ровно восемь решений. Каждое — в едином формате. Варианты, которые владелец **уже** закрыл 15.09, из вопросов убраны и названы явно, чтобы не переоткрывать.

---

## 0. Что уже решено после review 12.09 — в Wave 1 не спрашивается

| Решено | Источник | Влияет на |
|---|---|---|
| v0.12 — единственный рабочий successor для канонизации; старый черновик — historical / superseded, не альтернативная policy; v0.12 остаётся WORKING DRAFT до закрытия review gates | пакет 3 п. 7 (ответ на ARCH-F01 / F0) | **W1-05** — остаётся только остаток (lifecycle-термин, баннеры, целевой узел, момент переезда) |
| S1 medical escalation для Pilot РФ → **103 / 112**, не психологическая линия, не администратор как medical authority; safety-sensitive flow прекращается, понятный next step | пакет 3 п. 8 | **W1-06** — медицинская экстренность и психологический кризис уже разведены владельцем по каналу; вопрос W1-06 — только о **месте** кризиса в таксономии |
| safety-sensitive Controlled Pilot ждёт S1 detector validation; PASS — только после клинического эксперта | пакет 3 п. 5, 9 | **W1-01** — любая опция, опирающаяся на детекторы, действует не раньше этого gate |
| S1 без usable clarification → `STOP + ESCALATE_TO_MEDICAL_HELP`; S1 `GENERAL_EDUCATION` / self-care blocked до policy boundary; S3 / S4 без validated rule → `UNKNOWN / INCOMPLETE`; не собирать лишнюю медицинскую детализацию | RECOMMENDED §F0 (приняты владельцем 15.09) | O-11, O-13 q3, O-09 (S3 / S4) — вне Wave 1 |
| «отёки» — health-sensitive, fail-closed; «напряжение» без боли — потребность, не S2 | пакет 3 п. 6 / 6b | O-22 — вне Wave 1 |
| operational handoff recipient — владелец салона (Owner / Admin), **не** medical authority | пакет 3 п. 16 | O-12 q2 часть; **W1-07** (что видит пользователь) |
| состоявшиеся записи обезличиваются и хранятся 5 лет | RECOMMENDED D7 | **W1-08** (deletion linkage, TRANSACTION scope) |
| `REPLAY_LIVE_CAPTURE_ENABLED` не включать до проверки доступа к ПДн-трейсам и Privacy boundary | пакет 3 п. 13 | **W1-08** (ReplayTrace — отдельная gate) |

---

## W1-01 — No-signal semantics

```text
Decision ID:            W1-01
Source findings:        CF-01 (SAFETY-F01 BLOCKER; SAFETY-F15(a); CLINICAL-F10; CF-03); R-11 baseline; R-9 п. 8
Problem:                Baseline не определяет исход самого частого пути (>90 % ходов пилота): safety применима,
                        engine отработал, ни один сигнал S1–S10 не обнаружен. §4.1 «NORMAL = реально вычислена» и
                        §8.2 / OD-SAF-14 «нет validated rule → UNKNOWN» не соединены правилом. DRE §11.4 допускает
                        «нет правил + engine отработал → NORMAL»; преамбула baseline цитирует §11.4 как
                        «пустая таблица → UNKNOWN»; FINAL FREEZE §1: отсутствие данных / неизвестное правило —
                        никогда молчаливый NORMAL. Код: assessment.py ALLOW → NORMAL; pre_check default ALLOW.
Why this must be decided now:
                        Это единственное место, где silent downgrade возможен по построению, а не по ошибке
                        реализации. Без строки следующий исполнитель либо повторит ALLOW → NORMAL (fail-open),
                        либо даст UNKNOWN на каждом ходе без сигнала (pilot без рекомендательной поверхности).
                        Gate: CANON, PILOT. Зависит от W1-07 (до согласия S2–S9 не детектируются — см. ниже).
Existing constraints:   OD-SAF-13 (NORMAL = «safety evaluation completed and affected flow may continue»);
                        OD-SAF-14 (missing validated policy → UNKNOWN без вопроса; NO RULE != NOT_APPLICABLE;
                        NO RULE != SAFE); FINAL FREEZE §1, M2/M3 (неупомянутый признак = UNKNOWN presence,
                        не ABSENT), M5 (requires_health_check = mandatory evaluation; без валидного результата →
                        UNKNOWN), M13 (missing / invalid SafetyResult ≠ NORMAL); [OD-BOT §72]
                        (ELIG_SAFETY_CLEARED только из NORMAL; NOT_APPLICABLE — по типу поверхности);
                        DRE §11.4; CF-03 / reviewfix1 (S1 universal rule обязательна → «непустой реестр»
                        гарантирован минимум S1); пакет 3 п. 5 / 9 (detector validation PASS — только после
                        клинического эксперта); пакет 2 B6 (interim CLARIFY до утверждённой матрицы).
```

**Option A — Detector-negative достаточно для `NORMAL`.**
- Semantics: engine отработал на текущей revision с валидным artifact (минимум S1 universal rule), ни один детектор S1–S10 не дал `PRESENT` → `aggregate_state = NORMAL`, `evaluation_status = EVALUATED`, `capability_decisions` = `ALLOWED` для safety-sensitive capabilities (при прочих гейтах). Основание `NORMAL` = «сигналов нет», не «правило разрешило».
- Pros: совпадает с DRE §11.4 и с кодом (миграция R-9 п. 8 — переименование основания, не поведение); pilot получает рекомендательную поверхность на всех 14 предложениях; OD-SAF-13 wording «evaluation completed» буквально выполнен; не требует ни одного procedure-specific rule.
- Cons: `NORMAL` целиком зависит от recall детекторов — пропуск сигнала = fail-open по построению (прецедент DRF-973 «онемела рука не ловилась»; CLINICAL-F01 — ≥ 4 из 7 групп S1 без паттернов до S-1); для процедур с `requires_health_check = true` Ayla даёт `NORMAL` без единого validated rule — M5 «target — policy refs» не выполнен; `ELIG_SAFETY_CLEARED` читается как clearance (CF-16 q2); `NORMAL` без `rule_id` рвёт цепочку трассируемости FINAL FREEZE §1 (`evidence_ref → signal → rule_id → …`), если не ввести явное основание.
- Failure mode: пропуск детектора → `NORMAL` → `ELIG_SAFETY_CLEARED` → запись; ни один сторож не краснеет, потому что «сигнала нет» неотличимо от «сигнал пропущен».
- Effect on pilot: разблокирует (после S1 detector validation PASS — пакет 3 п. 5). Effect on runtime: `ALLOW → NORMAL` остаётся, но должен нести `basis = NO_SIGNAL_DETECTED` и `policy_version` artifact, на котором детекторы валидированы.
- Interaction with OD-SAF-14: совместимо только при чтении «OD-SAF-14 применяется, когда сигнал **есть**, а rule для пары сигнал × процедура нет». Это сужение OD-SAF-14 — владелец должен его подтвердить явно, иначе A противоречит «NO RULE != SAFE».

**Option B — `NORMAL` только если validated procedure / capability rule явно допускает отсутствие сигналов.**
- Semantics: для каждой пары `procedure class × capability` в artifact должна существовать permissive / N/A запись («при отсутствии сигналов S1–S10 → `ALLOWED`») с `rule_id` / provenance; нет записи → `UNKNOWN / INCOMPLETE` (ветка C S10) даже при полном отсутствии сигналов.
- Pros: полностью fail-closed; каждый `NORMAL` несёт `rule_id` (§1 трассируемость, §5.2 инвариант); буквальное OD-SAF-14 и `NO RULE != SAFE`; `ELIG_SAFETY_CLEARED` действительно означает «правило проверено»; DRE §11.4 при этом не нарушен (engine отработал и вернул UNKNOWN по правилу отсутствия записи).
- Cons: до появления permissive-записей для всех классов процедур slice каждый ход → `UNKNOWN` → только каталог / direct booking ([OD-BOT §72], C2); semantic Recommendation мёртв (13 / 14 предложений пилота); объём — N классов процедур × permissive запись, каждая требует той же валидации (CF-27: NO RULE ≠ NOT_APPLICABLE); давление «заполнить permissive быстро» = FINAL FREEZE §8 п. 21 (populate rules from common sense); расхождение с DRE §11.4 нужно закрыть spec-правкой.
- Failure mode: пилот «безопасен, но бесцелен» (формулировка Clinical для S2); либо permissive-записи пишутся без клинического источника ради запуска — silent policy invention.
- Effect on pilot: блокирует semantic Recommendation до permissive-записей (Owner + Clinical, OD-F0C3-11 / 15 / 17 / 18 / 19 / 21 / 30 / 33 / 45). Effect on runtime: новый rule kind «permissive default per procedure class»; `ALLOW → NORMAL` удаляется.
- Interaction with OD-SAF-14: тождественно OD-SAF-14 без сужения.

**Option C — Hybrid: generic non-health-sensitive flow — detector-negative достаточно; `requires_health_check` / safety-sensitive procedure — только validated rule.**
- Semantics: критерий «safety-sensitive» = `requires_health_check = true` у целевой процедуры (backend authority, M5) **или** любой сигнал S1–S10 `PRESENT` (тогда обычный путь rule / UNKNOWN). Для `requires_health_check = false` и отсутствия сигналов → `NORMAL` с `basis = NO_SIGNAL_DETECTED`; для `requires_health_check = true` без сигналов → `NORMAL` только по permissive rule, иначе `UNKNOWN` → маршрут к человеку по [OD-BOT §98].
- Pros: совпадает с M5 (RHC = mandatory evaluation с policy refs) и с [OD-BOT §98] (RHC = true уходит к человеку и без матрицы); pilot slice: 13 / 14 предложений RHC = false → поверхность жива; 1.3.24 (RHC = true) → как сегодня, к человеку; fail-closed ровно там, где домен объявил health-чувствительность; permissive-записи нужны только для RHC = true классов (объём мал).
- Cons: два основания `NORMAL` → consumer обязан видеть `basis` (иначе CF-16 «NORMAL ≠ clearance» невыразимо); `requires_health_check` становится **селектором строгости safety** — это не «contraindication / ranking factor» (§8 п. 12), но это policy-роль для backend-флага, которую надо объявить явно (V8: backend владеет RHC, не safety policy); сегодня RHC = false — умолчание шаблона, не суждение (A6: услуги без ServiceTemplate → HEALTH_CHECK_UNKNOWN), значит строгость зависит от полноты каталога; лицензируемые процедуры (CF-25, O-20) должны попасть в «safety-sensitive» отдельным атрибутом.
- Failure mode: RHC = false у health-чувствительной услуги (ошибка каталога) → detector-negative → `NORMAL`; ловится только сторожем каталога, не safety engine.
- Effect on pilot: разблокирует 13 / 14 после S1 detector validation PASS. Effect on runtime: `basis` в `SafetyResult` / `RuleResult` (форма — W1-04); ALLOW → NORMAL сохраняется только для RHC = false; для RHC = true — rule path.
- Interaction with OD-SAF-14: OD-SAF-14 действует без сужения для RHC = true и при любом `PRESENT` сигнале; сужение «нет сигнала → NORMAL» ограничено RHC = false — владелец подтверждает это как **область**, не как изменение ruling.

```text
Recommended option:     C — с двумя обязательными условиями:
                        (1) NORMAL всегда несёт machine-readable basis ∈ {NO_SIGNAL_DETECTED, RULE_ALLOWED}
                            и policy_version валидированного artifact; consumer / user-facing различают
                            (CF-16: NORMAL ≠ clearance);
                        (2) NORMAL-by-absence действует только ПОСЛЕ consent gate (W1-07): до согласия S2–S9
                            не детектируются, значит «сигнала нет» ≠ «сигнала не было» — там semantic
                            Recommendation закрыта по W1-07, не NORMAL.
                        Критерий «safety-sensitive» = requires_health_check = true (backend) до решения CF-25;
                        licensed_medical_service добавляется как второй критерий, когда решён.
Why:                    A делает NORMAL заложником recall (три reviewer'а назвали это единственным
                        silent-downgrade по построению); B закрывает pilot целиком и провоцирует §8 п. 21;
                        C кладёт fail-closed ровно туда, где домен уже объявил health-чувствительность (M5,
                        [OD-BOT §98]), и сохраняет OD-SAF-14 без изменения текста — сужается только область.
What becomes unblocked: §4.1 строка «сигнала нет»; T-GLOBAL no-signal (golden §10); миграция R-9 п. 8 по
                        FINAL FREEZE §7 с явным basis; pilot gate CF-01; W1-02 (агрегат при отсутствии
                        rule results); W1-04 (поле basis в RuleResult / SafetyResult).
What remains open:      CF-09 default CLARIFY S2 (S3 / S4 решены §F0); CF-10 interim mode B6 ↔ OD-SAF-14;
                        CF-25 licensed procedures как второй критерий; сторож каталога «RHC = false у
                        health-чувствительной услуги» (Backend + Safety); wording NORMAL-by-absence (CF-16).
Exact owner decision requested:
                        «Достаточное основание для NORMAL при отсутствии сигналов S1–S10:
                         (A) детекторы отработали на валидном artifact и ничего не нашли — для всех процедур;
                         (B) только validated permissive rule для целевой процедуры / capability;
                         (C) (A) для requires_health_check = false, (B) для requires_health_check = true
                             и для любого PRESENT сигнала.
                         При (A) или (C): подтверждаете ли, что NORMAL несёт basis NO_SIGNAL_DETECTED |
                         RULE_ALLOWED и что NORMAL-by-absence не действует до согласия HEALTH (W1-07)?
                         При (C): подтверждаете ли requires_health_check как критерий safety-sensitive
                         до решения CF-25?»
```

---

## W1-02 — Aggregate state vs capability decisions

```text
Decision ID:            W1-02
Source findings:        CF-02 (SAFETY-F02 BLOCKER; ARCH-F08; SAFETY-F07; ARCH-F07); R-9 п. 4–6, 9; R-10
Problem:                Единственный consumer (DRE §11, engine.py:264–345) читает safety.state:
                        STOP → BLOCKED на весь ход, CLARIFY → NEEDS_REQUIRED_CONTEXT; forbidden_capabilities не
                        читается. Baseline не задаёт: множество агрегации aggregate_state; значение при
                        INCOMPLETE / CONFLICTED / ERROR; исключение постоянно BLOCKED медицинских capabilities;
                        область CLARIFY (§4.1 «целиком» по B6 vs §8.3 «затронутые» по OD-SAF-09/22).
                        Реализация по DRE (state-driven) и по M13 (decision-driven) дадут разное поведение
                        на одних owner semantics — обе «по документу».
Why this must be decided now:
                        Gate: CANON, IMPL. Без формулы нельзя написать адаптер DRE, T-S8-13/17, T-S10-15,
                        сторож «enum = 4» на consumer-типе; без области CLARIFY golden §10 имеет два expected.
Existing constraints:   OD-SAF-08 (STOP > CLARIFY > CAUTION > NORMAL с учётом релевантности); OD-SAF-09
                        (сигнал ограничивает capability, не сценарий; исключение — только S1); OD-SAF-20 п. 11–12
                        (S8(B): STOP только для medical capability, unrelated wellness оцениваются независимо;
                        global STOP wellness-flow не вводить); OD-SAF-22 п. 10.6 (альтернатива — после
                        собственной evaluation); пакет 2 B6 (interim: до утверждённой матрицы semantic
                        Recommendation не формируется до ответа); канон v1.1 §7.1; FINAL FREEZE M10
                        (агрегация), M13 (consumers читают capability_decisions; aggregate_state ∈
                        NORMAL | CLARIFY | CAUTION | STOP | null), §2.2; [OD-BOT §72] (один safety-sensitive
                        кандидат БЕЗ валидного SafetyResult закрывает весь персонализированный ответ —
                        это про отсутствие результата, не про REQUIRES_RESOLUTION); §7 baseline (четыре
                        медицинские capabilities не «блокируются состоянием» — они не разрешены ни в одном
                        состоянии); DRE spec_version 1.0.0 (механизм расширения есть).
```

### A. Область `CLARIFY`

**Option A1 — Whole semantic recommendation paused** (B6, канон §7.1, DRE §11 сегодня).
- Pros: совпадает с interim B6 и с текущим consumer; один вопрос — один ход — нет ответа — нет рекомендации; проще для пилота; нет риска «рекомендовали альтернативу, пока человек отвечает про боль».
- Cons: противоречит OD-SAF-09 / OD-SAF-22 п. 10.6 / OD-SAF-20 п. 12 (unrelated capabilities оцениваются независимо) как **постоянная** семантика; при S5-вопросе про массаж закрывается и маникюр; T-S8-13/17, T-S10-15 недостижимы.
- Failure mode: CLARIFY по одному кандидату = de facto global CLARIFY пользователя — та самая «остановка сценария целиком», которую OD-SAF-09 запрещает.

**Option A2 — Only affected candidate / capability paused; незатронутые рекомендуются после собственной evaluation в том же ходе** (OD-SAF-09, OD-SAF-22 п. 10.6).
- Pros: буквально по owner rulings; агрегат = max по релевантным правилам per capability; golden §10 п. 15–16 выполнимы; ELIG по кандидату, не по ходу.
- Cons: ход одновременно содержит вопрос и рекомендацию — UX-риск «вопрос теряется» (не safety-риск, но CF-16 / OD-F0C3-08 wording); требует, чтобы DRE читал `capability_decisions` per candidate (spec_version bump; R-9 п. 4–5); [OD-BOT §72] нужно переформулировать: «без валидного SafetyResult» ≠ «с REQUIRES_RESOLUTION у одного кандидата».
- Failure mode: незатронутая альтернатива оценена **не** независимо (общий evidence set с открытым вопросом) → рекомендация «в обход» вопроса; ловится T-S10-15.

**Option A3 — Affected paused; unrelated alternatives only after clarification resolved.**
- Pros: компромисс: нет двух действий в одном ходе; OD-SAF-09 сохраняется как принцип (альтернатива не запрещена, отложена).
- Cons: на практике = A1 на один ход, а после ответа = A2; при исчерпанном clarification (OD-SAF-22 п. 10) альтернатива всё равно нужна — значит правило «только после resolution» имеет исключение по построению; два режима поведения одного состояния.
- Failure mode: «после ответа» никогда не наступает (budget exhausted) → альтернатива заблокирована навсегда, если исключение не прописано.

### B. Medical intent + wellness: `RECOMMEND_MEDICATION = BLOCKED`, wellness `ALLOWED` — что в `aggregate_state`?

**Option B1 — `aggregate_state = STOP`** (STOP свода 11.09 §3 п. 7 как state).
- Pros: consumer сегодня это и делает; «жёстче = безопаснее» интуитивно.
- Cons: прямо против OD-SAF-20 п. 11 (STOP только для medical capability) и п. 12 (unrelated wellness независимо); DRE → BLOCKED на весь ход; M10 подавляет wellness-CLARIFY (T-S8-13/17 красные по построению).
- Failure mode: «что выпить от головы? и запиши на маникюр» → весь ход BLOCKED — global STOP wellness-flow, который владелец запретил.

**Option B2 — `aggregate_state` отражает только wellness-релевантные rule results; медицинский `BLOCKED` виден только в `capability_decisions` (с reason).**
- Pros: буквально OD-SAF-20 п. 11–12; §7 baseline (медицинские capabilities не блокируются состоянием — они всегда BLOCKED); aggregate — summary, consumers читают решения (M13).
- Cons: `aggregate_state = NORMAL` при явном medication-intent выглядит «странно» в логах / observability — нужен reason code на capability и `response_constraints` для outbound; intent должен где-то давать `rule_id` (W1-03).
- Failure mode: consumer читает только state (R-9 п. 4) → NORMAL → LLM отвечает на медицинский вопрос; ловится только если DRE читает `capability_decisions` (адаптер обязателен).

**Option B3 — Медицинские capabilities исключены из множества агрегации по построению; `aggregate_state = null`, если в ходе нет ни одной safety-sensitive wellness capability для оценки.**
- Pros: формально чисто: aggregate вычисляется только над «оцениваемыми» capabilities; при чистом медицинском intent без wellness — `null` (M13 допускает null), не NORMAL и не STOP; при wellness — как B2.
- Cons: `null` уже занят под «не вычислялось» (M13: `aggregate_state: … | null`, `evaluation_status = NOT_REQUIRED`) — нужно различить `null` «не требовалось» и «оцениваемых capabilities нет»; сложнее объяснить.
- Failure mode: consumer трактует `null` как missing SafetyResult → BLOCKED всего (M13 fail-closed) — безопасно, но чистый медицинский вопрос заблокирует и unrelated разговор.

```text
Recommended option:     A = A2 как каноническая семантика (после canonicalization), с оговоркой: до
                        canonicalization действует interim B6 = A1 (это O-10, не Wave 1); [OD-BOT §72]
                        уточняется: «без валидного SafetyResult» — про отсутствие / невалидность результата,
                        не про REQUIRES_RESOLUTION одного кандидата.
                        B = B2 + элемент B3: медицинские четыре capabilities не входят в множество агрегации
                        (их BLOCKED — константа с reason OUT_OF_SCOPE_MEDICAL / NO_POLICY_RULING);
                        aggregate_state = max по rule results, релевантным оцениваемым wellness capabilities;
                        при отсутствии оцениваемых capabilities — null с evaluation_status = NOT_REQUIRED
                        (различимо от missing по evaluation_id).
                        Формула агрегата (architecture, без медицины):
                          aggregate_state = max_{rule ∈ relevant(capabilities_under_evaluation)}
                                             state_contribution(rule)   по приоритету OD-SAF-08;
                          evaluation_status ∈ {INCOMPLETE, CONFLICTED, ERROR} → aggregate_state = null
                                             ИЛИ последнее вычисленное? — НЕТ: null; capability_decisions
                                             при этом fail-closed по причине (M13, OD-SAF-22);
                          consumers читают capability_decisions; aggregate_state — observability / summary.
                        Consumer: DRE переводится со state-driven на decision-driven (spec_version 1.1.0),
                        forbidden_capabilities → tri-state capability_decisions.
Why:                    A2 / B2 — единственная пара, при которой T-S8-13/17, T-S10-15, OD-SAF-09 и
                        OD-SAF-20 п. 11–12 выполнимы одновременно; A1 / B1 — это то, что владелец
                        уже запретил как постоянную семантику, но разрешил как interim (B6).
What becomes unblocked: §4.1 / §8.3 (единая область CLARIFY); адаптер DRE и T-S10-14 на consumer-типе;
                        T-S8-04/13/17, T-S10-15, golden §10 п. 15–16; R-9 п. 4–6, 9 — путь миграции.
What remains open:      O-10 interim mode (B6 до canonicalization); wording «вопрос + рекомендация в одном
                        ходе» (OD-F0C3-08); closed set reason codes (W1-04 / Safety); DRE spec_version bump —
                        implementation после gates.
Exact owner decision requested:
                        «(A) Область CLARIFY как каноническая семантика: (A1) вся semantic Recommendation
                         до ответа; (A2) только затронутые capabilities, незатронутые — после собственной
                         evaluation в том же ходе; (A3) незатронутые — только после ответа.
                         (B) При RECOMMEND_MEDICATION = BLOCKED и wellness ALLOWED aggregate_state =
                         (B1) STOP; (B2) отражает только wellness, медицинский BLOCKED виден в
                         capability_decisions; (B3) медицинские capabilities вне множества агрегации,
                         null при отсутствии оцениваемых wellness capabilities.
                         Подтверждаете ли, что consumer (DRE) переводится на чтение capability_decisions
                         (M13) и что до canonicalization действует interim B6 (= A1)?»
```

---

## W1-03 — Intent-capability architecture

```text
Decision ID:            W1-03
Source findings:        CF-04 / OD-F0C3-04 (ARCH-F02 BLOCKER; ARCH-F13; SAFETY-F15(c)); R-5, R-9 п. 7;
                        packet §9 OD-F0C3-04 (architecture-review-001.md §5.2)
Problem:                Baseline не фиксирует, где живут medication request, diagnosis request, treatment
                        request и иные prohibited intents (FINAL FREEZE M4 класс 3): дают ли они вклад в
                        aggregate_state; есть ли у BLOCKED по intent rule_id / rule_version (цепочка §1);
                        три intent без адресата — «опасно ли мне?», юридический совет, незамапленный intent.
                        «Свод 11.09 §3 п. 7: запрос препарата — STOP» ↔ «FINAL FREEZE V2: intent ≠ STOP»
                        реконсилирован в baseline чтением «STOP = capability BLOCKED» (R-5) без ruling.
                        pre_check._risk_elevation() поднимает вердикт по LLM risk_level (§8 п. 8).
Why this must be decided now:
                        Gate: CANON, IMPL. Определяет форму RuleResult (W1-04 — нужны ли rule без
                        state_contribution) и множество агрегации (W1-02). Golden §10 п. 9, 15–16 имеют
                        два разных expected; миграция pre_check BLOCK / CLARIFY по FINAL FREEZE §7
                        невозможна без адресата.
Existing constraints:   OD-SAF-20 ветка B (medication intent → medical capability BLOCKED без medical ruling;
                        mention ≠ intent; global STOP не вводить); OD-SAF-22 (не закрывать OD-F0C3-04
                        автоматически; новый S-класс не придумывать); OD-SAF-10 / §7 baseline (четыре
                        медицинские capabilities не разрешены ни в одном состоянии); FINAL FREEZE M4
                        (класс 3 — Intent / Capability Safety Signals), V2 (intent ≠ STOP; framework для
                        медицины и права), §1 (каждое решение трассируемо до rule_id), §8 п. 8 (LLM не
                        выбирает state / severity); reviewfix1 §7 (DIAGNOSE = любое индивидуализированное
                        медицинское суждение); §2 baseline (юридический совет — вне периметра).
Ключевое наблюдение оркестратора (снижает ставки):
                        Четыре медицинские capabilities BLOCKED в ЛЮБОМ состоянии независимо от intent-гейта.
                        Значит intent-гейт не решает «разрешить или запретить» — он решает (a) reason code и
                        response_constraints (что и как сказать), (b) адресатов «опасно ли мне?» / legal /
                        unmapped, (c) не загрязняет ли intent aggregate_state. Ошибка детекции intent в
                        сторону «нашёл» = лишнее ограничение формулировки, не открытие capability.
```

**Option A — Отдельный S-класс (S11 «intent»).**
- State impact: S-структура подразумевает state contribution → intent начинает давать STOP / CLARIFY — против V2 и OD-SAF-20 п. 11.
- rule_id provenance: есть (строка реестра S11), но provenance — не медицинский источник, а product / legal policy; в медицинском реестре — чужеродно.
- Capability decision: как у остальных S — через RuleResult.
- Auditability: хорошая (единый реестр).
- LLM role: детектор intent = extraction; но S-структура толкает LLM в «оценку тяжести» intent (§8 п. 8).
- Interaction with aggregate: по умолчанию входит в M10 → global STOP wellness при medication-intent (T-S8-13/17 красные) — придётся вводить исключение «S11 не агрегируется» = S-класс без свойств S-класса.
- Pros: одна таблица; Cons: владелец велел не выдумывать S; расширение таксономии OD-SAF-01. Failure mode: intent → aggregate_state = STOP → global wellness STOP.

**Option B — Отдельный intent / capability gate, ортогональный S1–S10, со своим реестром (I-класс, M4 класс 3).**
- State impact: **никакого** — rule shape `intent → capability_decisions + response_constraints (+ escalation)`; `state_contribution = null`; сторож «intent никогда не меняет aggregate_state».
- rule_id provenance: есть — стоячие правила блокировки (`I-MEDICATION-REQUEST`, `I-DIAGNOSIS-REQUEST`, `I-TREATMENT-REQUEST`, `I-LEGAL-ADVICE`, `I-UNMAPPED`) с `rule_version`, provenance = OD-SAF-10 / OD-SAF-20 B / V2, владелец реестра — `ayla-knowledge` (V8); в одном artifact с S-правилами, но отдельная секция и отдельный валидатор-сторож.
- Capability decision: `BLOCKED` с reason `OUT_OF_SCOPE_MEDICAL` (не `NO_POLICY_RULING` «пока» — LEGAL-F18) для четырёх медицинских; `response_constraints` для outbound; `EXPLAIN_NEXT_STEP` / `GENERAL_EDUCATION` — по своим правилам.
- Auditability: полная (rule_id в DecisionEvidence), различимо от S-блокировок по классу правила.
- LLM role: intent presence `PRESENT | UNKNOWN`, origin только USER; LLM извлекает кандидат intent (MODEL_INFERENCE как детектор), не risk_level; `_risk_elevation` удаляется (§8 п. 8); асимметрия допустима: LLM-детекция может **только закрыть** формулировку, никогда открыть capability.
- Interaction with aggregate: не участвует (W1-02 B2/B3 согласованы).
- Pros: буквально M4 класс 3 и V2; закрывает T-S8-04/13/17, T-S3-06, T-S4-05, T-S6-14, T-S9-18 одним expected; адресаты для трёх intent. Cons: второй реестр внутри artifact (нужен валидатор «I-правила не имеют state_contribution»); детекторы intent — отдельный корпус. Failure mode: I-правило случайно получает state_contribution → ловится статическим валидатором.

**Option C — Часть recommendation / intent layer без Safety registry (чистый capability gate).**
- State impact: нет. rule_id provenance: **нет** — BLOCKED без rule_id / rule_version (цепочка §1 разорвана; reason «как предложение»); prompts / conditionals как safety authority (§8 п. 22, V8).
- Capability decision: жёстко в коде платформы. Auditability: слабая (нет policy_version). LLM role: intent-классификатор платформы = de facto authority.
- Interaction with aggregate: нет. Pros: минимальная реализация (pre_check уже так работает). Cons: второй safety authority в платформе; legal / «опасно ли» без адресата; миграция pre_check невозможна «по правилу». Failure mode: изменение промпта меняет safety-поведение без версии.

```text
Recommended option:     B — отдельный intent-реестр (I-класс), ортогональный S1–S10, в том же artifact
                        ayla-knowledge и том же evaluator (reviewfix1 §8.1 — один engine), без вклада в
                        aggregate_state; «STOP» свода 11.09 §3 п. 7 читается как capability BLOCKED (V2).
                        Подвопросы (technical recommendation):
                          «опасно ли мне?» — запрос safety explanation: не DIAGNOSE; отвечается
                            EXPLAIN_NEXT_STEP / GENERAL_EDUCATION в рамках response_constraints текущего
                            SafetyResult (при UNKNOWN — «не могу оценить», без «опасно»); индивидуальный
                            прогноз — DIAGNOSE (reviewfix1 §7);
                          юридический совет — вне периметра (§2), I-LEGAL-ADVICE → BLOCKED + нейтральное
                            объяснение границ, без capability в §7 (V2 framework — отдельное решение позже);
                          незамапленный intent — ни одна medical capability не открывается (они и так
                            BLOCKED), wellness — по S-оценке; I-UNMAPPED не даёт ограничений, только не
                            даёт разрешений.
Why:                    Только B удовлетворяет одновременно §1 (rule_id), V2 / OD-SAF-20 п. 11 (intent ≠
                        state), §8 п. 22 (нет второго authority) и «не выдумывать S». A — S без свойств S;
                        C — то, что есть сегодня, и это R-9 п. 7 / п. 9.
What becomes unblocked: OD-F0C3-04; R-5; expected для T-S8-04/13/17, T-S3-06, T-S4-05, T-S6-14, T-S9-18;
                        миграция pre_check BLOCK / CLARIFY и удаление _risk_elevation (FINAL FREEZE §7);
                        сторож «intent никогда не меняет aggregate_state»; W1-04 (RuleResult без
                        state_contribution — допустимый вид).
What remains open:      OD-F0C3-25 (включение медицинских capabilities — не здесь); периметр legal (V2
                        framework); wording (OD-F0C3-08); детекторный корпус intent; closed set reason codes.
Exact owner decision requested:
                        «Intent-сигналы (M4 класс 3): (A) отдельный S-класс S11; (B) отдельный intent-реестр
                         вне S1–S10 в том же artifact, intent даёт только capability decisions /
                         response constraints и никогда aggregate_state; (C) capability-гейт платформы без
                         реестра и без rule_id.
                         Подтверждаете ли чтение «STOP» свода 11.09 §3 п. 7 как capability BLOCKED (V2)?
                         Подвопросы: «опасно ли мне?» — safety explanation, не DIAGNOSE? юридический совет —
                         вне периметра (BLOCKED + объяснение границ)? незамапленный intent — ничего не
                         открывает, wellness по S-оценке?»
```

---

## W1-04 — RuleResult / RESTRICTED contract

```text
Decision ID:            W1-04
Source findings:        CF-05 / OD-F0C3-31 (ARCH-F03 BLOCKER; SAFETY-F12; ARCH-F07; SAFETY-F11; ARCH-F14;
                        LEGAL-F18); R-8; packet §9 OD-F0C3-31; reviewfix1 §8.9 (третий словарь)
Problem:                Три словаря исходов: RuleOutcome ALLOWED / RESTRICTED / BLOCKED (OD-SAF-15…21),
                        capability decision ALLOWED / REQUIRES_RESOLUTION / BLOCKED (M6 / M13), реестр
                        планировочных правил KNOWN_ALLOW / KNOWN_DENY / UNKNOWN / NOT_APPLICABLE (свод 11.09 §4);
                        четвёртый — бинарный forbidden_capabilities в DRE. Golden §10 п. 5–8 доказывает:
                        RESTRICTED — метка над картой capability → decision, не значение. Нет формы RuleResult,
                        closed set reason codes, матрицы допустимых комбинаций осей, представления
                        «controlled unresolved outcome».
Why this must be decided now:
                        Gate: CANON, IMPL. Без формы RuleResult нельзя собрать machine-readable реестр
                        (§15.2 п. 4), написать T-S*-12/15, доказать FINAL FREEZE §8 п. 1 (CAUTION без
                        restrictions невозможен); расхождение словарей Safety ↔ Planning — тот
                        POLICY_CONFLICT, который T-S6-12 обязан ловить, но нечем.
Existing constraints:   OD-SAF-15…21 (ALLOWED → NORMAL, RESTRICTED → CAUTION, BLOCKED → STOP для затронутой
                        capability — текст не меняется); FINAL FREEZE V1 (CAUTION = ограниченное продолжение,
                        не авто-разрешение и не авто-запрет записи), M6 (CAUTION restrictions explicit),
                        M13 (consumers читают ALLOWED | REQUIRES_RESOLUTION | BLOCKED; rule_results
                        сохраняются), §8 п. 1 (CAUTION без capability restrictions запрещён); OD-SAF-22
                        п. 9 (при исчерпанном clarification — REQUIRES_RESOLUTION или BLOCKED по причине,
                        новый enum не вводить); свод 11.09 §4 (форма записи реестра); W1-03 (I-правила без
                        state_contribution); W1-01 (basis NORMAL).
```

**Option A — `RESTRICTED` всегда → `REQUIRES_RESOLUTION`.**
- Pros: просто; fail-closed; один mapping-предикат.
- Cons: CAUTION становится неотличим от CLARIFY на consumer'е (обе → REQUIRES_RESOLUTION), тогда как V1 определяет CAUTION как **продолжение** с ограничениями (в т. ч. ALLOWED с execution constraints — «зону не трогать»); golden §10 п. 5–8 (CAUTION: одни capabilities ALLOWED с ограничениями, booking не авто-ALLOWED) недостижимы; RuleOutcome остаётся третьим enum.
- Failure mode: любое ограничение = «нужно разрешить» → без resolver'а (кто снимает «зону не трогать»?) capability заперта навсегда; либо resolver = мастер (V7 нарушен).

**Option B — `RESTRICTED` — производная summary-label над explicit per-capability decisions.**
- Pros: правило возвращает явную карту `capability → {ALLOWED | REQUIRES_RESOLUTION | BLOCKED} (+ constraints)`; RESTRICTED := производная метка (CAUTION) ⇔ ∃ capability ≠ ALLOWED ∨ constraints ≠ ∅; текст OD-SAF-15…21 сохраняется (RESTRICTED → CAUTION), но перестаёт быть enum в контракте; M13 выполнен буквально; словарь Planning сопоставляется (KNOWN_ALLOW ⇔ ALLOWED, KNOWN_DENY ⇔ BLOCKED, UNKNOWN ⇔ INCOMPLETE, NOT_APPLICABLE ⇔ applicability; REQUIRES_RESOLUTION → Plan INCOMPLETE).
- Cons: нужен валидатор инвариантов (`CAUTION ∧ ∀c ALLOWED ∧ constraints = ∅ ⇒ INVALID`); подвопрос владельцу: допустим ли CAUTION, где все safety-sensitive capabilities ALLOWED и есть только wording-constraints (а1 / а2).
- Failure mode: правило возвращает карту без constraints и с RESTRICTED-меткой «на всякий случай» → ловится статическим валидатором (INVALID), не runtime.

**Option C — Разделить `restrictions[]` и `requirements[]`.**
- Pros: разводит две природы «ограничения»: constraints на ALLOWED capability (execution / wording / escalation — не требуют ответа) и unresolved requirements (нужен факт / событие / решение → REQUIRES_RESOLUTION); закрывает вопрос «кто снимает ограничение» (никто — оно исполняется) vs «кто закрывает requirement» (resolvable_by, OD-F0C3-50); прямо соответствует M13 (`unresolved_requirements`, `response_constraints` — уже два поля).
- Cons: сам по себе не отвечает, что такое RESTRICTED (это делает B); без B — четвёртое представление.
- Failure mode: constraint, требующий действия человека (мастера), записан как constraint, а не requirement → «hollow promise» (CF-12 транспорт CAUTION-ограничений) — ловится только policy review.

```text
Recommended option:     B + C вместе: RESTRICTED = производная метка над явной per-capability картой (B);
                        RuleResult несёт раздельно constraints[] (исполняются) и unresolved_requirements[]
                        (закрываются resolvable_by) (C). Подвопрос а1 / а2 — рекомендация а1 (допустим
                        CAUTION с ALLOWED + только wording-constraints: V2 вариант C именно об этом),
                        но это владелец.
                        Concrete machine-readable shape (концептуально, без медицинского содержания):

                        RuleResult {
                          rule_id, rule_version, policy_version           # provenance (§1, §5.2)
                          rule_class: S | I                                # S1–S10 / intent (W1-03)
                          applicability: APPLICABLE | NOT_APPLICABLE       # только validated rule; NO RULE ≠ N/A
                          evaluation: EVALUATED | INCOMPLETE | CONFLICTED | ERROR
                          evidence_refs[]                                  # refs, не значения (M13)
                          capability_restrictions[]: {
                            capability, decision: ALLOWED | REQUIRES_RESOLUTION | BLOCKED,
                            reason_code, constraints_ref?, requirement_ref?
                          }
                          unresolved_requirements[]: {
                            kind: USER_FACT | EVIDENCE_CONFLICT | POLICY | HUMAN_CONFIRMATION | DOMAIN_EVENT,
                            question_id?, resolvable_by, scope            # resolvable_by — OD-F0C3-50
                          }
                          constraints[]: { kind: EXECUTION | WORDING | ESCALATION, ref }   # value-free
                          basis?: NO_SIGNAL_DETECTED | RULE_ALLOWED         # W1-01 (для NORMAL)
                          outcome_label: ALLOWED | RESTRICTED | BLOCKED     # производное, не authority
                          state_contribution: NORMAL | CAUTION | CLARIFY | STOP | null   # null для I-правил
                        }
                        Reason codes (closed set — предложение Safety, не owner):
                          BLOCKED_BY_RULE, MISSING_USER_FACT, MISSING_POLICY, EVIDENCE_CONFLICT,
                          POLICY_CONFLICT, TECHNICAL_ERROR, OUT_OF_SCOPE_MEDICAL (вместо NO_POLICY_RULING
                          «пока» — LEGAL-F18), ASK_EXHAUSTED, CONSENT_REQUIRED (W1-07; на уровне consumer).
                        Инварианты валидатора: RESTRICTED ⇔ CAUTION ∧ (∃c ≠ ALLOWED ∨ constraints ≠ ∅);
                          CAUTION ∧ ∀c ALLOWED ∧ constraints = ∅ ⇒ INVALID; I-правило с
                          state_contribution ≠ null ⇒ INVALID; REQUIRES_RESOLUTION без requirement_ref ⇒ INVALID.
                        Planning: KNOWN_ALLOW ⇔ ALLOWED, KNOWN_DENY ⇔ BLOCKED, UNKNOWN ⇔ INCOMPLETE,
                          NOT_APPLICABLE ⇔ applicability; REQUIRES_RESOLUTION → Plan INCOMPLETE (явно).
                        DRE: forbidden_capabilities (бинарный) → capability_decisions tri-state
                          (spec_version 1.1.0).
Why:                    Только B делает golden §10 п. 5–8 и §8 п. 1 доказуемыми; C — единственный способ
                        не превратить каждое ограничение исполнения в «нужен ответ» и не назначить мастера
                        resolver'ом (V7). A закрывает CAUTION как класс поведения, который владелец
                        утвердил (V1).
What becomes unblocked: схема реестра (FINAL FREEZE Phase 2); consumer DRE; T-S*-12/15; golden п. 5–8;
                        OD-F0C3-31, 37 (сопоставление Planning); §8.3 baseline; W1-02 (карта = вход агрегата).
What remains open:      содержание restrictions per family (Clinical, OD-F0C3-11…45); resolvable_by
                        (OD-F0C3-50 / CF-12); closed set reason codes — Safety; транспорт EXECUTION-
                        constraints к исполнителю (CF-12 q5, O-12) — не здесь.
Exact owner decision requested:
                        «Проекция RESTRICTED → CAUTION: (A) RESTRICTED всегда → REQUIRES_RESOLUTION;
                         (B) правило возвращает явную карту capability → {ALLOWED | REQUIRES_RESOLUTION |
                         BLOCKED} (+ constraints), RESTRICTED — производная метка; (C) раздельные
                         restrictions[] (исполняются) и requirements[] (закрываются) — совместимо с B.
                         При (B)/(C): CAUTION со всеми safety-sensitive capabilities ALLOWED и только
                         wording-constraints — (а1) допустим / (а2) недопустим?
                         Принимаете ли форму RuleResult выше как contract-каркас (значения полей —
                         Safety / Clinical позже)?»
```

---

## W1-05 — Authoritative successor path (остаток после пакета 3 п. 7)

```text
Decision ID:            W1-05
Source findings:        CF-06 / OD-F0C3-03 (ARCH-F01 BLOCKER); R-3; packet §9 OD-F0C3-03; DRF-2003 (S-4)
Problem:                Core решён владельцем 15.09 (пакет 3 п. 7): v0.12 — единственный successor,
                        черновик — historical / superseded, не альтернативная policy, v0.12 остаётся WORKING
                        DRAFT. Документы решение не догнали (DRF-2003): в черновике нет баннера; три документа
                        цепочки 08.09 читаются как открытые, хотя закрыты FINAL FREEZE V1–V8; execution plan
                        (docs/mapping/FORMULA_TELA_AUTOMAPPING_EXECUTION_PLAN_2026-09-12.md) цитирует черновик
                        как safety-вход; термин «superseded» (R-3) не является термином lifecycle-схемы
                        ayla-knowledge для неутверждённого draft (Ayla Domain and Metadata Registry: draft →
                        review | cancelled; superseded применяется к approved); baseline не в ayla-knowledge,
                        без frontmatter; целевой узел и момент переезда не названы.
Why this must be decided now:
                        Gate: CANON (§15.2 п. 1). Пока черновик без баннера, следующий агент, открыв F0-строку
                        старых документов, вернётся к порогам «> 2–3 нед / < 72 ч / < 6 нед» (M3 / M9
                        запрещают). Остаток — три конкретных подрешения, не новое обсуждение successor.
Existing constraints:   пакет 3 п. 7 (принято); FINAL FREEZE V8 (один нормативный источник —
                        ayla-knowledge, machine-readable artifact), §5 (ownership); ayla-knowledge lifecycle
                        (status: idea | draft | review | approved | approved-with-amendments | … | cancelled;
                        переходы draft → review | cancelled); OWNER_QUESTIONS F0 — уже «Отвечено: пакет 3
                        п. 7»; reviewfix1 (immutable record OD-SAF-11…22 — сделано); W1-06 (successor path
                        обязан сохранить кризисный путь и утверждённый текст — CF-07); DRF-2003 Backlog.
```

**Option A — Old symptom draft → `cancelled / historical proposal, not policy` (не удалять); F0/C3 → reviewed canonical source → `ayla-knowledge/06 Safety and Governance/` после §15.2 с frontmatter + machine-readable artifact; баннеры в трёх документах цепочки 08.09; execution plan → ссылка на baseline.**
- Pros: единственный путь, совместимый с V8 и §15.2 п. 1; адресат R-3 сохранён (черновик остаётся как исторический документ, на который R-3 ссылается); термин из lifecycle-схемы (`cancelled`), не выдуманный; баннеры — дешёвая защита от регресса к порогам.
- Cons: требует правок в четырёх документах вне `docs/safety/` (владелец — главное окно / DRF-2003); момент переезда в ayla-knowledge зависит от gates §15.2 (Wave 1 + W2), т. е. «после review» — не сейчас.
- Failure mode: баннер поставлен, но execution plan продолжает цитировать черновик как вход → два safety-входа в automapping.

**Option B — Old draft сохраняется как companion clinical-rule input.**
- Pros: сохраняет «сырьё» для rule families (symptom rows как кандидаты). Cons: две active policy рядом (против §15.2 п. 1, V8); пороги черновика прямо запрещены M3 / M9 / §15.3; прямо противоречит пакету 3 п. 7 («не альтернативная policy»). Failure mode: Clinical читает пороги как предложение — R-3 «superseded» теряет силу.

**Option C — Объединение документов.**
- Pros: один файл. Cons: смешивает symptom-rows с классами (против OD-SAF-01), теряет адресат R-3, переписывает baseline (owner-блоки / immutable record — хеши рвутся), противоречит пакету 3 п. 7. Failure mode: «объединённый» документ = новый, третий, successor.

```text
Recommended option:     A (единственная опция, совместимая с пакетом 3 п. 7). Остаточные подрешения:
                        (1) статус черновика — `cancelled` по lifecycle-схеме ayla-knowledge с пометкой
                            «historical proposal, not policy; superseded by docs/safety/F0-C3-safety-matrix.md»
                            (слово «superseded» — в описании, статус — cancelled);
                        (2) баннеры в трёх документах цепочки 08.09 и в execution plan — ставит команда
                            (DRF-2003) без чтения владельцем каждого;
                        (3) целевой узел — ayla-knowledge/06 Safety and Governance/ (frontmatter + artifact);
                            момент — после §15.2 (Wave 1 + W2 + S1 detector validation report);
                        (4) immutable record OD-SAF-11…22 — регистрируется как § в [OD-BOT] / DRF-1349
                            главным окном (reviewfix1 — рекомендация).
Why:                    Core уже решён; остаток — оформление решения в документах, которое иначе останется
                        красным сторожем DRF-2003.
What becomes unblocked: §15.2 п. 1, п. 4; F0-строка (уже отвечена); DRF-2003 CF-06 часть; R-3 lifecycle-термин.
What remains open:      формат machine-readable artifact (Phase 2); canonicalization текста после Wave 1 / W2;
                        кто владелец узла 06 в ayla-knowledge (§5 ownership — ayla-knowledge).
Exact owner decision requested:
                        «Остаток successor path: (1) статус старого черновика — `cancelled` по схеме
                         ayla-knowledge с описанием «historical proposal, superseded by F0/C3» — да / иначе;
                         (2) разрешаете ли команде проставить баннеры в трёх документах цепочки 08.09 и в
                         execution plan без вашего чтения каждого — да / нет; (3) целевой узел
                         ayla-knowledge/06 Safety and Governance/ после §15.2 — да / иной; (4) регистрация
                         immutable record OD-SAF-11…22 как § в реестре — да / нет.»
```

---

## W1-06 — Psychological crisis placement

> **RESOLVED — historical options retained for traceability.** Решение владельца 17.09.2026: **W1-06 = C** — [OD-BOT §154]. Текст ниже — история вариантов и рекомендация на момент пакета, не открытый вопрос.

```text
Decision ID:            W1-06
Source findings:        CF-07 (LEGAL-F01 BLOCKER; CLINICAL-F16 п. 1; SAFETY-F15(f)); R-9 п. 3; [OD-BOT §127];
                        пакет 3 п. 8 (S1 → 103 / 112)
Problem:                Рабочие группы S1 — только соматические; §2 baseline не объявляет психологический
                        кризис (suicide / self-harm / acute abuse) ни в периметре, ни вне; §11 упоминает
                        «кризисную линию» как существующий путь. При этом pre_check HANDOFF → CRISIS_REPLY_TEXT
                        (8-800-2000-122 + «112 — если жизни угрожает опасность») — единственный живой
                        user-facing safety-ответ с founder sign-off (по review — PR #1084). Владелец 15.09
                        развёл S1-медицину (103 / 112) и психологическую линию по КАНАЛУ (пакет 3 п. 8;
                        DRF-2000), но место кризиса в таксономии не назвал. При canonicalization с одним
                        successor path кризисный путь станет либо вторым safety authority вне канона
                        (§8 п. 22), либо кандидатом на удаление при миграции (FINAL FREEZE §7: «old HANDOFF →
                        remove from state axis; migrate to escalation» — некуда мигрировать сигнал).
Why this must be decided now:
                        Gate: CANON, PILOT. Duty of care; единственный юридически «подписанный» текст;
                        W1-05 (successor path обязан сохранить кризисный путь) без адреса не исполним;
                        S1 detector validation gate (reviewfix1) должен знать, входят ли crisis-фикстуры.
Existing constraints:   OD-SAF-11 п. 8 (семь групп — не расширять; symptom-based, без названия болезни);
                        OD-SAF-01 / OD-SAF-22 (S-класс не придумывать без отдельного ruling);
                        FINAL FREEZE V7 (escalation — отдельный результат; тип по причине), M4 (класс 1 —
                        Universal Safety Signals; не только медицинские по тексту), §7 (HANDOFF не state),
                        §8 п. 22 (нет второго authority), V5 (critical STOP — controlled templates);
                        [OD-BOT §127] (BLOCK и HANDOFF не схлопываются — различимость crisis handoff /
                        policy refusal); [OD-BOT §128] (утверждённый текст вместо непроверенного ответа);
                        пакет 3 п. 8 (медицинская экстренность → 103 / 112, не психологическая линия);
                        reviewfix1 §8.1 (один evaluator; разные policy-секции одного artifact допустимы).
```

**Option A — Отдельная subgroup внутри S1 (default STOP, handoff = REQUIRED, существующий утверждённый текст).**
- Taxonomy impact: S1 из «соматических red flags» становится «любая экстренность»; восьмая группа = изменение OD-SAF-11 п. 8 (текст владельца) — против «не менять S1–S10» и immutable record.
- State semantics: STOP по S1 для wellness capabilities — совпадает по механике; escalation тип — психологическая линия, а не 103 / 112 → одна S-строка с двумя разными каналами по подгруппе.
- Handoff: HANDOFF REQUIRED (как сейчас).
- Current founder-approved text: сохраняется как template подгруппы.
- Danger of mixing: **высокая** — R-9 п. 3 показывает ровно этот сбой (кардиальные фразы → психологическая линия); детектор одной строки с двумя каналами повторит его в обе стороны; symptom-based wording S1 (без названия болезни) для кризиса неприменим (нужен эмпатический текст, не «следующий шаг к помощи»).
- Canonicalization impact: реоткрывает OD-SAF-11; S1 detector validation gate расширяется на crisis-фикстуры (Clinical fidelity к «семи группам» перестаёт быть определением).
- Pros: минимум новых сущностей. Cons: см. выше. Failure mode: смешение каналов; изменение owner-текста S1.

**Option B — Отдельный universal class вне S1–S10 (например `U-CRISIS`), в этой матрице.**
- Taxonomy impact: расширение таксономии на один universal класс — «единственное допустимое расширение» (reconciliation CF-07), отдельный ruling OD-SAF-23; S1–S10 не тронуты.
- State semantics: STOP для safety-sensitive wellness capabilities + escalation `CRISIS_LINE` (тип по причине, V7); precedence с S1: оба protective, не конкурируют (если оба PRESENT — оба escalation результата; текст — controlled template с обоими контактами? — Legal / Owner).
- Handoff: REQUIRED; получатель — кризисная линия, не оператор салона.
- Current founder-approved text: становится controlled template класса (V5), [OD-BOT §128].
- Danger of mixing: низкая — отдельные детекторы, отдельный канал, отдельный wording-контракт; но матрица «медицинская» получает не-медицинский класс.
- Canonicalization impact: ещё один owner ruling до canonicalization; Legal review класса (duty of care, текст) — отдельно; матрица растёт.
- Pros: единый реестр / evaluator / audit (rule_id для кризиса). Cons: F0/C3 перестаёт быть «beauty / wellness health safety» документом; Legal-периметр (abuse — не health). Failure mode: класс без владельца текста → wording дрейфует.

**Option C — Отдельная Crisis Safety Policy (свой owner-текст, свой Legal review), на которую F0/C3 ссылается как на stronger / parallel universal safety source; тот же artifact-реестр и тот же evaluator.**
- Taxonomy impact: S1–S10 и матрица не меняются; §2 baseline объявляет: «психологический кризис — вне медицинского периметра этой матрицы, покрыт Crisis Safety Policy (ссылка)»; в artifact ayla-knowledge — отдельная секция `CRISIS-UNIVERSAL` с rule_id / version / provenance (founder sign-off).
- State semantics: правило кризиса возвращает capability decisions (wellness BLOCKED на ход) + escalation `CRISIS_LINE`; state_contribution — STOP (universal); precedence S1 ↔ CRISIS — оба protective, задаётся в policy (не в матрице).
- Handoff: REQUIRED; получатель — кризисная линия.
- Current founder-approved text: становится owner-текстом Crisis Safety Policy как есть (immutable, как OD-SAF record).
- Danger of mixing: низкая — разные policy-документы, разные детекторы, разные каналы; при этом **один** evaluator и один artifact (reviewfix1 §8.1, V8) — не второй authority.
- Canonicalization impact: F0/C3 canonicalization не ждёт Legal review кризиса (параллельно); пилот: кризисный путь остаётся как есть (founder-approved), миграция — при переносе pre_check по FINAL FREEZE §7.
- Pros: не трогает S1–S10; сохраняет живой текст без переписывания; Legal-периметр (abuse, не health) естественно отделён. Cons: два policy-документа с precedence-правилом между ними (нужен один абзац в обоих); риск «параллельный источник» = второй authority, если реестр / evaluator не общие. Failure mode: Crisis policy реализуется отдельным кодом вне engine → §8 п. 22.

```text
Recommended option:     C — отдельная Crisis Safety Policy (owner-текст = существующий founder-approved
                        CRISIS_REPLY_TEXT как immutable запись; Legal review отдельно), включённая в тот же
                        artifact ayla-knowledge как universal секция и исполняемая тем же Safety Engine;
                        F0/C3 §2 объявляет кризис вне медицинского периметра матрицы со ссылкой; precedence
                        S1 ↔ CRISIS — «оба protective, оба escalation результата, текст по controlled
                        template» — фиксируется в Crisis policy. В S1 автоматически НЕ переносить.
Why:                    A реоткрывает OD-SAF-11 и воспроизводит смешение каналов, которое владелец 15.09
                        только что развёл (п. 8); B тянет не-медицинский класс в медицинскую матрицу и
                        задерживает её canonicalization Legal-review'ем кризиса; C сохраняет обе вещи,
                        которые нельзя потерять, — живой утверждённый текст и один evaluator.
What becomes unblocked: §2 / §11 baseline (место кризиса — cross-ref); W1-05 (successor path сохраняет
                        кризис по адресу); FINAL FREEZE §7 миграция HANDOFF → escalation (есть куда);
                        S1 detector validation gate — область = семь групп, crisis-фикстуры — в Crisis
                        policy; DRF-2000 (S-2) — граница между 103/112 и кризисной линией.
What remains open:      текст и Legal review Crisis Safety Policy (duty of care; abuse — периметр);
                        детекторы кризиса (fidelity к тексту владельца — Legal + Owner, не Clinical);
                        precedence при одновременном S1 + crisis; параллельная эскалация к оператору
                        (OD-F0C3-10).
Exact owner decision requested:
                        «Психологический кризис (suicide / self-harm / acute abuse): (A) восьмая подгруппа
                         внутри S1 (меняет OD-SAF-11 п. 8); (B) отдельный universal класс в этой матрице
                         (новый ruling OD-SAF-23); (C) отдельная Crisis Safety Policy с вашим текстом,
                         тем же реестром и evaluator, на которую F0/C3 ссылается как на параллельный
                         universal source. Подтверждаете ли, что существующий CRISIS_REPLY_TEXT — ваш
                         утверждённый owner-текст и переносится без изменений (immutable запись)?»
```

---

## W1-07 — Consent gate before safety evidence

> **RESOLVED — historical options retained for traceability.** Решение владельца 17.09.2026: **W1-07 = A for Controlled Pilot** — [OD-BOT §155]. Текст ниже — история вариантов и рекомендация на момент пакета, не открытый вопрос.

```text
Decision ID:            W1-07
Source findings:        CF-08 / LEGAL-F02 (BLOCKER); PRIVACY-F05; PRIVACY-F11 (карта ≥ 8 контуров);
                        reviewfix1 §13.2 (носители); пакет 3 п. 13, 16
Problem:                Baseline выносит согласие за периметр («Privacy-контур, не safety-state»), но сам
                        конвейер — источник сбора (CLARIFY: зона, срок, лактация, препарат, timing) и
                        хранения (evidence history, обе версии при CONFLICTED, ledger, V4-persistence) данных
                        спецкатегории. Не описано: что делает Safety Engine без согласия HEALTH / при отзыве;
                        какая это ветка S10 (не A, не C, не D — скрытая пятая причина незавершённой оценки);
                        отличается ли S1-защита от S2–S9 elaborative сбора. Согласие HEALTH в коде — «экран
                        есть, текст не утверждён» (DRF-1729 покрывает профиль, не диалоговое evidence);
                        Consent Scope Registry §4: health_related_signal — persistent storage «вне MVP;
                        запрещено».
Why this must be decided now:
                        Gate: PILOT, IMPL. Без ответа реализация расширяет объём обработки спецкатегории на
                        не утверждённом основании; для Legal — скрытая пятая причина, которую consumer
                        прочитает как A (спросить) или C (нет policy). W1-01 (NORMAL-by-absence) и W1-08
                        (persistence) не решаются без этого.
Existing constraints:   152-ФЗ (спецкатегория — данные о здоровье; форма согласия / основания — Legal, здесь
                        не квалифицируется); Consent Scope Registry §2 п. 1, §4; DRF-1728 / 1729 / 1732 /
                        1733; FINAL FREEZE M8 (sensitive evidence), M7 (no auto-promote), §8 п. 16–18;
                        OD-SAF-11 (S1 — protective action first; M2); [OD-BOT §72] / C2 (каталог и direct
                        booking доступны без semantic Recommendation); пакет 2 B6; пакет 3 п. 16
                        (operational handoff recipient — не medical authority); RECOMMENDED §F0 («не
                        собирать лишнюю чувствительную медицинскую детализацию»).
Инвариант (форма, не policy):
                        consent gate != safety state
                        Отсутствие согласия — не S10-ветка, не UNKNOWN, не ERROR, не CLARIFY: это отдельная
                        предпосылка на уровне consumer (DecisionReadiness) с reason CONSENT_REQUIRED;
                        SafetyResult без согласия либо не вычисляется для S2–S9 (evaluation_status =
                        NOT_REQUIRED + consumer-gate), либо вычисляется в restricted mode (см. опции) —
                        но никогда не рендерится как «опасно» / «не могу оценить по медицине».
```

**Option A — Только S1 protective detection, без clarification и без durable evidence.**
- Semantics: до согласия engine исполняет только S1 universal rule (in-turn, transient, без записи evidence value); S2–S9 не детектируются; вопросы не задаются; semantic Recommendation для safety-sensitive capabilities закрыта с reason `CONSENT_REQUIRED` (consumer), каталог / direct booking открыты ([OD-BOT §72], C2).
- Pros: минимальная обработка спецкатегории до согласия (Legal — подтвердить основание для S1: защита жизни / здоровья); нет hidden fifth reason — consumer видит `CONSENT_REQUIRED`; согласуется с W1-01 условием (2): нет NORMAL-by-absence до согласия — потому что «сигнала нет» ≠ «сигнала не было»; пилот: RHC = true и так к человеку.
- Cons: до согласия Ayla не рекомендует **ничего** safety-sensitive даже при отсутствии health-темы в диалоге — пользователь без health-контекста получает consent request «ни за что» (UX; Legal: согласие «на всякий случай» — не минимизация); S2–S9 evidence, сказанное до согласия, не сохраняется — после согласия человек повторяет.
- Failure mode: пользователь отказывает в согласии → навсегда только каталог; либо продукт «прячет» consent request в onboarding — тогда это не gate, а формальность.

**Option B — Detection всех классов, но без clarification / persistence; затронутые capabilities fail-closed с сообщением о необходимости согласия.**
- Semantics: engine детектирует S1–S9 in-turn (transient); S1 → protective как в A; S2–S9 PRESENT → затронутые capabilities `REQUIRES_RESOLUTION` с reason `CONSENT_REQUIRED` (не MISSING_USER_FACT) и consent request как «вопрос»; без сигналов → semantic Recommendation по W1-01 (basis NO_SIGNAL_DETECTED) — **только если** Legal подтвердит, что transient детекция без хранения допустима до согласия.
- Pros: consent request появляется **по поводу** (когда health-тема реально возникла) — минимизация по смыслу; поверхность жива для пользователей без health-контекста; reason code специфичен.
- Cons: детекция S2–S9 = обработка спецкатегории до согласия (Legal должен квалифицировать transient in-turn processing); риск: «детектировали и не сохранили» недоказуемо без T-PRIV-сторожей и без ReplayTrace-policy (T-PRIV-07); сложнее пилота.
- Failure mode: transient evidence всё же попадает в носители (ledger, ReplayTrace, логи — §13.2) → обработка без основания; ловится T-PRIV-01…07 только если они существуют.

**Option C — Никакой health processing до согласия, кроме legally-required universal emergency handling if applicable.**
- Semantics: engine не исполняется для S1–S10 до согласия; «emergency handling» — только если Legal определит его как обязанность (не safety policy); semantic Recommendation закрыта (`CONSENT_REQUIRED`); каталог / direct booking открыты.
- Pros: максимально консервативно по данным. Cons: S1 red flag до согласия остаётся без protective action, если «legally-required» не определён → duty of care против минимизации; «if applicable» — неопределённость, которую реализует разработчик (§8 п. 21 по аналогии); пилот теряет единственный safety-ответ до consent-экрана.
- Failure mode: «не могу вдохнуть» до согласия → обычный поток → задержка помощи.

**Что видит пользователь без согласия (для всех опций — форма, wording OD-F0C3-08):**
- consent request — по A: при первом входе в safety-sensitive flow; по B: при первом health-сигнале; по C: при первом входе;
- каталог и direct booking — открыты всегда ([OD-BOT §72], C2), для RHC = true — по [OD-BOT §98] к человеку (пакет 3 п. 16: operational, не medical);
- blocked recommendation — «персональную рекомендацию дам после согласия на обработку данных о здоровье», не «опасно», не «не могу оценить по медицине»;
- safe explanation — нейтральное объяснение границ (reviewfix1 §7: не DIAGNOSE);
- S1 protective text — по A / B всегда; по C — только если «legally-required» определён.

```text
Recommended option:     A как floor для пилота (S1 protective detection всегда; S2–S9 не детектируются;
                        semantic Recommendation safety-sensitive capabilities закрыта с CONSENT_REQUIRED на
                        consumer-уровне; каталог / direct booking открыты) — с переходом к B для полного
                        продукта, ЕСЛИ Legal подтвердит допустимость transient in-turn детекции без хранения
                        и T-PRIV-01…07 + ReplayTrace policy gate зелёные. C — не рекомендуется: оставляет
                        S1 без защиты на неопределённом «if applicable».
                        Обязательное для любой опции: consent gate != safety state — отдельный consumer
                        reason CONSENT_REQUIRED; отзыв согласия → §13.2 носители по W1-08 (deletion
                        linkage), state не «сбрасывается» — evaluation становится NOT_REQUIRED / consumer-gate.
Why:                    A — единственная опция, у которой основание обработки до согласия сводится к одной
                        функции (защита жизни, S1), которую Legal может квалифицировать отдельно; B зависит
                        от юридической квалификации transient processing и от сторожей, которых ещё нет;
                        C жертвует S1.
What becomes unblocked: §2 baseline (consent — форма гейта); §6.10 (нет скрытой пятой причины);
                        W1-01 условие (2); W1-08 (что вообще может персистироваться); DRF-1729 / 1732 текст
                        согласия — Legal; consumer reason CONSENT_REQUIRED (W1-04 reason set).
What remains open:      Legal: основание S1-детекции до согласия (152-ФЗ; QUESTIONS_FOR_LAWYER); текст
                        согласия HEALTH для диалогового evidence (DRF-1729 / 1732); поведение при отзыве
                        (Q-CLIENT-03 — W1-08); transient processing (для перехода A → B); wording
                        (OD-F0C3-08).
Exact owner decision requested:
                        «До согласия HEALTH Safety Engine: (A) только S1 protective detection, без вопросов и
                         без сохранения evidence; semantic Recommendation safety-sensitive capabilities
                         закрыта с reason CONSENT_REQUIRED; (B) детекция всех классов без вопросов и без
                         хранения, затронутые capabilities fail-closed с сообщением о согласии (при
                         подтверждении Legal); (C) ничего, кроме legally-required emergency handling.
                         Что видит пользователь без согласия: consent request + каталог / direct booking +
                         нейтральное «персональную рекомендацию — после согласия» — да / иначе?
                         Подтверждаете ли инвариант consent gate != safety state (отдельный consumer
                         reason, не S10-ветка)?»
```

---

## W1-08 — Safety evidence persistence / retention

```text
Decision ID:            W1-08
Source findings:        CF-08 (PRIVACY-F01 BLOCKER; PRIVACY-F05; PRIVACY-F11); CF-14 (SAFETY-F13);
                        REVIEW-CONFLICT-01; CF-20 (PRIVACY-F02 — promoted memory); reviewfix1 §8.7 (scope),
                        §13.1 (T-PRIV), §13.2 (носители); RECOMMENDED D7; пакет 3 п. 13
Problem:                Как хранить SafetyEvidence (значения), conflict history (обе версии), question ledger,
                        safety requirement resolutions, rule evaluation refs. Ни для одного не названы
                        носитель, custodian, retention, authorization basis / scope_id, deletion linkage.
                        REVIEW-CONFLICT-01: Safety (V4 — evidence должно переживать ConversationState TTL 2 ч
                        для reevaluation; S7 / S8(A) по природе PERSISTENT_REPORTED_FACT) против Privacy
                        (CSR §4 запрещает persistent storage health_related_signal в MVP; refs без значений —
                        минимизирующий вариант). Refs без значений не позволяют reevaluation по rule
                        (правило читает значение). Ledger и DecisionEvidence уже персистируются в PostgreSQL.
Why this must be decided now:
                        Gate: CANON, IMPL. Без класса данных запреты M7 / M8 непроверяемы; conflict history +
                        supersedes + ledger = профиль здоровья по факту даже без имени risk_user; реализация
                        расширяет объём обработки спецкатегории на не утверждённом основании.
Existing constraints:   FINAL FREEZE V4 (SafetyState не сбрасывается репликой / TTL; ConversationState TTL ≠
                        safety evidence validity), M12 (scopes CURRENT_STATE | EVENT_SPECIFIC |
                        PERSISTENT_REPORTED_FACT | TRANSACTION_SPECIFIC; supersedes без уничтожения истории),
                        M7 (no auto-promote), M8 (не persistent risk-флаги; не мастеру), §8 п. 16–18;
                        свод 11.09 §3 инвариант 8 (state, rule_id, evidence_ref, activated_at,
                        policy_version, resolution / expiry в safety-контексте); OD-SAF-17…22 (не в durable
                        memory без отдельного основания; нет persistent risk flag; обе версии при
                        CONFLICTED сохраняются); CSR §4 (health_related_signal persistent storage — вне MVP,
                        запрещено); DIM v1.0; пакет 2 B13 (immutable record с отдельной retention —
                        прецедент); RECOMMENDED D7 (состоявшиеся записи обезличиваются, 5 лет); Q-CLIENT-03
                        (удаление аккаунта, 30 дней); канон v1.1 §11 Memory Promotion Policy (REJECT |
                        SESSION_ONLY | REQUIRE_CONFIRMATION | PROMOTE; never auto-promote safety
                        observations); DRE §22 Q1 (promoted memory как safety slot — CF-20, O-19);
                        пакет 3 п. 13 (ReplayTrace не включать до проверки Privacy boundary); W1-07.
```

| Критерий | **A — session-only** (значения живут в ConversationState 2 ч; после — ничего, кроме refs / rule_id без значений) | **B — durable audit, raw values удаляются после сессии** (ConflictRecord, ledger, RuleResult refs, evaluation_id, policy_version — durable; `value` → tombstone) | **C — durable sensitive evidence с explicit consent + retention** (значения хранятся под отдельным HEALTH scope в CSR, срок N, deletion linkage) | **D — hybrid по scope** (CURRENT_STATE = session-only; PERSISTENT_REPORTED_FACT = consent-governed durable (C); EVENT / TRANSACTION = domain-authoritative retention backend (D7)) |
|---|---|---|---|---|
| privacy | максимальная минимизация; совпадает с CSR §4 MVP | значения не хранятся; но `rule_id` «condition × procedure» в user-linked audit = health data (T-PRIV-04) — нужна классификация | наибольший объём спецкатегории; требует утверждённого текста согласия и scope | минимизация по природе факта; CURRENT_STATE (боль, температура) не переживает сессию; хроническое — только с согласием |
| reassessment (V4) | невозможна после 2 ч — следующая сессия молча начинается «с нуля» (Safety-позиция REVIEW-CONFLICT-01); S1 STOP снимается TTL де-факто (против V4; DRF-2040) | reassessment по refs невозможна (rule читает значение); но факт «был STOP, не снят» переживает сессию → fail-closed до recheck (DRF-2040 совместимо) | полная | полная для PERSISTENT / EVENT; для CURRENT_STATE — переспрос (по природе факта уместен) |
| continuity | пользователь повторяет всё каждую сессию | повторяет значения, но не «историю решений» | не повторяет | повторяет только текущее состояние |
| deletion (Q-CLIENT-03, отзыв) | тривиально (TTL) | tombstone refs; D7-подобно | 30 дней / обезличивание — нужно определить | по scope: session — TTL; PERSISTENT — 30 дней после отзыва; EVENT / TRANSACTION — D7 (обезличивание, 5 лет) |
| conflict resolution (S10 B, OD-F0C3-50) | конфликт живёт 2 ч; «обе версии сохраняются» — только в сессии | ConflictRecord durable без значений — статус OPEN / RESOLVED и resolved_by аудируемы, содержание нет | полная | полная для PERSISTENT / EVENT; CURRENT_STATE-конфликты — в сессии |
| audit (свод §3 инв. 8; B13) | нарушает «activated_at / resolution / policy_version» за пределами сессии | выполняет (refs + версии) — прецедент B13 | выполняет | выполняет |
| pilot feasibility | **есть сегодня** (Redis 2 ч) | нужен durable ConflictRecord / evaluation audit — небольшой объём | нужен текст согласия HEALTH (не утверждён), CSR scope, storage, deletion — не до пилота | зависит от C для PERSISTENT; для пилота = A + B |
| failure mode | S7 «онкология в лечении» сказано вчера → сегодня NORMAL (silent) | `rule_id` в audit читается аналитикой как health-профиль | хранение без утверждённого основания = обработка спецкатегории «на будущее» | scope назначен неверно (CURRENT_STATE как PERSISTENT) → хранится лишнее; ловится классификацией per class (O-14) |

```text
Recommended option:     D как целевая модель, с B как обязательным audit-слоем и A как режимом пилота:
                          пилот  = A (значения — сессия) + B (durable: evaluation_id, rule_id, policy_version,
                                   ConflictRecord status / resolved_by, ledger asked / resolved, RuleResult refs;
                                   value → tombstone после сессии; rule_id в user-linked audit — health data,
                                   доступ как к evidence refs; T-PRIV-04);
                          продукт = D: CURRENT_STATE — сессия; PERSISTENT_REPORTED_FACT (S7 condition,
                                   S8(A) therapy, S5 pregnancy до resolution по rule) — durable только под
                                   отдельным зарегистрированным HEALTH-scope в CSR (не memory promotion, не
                                   profile flag; не «wellness_observation» свода §13 — это другой контур),
                                   удаление — 30 дней после отзыва / удаления аккаунта, tombstone refs;
                                   EVENT_SPECIFIC / TRANSACTION_SPECIFIC (S6 через booking, S9 через
                                   Ayla-запись) — backend authoritative retention (D7: обезличивание, 5 лет),
                                   safety хранит только refs.
                        Promoted memory (CF-20 / DRE §22 Q1) — рекомендация C того вопроса: только для
                        PERSISTENT_REPORTED_FACT и только как предзаполнение вопроса с подтверждением в
                        сессии — но это O-19, не Wave 1.
                        ReplayTrace — вне этой модели: отдельная policy / retention gate (T-PRIV-07; пакет 3 п. 13).
Why:                    A одна нарушает V4 и делает S7 / S8(A) невидимыми для следующей сессии; C одна —
                        обработка спецкатегории до утверждённого согласия; B без значений не даёт
                        reevaluation, но даёт единственное, что нужно пилоту помимо сессии — «STOP не снят»
                        (DRF-2040) и аудит; D разводит REVIEW-CONFLICT-01 по природе факта, а не по
                        компромиссу «немного хранить».
What becomes unblocked: §5.2 / §8.7 (scope per class — O-14 получает рамку); §13.2 retention-колонка;
                        T-S1-07, T-S3-09, T-S7-*, T-S8-* stale / reassessment; OD-F0C3-09 / 13 (resolution
                        sources — вход); CSR scope registration (Privacy); DRF-2040; REVIEW-CONFLICT-01 закрыт.
What remains open:      scope-классификация per class S2–S9 (O-14 — Safety + Clinical); текст согласия HEALTH
                        и срок N (Legal / Privacy); ReplayTrace policy (A7); promoted memory (O-19);
                        deletion при отзыве — Privacy по Q-CLIENT-03; состав audit-полей (Safety).
Exact owner decision requested:
                        «Хранение safety evidence: (A) только сессия (2 ч), далее refs / rule_id без значений;
                         (B) durable audit без значений (ConflictRecord, ledger, refs, версии) — значения
                         tombstone после сессии; (C) durable sensitive evidence с отдельным согласием HEALTH и
                         retention N; (D) hybrid по scope: CURRENT_STATE — сессия; PERSISTENT_REPORTED_FACT —
                         consent-governed durable; EVENT / TRANSACTION — retention backend (D7).
                         Для пилота — (A)+(B) до утверждения согласия HEALTH — да / нет?
                         При удалении аккаунта / отзыве: PERSISTENT — 30 дней, EVENT / TRANSACTION — D7
                         (обезличивание, 5 лет), audit — tombstone refs — да / иначе?»
```

---

## Dependency graph (проверено по содержанию, не скопировано)

```text
                    W1-05 successor path (остаток; core решён 15.09 п. 7)
                    │  где живёт artifact и его lifecycle
                    ├────────────────────────────────┐
                    ▼                                ▼
   W1-03 intent-capability                 W1-06 crisis placement
   (I-реестр в том же artifact;             (constraint для W1-05: путь и текст
    вклад в aggregate = 0?)                  сохраняются, где бы ни жил кризис;
                    │                        область S1 detector gate)
                    ▼
   W1-04 RuleResult / RESTRICTED  ◄──────── W1-01 no-signal (поле basis в RuleResult /
   (карта capability → decision;             SafetyResult; NORMAL-by-absence)
    I-правила без state_contribution)                ▲
                    │                                │  «сигнала нет» ≠ «сигнала не было»
                    ▼                                │  до согласия
   W1-02 aggregate contract          W1-07 consent gate ──────────────► W1-08 persistence
   (агрегат над картой W1-04;        (что вообще детектируется /        (что вообще может
    интент вне множества — W1-03;     собирается до согласия)            персистироваться;
    при отсутствии results — W1-01)                                      refs из W1-04 — слабая)
                    │
                    ▼
   DRE adapter (state-driven → decision-driven), T-S8-13/17, T-S10-15, golden §10

   W1-01 ──► pilot safety behaviour (после S1 detector validation PASS — пакет 3 п. 5 / 9)
   W1-06 ──► universal safety scope (S1 gate = семь групп; crisis — своя policy / класс)
```

Отличия от ожидаемой схемы брифа: (1) **W1-01 не изолирован** — он вход для W1-04 (поле `basis`) и W1-02 (агрегат при отсутствии rule results) и ограничен W1-07 (до согласия NORMAL-by-absence недопустим); (2) **W1-06 связан с W1-05 ограничением**, а не последовательностью: W1-05 можно решать первым при условии «кризисный путь сохраняется по адресу W1-06»; (3) **W1-08 ← W1-04** — слабая связь (что такое «rule evaluation ref»); (4) **W1-05 → W1-03** — не семантическая, а «где живёт реестр» — при решении C по W1-06 и B по W1-03 в одном artifact оказываются три секции (S, I, CRISIS) — это и есть причина решать W1-05 первым.

## Recommended owner order (минимум переоткрытий)

| # | Решение | Почему в этой позиции | Кто в комнате |
|---|---|---|---|
| 1 | **W1-05** (остаток) | core уже принят; фиксирует artifact и lifecycle, к которым привязаны все остальные; ничто последующее его не переоткроет | Owner |
| 2 | **W1-06** | независим по семантике; если решать позже и выбрать A — переоткроются OD-SAF-11, S1 detector gate и W1-05; решённый рано — снимает ограничение с W1-05 и определяет область S1 gate | Owner + Legal |
| 3 | **W1-07** | Legal-bound; задаёт, что вообще существует до согласия; без него W1-01 и W1-08 будут пересмотрены | Owner + Legal + Privacy |
| 4 | **W1-01** | pilot-critical (>90 % ходов); зависит от W1-07 и от S1 gate (уже gate); даёт поле `basis` в W1-04 | Owner + Safety |
| 5 | **W1-03** | определяет, есть ли I-правила в RuleResult и участвует ли intent в агрегате — до формы контракта | Owner + Safety / Arch |
| 6 | **W1-04** | форма контракта после того, как известны все виды правил (S, I) и basis NORMAL | Owner + Safety / Arch |
| 7 | **W1-02** | агрегация над картой W1-04; область CLARIFY; адаптер DRE — последнее из contract-решений | Owner + Safety / Arch |
| 8 | **W1-08** | после W1-07 (что собирается) и W1-04 (что такое ref); Privacy-сессия отдельно | Owner + Privacy + Legal |

Три сессии: **I** (Owner + Legal): W1-05, W1-06, W1-07. **II** (Owner + Safety / Arch): W1-01, W1-03, W1-04, W1-02 — одним пакетом, потому что они взаимно связаны и ответы должны быть согласованы (рекомендованные C / B / B+C / A2+B2 — согласованы между собой). **III** (Owner + Privacy): W1-08.

## Как отвечать

Буква (и подпункты) по каждому W1; «иначе» — свободным текстом. Ответы переносятся: (1) в `[OD-BOT]` / DRF-1349 главным окном как § с датой; (2) в baseline отдельным delta `v0.12-reviewfix2` (owner-dependent), где owner-блоки §6.1–6.10 и OD-SAF-11…22 по-прежнему не меняются — меняются §4, §7, §8, §9, §11, §13, §14; (3) в Wave 2 — O-09…O-25 по reconciliation report §17 (те, что не закрыты пакетом 3 и §F0).

**Не решено этим документом:** ничего. **Изменено этим документом в baseline:** ничего.
