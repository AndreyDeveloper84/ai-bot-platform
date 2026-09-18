# Owner rulings OD-SAF-11…OD-SAF-22 — immutable record (F0 / C3 Safety Matrix)

**Статус:** `IMMUTABLE RECORD` — текст зафиксирован; правки в этот файл не вносятся (новое слово владельца = новая запись с новым ID / версией, не правка этой). Record v1.0.
**Дата слова владельца:** 12.09.2026 (все двенадцать rulings). **Дата фиксации record:** 17.09.2026 (reviewfix1; CF-28 reconciliation report, owner decision не требуется).
**Источник текста:** слово владельца 12.09, записанное инженерным агентом в `docs/safety/F0-C3-safety-matrix.md` v0.2…v0.11 и не менявшееся в v0.12 (§16 change history). Отдельного owner-документа с этими текстами **не существовало** (baseline §3.1) — единственная запись была внутри редактируемого working draft; этот файл выносит её наружу. Владелец подтверждает fidelity отдельно (см. «Что этот record не доказывает»).
**Baseline, из которого взят текст:** `docs/safety/F0-C3-safety-matrix.md` v0.12 (2026-09-12), sha256 `12451baf4931e3e72c8dff1dd567b2433c4cfb85323cbcb7a7b29a1678516e49` — тот же байтовый baseline, который читали пять внешних reviewers (reconciliation report §1).
**Реестр:** регистрация как § в живом реестре §-решений `ai-bot-platform/docs/OPEN_DECISIONS.md` (`origin/dev`) / DRF-1349 — **рекомендация главному окну**; этим файлом не выполнена (реестр — другой git-репозиторий; `Ayla/docs/OPEN_DECISIONS.md` — устаревшая копия до §100 с чужой незакоммиченной правкой). До регистрации record живёт здесь; baseline ссылается на него по пути и sha256 (§3.1).
**Приоритет:** слово владельца — выше baseline; baseline §5.1 / §6 — рабочая копия этих текстов. Расхождение рабочей копии с record = дефект копии, не новое ruling.

## Что этот record фиксирует

Для каждого OD-SAF-11…22: ID; дата; источник; **exact text** — строка §5.1 baseline v0.12 дословно (owner ruling в сводной формулировке); для классовых rulings — **full-text holder**: owner-блок соответствующего §6.x v0.12 (от заголовка `### 6.x` до строки перед `#### Open questions`: Purpose, Example triggers, Default state, Escalation conditions, Minimum clarification, Allowed / Restricted / Blocked capabilities, User-facing behaviour — включая нумерацию «п. N», на которую ссылаются тесты), его строки и sha256; полный текст блоков — в Приложении A дословно. Подразделы `Open questions`, `Required review`, `Test cases` в owner-блок **не входят** — это рабочие части документа, не слово владельца.

## Что этот record не доказывает

Record доказывает **неизменность** текста относительно v0.12, не его **соответствие слову владельца 12.09** (запись делалась агентом; владелец читал v0.2 и принял структуру — baseline §16). Fidelity к слову владельца подтверждает владелец: до его подтверждения Clinical fidelity = «внутренне согласовано с этим record», не «соответствует слову владельца» (CF-28). Record не содержит решений Wave 1 и не меняет ни одного ruling.

## Реестр rulings

| ID | Дата | Источник | Внесён в baseline | Exact text (строка §5.1 v0.12) | Full-text holder (owner-блок §6.x v0.12) | sha256 owner-блока |
|---|---|---|---|---|---|---|
| OD-SAF-11 | 2026-09-12 | слово владельца 12.09; записано инженерным агентом (v0.2) | v0.2, §5.1 строка 126 | `\| OD-SAF-11 \| S1 Acute / emergency red flags: `default_action = STOP`; явный red flag → `STOP` без обязательного `CLARIFY`; неоднозначный сигнал → допустим `CLARIFY`; перечень блокируемых и остающихся capabilities; symptom-based / escalation-based поведение, без названия болезни. Полный текст — S1 (§6.1) \|` | §6.1 S1 — Acute / emergency red flags — строки 185–254 v0.12; Приложение A | `5dc7e4e1d6491a01a9a1b70cd17c40efc1c43486373b5e2b10f1bf95f7fafb17` |
| OD-SAF-12 | 2026-09-12 | слово владельца 12.09; записано инженерным агентом (v0.3) | v0.3, §5.1 строка 127 | `\| OD-SAF-12 \| S2 Pain / neurological symptoms: `default_action = CLARIFY`; боль сама по себе не `STOP`; различать обычный musculoskeletal / wellness контекст и потенциально acute / neurologic; minimum sufficient clarification по dimensions onset / progression / neurological component / trauma-exertion (не анкета); признаки S1 → route S1 → `STOP`; после clarification `CAUTION` / `NORMAL` только по validated procedure-specific rule; без диагноза источника боли; услуга не представляется как лечение боли, коммерческие labels не создают `TREAT_PAIN`; при unresolved — `REQUIRES_RESOLUTION` для затронутых wellness-capabilities. Полный текст — S2 (§6.2) \|` | §6.2 S2 — Pain / neurological symptoms — строки 290–322 v0.12; Приложение A | `d8fa5ff31c90d517d2aa9c594616aed0f1c58e27416cf5902b5d64bda105884e` |
| OD-SAF-13 | 2026-09-12 | слово владельца 12.09; записано инженерным агентом (v0.3) | v0.3, §5.1 строка 128 | `\| OD-SAF-13 \| Каноническое runtime-имя первого состояния — `NORMAL`, не `continue`. Enum: `NORMAL`, `CAUTION`, `CLARIFY`, `STOP`. `continue` — только человекочитаемая семантика (*safety evaluation completed and affected flow may continue*); alias в контрактах/API/runtime не создавать. Закрывает R-1 / OD-F0C3-01 \|` | — (общее уточнение; полный текст = строка §5.1) | `—` |
| OD-SAF-14 | 2026-09-12 | слово владельца 12.09; записано инженерным агентом (v0.3) | v0.3, §5.1 строка 129 | `\| OD-SAF-14 \| Missing decision-changing user/context fact (можно спросить: срок беременности при rule, зависящем от срока; зона симптома при rule, зависящем от зоны) → `CLARIFY`. Missing validated safety policy knowledge (нет rule / validated knowledge / provenance / authoritative artifact) → `UNKNOWN / INCOMPLETE`, fail-closed для затронутой safety-sensitive capability, **без вопроса пользователю** (*user cannot resolve missing policy*). `UNKNOWN` не объединять с enum состояния. Закрывает R-2 / OD-F0C3-02. S5-поправка: `NO RULE != NOT_APPLICABLE` — только validated policy устанавливает `NOT_APPLICABLE` или отсутствие restriction; OD-SAF-07 действует \|` | — (общее уточнение; полный текст = строка §5.1) | `—` |
| OD-SAF-15 | 2026-09-12 | слово владельца 12.09; записано инженерным агентом (v0.4) | v0.4, §5.1 строка 130 | `\| OD-SAF-15 \| S3 Infection / inflammation / fever: `default_action = CLARIFY`; факт температуры / инфекции / воспаления — не автоматический emergency `STOP`; признаки S1 → S1 → `STOP`; решение procedure-aware, пользователь и каталог не блокируются автоматически; после достаточного evidence — по validated procedure-specific policy: `ALLOWED → NORMAL`, `RESTRICTED → CAUTION`, `BLOCKED → STOP` для затронутой capability, missing rule → `UNKNOWN / INCOMPLETE` fail-closed; `NO RULE != SAFE` — отсутствие policy не компенсируется медицинским рассуждением LLM; temporal context decision-relevant (текущее / прошедшее / недостаточно временной информации — смысл, не новый enum); minimum sufficient clarification — не спрашивать точную температуру, длительность и иные детали, которые validated rule не использует (*Do not collect precision that policy does not consume*); без диагноза инфекции/воспаления и их причины; процедура не представляется как лечение S3-состояния; unresolved → `REQUIRES_RESOLUTION` для четырёх wellness-capabilities; доступны `ASK_CLARIFYING_QUESTION`, `GENERAL_EDUCATION`, `EXPLAIN_NEXT_STEP`; локальный сигнал не влияет на нерелевантную процедуру автоматически, но `NOT_APPLICABLE` следует из validated policy (`NO RULE != NOT_APPLICABLE`). Полный текст — S3 (§6.3) \|` | §6.3 S3 — Infection / inflammation / fever — строки 347–381 v0.12; Приложение A | `0decc93da919632f05ed43bfca053aa8cae8aa918d51827cf8831c3571bc467b` |
| OD-SAF-16 | 2026-09-12 | слово владельца 12.09; записано инженерным агентом (v0.5) | v0.5, §5.1 строка 131 | `\| OD-SAF-16 \| S4 Skin integrity / skin condition: `default_action = CLARIFY`; факт кожного изменения / повреждения / жалобы — не автоматический `STOP`; zone-aware и procedure-aware; minimum sufficient clarification — где изменение и пересекается ли с зоной процедуры, иные факты только если validated rule их использует (не дерматологическая анкета); Ayla без отдельной medical capability не определяет диагноз, инфекционность, заразность, причину («это герпес / грибок / заразно / аллергия» запрещены); признаки S1 → S1 → `STOP`; после достаточного evidence — validated procedure-specific policy `ALLOWED → NORMAL`, `RESTRICTED → CAUTION`, `BLOCKED → STOP` для затронутой procedure/capability, missing rule → `UNKNOWN / INCOMPLETE`; исход не определяется рассуждением LLM; сигнал вне зоны не считается нерелевантным автоматически — `NOT_APPLICABLE` только из validated rule (`NO RULE != NOT_APPLICABLE`); пользователь / каталог не блокируются; unresolved → `REQUIRES_RESOLUTION` для четырёх wellness-capabilities; доступны `ASK_CLARIFYING_QUESTION`, `GENERAL_EDUCATION`, `EXPLAIN_NEXT_STEP`, `ESCALATE_TO_MEDICAL_HELP` не блокируется; процедура не предлагается как лечение кожного заболевания / повреждения / воспаления / инфекции / иной жалобы. Полный текст — S4 (§6.4) \|` | §6.4 S4 — Skin integrity / skin condition — строки 405–437 v0.12; Приложение A | `ad0e44be6c0fa8312332d60b0545f1344c5fcfa2aa67faef5649c262785f8fd0` |
| OD-SAF-17 | 2026-09-12 | слово владельца 12.09; записано инженерным агентом (v0.6) | v0.6, §5.1 строка 132 | `\| OD-SAF-17 \| S5 Pregnancy / postpartum (+ lactation как qualifier): pregnancy, postpartum, lactation — context signals, не safety state; факт беременности — не `STOP`, не автоматический `CAUTION`, не обязан автоматически вызывать `CLARIFY`; procedure-aware; решение только по validated procedure-specific policy; нет validated rule → `UNKNOWN / INCOMPLETE`, fail-closed, без вопроса (*user cannot resolve missing policy*); rule есть, но нет decision-changing факта → `CLARIFY`; спрашивать только факты, которые конкретное rule использует (stage / timing, postpartum timing, lactation status — только если rule от них зависит); не собирать «для уверенности» способ родоразрешения, осложнения, диагнозы, акушерские и иные sensitive детали (*Do not collect precision that policy does not consume*); после достаточного evidence `ALLOWED → NORMAL`, `RESTRICTED → CAUTION`, `BLOCKED → STOP` для затронутой procedure/capability; пользователь / каталог не блокируются; «врач разрешил» — evidence с provenance, не bypass; процедура не представляется как лечение беременности / postpartum / lactation-related / связанных состояний; sensitive S5 evidence — не для коммерческого ranking / targeting, не мастеру / салону автоматически, не в durable memory без отдельного основания; lactation — qualifier / context dimension внутри S5, postpartum — temporal context внутри S5; новых signal classes не создавать. Полный текст — S5 (§6.5) \|` | §6.5 S5 — Pregnancy / postpartum — строки 462–517 v0.12; Приложение A | `496f910b05fecc3106b0f966bedd53d5bb55478cc53588f06bc4820bfa351d66` |
| OD-SAF-18 | 2026-09-12 | слово владельца 12.09; записано инженерным агентом (v0.7) | v0.7, §5.1 строка 133 | `\| OD-SAF-18 \| S6 Recent surgery / injury / invasive procedure: event signal, не state; факт события — не `STOP`, не авто-`CAUTION`, не обязательный `CLARIFY`; event-aware, temporal, zone-aware где релевантно, procedure-aware; решение только по validated, versioned rule с provenance; нет rule → `UNKNOWN / INCOMPLETE` fail-closed, пользователь не компенсирует отсутствие policy; rule есть, нет decision-changing факта → `CLARIFY`; спрашивать только факты, которые rule использует (тип события, когда, зона, пересечение с зоной процедуры — не анкета); без validated rule Ayla не выводит recovery window, healing period, «уже можно / ещё нельзя», medical clearance, severity, безопасный интервал; «недавно» не превращается LLM в дату / интервал — если timing decision-changing и факта нет → `CLARIFY`; `ALLOWED → NORMAL`, `RESTRICTED → CAUTION`, `BLOCKED → STOP` для затронутой procedure/capability; признаки S1 → S1 → `STOP`, S1 outranks S6; «врач разрешил» / «врач сказал подождать» — evidence с provenance, не bypass; Safety timing и Planning timing — один authoritative versioned rule source («можно ли сейчас?» / «когда можно?» — одна provenance); истечение времени не даёт auto-`NORMAL` — controlled reevaluation по rule; пользователь / каталог не блокируются; процедура не представляется как лечение последствий операции / травмы / вмешательства / осложнений; без диагноза по травме / послеоперационному состоянию и без определения степени повреждения. Полный текст — S6 (§6.6) \|` | §6.6 S6 — Recent surgery / injury / invasive procedure — строки 545–599 v0.12; Приложение A | `f1d96f5f90626200efe343bf4092f17c7c17183bab4e6c9d013bac06091bbc84` |
| OD-SAF-19 | 2026-09-12 | слово владельца 12.09; записано инженерным агентом (v0.8) | v0.8, §5.1 строка 134 | `\| OD-SAF-19 \| S7 Relevant chronic condition: user-reported chronic condition — health evidence, не подтверждённая медицинская истина Ayla; факт состояния — не `STOP`, не авто-`CAUTION`, не обязательный `CLARIFY`; condition-aware, procedure-aware, evidence/provenance-aware; решение только по validated `condition × procedure` rule с provenance; нет rule → `UNKNOWN / INCOMPLETE` fail-closed, пользователь не компенсирует отсутствие policy; rule есть, нет decision-changing факта → `CLARIFY`; спрашивать только факты, которые rule использует (какое состояние, текущий relevant qualifier, иной rule-required context — не анамнез «для уверенности»); без validated evidence / rule Ayla не определяет диагноз, стадию, тяжесть, «компенсировано / не», ремиссию, стабильность, risk category; `ALLOWED → NORMAL`, `RESTRICTED → CAUTION`, `BLOCKED → STOP` для затронутой procedure/capability; признаки S1 → S1 → `STOP`, S1 outranks S7; user-reported diagnosis не создаёт глобальный `risk_user`; condition evidence — не для коммерческого ranking / targeting, не мастеру / салону автоматически, не в durable memory без основания; «врач сказал, что у меня X» и медицинский документ (если будет поддержан) различаются по provenance, ни один — не bypass; процедура не представляется как лечение хронического заболевания; нерелевантность — только validated rule (`NO RULE != NOT_APPLICABLE`); пользователь / каталог не блокируются. Полный текст — S7 (§6.7) \|` | §6.7 S7 — Relevant chronic condition — строки 627–681 v0.12; Приложение A | `9c799b6dfe093908ab465f3f042e27ed2b9c505f9fcc7e8a5913ee0a022e5ea9` |
| OD-SAF-20 | 2026-09-12 | слово владельца 12.09; записано инженерным агентом (v0.9) | v0.9, §5.1 строка 135 | `\| OD-SAF-20 \| S8 Medication / active therapy — две независимые ветки, не смешивать. **A. medication / therapy evidence** («принимаю X», «на антибиотиках», «врач назначил курс», «прохожу терапию»): упоминание — не `STOP`, не авто-`CAUTION`, не обязательный `CLARIFY` (*medication mention != medication recommendation request*); therapy-aware, procedure-aware, provenance-aware, temporal если rule использует время / current status; решение только по validated `medication/therapy × procedure` rule с provenance, LLM не authority совместимости; нет rule → `UNKNOWN / INCOMPLETE` fail-closed без вопросов; rule есть, нет факта → `CLARIFY`; спрашивать только rule-required факты (какой препарат / therapy, relevant class, active ли сейчас, иной qualifier) — не полный medication history; без validated rule / medical capability Ayla не определяет совместимость, pharmacologic class вне validated controlled mapping, dose, необходимость отмены / изменения схемы / временного прекращения, drug interaction, clinical significance, «этот препарат не влияет на процедуру»; `ALLOWED → NORMAL`, `RESTRICTED → CAUTION`, `BLOCKED → STOP` для затронутой procedure/capability. **B. medication-management intent** («что мне выпить?», «какую дозу?», «можно отменить?», «увеличить дозу?», «чем заменить?»): запрос подобрать / назначить / рекомендовать / дозировать / изменить / отменить / заменить / изменить схему → соответствующие medical capabilities `BLOCKED` (минимум `RECOMMEND_MEDICATION`, `CHANGE_MEDICATION`) без отдельного medical ruling; `STOP` относится к запрещённой medical capability и **не** блокирует unrelated wellness-capabilities автоматически — `medication question → global STOP of wellness flow` не вводить; evidence в той же реплике оценивается отдельно по validated rule (не схлопывать ветки); mention ≠ intent. Provenance: «врач назначил X», prescription, medical document различаются по provenance, ни один не bypass и не global rule; evidence — не для ranking / targeting, не мастеру / салону автоматически, не в durable memory без основания; признаки S1 → S1 → `STOP`, S1 outranks S8; нет глобального `medication_risk_user`. Полный текст — S8 (§6.8) \|` | §6.8 S8 — Medication / active therapy — строки 711–781 v0.12; Приложение A | `c76e2e05dc20cbc15fc02ca51a17f1aeabd60bee65c6394a929cb2be5f69dcb8` |
| OD-SAF-21 | 2026-09-12 | слово владельца 12.09; записано инженерным агентом (v0.10) | v0.10, §5.1 строка 136 | `\| OD-SAF-21 \| S9 Adverse reaction / complication: user-reported adverse reaction — event-linked health evidence, не подтверждённый диагноз; факт реакции — не `STOP`, не авто-`CAUTION`, не обязательный `CLARIFY`; event-linked, procedure-aware, temporal если rule использует timing, provenance-aware; признаки S1 → S1 → `STOP`, S1 outranks S9, отдельных S9 emergency criteria вне S1 не создавать; ниже S1 — только validated `adverse-event × target-procedure` rule с provenance, LLM не authority допустимости / тяжести / причины / прогноза / лечения; нет rule → `UNKNOWN / INCOMPLETE` fail-closed без вопросов (*user cannot resolve missing policy*); rule есть, нет факта → `CLARIFY`; спрашивать только rule-required факты (после какой процедуры, когда, зона, сохраняется ли сейчас, иной qualifier) — не анамнез; Ayla не определяет диагноз / причину / severity вне S1 / «нормально или нет» / prognosis / «скоро пройдёт» / «неопасно» / treatment / необходимость терапии — без medical capability / validated policy → outbound `REVISE/BLOCK`; `ALLOWED → NORMAL`, `RESTRICTED → CAUTION`, `BLOCKED → STOP` для затронутой procedure/capability; **wellness-as-treatment запрещён**: не рекомендовать beauty/wellness-процедуру, чтобы вылечить / исправить / компенсировать / убрать / снять / устранить реакцию («лимфодренаж, чтобы убрать отёк после процедуры» и т. п.); на «что сделать, чтобы убрать осложнение?» — `RECOMMEND_MEDICAL_TREATMENT` остаётся `BLOCKED`, wellness recommendation не обход; попытка такого framing → outbound `REVISE/BLOCK`; нет global wellness `STOP` — незатронутая процедура проходит собственную evaluation, «adverse event → global user STOP» и «past complication → whole catalog blocked» не вводить; operational flow (complaint, support case, provider follow-up, refund, quality investigation) ≠ safety resolution — не снимает safety state, не заменяет medical escalation, не validated rule; `HUMAN_HANDOFF / SUPPORT` и `ESCALATE_TO_MEDICAL_HELP` — разные оси, один не заменяет другой; «мастер сказал, что это нормально» / «врач сказал, что всё в порядке» — evidence с provenance, различимы по origin, не bypass, не global rule; S9 evidence — не для ranking / targeting, не другому мастеру / салону автоматически, не в durable memory без основания; нет `adverse_event_user` / `complication_risk_user` / аналогичного persistent flag; доступны `ASK_CLARIFYING_QUESTION`, `GENERAL_EDUCATION`, `EXPLAIN_NEXT_STEP`, `ESCALATE_TO_MEDICAL_HELP`; support flow — не safety capability. Полный текст — S9 (§6.9) \|` | §6.9 S9 — Adverse reaction / complication — строки 812–882 v0.12; Приложение A | `5f5c39ef9331cfb0e3b45e8bbc7ffe2f93d9a6ccc84737fc089b1b26329a6e7d` |
| OD-SAF-22 | 2026-09-12 | слово владельца 12.09; записано инженерным агентом (v0.11) | v0.11, §5.1 строка 137 | `\| OD-SAF-22 \| S10 Insufficient / contradictory health context — evaluation / governance class, не medical signal: без собственной severity, не диагноз, не противопоказание, не medical inference, не новый safety state. Четыре различимые ветки, не схлопывать в одно `CLARIFY`: **A. MISSING_USER_FACT** → `CLARIFY`, один highest-value `question_id` за ход, затронутые safety-sensitive capabilities `REQUIRES_RESOLUTION`; lifecycle: asked ≠ resolved, resolved requirement ≠ ask again, usable answer → resolved + reevaluate all relevant rules; controlled reask только с reason (`contradictory_evidence`, `ambiguous_answer`, `user_correction`, `material_context_change`, `stale_evidence` — семантика; существующие canonical enum не переименовывать, mismatch фиксировать); запрещены `MODEL_FORGOT`, `LLM_WANTS_MORE_CONFIDENCE` и любая причина «только неуверенность модели»; бесполезный reask не повторяется — loop завершается controlled fail-closed outcome; при исчерпанном clarification capabilities не `ALLOWED`, не guessed `NORMAL`, остаются `REQUIRES_RESOLUTION` или `BLOCKED` по причине; alternative outcome (закрывает OD-F0C3-23): не повторять вопрос, не угадывать, честно сообщить о невозможности безопасно разрешить сценарий, удержать только затронутые capabilities, разрешить `GENERAL_EDUCATION` / `EXPLAIN_NEXT_STEP` / безопасный unrelated conversation / `ESCALATE_TO_MEDICAL_HELP` где уместно, альтернативный сценарий только после собственной независимой safety evaluation, не маскировать unresolved safety, universal fallback procedure не создавать. **B. EVIDENCE_CONFLICT** → `evaluation_status = CONFLICTED` (не state), controlled reask допустим, fail-closed; обе версии evidence сохраняются, user statement не уничтожает историю и не перезаписывает authoritative domain event / document («операции не было» против authoritative event → обе версии, conflict, governed resolution flow, «правду» LLM не определяет); после resolution → reevaluate all, не автоматический `NORMAL`. **C. MISSING_POLICY** (нет validated rule / knowledge / provenance / artifact / governed mapping) → `UNKNOWN / INCOMPLETE`, fail-closed, без вопроса (*user cannot resolve missing policy*); добровольные дополнительные данные не создают policy (*more evidence != missing rule resolution*); `UNKNOWN / INCOMPLETE` — не `STOP`, не противопоказание, не обнаруженный риск, не `NORMAL`, не `NOT_APPLICABLE`; не говорить «процедура опасна» из-за отсутствия policy. **D. TECHNICAL_EVALUATION_FAILURE** (Safety Engine, rule lookup, policy / provenance loader, dependency) → `evaluation_status = ERROR` — не `STOP`, не medical risk, не contraindication, не `UNKNOWN`-policy семантика, но fail-closed; сообщение — «сейчас не удалось безопасно выполнить проверку», не health finding. `UNKNOWN`, `INCOMPLETE`, `CONFLICTED`, `ERROR`, `POLICY_CONFLICT` не добавляются в enum `NORMAL \| CAUTION \| CLARIFY \| STOP`; `state = S10 / UNKNOWN / ERROR / CONFLICTED` не создаётся; consumers читают `capability_decisions`. Нет persistent `unknown_user` / `conflicted_user` / `safety_risk_user`. Resolution одного requirement / conflict → reevaluate all relevant rules (M10). Полный текст — S10 (§6.10) \|` | §6.10 S10 — Insufficient / contradictory health context — строки 915–1021 v0.12; Приложение A | `0726f31bdfc885d84352842aaac24860eb29a2b826f8b74aa531d758f294f347` |

Sha256 строк §5.1 (каждая строка целиком, UTF-8, без перевода строки):

| ID | sha256 строки §5.1 |
|---|---|
| OD-SAF-11 | `6f82f58cd1adcb982243fd4adbe281842d3675d710af635d6ae85a4fc7b615fc` |
| OD-SAF-12 | `9ec718b4ccd0e198f2adb562c038f065d733d55d183ab1cb2556b7e3f7276a1f` |
| OD-SAF-13 | `206724b0c9fce795e67009380282f79e16a5c8a2fe20b6dd1b7b3ca831d07ff4` |
| OD-SAF-14 | `a583fa00f5f704fe99f613ba280a131fa713f4673194bbc9948a314ca0af6c33` |
| OD-SAF-15 | `cde2d7bca7199d121983677c82171da4b6169d2d09e0df652d7ec8e1b9fcbb99` |
| OD-SAF-16 | `29bbda9bd3eb33a9e52aa75ddfa5aebe80a5df2fb2e4c15a9d34b654d09d46b7` |
| OD-SAF-17 | `546a109ea8118b10163dfd9f5af0a00b5c4b6f266f2dd6395360ff2a4ec93856` |
| OD-SAF-18 | `195989040a0d93d25067a59ac6086b76f66b4b232344f6d0f009ce0ae2d80e44` |
| OD-SAF-19 | `54beca9a2eb27632a96fa1cba28a4f5d6f9a69d965eb25761fda629d637aeb73` |
| OD-SAF-20 | `7505efa3fbfd7a44d0cfe30e9f1a9a3cffb34037670602eb3162f863184b051f` |
| OD-SAF-21 | `67604a546c0f925492b4f7d6acd26378f6cff89f6b92a721d7082bac42cb667e` |
| OD-SAF-22 | `79107964393cd7ce7ae032f9fd3020f36a7c0ead80576269a8a4a07a6641f4f4` |

## Как проверять fidelity

```text
1. Взять текущий docs/safety/F0-C3-safety-matrix.md.
2. Для каждого §6.x вырезать owner-блок: от строки '### 6.x ' до строки перед '#### Open questions' (включая заголовок).
3. sha256(блок, UTF-8, LF, без завершающего перевода строки) == значение в таблице выше.
4. Для каждой строки '| OD-SAF-NN |' в §5.1: sha256(строка) == значение в таблице выше.
5. Любое расхождение = рабочая копия отклонилась от слова владельца → дефект копии; record не правится.
```

## Приложение A — owner-блоки §6.1–6.10 baseline v0.12, дословно

Каждый блок приведён байт в байт (заголовок `### 6.x` включён). Ссылки вида `§72`, `§98`, `§127` внутри блоков — реестр `ai-bot-platform/docs/OPEN_DECISIONS.md` (baseline §3.4).

---

**Блок §6.1 — строки 185–254 v0.12 — sha256 `5dc7e4e1d6491a01a9a1b70cd17c40efc1c43486373b5e2b10f1bf95f7fafb17`**

<!-- BEGIN OWNER BLOCK 6.1 -->
### 6.1 S1 — Acute / emergency red flags

**Источник:** OD-SAF-11 (12.09.2026), OD-SAF-04, OD-SAF-10; FINAL FREEZE M1, M2, V3, V5, V7.

#### Purpose

Обнаружить сигналы внезапного тяжёлого состояния, при котором любое продолжение beauty/wellness-сценария способно **заменить или задержать** получение медицинской помощи, и немедленно остановить именно эти capabilities, оставив человеку путь к помощи.

#### Example triggers

Рабочие группы red flags (OD-SAF-11 п. 8; **рабочие категории, не полный клинический справочник**):

* выраженное нарушение дыхания / удушье;
* потеря сознания или выраженное нарушение сознания;
* внезапная сильная боль / давление в груди, особенно с системным ухудшением;
* внезапные выраженные неврологические нарушения: слабость одной стороны тела, нарушение речи, выраженное нарушение координации, внезапная потеря движения / чувствительности;
* значительное или неконтролируемое кровотечение;
* признаки тяжёлой аллергической реакции с дыхательными / системными проявлениями;
* иное внезапное тяжёлое системное ухудшение.

Иллюстративные фразы (для детекторов и фикстур, не политика): «не могу вдохнуть», «теряю сознание», «резко давит в груди и плохо», «не чувствую половину лица, речь заплетается», «кровь не останавливается», «после укола отекло горло, тяжело дышать».

#### Default state

`STOP` (`default_action = STOP`, OD-SAF-11 п. 1).

#### Escalation conditions

* Red flag выражен **явно** → `STOP` **без обязательного** `CLARIFY` (OD-SAF-11 п. 2; M2: не задерживать защитное действие вопросами).
* Сигнал **неоднозначен**, и **короткий** уточняющий вопрос способен отличить обычное состояние от потенциально неотложного → допустим `CLARIFY` (OD-SAF-11 п. 3).
* Критерии «явный / неоднозначный» на уровне конкретных формулировок — `OPEN` (OD-F0C3-05): владелец задал принцип, не словарь. Числовых порогов и диагностических критериев не вводить.
* Escalation (V7) — отдельный результат, не state: `STOP` по S1 **не** означает автоматически `HUMAN_HANDOFF`; тип эскалации соответствует причине; салонный оператор — не медицинский resolution authority. Куда именно направлять (какой контакт / текст экстренной помощи) — `OPEN` (OD-F0C3-06).

#### Minimum clarification

* При явном red flag — **вопросов нет** (защитное действие первым).
* При неоднозначном сигнале — **один** короткий вопрос, ответ на который отличает обычное состояние от потенциально неотложного. Формулировки вопросов — `OPEN` (OD-F0C3-05), каталог `question_id` — M11.
* Ayla не собирает анамнез, не уточняет детали «для полноты» — это выглядело бы как диагностика (M11: `LLM_WANTS_MORE_CONFIDENCE` запрещён).

#### Allowed capabilities (после `S1 → STOP`, OD-SAF-11 п. 5)

* `EXPLAIN_NEXT_STEP`
* `ESCALATE_TO_MEDICAL_HELP`
* `GENERAL_EDUCATION` — в безопасных пределах
* безопасная conversational support (не отдельная capability; см. §7 примечание)
* `ASK_CLARIFYING_QUESTION` — только в ветке неоднозначного сигнала (п. 3)

#### Restricted capabilities

* `GIVE_SELF_CARE_ADVICE` — **блокируется, если** такой совет способен заменить или задержать получение медицинской помощи (OD-SAF-11 п. 4). Граница «способен заменить/задержать» решается правилом, а не runtime-догадкой — кто её задаёт: `OPEN` (OD-F0C3-07).
* `GENERAL_EDUCATION` — ограничена «безопасными пределами»; предел не определён словарно — `OPEN` (OD-F0C3-07).

#### Blocked capabilities (OD-SAF-11 п. 4 — минимум)

* `RECOMMEND_SERVICE`
* `RANK_SERVICE`
* `RECOMMEND_PROVIDER`
* `BOOK_SERVICE`
* `GIVE_SELF_CARE_ADVICE` — в описанной выше части
* `DIAGNOSE`, `RECOMMEND_MEDICATION`, `CHANGE_MEDICATION`, `RECOMMEND_MEDICAL_TREATMENT` — не разрешены ни в каком состоянии без отдельного ruling (§7)

#### User-facing behaviour (принципы; окончательные тексты — не здесь)

* Поведение **symptom-based и escalation-based** (OD-SAF-11 п. 7): Ayla говорит о симптоме и о следующем шаге к помощи, **не называет заболевание**. Не писать: «у вас инсульт / инфаркт / анафилаксия» и т. п.
* `STOP` — остановка небезопасной beauty/wellness capability, **не** завершение разговора (OD-SAF-11 п. 6; OD-SAF-04): после защитного сообщения человек может продолжать общение, получить объяснение следующего шага, эскалацию.
* Без «записать всё равно» и без wellness-CTA против блокирующей политики (канон §7.2).
* Critical `STOP` использует controlled response semantics / templates (V5); LLM формулирует только внутри контракта ответа.
* Различимость `STOP`-кризиса и `STOP`-политики сохраняется (§127).
* Production wording — `OPEN` (канон §7.2 «exact production wording — still open»; OD-F0C3-08).

<!-- END OWNER BLOCK 6.1 -->

---

**Блок §6.10 — строки 915–1021 v0.12 — sha256 `0726f31bdfc885d84352842aaac24860eb29a2b826f8b74aa531d758f294f347`**

<!-- BEGIN OWNER BLOCK 6.10 -->
### 6.10 S10 — Insufficient / contradictory health context

**Источник:** OD-SAF-22 (12.09.2026); OD-SAF-03, OD-SAF-06, OD-SAF-08, OD-SAF-14; FINAL FREEZE M2, M8, M10, M11, M12, M13, M14, V3, V4, V6; DRE §8.1, §11.3–11.4, §13.5.

**Природа класса (п. 1):** S10 — **evaluation / governance class**, не medical signal. У него нет собственной медицинской severity; он не диагноз, не противопоказание, не создаёт medical inference и **не создаёт нового safety state**. `state = S10 / UNKNOWN / ERROR / CONFLICTED` не существует (п. 23); consumers читают canonical `capability_decisions` (M13).

#### Purpose
Не дать отсутствию, неполноте, противоречивости safety-релевантной информации или техническому сбою превратиться в молчаливый `NORMAL` **или** в выдуманный медицинский риск — и сделать причину незавершённой оценки **различимой**: чего именно не хватает и кто может это восполнить.

#### Example triggers
* нет decision-changing факта, который пользователь реально может дать (зона, срок, «сейчас ли») → A;
* новое explicit evidence противоречит прежнему user evidence или authoritative event / document («операции не было» против записи о процедуре) → B;
* нет validated rule / knowledge / provenance / artifact / governed mapping для пары «сигнал × процедура»; пустая таблица policy; `requires_health_check = true` без валидного `SafetyResult` → C;
* сбой Safety Engine, rule lookup, policy / provenance loader, required dependency → D.

#### Default state
**Собственного state нет.** Default behaviour — классификация причины на **четыре различимые ветки** (п. 2), которые нельзя схлопывать в одно `CLARIFY`:

```text
safety evaluation cannot complete cleanly
        ↓
classify reason
        │
        ├─ A. MISSING_USER_FACT — нет decision-changing USER/CONTEXT факта, пользователь может его дать
        │      → CLARIFY (state)
        │      → один highest-value question_id за ход
        │      → затронутые safety-sensitive capabilities: REQUIRES_RESOLUTION
        │
        ├─ B. EVIDENCE_CONFLICT — explicit evidence materially конфликтует с другим relevant evidence
        │      → evaluation_status = CONFLICTED (не state)
        │      → controlled reask / governed resolution
        │      → затронутые capabilities fail-closed
        │
        ├─ C. MISSING_POLICY — нет validated rule / knowledge / provenance / artifact / governed mapping
        │      → UNKNOWN / INCOMPLETE (не state)
        │      → вопрос пользователю НЕ задаётся
        │      → затронутые capabilities fail-closed
        │
        └─ D. TECHNICAL_EVALUATION_FAILURE — сбой engine / lookup / loader / dependency
               → evaluation_status = ERROR (не state)
               → никакого medical inference
               → затронутые capabilities fail-closed
```

| Ветка | Что именно | Кто может восполнить | Исход | Источник |
|---|---|---|---|---|
| A. `MISSING_USER_FACT` | decision-changing факт, который rule реально использует | пользователь | `CLARIFY`; `REQUIRES_RESOLUTION` до resolution | OD-SAF-22 п. 3–4; OD-SAF-03/14; M2, M11 |
| B. `EVIDENCE_CONFLICT` | согласованность evidence | governed resolution flow (controlled reask, authoritative source) | `evaluation_status = CONFLICTED`; fail-closed | п. 11–14; M12 |
| C. `MISSING_POLICY` | validated policy knowledge | только владелец policy (`ayla-knowledge`), не пользователь | `UNKNOWN / INCOMPLETE`; fail-closed; без вопроса | п. 15–18; OD-SAF-14; M8; DRE §11.4 |
| D. `TECHNICAL_EVALUATION_FAILURE` | работоспособность оценки | инженерия / инцидент | `evaluation_status = ERROR`; fail-closed; без medical inference | п. 19–21; M14 |

Ни одно из значений `UNKNOWN`, `INCOMPLETE`, `CONFLICTED`, `ERROR`, `POLICY_CONFLICT` не добавляется в enum `NORMAL | CAUTION | CLARIFY | STOP` (п. 22) — это evaluation / knowledge axes (§4.2).

**Clarification lifecycle (ветка A; п. 5–10):**

```text
CLARIFY
   ↓
usable answer?
 ├─ yes → resolve requirement → reevaluate ALL relevant rules (M10)
 │        (resolved requirement ≠ ask again)
 └─ no
      ↓
controlled reask still decision-changing?
 ├─ yes → один controlled reask с valid reason
 └─ no  → stop clarification loop
          → controlled unresolved outcome
          → затронутая capability остаётся unresolved / fail-closed (REQUIRES_RESOLUTION или BLOCKED по причине)
          → safe next step
```

Запрещено: `CLARIFY → same CLARIFY → same CLARIFY → …` (п. 8). При исчерпанном clarification затронутые capabilities **не** становятся `ALLOWED`, guessed `NORMAL` не выставляется; новое decision enum не вводится (п. 9).

**Alternative outcome (п. 10; закрывает owner-семантику OD-F0C3-23):** если clarification исчерпан / бесполезен — (1) не повторять вопрос; (2) не угадывать safety outcome; (3) честно сообщить, что текущий сценарий нельзя безопасно разрешить на имеющейся информации; (4) удержать / заблокировать **только** затронутые safety-sensitive capabilities; (5) разрешить в рамках global constraints `GENERAL_EDUCATION`, `EXPLAIN_NEXT_STEP`, безопасный unrelated conversation, `ESCALATE_TO_MEDICAL_HELP` где уместно по отдельным правилам; (6) альтернативный wellness-сценарий / процедура — **только после собственной независимой safety evaluation**; (7) alternative outcome не маскирует unresolved safety. Universal fallback procedure не создаётся.

#### Escalation conditions
Не медицинская эскалация: S10 не порождает `STOP`. Но:
* **S1 outranks S10-процесс:** если одновременно есть более сильное S1 evidence, protective action S1 не задерживается разрешением конфликта или ожиданием ответа — `STOP` по S1; конфликт / unresolved requirement остаётся отдельным evaluation fact и историей (M2, M10; OD-SAF-08).
* B: material conflict → `CONFLICTED`; обе версии evidence сохраняются; user statement не уничтожает историю и **не перезаписывает** authoritative domain event / document (п. 12–13); governed resolution flow; «правду» LLM не определяет; после resolution → reevaluate all, не автоматический `NORMAL` (п. 14).
* C: `UNKNOWN / INCOMPLETE` означает **только** отсутствие policy (п. 18): не `STOP`, не противопоказание, не обнаруженный медицинский риск, не `NORMAL`, не `NOT_APPLICABLE`. Пользователю не сообщается «процедура опасна». Добровольные дополнительные данные policy не создают (п. 17).
* D: `ERROR` — не `STOP`, не medical risk, не contraindication, не `UNKNOWN`-policy семантика (п. 20); fail-closed для затронутых safety-sensitive capabilities; технический инцидент — по observability (FINAL FREEZE §11), не через safety state.
* `POLICY_CONFLICT` (противоречащие authoritative rules) — отдельный evaluation status, fail-closed, наблюдаемый инцидент (M10; §9 п. 7).

#### Minimum clarification
* A: **один** `question_id`, один highest-value decision-changing вопрос за ход (п. 4); не медицинская анкета; не «для уверенности». `asked ≠ resolved`; после usable answer requirement resolved и тот же вопрос **не повторяется** (п. 5). Controlled reask только с reason (п. 6): семантика — `contradictory_evidence`, `ambiguous_answer`, `user_correction`, `material_context_change`, `stale_evidence`. **Mismatch с текущим canonical enum, не переименовывается:** код `AskReason` (DRE §13.5, закрытый список) — `reask_answer_retracted`, `reask_answer_expired`, `reask_semantics_changed`, `reask_candidate_set_changed`, `reask_safety_reevaluation`; прямых членов для `contradictory_evidence` и `ambiguous_answer` нет; сопоставление — OD-F0C3-48. Запрещены `MODEL_FORGOT`, `LLM_WANTS_MORE_CONFIDENCE` и любые причины, означающие только неуверенность модели (п. 7): LLM uncertainty не даёт права собирать ещё health data.
* B: controlled reask с reason (семантика `contradictory_evidence`); при конфликте с authoritative source — governed resolution, не переспрос «кому верить».
* C: **без вопроса** (п. 16) — *user cannot resolve missing policy*; `more evidence != missing rule resolution` (п. 17).
* D: без вопроса — пользователь не чинит технический сбой.
* S10 не имеет собственного каталога вопросов: вопросы приходят из каталогов классов S2–S9 (OD-F0C3-27/29/32/34/35/38/41/44); отдельный `question_id`-реестр для S10 не требуется.

#### Allowed capabilities
В рамках global constraints во всех ветках: `GENERAL_EDUCATION`, `EXPLAIN_NEXT_STEP`, безопасный unrelated conversation; `ESCALATE_TO_MEDICAL_HELP` — где уместно по отдельным правилам; `ASK_CLARIFYING_QUESTION` — только A и B (в C и D вопросы не задаются). Незатронутые процедуры / capabilities — по собственной независимой safety evaluation (п. 10.6; OD-SAF-09). При operational incident — support flow, не safety capability (§6.9).

#### Restricted capabilities
A: `RECOMMEND_SERVICE`, `RANK_SERVICE`, `RECOMMEND_PROVIDER`, `BOOK_SERVICE` → `REQUIRES_RESOLUTION` для затронутых safety-sensitive кандидатов до resolution; при controlled unresolved outcome остаются `REQUIRES_RESOLUTION` или становятся `BLOCKED` — по причине unresolved и существующему canonical contract (п. 9), не по новому enum. B: те же — fail-closed до governed resolution.

#### Blocked capabilities
C и D: затронутые safety-sensitive capabilities fail-closed (`BLOCKED` для затронутых кандидатов) до появления policy / восстановления оценки; **не** блокируется всё, что safety-нерелевантно по validated policy (§72: `NOT_APPLICABLE` — по типу поверхности / validated rule, не по отсутствию данных; `NO RULE != NOT_APPLICABLE`). Пустая таблица policy → `UNKNOWN / INCOMPLETE` → fail-closed (DRE §11.4), не `NORMAL`, не `NOT_APPLICABLE`. Всегда: четыре медицинские (§7).

#### User-facing behaviour
* A: один вопрос без анкеты; после usable answer — не переспрашивать.
* A, исчерпано: честно сказать, что безопасно разрешить именно этот сценарий на имеющейся информации нельзя; не рекомендовать затронутую процедуру; предложить безопасный next step; альтернативу — только после её собственной evaluation; не маскировать unresolved под «всё в порядке».
* B: не спорить «кому верить» и не объявлять одну версию истиной; сообщить, что сведения расходятся и для этой процедуры нужна проверка; сохранить обе версии.
* C: сказать, что оценить допустимость для этой процедуры не может, и направить к специалисту — **без** «процедура опасна / противопоказана» (п. 18).
* D: смысл — «сейчас не удалось безопасно выполнить проверку», **не** «мы обнаружили опасность», «вам противопоказано», «это медицинский риск» (п. 21); production wording — `OPEN` (OD-F0C3-51).
* Во всех ветках: разговор не завершается; unrelated conversation не обязан прекращаться. Production wording — `OPEN` (OD-F0C3-08).

<!-- END OWNER BLOCK 6.10 -->

---

**Блок §6.2 — строки 290–322 v0.12 — sha256 `d8fa5ff31c90d517d2aa9c594616aed0f1c58e27416cf5902b5d64bda105884e`**

<!-- BEGIN OWNER BLOCK 6.2 -->
### 6.2 S2 — Pain / neurological symptoms

**Источник:** OD-SAF-12 (12.09.2026); OD-SAF-11 (граница с S1); OD-SAF-06, OD-SAF-10, OD-SAF-14; FINAL FREEZE M2, M3, M10, M11, V3.

#### Purpose
Обнаружить боль и неврологические проявления и **различить два контекста** (OD-SAF-12 п. 3): обычный musculoskeletal / wellness («ноет спина после работы») и потенциально более серьёзный acute / neurologic — не диагностируя источник боли и не задерживая защитное действие там, где признаки удовлетворяют S1.

#### Example triggers
«ноет спина после работы», «тянет шею после тренировки», «болит с утра», «отдаёт в руку», «немеют пальцы», «после падения болит поясница». Иллюстративно; не список симптомов.

#### Default state
`CLARIFY` (`default_action = CLARIFY`, OD-SAF-12 п. 1). Сам факт боли — **не** автоматический `STOP` (п. 2).

#### Escalation conditions
* Признаки удовлетворяют рабочим группам S1 (OD-SAF-11 п. 8) → route → S1, итоговое состояние `STOP` (п. 6). **S1 outranks S2.**
* Особо значимые признаки (п. 5), при которых уточнение обязано отличить контексты: внезапное начало; быстрое ухудшение; выраженная слабость; онемение / потеря чувствительности; нарушение движения; связь с травмой; комбинация с S1 red flags. Это признаки для маршрутизации и уточнения, **не** пороги и не диагностические критерии.
* После clarification, если S1 / red flags не подтверждены, информации достаточно и **validated procedure-specific rule** разрешает сценарий → `CAUTION` или `NORMAL` **по конкретному правилу** (п. 7). Не определять «по здравому смыслу». Нет validated rule → `UNKNOWN / INCOMPLETE`, fail-closed для затронутой capability (OD-SAF-14).

#### Minimum clarification
Только вопрос, способный изменить safety decision (п. 4; OD-SAF-06). Relevant dimensions при необходимости: **onset; progression; neurological component; trauma / exertion context.** Это **не** обязательная анкета из четырёх вопросов: один highest-value вопрос за ход (M11); если более сильное активное состояние уже определяет next action — вопрос слабого правила не задаётся (M10). Каталог `question_id` — `OPEN` (OD-F0C3-27).

#### Allowed capabilities
Пока S2 unresolved / `CLARIFY` (п. 10), в рамках уже существующих global constraints: `ASK_CLARIFYING_QUESTION`; `GENERAL_EDUCATION`; `EXPLAIN_NEXT_STEP`. `ESCALATE_TO_MEDICAL_HELP` не блокируется S2 (§8.3).

#### Restricted capabilities
Пока S2 unresolved / `CLARIFY`: `RECOMMEND_SERVICE`, `RANK_SERVICE`, `RECOMMEND_PROVIDER`, `BOOK_SERVICE` → `REQUIRES_RESOLUTION`, **если их безопасность зависит от отсутствующего факта** (п. 10). После resolution — по validated procedure-specific rule (`CAUTION`: явные ограничения правила; `NORMAL`: без ограничений).

#### Blocked capabilities
При маршрутизации в S1 — по S1 (§6.1). `TREAT_PAIN` и любая иная «лечебная» capability в таксономии отсутствует и **не создаётся** коммерческими названиями услуг (п. 9). Всегда: `DIAGNOSE`, `RECOMMEND_MEDICATION`, `CHANGE_MEDICATION`, `RECOMMEND_MEDICAL_TREATMENT` (§7).

#### User-facing behaviour
Ayla **не диагностирует источник боли** (п. 8). Beauty/wellness-услуга **не представляется как лечение причины боли** (п. 9): коммерческие названия вида «Массаж спины — снятие боли и зажимов», «Спина без боли — комплекс массажа» — labels каталога; они не создают medical capability и не дают Ayla права обещать устранение причины. При `CLARIFY` — один вопрос без вывода; при `CAUTION` — grounded ограничения правила; «мастер уточнит самочувствие» допустимо только как оговорка, не как перенос safety на мастера. Production wording — `OPEN` (OD-F0C3-08).

<!-- END OWNER BLOCK 6.2 -->

---

**Блок §6.3 — строки 347–381 v0.12 — sha256 `0decc93da919632f05ed43bfca053aa8cae8aa918d51827cf8831c3571bc467b`**

<!-- BEGIN OWNER BLOCK 6.3 -->
### 6.3 S3 — Infection / inflammation / fever

**Источник:** OD-SAF-15 (12.09.2026); OD-SAF-11 (граница с S1); OD-SAF-06, OD-SAF-10, OD-SAF-14; FINAL FREEZE M2, M4, M11, M12, V3, V4.

#### Purpose
Обнаружить признаки инфекционного / воспалительного процесса или температуры и определить — **для конкретной процедуры / capability и с учётом времени** (сейчас / в прошлом / неизвестно) — допустимо ли продолжение, не диагностируя причину и не превращая факт болезни в остановку всего сценария.

#### Example triggers
«температура», «болею», «озноб», «ОРВИ сейчас», «на прошлой неделе болела», «воспалилось на щеке», «десна воспалилась». Иллюстративно; не список.

#### Default state
`CLARIFY` (`default_action = CLARIFY`, OD-SAF-15 п. 1). Сам факт температуры, инфекции или воспаления — **не** автоматический emergency `STOP` (п. 2).

#### Escalation conditions
* Признаки удовлетворяют рабочим группам S1 (OD-SAF-11 п. 8: выраженное нарушение дыхания, нарушение сознания, системное ухудшение и т. д.) → управление передаётся S1, итоговое состояние `STOP` (п. 3).
* После достаточного user/context evidence решение по затронутой procedure / capability определяется **validated procedure-specific policy** (п. 5): `ALLOWED → NORMAL`; `RESTRICTED → CAUTION` (с явными ограничениями правила, M6); `BLOCKED → STOP` для затронутой capability; **missing validated rule → `UNKNOWN / INCOMPLETE`, fail-closed**.
* `NO RULE != SAFE` (п. 6): отсутствие validated policy не компенсируется медицинским рассуждением LLM.
* Решение **procedure-aware** (п. 4, п. 13): S3-сигнал не блокирует пользователя или весь каталог; локальный сигнал не влияет на нерелевантную процедуру автоматически — но нерелевантность (`NOT_APPLICABLE` / отсутствие restriction) устанавливает только validated policy: `NO RULE != NOT_APPLICABLE`.
* **Temporal context decision-relevant** (п. 7): система различает текущее состояние, прошедшее состояние и отсутствие достаточной временной информации, если это меняет safety decision. Смысл фиксируется; новый enum в рамках этой задачи не вводится (соответствие scopes M12 `CURRENT_STATE` / `EVENT_SPECIFIC` — реализация). Прошлый S3 не считается автоматически текущим; истечение предполагаемого окна → reassessment, не автоматический `NORMAL` (V4).

#### Minimum clarification
Только вопрос, способный изменить safety decision (п. 8; OD-SAF-06). Принципиально могут потребоваться: **сейчас ли** (temporal qualifier); затрагивает ли зона / характер сигнала целевую процедуру — если validated rule этого требует. **Не спрашивать** точное значение температуры, длительность болезни, иные медицинские детали, если validated rule их не использует: *Do not collect precision that policy does not consume*. Отсутствие временной информации, меняющей решение, → один вопрос (`CLARIFY`), не `UNKNOWN` — это факт пользователя (OD-SAF-14). Каталог `question_id` — `OPEN` (OD-F0C3-29).

#### Allowed capabilities
Пока S3 unresolved, в пределах global safety constraints (п. 12): `ASK_CLARIFYING_QUESTION`, `GENERAL_EDUCATION`, `EXPLAIN_NEXT_STEP`. `ESCALATE_TO_MEDICAL_HELP` не блокируется S3 (§8.3). После resolution — по validated rule (`ALLOWED → NORMAL`: обычный поток для этой capability).

#### Restricted capabilities
Пока S3 unresolved — для затронутой safety-sensitive procedure / capability (п. 11): `RECOMMEND_SERVICE`, `RANK_SERVICE`, `RECOMMEND_PROVIDER`, `BOOK_SERVICE` → `REQUIRES_RESOLUTION`. После resolution при `RESTRICTED → CAUTION` — ограничения только те, что явно возвращает правило (M6); состав — `OPEN` (OD-F0C3-30).

#### Blocked capabilities
При `BLOCKED → STOP` по validated rule — затронутая capability для затронутой процедуры (не пользователь, не каталог). При маршрутизации в S1 — по S1. Всегда: `DIAGNOSE`, `RECOMMEND_MEDICATION`, `CHANGE_MEDICATION`, `RECOMMEND_MEDICAL_TREATMENT` (§7).

#### User-facing behaviour
Ayla **не ставит диагноз** инфекции / воспаления и не определяет их причину (п. 9). Beauty/wellness-процедура **не представляется как лечение** инфекции, воспаления, температуры или иного S3-состояния (п. 10) — это outbound-ограничение (`response_constraints`; V6 `REVISE` / `BLOCK`). При `CLARIFY` — один вопрос без медицинских уточнений сверх правила; при `STOP` для процедуры — сказать, что именно и на каком основании (без диагноза), что остаётся доступным; перенос существующей записи и напоминание — продуктовые действия, не safety-capability. Production wording — `OPEN` (OD-F0C3-08).

<!-- END OWNER BLOCK 6.3 -->

---

**Блок §6.4 — строки 405–437 v0.12 — sha256 `ad0e44be6c0fa8312332d60b0545f1344c5fcfa2aa67faef5649c262785f8fd0`**

<!-- BEGIN OWNER BLOCK 6.4 -->
### 6.4 S4 — Skin integrity / skin condition

**Источник:** OD-SAF-16 (12.09.2026); OD-SAF-11 (граница с S1); OD-SAF-06, OD-SAF-10, OD-SAF-14; FINAL FREEZE M2, M6, M11, V3, V6.

#### Purpose
Обнаружить изменения, повреждения или жалобы на кожу и определить — **для конкретной зоны и конкретной процедуры / capability** — допустимо ли продолжение, не определяя диагноз, инфекционность, заразность или причину и не превращая факт кожной жалобы в остановку всего сценария.

#### Example triggers
«сыпь на руке», «ранка на пальце», «чешется на щеке», «воспаление на лице», «шелушится кожа головы», «пятно на спине». Иллюстративно; не дерматологическая классификация.

#### Default state
`CLARIFY` (`default_action = CLARIFY`, OD-SAF-16 п. 1). Сам факт кожного изменения, повреждения или жалобы — **не** автоматический `STOP` (п. 2).

#### Escalation conditions
* Признаки удовлетворяют рабочим группам S1 (OD-SAF-11 п. 8: например, сыпь с выраженным нарушением дыхания / признаки тяжёлой аллергической реакции с дыхательными или системными проявлениями, значительное кровотечение) → route → S1, итоговое состояние `STOP` (п. 6). **S1 имеет приоритет.**
* После достаточного user/context evidence решение по затронутой procedure / capability определяется **validated procedure-specific policy** (п. 7): `ALLOWED → NORMAL`; `RESTRICTED → CAUTION` (с явными ограничениями правила, M6); `BLOCKED → STOP` для затронутой procedure / capability; **missing validated rule → `UNKNOWN / INCOMPLETE`, fail-closed**. Исход не определяется медицинским рассуждением LLM.
* **Zone-aware и procedure-aware** (п. 3, п. 8, п. 9): кожный сигнал **вне** зоны процедуры **не** считается нерелевантным автоматически — `NOT_APPLICABLE` или отсутствие restriction следуют только из validated rule (`NO RULE != NOT_APPLICABLE`). Ограничение относится к затронутой procedure / capability; пользователь и каталог целиком не блокируются.

#### Minimum clarification
Только decision-changing facts (п. 4; OD-SAF-06). В первую очередь: **где** находится изменение; **пересекается ли** оно с зоной процедуры. Иные факты (характер, давность, сопутствующее) — только если validated procedure-specific rule реально их использует. Это **не** полная дерматологическая анкета; один highest-value вопрос за ход (M11); reask «для уверенности» запрещён. Каталог `question_id` — `OPEN` (OD-F0C3-32).

#### Allowed capabilities
Пока S4 unresolved, в рамках global safety constraints (п. 11): `ASK_CLARIFYING_QUESTION`, `GENERAL_EDUCATION`, `EXPLAIN_NEXT_STEP`. `ESCALATE_TO_MEDICAL_HELP` **не блокируется** S4. После resolution — по validated rule (`ALLOWED → NORMAL`: обычный поток для этой capability).

#### Restricted capabilities
Пока S4 unresolved — для затронутой procedure / capability (п. 10): `RECOMMEND_SERVICE`, `RANK_SERVICE`, `RECOMMEND_PROVIDER`, `BOOK_SERVICE` → `REQUIRES_RESOLUTION`. После resolution при `RESTRICTED → CAUTION` — только ограничения, явно возвращённые правилом (M6); состав — `OPEN` (OD-F0C3-33).

#### Blocked capabilities
При `BLOCKED → STOP` по validated rule — затронутая capability для затронутой procedure (не пользователь, не каталог). При маршрутизации в S1 — по S1. Всегда: `DIAGNOSE`, `RECOMMEND_MEDICATION`, `CHANGE_MEDICATION`, `RECOMMEND_MEDICAL_TREATMENT` (§7). Определение инфекционности / заразности / причины — часть `DIAGNOSE`, без отдельной разрешённой medical capability недоступно (п. 5).

#### User-facing behaviour
Ayla **не определяет** диагноз, инфекционность, заразность и причину кожного изменения (п. 5): запрещены утверждения «это герпес», «это грибок», «это заразно», «это аллергия» и аналогичные — как и их отрицания («это не заразно»), поскольку и то и другое — diagnosis / infectiousness assertion. Beauty/wellness-процедура **не предлагается как лечение** кожного заболевания, повреждения, воспаления, инфекции или иной жалобы (п. 12) — outbound-ограничение (`response_constraints`; V6 `REVISE` / `BLOCK`). При `CLARIFY` — один вопрос о месте / пересечении с зоной; при ограничении — сказать, какая процедура / зона затронута и что остаётся доступным; направление к специалисту — через `EXPLAIN_NEXT_STEP` / `ESCALATE_TO_MEDICAL_HELP` без называния болезни. Production wording — `OPEN` (OD-F0C3-08).

<!-- END OWNER BLOCK 6.4 -->

---

**Блок §6.5 — строки 462–517 v0.12 — sha256 `496f910b05fecc3106b0f966bedd53d5bb55478cc53588f06bc4820bfa351d66`**

<!-- BEGIN OWNER BLOCK 6.5 -->
### 6.5 S5 — Pregnancy / postpartum

**Источник:** OD-SAF-17 (12.09.2026); OD-SAF-07; OD-SAF-14 и S5-поправка 12.09; OD-SAF-08, OD-SAF-10, OD-SAF-11 (граница с S1); FINAL FREEZE M7, M8, M11, M12.

Внутри класса — три **измерения контекста** одного сигнала, не отдельные классы (OD-SAF-17 п. 14–15): **pregnancy** (stage / timing как qualifier), **postpartum** (temporal context), **lactation** (qualifier / context dimension). Новые signal classes не создаются.

#### Purpose
Учитывать беременность, послеродовой период и лактацию как **context signals** (п. 1), влияющие на допустимость конкретной процедуры / capability только через validated procedure-specific policy — **не** превращая сам факт в safety state, в запрет или в обязательный вопрос.

#### Example triggers
«я беременна», «возможно беременна», «N-й триместр», «недавно родила», «кормлю грудью», «после родов прошло …». Иллюстративно.

#### Default state
**Безусловного default state нет** (п. 2): сам факт беременности / postpartum / lactation — не `STOP`, не автоматический `CAUTION`, не обязательный `CLARIFY`. Default behaviour — детерминированная последовательность:

```text
pregnancy / postpartum / lactation context
        ↓
validated procedure-specific rule exists?
    ├─ no  → UNKNOWN / INCOMPLETE, fail-closed для затронутой safety-sensitive capability
    │        (вопрос пользователю не задаётся: user cannot resolve missing policy — п. 5, OD-SAF-14)
    └─ yes
         ↓
missing decision-changing fact required by rule?
    ├─ yes → CLARIFY (один вопрос о факте, который rule реально использует — п. 6–7)
    └─ no
         ↓
evaluate rule (п. 9)
    ├─ ALLOWED    → NORMAL
    ├─ RESTRICTED → CAUTION (с явными ограничениями правила, M6)
    └─ BLOCKED    → STOP для затронутой procedure / capability
```

`NO RULE != NOT_APPLICABLE` и `NO RULE != SAFE` (S5-поправка 12.09; OD-SAF-14): только validated policy устанавливает `NOT_APPLICABLE` или отсутствие restriction; «беременность + процедура, кажущаяся нерелевантной» без validated rule → `UNKNOWN / INCOMPLETE`, не автоматический `NORMAL`. При этом OD-SAF-07 / п. 10: пользователь и каталог целиком не блокируются — fail-closed относится к затронутой capability для затронутой процедуры.

#### Escalation conditions
* Признаки удовлетворяют рабочим группам S1 (OD-SAF-11 п. 8; например, значительное кровотечение, выраженное нарушение дыхания, нарушение сознания) → route → S1; **S1 outranks S5**; `STOP` по S1.
* Исход по S5 — только validated procedure-specific policy (п. 4, п. 9); не определяется медицинским рассуждением LLM. Различать `BLOCKED_BY_RULE` и `SAFETY_EVIDENCE_INSUFFICIENT` (M8).
* «Врач разрешил» — evidence с provenance (M7, п. 11), **не** bypass safety policy: итог всё равно определяется правилом.
* Sensitive S5 evidence (п. 13; M8): не используется для коммерческого ranking / targeting; не передаётся мастеру / салону автоматически (только отдельный controlled disclosure purpose / consent); не auto-promote в durable memory без отдельного разрешённого основания.

#### Minimum clarification
Спрашивать разрешено **только** факты, которые конкретное validated rule реально использует (п. 7): концептуально — pregnancy stage / timing, postpartum timing, lactation status — и только если правило от них зависит. Один decision-changing вопрос за ход (M11). **Не собирать «для уверенности»** (п. 8): способ родоразрешения, медицинские осложнения, диагнозы, дополнительные акушерские данные, иные sensitive детали — если validated rule их не использует (*Do not collect precision that policy does not consume*). Если validated rule отсутствует — вопрос не задаётся вовсе (п. 5). Каталог `question_id` — `OPEN` (OD-F0C3-34).

#### Allowed capabilities
В рамках global safety constraints: `ASK_CLARIFYING_QUESTION` (только в ветке `CLARIFY`), `GENERAL_EDUCATION`, `EXPLAIN_NEXT_STEP`; `ESCALATE_TO_MEDICAL_HELP` не блокируется S5. `RECOMMEND_SERVICE` / `RANK_SERVICE` / `RECOMMEND_PROVIDER` / `BOOK_SERVICE` — для процедур, по которым validated rule даёт `ALLOWED → NORMAL` (обычный поток для этой capability). «Правила нет» ≠ «ограничений нет».

#### Restricted capabilities
Пока S5 unresolved (`CLARIFY` — rule есть, факта нет) — для затронутой procedure / capability: `RECOMMEND_SERVICE`, `RANK_SERVICE`, `RECOMMEND_PROVIDER`, `BOOK_SERVICE` → `REQUIRES_RESOLUTION`. После resolution при `RESTRICTED → CAUTION` — только ограничения, явно возвращённые правилом (M6); состав — `OPEN` (OD-F0C3-15).

#### Blocked capabilities
При `BLOCKED → STOP` по validated rule — затронутая capability для затронутой процедуры (не пользователь, не каталог — п. 10). Без validated rule — `UNKNOWN / INCOMPLETE`, fail-closed для затронутой safety-sensitive capability (это не `STOP` и не противопоказание). При маршрутизации в S1 — по S1. Всегда: `DIAGNOSE`, `RECOMMEND_MEDICATION`, `CHANGE_MEDICATION`, `RECOMMEND_MEDICAL_TREATMENT` (§7).

#### User-facing behaviour
Не сообщать «беременным / кормящим / после родов нельзя» как общее суждение — говорить о конкретной процедуре и о том, что остаётся доступным. Не задавать вопрос о сроке / статусе, если он не меняет решения по выбранной процедуре. Beauty/wellness-процедура **не представляется как лечение** беременности, postpartum-состояния, lactation-related состояния или связанных медицинских состояний (п. 12) — outbound-ограничение (`response_constraints`; V6 `REVISE` / `BLOCK`). Без диагноза и без медицинских выводов о течении беременности / восстановления. Sensitive факты не озвучиваются мастеру / салону автоматически (п. 13); при записи доменный гейт `requires_health_check` (§98 / M5) применяется отдельно. Production wording — `OPEN` (OD-F0C3-08).

<!-- END OWNER BLOCK 6.5 -->

---

**Блок §6.6 — строки 545–599 v0.12 — sha256 `f1d96f5f90626200efe343bf4092f17c7c17183bab4e6c9d013bac06091bbc84`**

<!-- BEGIN OWNER BLOCK 6.6 -->
### 6.6 S6 — Recent surgery / injury / invasive procedure

**Источник:** OD-SAF-18 (12.09.2026); OD-SAF-06, OD-SAF-08, OD-SAF-10, OD-SAF-11 (граница с S1), OD-SAF-14; FINAL FREEZE M9, M11, M12, V4.

#### Purpose
Учитывать операции, травмы, инвазивные и косметологические вмешательства как **event signals** (п. 1) — с типом, временем и зоной события — и определять допустимость конкретной процедуры / capability **сейчас** только по validated, versioned rule с provenance (п. 4), не выводя сроков, «уже можно / ещё нельзя», тяжести или clearance самостоятельно (п. 8).

#### Example triggers
«недавно была операция», «подвернула ногу», «неделю назад делала инъекции», «ушиб», «сняли гипс», «после лазера на лице». Иллюстративно; не классификация событий.

#### Default state
**Безусловного default state нет** (п. 2): сам факт события — не `STOP`, не автоматический `CAUTION`, не обязательный `CLARIFY`. Default behaviour — детерминированная последовательность:

```text
event signal detected
        ↓
validated rule exists?
    ├─ no  → UNKNOWN / INCOMPLETE, fail-closed для затронутой safety-sensitive capability
    │        (пользователь не компенсирует отсутствие policy ответом — п. 5, OD-SAF-14)
    └─ yes
         ↓
missing decision-changing fact required by rule?
    ├─ yes → CLARIFY (один вопрос о факте, который rule реально использует — п. 6–7, п. 9)
    └─ no
         ↓
evaluate rule (п. 10)
    ├─ ALLOWED    → NORMAL
    ├─ RESTRICTED → CAUTION (с явными ограничениями правила, M6)
    └─ BLOCKED    → STOP для затронутой procedure / capability
```

S6 — **event-aware, temporal, zone-aware где релевантно, procedure-aware** (п. 3). `NO RULE != NOT_APPLICABLE`: событие в другой зоне не считается нерелевантным автоматически — `NOT_APPLICABLE` или отсутствие restriction только из validated rule; без rule → `UNKNOWN / INCOMPLETE`, не автоматический `NORMAL`. Пользователь и каталог целиком не блокируются (п. 15) — fail-closed относится к затронутой procedure / capability.

#### Escalation conditions
* Признаки удовлетворяют рабочим группам S1 (OD-SAF-11 п. 8: неконтролируемое кровотечение, выраженное системное ухудшение, нарушение дыхания / сознания и т. д.) → route → S1; **S1 outranks S6**; `STOP` по S1 (п. 11).
* Исход по S6 — только validated, versioned rule с provenance (п. 4, п. 10); не определяется рассуждением LLM. Без validated rule Ayla **не выводит** (п. 8): recovery window; healing period; «уже можно»; «ещё нельзя»; medical clearance; severity; безопасный интервал между событием и процедурой. Нет rule / provenance → `UNKNOWN / INCOMPLETE`, не invented timing (M9).
* **Истечение времени само по себе не даёт auto-`NORMAL`** (п. 14; V4): после истечения relevant interval / window — controlled reevaluation по rule.
* «Врач разрешил» / «врач сказал подождать» — evidence с provenance (п. 12; M7, M12); **не** bypass safety policy; учитывается через policy / evidence reconciliation, Ayla не заменяет его своим сроком.
* **Единый источник timing** (п. 13; M9): Safety («можно ли сейчас?») и Planning («когда можно?») опираются на один authoritative versioned rule source / provenance в `ayla-knowledge`; двух независимых источников истины быть не должно; расхождение значений для одного rule → `POLICY_CONFLICT`, fail-closed, наблюдаемый инцидент (M10), не silent divergence.

#### Minimum clarification
Спрашивать разрешено **только** факты, которые конкретное validated rule реально использует (п. 7; OD-SAF-06): концептуально — тип события; когда оно произошло; зона события; пересекается ли она с зоной целевой процедуры. **Не** обязательная анкета: один highest-value вопрос за ход (M11). Слово «недавно» **не** преобразуется LLM в дату или интервал (п. 9; M9): если timing decision-changing по rule и точного факта нет → `CLARIFY`; если rule от точной даты не зависит — дата не спрашивается. Если validated rule отсутствует — вопрос не задаётся вовсе (п. 5). Каталог `question_id` — `OPEN` (OD-F0C3-35).

#### Allowed capabilities
В рамках global safety constraints: `ASK_CLARIFYING_QUESTION` (только в ветке `CLARIFY`), `GENERAL_EDUCATION`, `EXPLAIN_NEXT_STEP`; `ESCALATE_TO_MEDICAL_HELP` не блокируется S6. `RECOMMEND_SERVICE` / `RANK_SERVICE` / `RECOMMEND_PROVIDER` / `BOOK_SERVICE` — для процедур, по которым validated rule даёт `ALLOWED → NORMAL`. «Правила нет» ≠ «ограничений нет».

#### Restricted capabilities
Пока S6 unresolved (`CLARIFY` — rule есть, факта нет) — для затронутой procedure / capability: `RECOMMEND_SERVICE`, `RANK_SERVICE`, `RECOMMEND_PROVIDER`, `BOOK_SERVICE` → `REQUIRES_RESOLUTION`. После resolution при `RESTRICTED → CAUTION` — только ограничения, явно возвращённые правилом (M6); состав — `OPEN` (OD-F0C3-18).

#### Blocked capabilities
При `BLOCKED → STOP` по validated rule — затронутая capability для затронутой процедуры (не пользователь, не каталог — п. 15). Без validated rule — `UNKNOWN / INCOMPLETE`, fail-closed для затронутой safety-sensitive capability (не `STOP`, не противопоказание, не интервал). При маршрутизации в S1 — по S1. Всегда: `DIAGNOSE`, `RECOMMEND_MEDICATION`, `CHANGE_MEDICATION`, `RECOMMEND_MEDICAL_TREATMENT` (§7); определение степени повреждения / послеоперационного состояния — часть `DIAGNOSE` (п. 17).

#### User-facing behaviour
Без выдуманных сроков: формулировки вида «через две недели уже можно», «после операции нельзя X дней», «уже зажило» без validated rule — outbound `REVISE` / `BLOCK` (п. 8; V6). Если rule нет — Ayla говорит, что оценить допустимость сейчас не может, и направляет к специалисту через `EXPLAIN_NEXT_STEP` / `ESCALATE_TO_MEDICAL_HELP`, не называя срока. Ayla **не ставит диагноз** по травме / послеоперационному состоянию и **не определяет степень повреждения** (п. 17). Beauty/wellness-процедура **не представляется как лечение последствий** операции, травмы, инвазивного вмешательства или осложнений после него (п. 16) — outbound-ограничение (`response_constraints`). «Врач сказал подождать» не переспоривается своим сроком. При ограничении — говорить о конкретной процедуре / зоне и о том, что остаётся доступным. Production wording — `OPEN` (OD-F0C3-08).

<!-- END OWNER BLOCK 6.6 -->

---

**Блок §6.7 — строки 627–681 v0.12 — sha256 `9c799b6dfe093908ab465f3f042e27ed2b9c505f9fcc7e8a5913ee0a022e5ea9`**

<!-- BEGIN OWNER BLOCK 6.7 -->
### 6.7 S7 — Relevant chronic condition

**Источник:** OD-SAF-19 (12.09.2026); OD-SAF-06, OD-SAF-08, OD-SAF-10, OD-SAF-11 (граница с S1), OD-SAF-14; FINAL FREEZE M7, M8, M11, M12.

#### Purpose
Учитывать названные пользователем хронические состояния как **health evidence с provenance** (п. 1) — не как подтверждённую медицинскую истину Ayla и не как свойство пользователя — и определять допустимость конкретной процедуры / capability только по validated `condition × procedure` rule (п. 4), не выводя диагноз, стадию, тяжесть или risk category самостоятельно (п. 8).

#### Example triggers
«у меня гипертония», «диабет», «варикоз», «онкология в ремиссии», «эпилепсия», «врач сказал, что у меня X». Иллюстративно; **не** список нозологий и не основание для правил.

#### Default state
**Безусловного default state нет** (п. 2): сам факт хронического состояния — не `STOP`, не автоматический `CAUTION`, не обязательный `CLARIFY`. Default behaviour — детерминированная последовательность:

```text
chronic condition evidence
        ↓
validated condition × procedure rule exists?
    ├─ no  → UNKNOWN / INCOMPLETE, fail-closed для затронутой safety-sensitive capability
    │        (пользователь не компенсирует отсутствие policy ответами — п. 5, OD-SAF-14)
    └─ yes
         ↓
missing decision-changing fact required by rule?
    ├─ yes → CLARIFY (один вопрос о факте, который rule реально использует — п. 6–7)
    └─ no
         ↓
evaluate rule (п. 9)
    ├─ ALLOWED    → NORMAL
    ├─ RESTRICTED → CAUTION (с явными ограничениями правила, M6)
    └─ BLOCKED    → STOP для затронутой procedure / capability
```

S7 — **condition-aware, procedure-aware, evidence/provenance-aware** (п. 3). `NO RULE != NOT_APPLICABLE` (п. 15): нерелевантность состояния конкретной процедуре устанавливается только validated rule; без rule → `UNKNOWN / INCOMPLETE`, не автоматический `NORMAL` / `NOT_APPLICABLE`. Пользователь и каталог целиком не блокируются (п. 16) — fail-closed относится к затронутой procedure / capability.

#### Escalation conditions
* Признаки соответствуют рабочим группам S1 (OD-SAF-11 п. 8) → route → S1; **S1 outranks S7**; `STOP` по S1 (п. 10).
* Исход по S7 — только validated `condition × procedure` rule с provenance (п. 4, п. 9); не определяется рассуждением LLM. Без validated evidence / rule Ayla **не определяет** (п. 8): диагноз; стадию заболевания; тяжесть; «компенсировано / не компенсировано»; ремиссию; стабильность состояния; risk category.
* User-reported diagnosis **не создаёт** глобальный `risk_user` или аналогичный глобальный risk flag (п. 11; M8) — влияние только через конкретное правило для конкретной процедуры.
* Provenance (п. 13; M7, M12): «врач сказал, что у меня X» — user-reported professional statement; медицинский документ, если такой источник будет поддерживаться, — отдельный origin. Они различаются по provenance, и **ни один не является bypass** safety policy; user-specific professional instruction не становится global rule (M7).
* Sensitive condition evidence (п. 12; M8): не используется для коммерческого ranking / targeting; не передаётся мастеру / салону автоматически (только отдельный controlled disclosure purpose / consent); не auto-promote в durable memory без отдельного разрешённого основания.

#### Minimum clarification
Спрашивать разрешено **только** факты, которые конкретное validated rule реально использует (п. 7; OD-SAF-06): концептуально — какое именно состояние; текущий relevant qualifier; иной rule-required context. **Не** медицинский анамнез «для уверенности»: если rule не использует remission / compensation status — эти факты не спрашиваются. Один highest-value вопрос за ход (M11); reask с reason `LLM_WANTS_MORE_CONFIDENCE` запрещён. Если validated rule отсутствует — вопрос не задаётся вовсе (п. 5). Каталог `question_id` — `OPEN` (OD-F0C3-38).

#### Allowed capabilities
В рамках global safety constraints: `ASK_CLARIFYING_QUESTION` (только в ветке `CLARIFY`), `GENERAL_EDUCATION`, `EXPLAIN_NEXT_STEP`; `ESCALATE_TO_MEDICAL_HELP` не блокируется S7. `RECOMMEND_SERVICE` / `RANK_SERVICE` / `RECOMMEND_PROVIDER` / `BOOK_SERVICE` — для процедур, по которым validated rule даёт `ALLOWED → NORMAL`. «Правила нет» ≠ «ограничений нет».

#### Restricted capabilities
Пока S7 unresolved (`CLARIFY` — rule есть, факта нет) — для затронутой procedure / capability: `RECOMMEND_SERVICE`, `RANK_SERVICE`, `RECOMMEND_PROVIDER`, `BOOK_SERVICE` → `REQUIRES_RESOLUTION`. После resolution при `RESTRICTED → CAUTION` — только ограничения, явно возвращённые конкретным правилом (M6); состав — `OPEN` (OD-F0C3-19).

#### Blocked capabilities
При `BLOCKED → STOP` по validated rule — затронутая capability для затронутой процедуры (не пользователь, не каталог — п. 16). Без validated rule — `UNKNOWN / INCOMPLETE`, fail-closed для затронутой safety-sensitive capability (не `STOP`, не противопоказание). При маршрутизации в S1 — по S1. Всегда: `DIAGNOSE`, `RECOMMEND_MEDICATION`, `CHANGE_MEDICATION`, `RECOMMEND_MEDICAL_TREATMENT` (§7); определение стадии / тяжести / ремиссии / risk category — часть `DIAGNOSE` (п. 8).

#### User-facing behaviour
Без диагноза и медицинской классификации: формулировки вида «у вас тяжёлая гипертония», «у вас всё компенсировано», «вы в группе риска» без validated evidence / rule — unsupported medical inference → outbound `REVISE` / `BLOCK` (п. 8; V6). Без «при вашем заболевании нельзя» как общего суждения — говорить о конкретной процедуре и о том, что остаётся доступным. Beauty/wellness-процедура **не представляется как лечение** хронического заболевания (п. 14) — outbound-ограничение (`response_constraints`). Если rule нет — Ayla говорит, что оценить допустимость для этой процедуры не может, и направляет к специалисту через `EXPLAIN_NEXT_STEP` / `ESCALATE_TO_MEDICAL_HELP`. Sensitive факты не озвучиваются мастеру / салону автоматически (п. 12). Production wording — `OPEN` (OD-F0C3-08).

<!-- END OWNER BLOCK 6.7 -->

---

**Блок §6.8 — строки 711–781 v0.12 — sha256 `c76e2e05dc20cbc15fc02ca51a17f1aeabd60bee65c6394a929cb2be5f69dcb8`**

<!-- BEGIN OWNER BLOCK 6.8 -->
### 6.8 S8 — Medication / active therapy

**Источник:** OD-SAF-20 (12.09.2026); свод 11.09 §3; OD-SAF-06, OD-SAF-08, OD-SAF-10, OD-SAF-11 (граница с S1), OD-SAF-14; FINAL FREEZE M7, M8, M9, M11, M12, M13.

S8 содержит **две логически независимые ветки** (п. 1), которые не смешиваются и не схлопываются в одно решение (п. 13):

* **A. medication / active therapy evidence** — health evidence («принимаю препарат X», «я на антибиотиках», «врач назначил курс», «прохожу терапию»);
* **B. medication-management intent** — medical intent («что мне выпить?», «какую дозу принять?», «можно отменить препарат?», «увеличить дозу?», «чем заменить?»).

#### Purpose
(A) Учитывать приём лекарств / активную терапию как **evidence с provenance**, влияющее на допустимость конкретной процедуры / capability только через validated `medication/therapy × procedure` rule (п. 4), не выводя совместимость самостоятельно (п. 8). (B) Опознавать запрос на управление лекарствами как medical intent и блокировать соответствующую **medical capability**, не останавливая unrelated wellness-flow (п. 10–11).

#### Example triggers
(A) «принимаю парацетамол, это помешает?», «на антибиотиках», «прохожу курс», «врач назначил X», «принимаю таблетки от давления». (B) «что мне выпить перед массажем?», «подберите обезболивающее», «сколько таблеток», «можно отменить препарат перед процедурой?», «увеличить дозу?», «чем заменить?». Смешанная реплика: «Я принимаю X. Что мне выпить перед массажем?» — одновременно evidence `X` (A) и intent (B). Иллюстративно; не список препаратов и не классификация.

#### Default state
**Безусловного default state нет.** (A) Простое упоминание — не `STOP`, не автоматический `CAUTION`, не обязательный `CLARIFY` (п. 2): *medication mention != medication recommendation request*. Default behaviour evidence-ветки — детерминированная последовательность:

```text
medication / therapy evidence
        ↓
validated medication/therapy × procedure rule exists?
    ├─ no  → UNKNOWN / INCOMPLETE, fail-closed для затронутой safety-sensitive capability
    │        (вопросы, не компенсирующие отсутствие policy, не задаются — п. 5, OD-SAF-14)
    └─ yes
         ↓
missing decision-changing fact required by rule?
    ├─ yes → CLARIFY (один вопрос о факте, который rule реально использует — п. 6–7)
    └─ no
         ↓
evaluate rule (п. 9)
    ├─ ALLOWED    → NORMAL
    ├─ RESTRICTED → CAUTION (с явными ограничениями правила, M6)
    └─ BLOCKED    → STOP для затронутой procedure / capability
```

(B) Medication-management intent:

```text
request to recommend / dose / stop / change medication
        ↓
medical capability BLOCKED (минимум RECOMMEND_MEDICATION, CHANGE_MEDICATION)
        ↓
unrelated wellness capability evaluated independently
(по собственным safety-сигналам и validated rules; global STOP wellness-flow не вводится — п. 11)
```

Evidence-ветка (A) — therapy-aware, procedure-aware, provenance-aware; temporal, если конкретный validated rule использует время / current status (п. 3). Mention ≠ intent (п. 14): «принимаю таблетки от давления» не даёт `STOP` medical intent. Обе ветки в одной реплике оцениваются **раздельно** (п. 13). `NO RULE != NOT_APPLICABLE`: нерелевантность препарата процедуре — только validated rule.

#### Escalation conditions
* Признаки соответствуют рабочим группам S1 (OD-SAF-11 п. 8; например, признаки тяжёлой аллергической реакции с дыхательными / системными проявлениями) → route → S1; **S1 outranks S8**; `STOP` по S1 (п. 17).
* (A) Исход — только validated `medication/therapy × procedure` rule с provenance (п. 4, п. 9); LLM **не является authority совместимости**. Без validated rule / отдельной medical capability Ayla **не определяет** (п. 8): совместимость лекарства с процедурой; pharmacologic class, если его нельзя получить из validated controlled mapping; dose; необходимость отмены; необходимость изменения схемы; drug interaction; clinical significance; необходимость временно прекратить терапию; «этот препарат не влияет на процедуру».
* (B) `STOP` относится к запрещённой medical capability (п. 11); wellness-capabilities текущего хода проходят **собственную** safety evaluation (п. 12) — `BOOK_SERVICE` / `RECOMMEND_SERVICE` не становятся `STOP` только из-за запроса лекарства.
* Provenance (п. 15; M7, M12): «врач назначил X», prescription, medical document (если такие источники будут поддерживаться) различаются по provenance; **ни один** не bypass safety policy и не превращается автоматически в global rule.
* Sensitive evidence (п. 16; M8): не используется для коммерческого ranking / targeting; не передаётся мастеру / салону автоматически (только отдельный controlled disclosure purpose / consent); не auto-promote в durable memory без отдельного разрешённого основания. S8 **не создаёт** глобальный `medication_risk_user` или аналогичный флаг (п. 18).

#### Minimum clarification
(A) Спрашивать разрешено **только** факты, которые конкретное validated rule реально использует (п. 7; OD-SAF-06): концептуально — какой препарат / therapy; relevant medication / therapy class (из validated controlled mapping); active ли сейчас; иной rule-required qualifier. **Не** собирать полный medication history «для уверенности»; dose не спрашивается, если rule её не использует. Один highest-value вопрос за ход (M11). Если validated rule отсутствует — вопрос не задаётся вовсе (п. 5). (B) Вопросов нет: intent опознан → medical capability `BLOCKED` → безопасный next step. Каталог `question_id` — `OPEN` (OD-F0C3-41).

#### Allowed capabilities
В рамках global safety constraints: `ASK_CLARIFYING_QUESTION` (только в ветке `CLARIFY` evidence-ветки), `GENERAL_EDUCATION`, `EXPLAIN_NEXT_STEP` (в т. ч. «это к врачу» при intent), `ESCALATE_TO_MEDICAL_HELP` не блокируется S8. `RECOMMEND_SERVICE` / `RANK_SERVICE` / `RECOMMEND_PROVIDER` / `BOOK_SERVICE` — по собственной safety evaluation: при (A) — для процедур, по которым validated rule даёт `ALLOWED → NORMAL`; при (B) — не блокируются самим intent (п. 11–12).

#### Restricted capabilities
(A) Пока evidence-ветка unresolved (`CLARIFY` — rule есть, факта нет) — для затронутой procedure / capability: `RECOMMEND_SERVICE`, `RANK_SERVICE`, `RECOMMEND_PROVIDER`, `BOOK_SERVICE` → `REQUIRES_RESOLUTION`. После resolution при `RESTRICTED → CAUTION` — только ограничения, явно возвращённые правилом (M6); состав — `OPEN` (OD-F0C3-21).

#### Blocked capabilities
(B) `RECOMMEND_MEDICATION`, `CHANGE_MEDICATION` — `BLOCKED` при запросе подобрать / назначить / рекомендовать / определить или изменить дозировку / отменить / заменить / изменить схему (п. 10); остаются запрещёнными без отдельного medical ruling (§7; OD-F0C3-25). (A) При `BLOCKED → STOP` по validated rule — затронутая capability для затронутой процедуры (не пользователь, не каталог). Без validated rule — `UNKNOWN / INCOMPLETE`, fail-closed для затронутой safety-sensitive capability (не `STOP`, не противопоказание). При маршрутизации в S1 — по S1. Всегда: `DIAGNOSE`, `RECOMMEND_MEDICAL_TREATMENT` (§7).

#### User-facing behaviour
(B) «Подбирать / менять / отменять лекарства не могу — это к врачу» — смысл, не текст; безопасный next step через `EXPLAIN_NEXT_STEP` / `ESCALATE_TO_MEDICAL_HELP`; разговор и wellness-запрос продолжаются по собственной оценке. (A) Упоминание не превращается в отказ; **без утверждений совместимости** без validated rule — формулировки вида «этот препарат совместим с массажем», «X не влияет на процедуру», «можно пропустить приём» → outbound `REVISE` / `BLOCK` (п. 8; V6). Если rule нет — Ayla говорит, что оценить влияние на эту процедуру не может, и направляет к специалисту, не называя класса / дозы / схемы. Sensitive факты не озвучиваются мастеру / салону автоматически (п. 16). Production wording — `OPEN` (OD-F0C3-08).

<!-- END OWNER BLOCK 6.8 -->

---

**Блок §6.9 — строки 812–882 v0.12 — sha256 `5f5c39ef9331cfb0e3b45e8bbc7ffe2f93d9a6ccc84737fc089b1b26329a6e7d`**

<!-- BEGIN OWNER BLOCK 6.9 -->
### 6.9 S9 — Adverse reaction / complication

**Источник:** OD-SAF-21 (12.09.2026); OD-SAF-04, OD-SAF-06, OD-SAF-08, OD-SAF-09, OD-SAF-10, OD-SAF-11 (граница с S1), OD-SAF-14; FINAL FREEZE M7, M8, M10, M11, M12, M13, V6, V7.

#### Purpose
Учитывать сообщённые пользователем нежелательные реакции и осложнения после процедур как **event-linked health evidence** (п. 1) — не как подтверждённый диагноз Ayla — и определять допустимость конкретной целевой процедуры / capability только по validated `adverse-event × target-procedure` rule (п. 4); не давать wellness-сценарию «лечить» осложнение другой процедурой (п. 10–12) и не смешивать safety с operational flow (п. 14–15).

#### Example triggers
«после чистки лицо горит и опухло», «после массажа синяки и сильная боль», «после инъекций отёк», «после окрашивания чешется кожа головы»; intent-форма: «что мне сделать, чтобы убрать эту реакцию?». Иллюстративно; не complication taxonomy и не список диагнозов.

#### Default state
**Безусловного default state нет** (п. 1): сам факт нежелательной реакции — не `STOP`, не автоматический `CAUTION`, не обязательный `CLARIFY`. Default behaviour — детерминированная последовательность:

```text
adverse-event evidence
        ↓
S1 red flag?
    ├─ yes → S1 → STOP (S1 outranks S9 — п. 3)
    └─ no
         ↓
validated adverse-event × target-procedure rule exists?
    ├─ no  → UNKNOWN / INCOMPLETE, fail-closed для затронутой safety-sensitive capability
    │        (вопросы, не компенсирующие отсутствие policy, не задаются — п. 5, OD-SAF-14)
    └─ yes
         ↓
missing decision-changing fact required by rule?
    ├─ yes → CLARIFY (один вопрос о факте, который rule реально использует — п. 6–7)
    └─ no
         ↓
evaluate rule (п. 9)
    ├─ ALLOWED    → NORMAL
    ├─ RESTRICTED → CAUTION (с явными ограничениями правила, M6)
    └─ BLOCKED    → STOP для затронутой procedure / capability
```

Отдельная intent / outbound ветка (п. 11–12):

```text
"что сделать, чтобы убрать осложнение?"
        ↓
не использовать wellness recommendation как medical treatment
        ↓
RECOMMEND_MEDICAL_TREATMENT = BLOCKED   и/или   outbound REVISE / BLOCK
(EXPLAIN_NEXT_STEP / ESCALATE_TO_MEDICAL_HELP остаются доступны)
```

S9 — **event-linked, procedure-aware, temporal (если validated rule использует timing), provenance-aware** (п. 2). `NO RULE != NOT_APPLICABLE`: нерелевантность события целевой процедуре — только validated rule. Незатронутые wellness-capabilities проходят **собственную** safety evaluation (п. 13): «adverse event → global user STOP» и «past complication → whole catalog blocked» не вводятся (OD-SAF-04/09).

#### Escalation conditions
* Признаки соответствуют рабочим группам S1 (OD-SAF-11 п. 8: тяжёлая аллергическая реакция с дыхательными / системными проявлениями, значительное кровотечение, системное ухудшение и т. д.) → route → S1; **S1 outranks S9**; `STOP` по S1 (п. 3). Отдельные S9 emergency criteria вне S1-модели не создаются.
* Ниже границы S1 исход — только validated `adverse-event × target-procedure` rule с provenance (п. 4, п. 9). LLM **не authority** для допустимости процедуры, тяжести реакции, причины, прогноза, лечения. Без medical capability / validated policy Ayla **не определяет** (п. 8): диагноз осложнения; причину; severity вне S1 criteria; «нормальная / ненормальная» реакция; prognosis; «это скоро пройдёт»; «это неопасно»; treatment; необходимость конкретной медицинской терапии — такие утверждения → outbound `REVISE` / `BLOCK` (V6).
* **Operational flow ≠ safety resolution** (п. 14): если событие связано с услугой / мастером / салоном Ayla, оно может породить complaint, support case, provider follow-up, refund / compensation, quality investigation — но operational action не снимает safety state, не заменяет medical escalation и не является validated safety rule.
* **Две оси эскалации** (п. 15; V7): `HUMAN_HANDOFF / SUPPORT` и `ESCALATE_TO_MEDICAL_HELP` различимы, один не заменяет другой; оба могут быть активны одновременно.
* Provenance (п. 16; M7, M12): «мастер сказал, что это нормально», «врач сказал, что всё в порядке» — evidence с provenance, различаются по origin; **не** bypass safety policy, не превращаются в global rule; мастер не становится safety resolution authority.
* Sensitive evidence (п. 17; M8): не используется для коммерческого ranking / targeting; не передаётся другому мастеру / салону автоматически (только отдельный controlled disclosure purpose / consent); не auto-promote в durable memory без отдельного разрешённого основания. S9 **не создаёт** глобальный `adverse_event_user`, `complication_risk_user` или аналогичный persistent flag (п. 18).

#### Minimum clarification
Спрашивать разрешено **только** факты, которые конкретное validated rule реально использует (п. 7; OD-SAF-06): концептуально — после какой процедуры возникла реакция; когда возникла; какая зона затронута; сохраняется ли сейчас; иной rule-required qualifier. **Не** превращать S9 в медицинский анамнез; не задавать вопросы «для уверенности» (M11); точное время не спрашивается, если rule его не использует. Один highest-value вопрос за ход. Если validated rule отсутствует — вопрос не задаётся вовсе (п. 5). Каталог `question_id` — `OPEN` (OD-F0C3-44).

#### Allowed capabilities
В рамках global safety constraints (п. 19): `ASK_CLARIFYING_QUESTION` (только в ветке `CLARIFY`), `GENERAL_EDUCATION`, `EXPLAIN_NEXT_STEP`, `ESCALATE_TO_MEDICAL_HELP`. При operational incident — отдельный support flow, который **не** считается safety capability и не снимает safety result. `RECOMMEND_SERVICE` / `RANK_SERVICE` / `RECOMMEND_PROVIDER` / `BOOK_SERVICE` — для незатронутых процедур по собственной evaluation; для затронутой — при validated rule `ALLOWED → NORMAL`.

#### Restricted capabilities
Пока S9 unresolved (`CLARIFY` — rule есть, факта нет) — для затронутой procedure / capability: `RECOMMEND_SERVICE`, `RANK_SERVICE`, `RECOMMEND_PROVIDER`, `BOOK_SERVICE` → `REQUIRES_RESOLUTION`. После resolution при `RESTRICTED → CAUTION` — только ограничения, явно возвращённые правилом (M6); состав — `OPEN` (OD-F0C3-45).

#### Blocked capabilities
`RECOMMEND_MEDICAL_TREATMENT` — `BLOCKED` на запрос «что сделать, чтобы убрать осложнение?» (п. 11), без отдельного ruling (§7; OD-F0C3-25); wellness service recommendation не используется как обход этого запрета. При `BLOCKED → STOP` по validated rule — затронутая capability для затронутой процедуры (не пользователь, не каталог). Без validated rule — `UNKNOWN / INCOMPLETE`, fail-closed для затронутой safety-sensitive capability (не `STOP`, не диагноз). При маршрутизации в S1 — по S1. Всегда: `DIAGNOSE` (включая диагноз / причину / severity осложнения — п. 8), `RECOMMEND_MEDICATION`, `CHANGE_MEDICATION` (§7).

#### User-facing behaviour
Без диагноза, причины, прогноза и оценки «нормально / ненормально»: «это нормальная реакция, скоро пройдёт», «это неопасно» без validated medical authority / rule → outbound `REVISE` / `BLOCK` (п. 8). **Wellness-as-treatment запрещён** (п. 10): не рекомендовать beauty/wellness-процедуру, чтобы вылечить / исправить / компенсировать / убрать / снять / устранить реакцию — примеры запрещённого framing: «сделайте лимфодренаж, чтобы убрать отёк после процедуры», «успокаивающий уход исправит реакцию», «массаж снимет осложнение», «процедура ускорит восстановление после реакции» → `REVISE` / `BLOCK` (п. 12). На вопрос «что мне сделать?» — безопасный next step через `EXPLAIN_NEXT_STEP` / `ESCALATE_TO_MEDICAL_HELP`, без назначения лечения. Если событие связано с услугой Ayla — отдельно предложить support / complaint flow, не выдавая его за решение safety-вопроса. Sensitive факты не озвучиваются другому мастеру / салону автоматически (п. 17). Production wording — `OPEN` (OD-F0C3-08).

<!-- END OWNER BLOCK 6.9 -->

