# MVP_DELIVERY_TRACKER_2026-07

> **Назначение:** управляющий контур поверх [`2026-07-02-MVP_GAP_MAP.md`](2026-07-02-MVP_GAP_MAP.md) (v1.2). Здесь — объём (SP), этапы, контрольные даты, milestones, velocity и еженедельный delta «обгоняем / по плану / запаздываем».
>
> **✅ BASELINE (утверждён founder 2026-07-02, SP-выравнивание v1.2):**
> - **Даты (two-tier):** **08.08 (конец W5) = gates-green candidate / internal target** (≈155–162 SP «core must-have»); **15.08 = committed pilot-ready + launch** (до **177 SP**, хвост must-have добирается в W6).
> - **SP-рамка:** Baseline pilot scope **155 SP** + New scope **+16 SP** (дискавери 13→29, Stream 4 Фаза 1+2) + мелочи = **Current pilot scope ≈ 171–177 SP**. W6 = **must-have tail + buffer** (не чистый буфер).
> - **15.07 НЕ фиксируется как Ayla-REST MVP** (P0/P1-блокеры). Показ 15.07 — только **демо/legacy YClients path**, без заявления «MVP готов».
> - **Velocity:** 2 параллельных код-агента + ежедневное ревью ≈ **35 SP/нед** (база). 3-й агент **не в базовой скорости** — точечно на независимые задачи (docs/tests/eventbus/catalog audit).
> - **New-scope правило:** любое увеличение pilot-scope сначала фиксируется здесь как New Scope SP, иначе задача не берётся (GAP MAP §10).
>
> **🟢 СТАТУС 2026-07-03 (обновл.).** **Смержено в dev:** S0-A (PR #1065) · **S0.5 event_id 26→36 end-to-end** (#1067 + #1070 + hotfix #1073; #1058/#1066 closed) · **S0-B** клиенты→builder (#1071) · **S1-A** global onboarding+consent (#1072, флаг OFF). **dev зелёный.** **Разблокировано/next:** S0-C contract tests (после S0-A+B) · **S1-B** safety pre_check (в окне S1 от dev-с-S1-A; кризис-копирайт → founder sign-off) · ShiroPy #949. **Не стартовали:** S1-C/D, Wave 2/S3 (gated).
> **🔴 РЕФРЕЙМ #1044 (2026-07-03): Stream 3 = Catalog domain rebuild.** S3 → 50–70 SP; pilot scope ~205–225; **15.08 committed но At Risk**; 08.08 = aggressive candidate. **Wave 2 НЕ стартовать пока не закрыты 4 условия:** (1) S3 design locked; (2) **G-CalendarSync** decision записан (Variant A Ayla-primary / Variant B YClients webhook→busy) по пилотному салону; (3) источник данных Пензы подтверждён; (4) Ayla-side breakdown принят. Отдельный **Ayla-агент** (beautygo_backend) на S3A/S3C/S3-CAL — рекомендация.
> **🔴 ПАМЯТЬ В ПИЛОТ (2026-07-03): Stream 5 Memory Foundation (+32 SP) — ров.** Ayla владеет всей памятью (зоны+шифрование), bot = API-клиент по ayla_user_id. Pilot scope → **~237–257 SP**. **Capacity-конфликт:** Ayla-сторона ≈ 50–70 SP (catalog + memory на одном агенте) → **нужно решение: 2-й Ayla-агент / scope-cut / date move.** Старт Stream 5 гейтит §8 дизайн-дока (EncryptedField / green-consent / fill-rate / global-identity). См. Этап 8.

---

## 1. Шкала Story Points

| SP | Значение | ~время |
|---|---|---|
| 1 | мелкая правка | 0.5 дня |
| 2 | простая | 1 день |
| 3 | средняя | 1–2 дня |
| 5 | сложная | 2–4 дня |
| 8 | крупная | 4–7 дней |
| 13 | очень крупная/рискованная — дробить | — |

SP важнее часов: агенты идут параллельно, но риск/ревью/интеграция всё равно съедают время.

## 2. Календарь (provisional)

| Week | Даты | Фокус | План SP | Milestone |
|---|---|---|---|---|
| **W1** | 02.07–11.07 | Contract Stabilization + event_id | 35 | M1 Contract green · M0 baseline |
| **W2** | 14.07–18.07 | Safety/consent/handoff + event hardening | 30 | M2 Safety green · M3 Event green |
| **W3** | 21.07–25.07 | Catalog bridge + booking prep | 35 | M4 Catalog green |
| **W4** | 28.07–01.08 | Booking REST + marketplace E2E | 35 | M5 Booking green |
| **W5** | 04.08–08.08 | Marketplace + gates + smoke | 20 | M6 Marketplace green · **M7 Pilot-ready (gates green)** |
| **W6** | 11.08–15.08 | Буфер / hardening / deferred | 15–25 | 🚀 **Pilot launch 15.08** |

**Итог pilot MVP ≈ 155–165 SP.** Полная стабилизация до «сильного MVP» ≈ 240 SP (deferred-хвост в W6+ / post-pilot).

## 3. Milestones (done-criteria)

| M | Название | Срок | Готово, когда |
|---|---|---|---|
| **M0** | Delivery baseline | день 1–2 | таблица есть; streams; first-PR queue; freeze rule; DoD — **готово (GAP MAP v1.1)** |
| **M1** | Contract green | конец W1 | AylaUrlBuilder; profile_client fixed; recommendations_client fixed; auth unified; contract tests green |
| **M2** | Safety green | сер./конец W2 | global path через consent; safety pre_check; should_handoff → AdminTask; HUMAN_HANDOFF глушит бота; red-flag тесты green |
| **M3** | Event green | конец W2 | event_id совместим; dedupe/DLQ не падают; allowlist безопасен |
| **M4** | Catalog green | W3 | sync/rebuild из Ayla; ayla_service_id заполнен; ayla_user_id заполнен; health-grounding работает; coverage ≥ порога |
| **M5** | Booking REST green | W4 | slots с service_id; cancel/reschedule идемпотентны; ayla_user_id провижинится; flag готов к flip; E2E booking проходит |
| **M6** | Marketplace-light green | W4–5 | intent → recs; top-3 в чате; recommendation → slots; confirm → booking; сложные → Mini App/handoff |
| **M7** | Pilot ready → **launch** | W5 gates green · **launch 15.08** | закрыты все gates: G-Contract, G-Event, G-Safety, **G-Catalog, G-CalendarSync**, G-Booking, G-Notify; smoke + rollback готовы |

## 4. Master task table

Owner: **BE** = бэкенд (ты / код-агенты) · **FE** = ShiroPy (фронт, всё ревьюим). Pilot: **M**=must-have · **D**=deferred (сильный MVP / буфер). Status: Backlog/Ready/In Progress/In Review/Blocked/Done.

### Этап 0 — Planning baseline (10 SP, 8 done)
| ID | Задача | Repo | Issue | Owner | SP | Week | Gate | Pilot | Status |
|---|---|---|---|---|---|---|---|---|---|
| D0.1 | Утвердить MVP scope (GAP MAP v1.1) | — | — | BE | 2 | W1 | — | M | Done |
| D0.2 | Streams + порядок PR (§11/§12) | — | — | BE | 2 | W1 | — | M | Done |
| D0.3 | Delivery tracker (этот док) | — | — | BE | 3 | W1 | — | M | In Progress |
| D0.4 | DoD per stream/gate | — | — | BE | 3 | W1 | — | M | Done |

### Этап 1 — Contract Stabilization (36 SP) · G-Contract
| ID | Задача | Repo | Issue | Owner | SP | Week | Pilot | Status |
|---|---|---|---|---|---|---|---|---|
| S0A.1 | AylaUrlBuilder в bot-platform | bot | #1049 | agent-s0a | 5 | W1 | M | ✅ Done (PR #1065) |
| S0A.2 | AYLA_BASE_URL host-only validator | bot | #1049 | agent-s0a | 3 | W1 | M | ✅ Done (PR #1065) |
| S0A.4 | s2s-auth settings foundation (INTERNAL/NUTRITION token, AYLA_SERVICE_TOKEN deprecated) | bot | #1050 | agent-s0a | 3 | W1 | M | ✅ Done (PR #1065) |
| S0A.3 | Убрать ad-hoc URL f-строки во всех клиентах → S0-B | bot | #1049 | agent-s0b | 5 | W1/W2 | M | ✅ Done (PR #1071) |
| S0B.1 | fix profile_client path+token (+builder) | bot | #978 | agent-s0b | 3 | W1/W2 | M | ✅ Done (PR #1071) |
| S0B.2 | fix recommendations_client path+token (+builder) | bot | #1048 | agent-s0b | 3 | W1/W2 | M | ✅ Done (PR #1071) |
| S0B.3 | nutrition token alignment + remove AYLA_SERVICE_TOKEN refs | bot | #1050 | agent-s0b | 3 | W1/W2 | M | ✅ Done (PR #1071) |
| S0C.1 | contract tests vs Ayla route-table | bot(+Ayla) | — | BE | 8 | W2 | M | **Ready (unblocked — S0-A+B в dev)** |
| S0C.2 | обновить contract docs/ADR | bot | #1050 | BE | 3 | W2 | M | **Ready (unblocked)** |

### Этап 2 — Event compatibility (23 SP; pilot 13) · G-Event
| ID | Задача | Repo | Issue | Owner | SP | Week | Pilot | Status |
|---|---|---|---|---|---|---|---|---|
| S05.1 | event_id 26→36 (eventbus, 6 колонок) | bot | #1058 | agent-s05 | 5 | W1 | M | ✅ Done (PR #1067) |
| S05.2 | миграция 0009 + тесты dedupe/DLQ/failure + guard→DLQ | bot | #1058 | agent-s05 | 5 | W1 | M | ✅ Done (PR #1067) |
| S05.3 | allowlist check + no_show/revoked | bot | #946 | agent-s05 | 3 | W1 | M | ✅ Done (PR #1067) |
| **S05.6** | **event_id 26→36 кросс-app** (RemoteBookingProxy.last_synced_event_id + Conversation.last_payment_event_id) — завершает #1058 end-to-end | bot | **#1066** | agent-s05 (cross-stream authz ✓) | 2 | W1 | **M (pilot-critical)** | ✅ Done (PR #1070 merged; #1058 + #1066 closed; CI mypy hotfix PR #1073 merged) |
| S05.4 | retention cleanup beat (4 вторичных леджера 120d, chunked) | bot | #1056 | agent-s05 | 5 | (наперёд) | D→pull-forward | ✅ Done (PR #1080; follow-up #1085) |
| S0B.e2e | e2e staging round-trip profile/recs (authenticated, nightly) | bot | #1078 | agent-s0b | 3 | W2 | M | ✅ Done (PR #1079; ops secrets → nightly) |
| S05.5 | double-contact + MasterNotificationPrefs dispatcher (G-Notify) | bot | #1057 | agent-s05 | 5 | (наперёд) | D→pull-forward | **In Progress** |

### Этап 3 — Global MAX safety/consent/handoff (45 SP; pilot 31) · G-Safety · P0
| ID | Задача | Repo | Issue | Owner | SP | Week | Pilot | Status |
|---|---|---|---|---|---|---|---|---|
| S1.1 (S1-A) | consent-гейт на global path (backend) | bot | #1046 | agent-s1 | 5 | W2 | M | ✅ Done (PR #1072) |
| S1.FE | ~~Consent UI ShiroPy~~ → #1046 consent-гейт разговорный (backend) = **agent-s1 (S1-A)**; ShiroPy строго-фронт = **#949** SUPPORT_DEEPLINK | bot | #1046 / #949 | agent-s1 + FE #949 | — | W2 | M | Переназначено |
| S1.2 (S1-B) | safety pre_check до discovery (оба хендлера; global=canned-only) | bot | #1053 | agent-s1 | 5 | W2 | M | **In Review (PR #1084)** — merge gated: (1) founder sign-off кризис-копирайта, (2) **P0 #1081** (regex не ловит «хочу умереть» — сеть sole+live) |
| S1.3 (S1-C) | should_handoff → AdminTask | bot | #1047 | agent-s1 | 5 | W2 | M | **In Progress** |
| S1.4 (S1-C) | HUMAN_HANDOFF: бот молчит | bot | #1047 | agent-s1 | 3 | W2 | M | **In Progress** |
| S1.5 (S1-D) | тесты suicide/red-flag/complaint/human/bookingfail | bot | — | agent-s1 | 8 | W2 | M | **In Progress** |
| S1.6 | de-drift двух MAX-хендлеров | bot | #1053 | BE | 8 | W6 | D | Backlog |
| S1.7 | ConsentRecord → memory_writer | bot | #1054 | BE | 3 | W2 | D | Backlog |
| S1.8 | DOB/is_adult endpoint (Ayla) | Ayla | #202 | BE | 3 | W6 | D | Backlog |

### Этап 4 — Catalog domain rebuild for Ayla booking (50–70 SP) · G-Catalog + G-CalendarSync
> **Рефрейм #1044 (2026-07-03):** не bridge/coverage, а построение canonical bookable-каталога (ServiceTemplate=таксономия → SalonService → SpecialistService). #1043/mysite удаляются. Онбординг «Confirm, don't create». **Статус: At Risk until S3 design locked.** ⚠️ **Не стартовать (Wave 2)** пока: G-CalendarSync decision (Variant A/B) записан · источник данных Пензы подтверждён · Ayla-side breakdown принят. **Owner:** S3A/S3C/S3-CAL — **Ayla-агент (beautygo_backend)**; S3B — bot; см. рекомендацию про отдельный Ayla-стрим.

| ID | Задача | Repo | Issue | Owner | SP | Week | Pilot | Status |
|---|---|---|---|---|---|---|---|---|
| **S3A** | Ayla catalog domain rebuild (ServiceTemplate+ · SalonService · SpecialistService · select-only · stable-id internal API resolved duration/health/review_count · миграции+тесты) | Ayla | #200 (+нов.) | Ayla-agent | 20–30 | W3 | M | Ready* |
| **S3B** | bot catalog mirror from Ayla (key=stable-id; **удалить #1043 + mysite-sync**; #1052 дубль-поле; #1060 review_count; resolved duration/health; KB/event consumers) | bot | #1044 #1052 #1060 | BE | 12–18 | W3/W4 | M | Ready* |
| **S3C** | pilot salon intake + draft confirmation (minimal, 1–2 источника → DraftSalonService → confirm → bookable) | Ayla/admin | (нов.) | Ayla-agent | 8–13 | W3/W4 | M | Blocked (данные Пензы) |
| **S3-CAL** | G-CalendarSync (Variant A Ayla-primary ИЛИ Variant B YClients inbound webhook → external busy intervals; MVP-min inbound) | Ayla+integr | (нов.) | Ayla-agent | 8–15 | W4 | M | Blocked (решение A/B) |
| **S3D** | catalog contract/API tests (stable-id · resolved duration/health · inactive/unconfirmed не в booking · mirror только из Ayla · mysite отсутствует) | оба | (нов.) | BE | 8–13 | W4 | M | Ready* |

*Ready\* = готово к старту после снятия 3 блокеров Wave 2 (G-CalendarSync decision · данные Пензы · breakdown accepted). Полная мульти-источниковая автоматизация intake — **post-MVP**.

### Этап 5 — Booking via Ayla REST (29 SP; pilot 24) · G-Booking
| ID | Задача | Repo | Issue | Owner | SP | Week | Pilot | Status |
|---|---|---|---|---|---|---|---|---|
| S2.1 | slots: service_id обязателен/fallback | bot | #1051 | BE | 5 | W3 | M | Ready |
| S2.2 | idempotency cancel/reschedule | Ayla | #203 | BE | 5 | W4 | M | Ready |
| S2.3 | auto-provision ayla_user_id (global bot) | bot | #1016 | BE | 5 | W4 | M | Ready |
| S2.4 | RemoteBookingProxy consistency | bot | #1016 | BE | 3 | W4 | M | Ready |
| S2.5 | flip-plan BOOKING_VIA_AYLA_REST | bot | #1016 | BE | 3 | W4 | M | Ready |
| S2.6 | E2E confirm→Ayla REST→proxy | bot | #1016 | BE | 8 | W4 | M | Ready |

### Этап 6 — Marketplace-light «умная дискавери» (pilot ≈29 SP; availability/персон. deferred)
> Развилка: персонализация (Фаза 4) конфликтует с consent-гейтом #1046 + зависит от памяти G2 → **post-pilot**. Пилот = «умно, но без личной истории» (relevance+trust+geo+goal/price). Детали — GAP MAP §7 Stream 4. ⚠️ Рост pilot-scope дискавери с ~13 → ~29 SP (учесть как new scope в velocity).

**Фаза 1 — быстрые улучшения (в пилот):**
| ID | Задача | Repo | Issue | Owner | SP | Week | Pilot | Status |
|---|---|---|---|---|---|---|---|---|
| S4.0 | `review_count` в mirror — **prereq trust-score, вынесен в Этап 4 (Catalog)** | — | **#1060** | — | (3) | W3/W4 | M | → Этап 4 |
| S4.1a | синоним-recall перед icontains | bot | #1018 | BE | 2 | W4 | M | Ready |
| S4.1b | Bayesian trust-score | bot | #1018 | BE | 3 | W4 | M | Ready |
| S4.1c | trust-floor (Guardian-lite) | bot | #1018 | BE | 2 | W4 | M | Ready |
| S4.1d | diversity ≤2 мастера/салон | bot | #1018 | BE | 2 | W4 | M | Ready |
| S4.1e | reasoning-текст (шаблон) | bot | #1018 | BE | 2 | W5 | M | Ready |
| S4.1f | fallback пустого результата | bot | #1018 | BE | 2 | W5 | M | Ready |

**Фаза 2 — скоринговый движок + goal/price (в пилот):**
| ID | Задача | Repo | Issue | Owner | SP | Week | Pilot | Status |
|---|---|---|---|---|---|---|---|---|
| S4.2a | единая функция скоринга (relevance/trust/geo/popularity/diversity) | bot | #1018 | BE | 8 | W4 | M | Ready |
| S4.2b | goal/price-aware (MasterServiceOffering→goals/price) + DTO/MKT1 | bot | #1018 | BE | 5 | W5 | M | Ready |
| S4.2c | show_masters: goal/price_max/sort | bot | #1018 | BE | 3 | W5 | M | Ready |
| S4.2d | нить: recommendation → slots → booking | bot | #1020 | BE | 8 | W5 | M | Ready |

**Фаза 3 — availability-aware (fast-follow, зависит от G8):**
| ID | Задача | Repo | Issue | Owner | SP | Week | Pilot | Status |
|---|---|---|---|---|---|---|---|---|
| S4.3a | availability-буст (топ-N + кэш слотов) | bot | — | BE | 8 | W6+ | D | Backlog |
| S4.3b | availability в reasoning | bot | — | BE | 3 | W6+ | D | Backlog |

**Фаза 4 — персонализация + 3 слоя (post-pilot, зависит от G2+согласия):**
| ID | Задача | Repo | Issue | Owner | SP | Week | Pilot | Status |
|---|---|---|---|---|---|---|---|---|
| S4.4a | 3-слойная выдача (Твои/Ayla/Исследовать) | bot | — | BE | 8 | post | D | Backlog |
| S4.4b | персональный буст (память+согласие) | bot | — | BE | 5 | post | D | Backlog |
| S4.4c | кросс-тенантная история (часть G2) | bot | — | BE | — | post | D | Backlog |
| S4.4d | ИИ-reasoning вместо шаблонов | bot | — | BE | 5 | post | D | Backlog |

### Этап 7 — Pilot hardening / gates (30 SP; pilot 18)
| ID | Задача | Repo | Issue | Owner | SP | Week | Pilot | Status |
|---|---|---|---|---|---|---|---|---|
| G.1 | G-Contract check | — | — | BE | 3 | W5 | M | Backlog |
| G.2 | G-Event check | — | — | BE | 3 | W5 | M | Backlog |
| G.3 | G-Safety check | — | — | BE | 5 | W5 | M | Backlog |
| G.4 | G-Booking check | — | — | BE | 5 | W5 | M | Backlog |
| G.5 | G-Catalog check | — | — | BE | 3 | W5 | M | Backlog |
| G.6 | G-Notify check | — | — | BE | 3 | W6 | D | Backlog |
| G.7 | Pilot smoke test script | — | — | BE | 5 | W5 | M | Backlog |
| G.8 | Rollback plan | — | — | BE | 3 | W5 | M | Backlog |

### Этап 8 — Memory Foundation (⚠️ pilot-critical, ~32 SP) · G-Memory
> **Решение founder 2026-07-03: память = ров пилота** (`docs/plans/2026-07-03-MEMORY_FOUNDATION_DESIGN.md`). Ayla владеет ВСЕЙ памятью (зоны 🟢🟡🔴 + шифрование); bot = read/write API-клиент по `ayla_user_id`. BUILD фундамент / ACTIVATE узко (green + surfacing) / PLUG-IN post-pilot. **Supersedes #1055 «post-pilot Вариант B».** ⚠️ **M-A (Ayla) конкурирует за Ayla-агента с catalog S3A.** **Старт гейтит §8 дизайн-дока** (EncryptedField / green-consent / fill-rate / global-identity).

| ID | Задача | Repo | Issue | Owner | SP | Week | Pilot | Status |
|---|---|---|---|---|---|---|---|---|
| M-A1 | zone-тэги на поля + EncryptedField (yellow/red) + миграция | Ayla | (нов.) | Ayla-agent | 5 | W3/W4 | M | Blocked (§8) |
| M-A2 | skip/delete-field/wipe endpoints + RedZoneAccessLog (152-ФЗ) | Ayla | (нов.) | Ayla-agent | 3 | W4 | M | Blocked (§8) |
| M-A3 | internal API read/write personal-context по ayla_user_id (#187, сервис-токен) | Ayla | #187 | Ayla-agent | 5 | W4 | M | Blocked (§8) |
| M-A4 | behavioral-beat (Celery) + метрики fill/answer/usage/skip | Ayla | (нов.) | Ayla-agent | 3 | W5 | M | Blocked (§8) |
| M-B1 | sentinel-tenant ayla_user_id resolve + Ayla context-клиент | bot | #1055 | BE | 5 | W4 | M | Ready (после M-A3) |
| M-B2 | concierge: read→инъекция; конец сессии should_ask→write | bot | (нов.) | BE | 5 | W4/W5 | M | Ready (после M-A3, M-C1) |
| M-B3 | consent-типы memory_green/yellow/red + гейт green | bot | #1046 | BE | 3 | W4 | M | Ready |
| M-C1 | ai-core context_builder → surfacing personal-context в промпт | ai-core | (нов.) | BE | 3 | W4 | M | Ready |
| M-C2 | contextual-extraction WRITE из чата | ai-core | (нов.) | BE | 5 | post | D | Backlog |

### ACK (закрыто)
| ACK.1 | cross-ref docstring #1055 в bot-модели | bot | #1055 | 1 | W1 | ✅ Done (48e078f) |

**Итоги (SP-рамка v1.3 — память в пилот):**
- **Baseline** 155 · **дискавери** +16 · **Stream 3 rebuild** +30–50 · **Stream 5 memory** +32 → **Current pilot scope ≈ 237–257 SP**.
- **08.08 candidate** · **15.08 committed — теперь ВЫСОКИЙ риск** (см. capacity ниже).
- 🔴 **Capacity-конфликт (критично):** Ayla-сторона теперь = catalog S3A (~20–30) + S3C + S3-CAL + #1016-Ayla + **M-A memory (~16)** ≈ **50–70 SP на Ayla**. Один Ayla-агент физически не закроет это к 15.08. **Нужно решение founder:** (а) 2-й Ayla-агент, ИЛИ (б) что режем/двигаем (catalog-minimal? memory ACTIVATE-only без части BUILD? date move?).
- deferred ≈ **100 SP** · полный объём ≈ **320–340 SP**.

## 5. Velocity tracking (заполнять еженедельно)

**Scope baseline (фиксируем на старте):** Baseline 155 SP · New scope +16 (дискавери) · Current pilot 171–177 SP. Любой рост — новая строка в колонке New scope.

| Week | Planned SP | Completed SP | Carried-over | New scope | Blocked | Delta (Compl−Plan) | Статус |
|---|---|---|---|---|---|---|---|
| W1 | 35 | — | — | — | — | — | In Progress (1A: S0-A, S0.5) |
| W2 | 30 | — | — | — | — | — | — |
| W3 | 35 | — | — | +3 (S4.0 #1060) | — | — | — |
| W4 | 35 | — | — | +13 (дискавери Фаза 1/2) | — | — | — |
| W5 | 20 | — | — | — | — | — | — |
| W6 (tail+buffer) | 15–25 | — | — | — | — | — | — |

**Delta-легенда:** Ahead (раньше плана) · On Track · At Risk (риск задержки) · Delayed (уже отстаём) · Blocked.
`Schedule delta = Completed SP − Planned SP` (отрицательный = отстаём на N SP).

## 6. Шаблон еженедельного статуса

```
Неделя N / MVP Pilot
План: X SP · Факт: Y SP · Delta: (Y−X) SP · Статус: On Track / At Risk / ...
Готово: <ID+названия>
Не готово / перенос: <ID>
Блокеры: <что и почему>
Решение: <напр. не запускать Stream 2 до закрытия G-Contract>
Gate-прогресс: G-Contract [ ] G-Event [ ] G-Safety [ ] G-Catalog [ ] G-CalendarSync [ ] G-Booking [ ] G-Notify [ ]
```

## 7. Связанные документы
- [`2026-07-02-MVP_GAP_MAP.md`](2026-07-02-MVP_GAP_MAP.md) — что готово/частично/риск, release gates, freeze rule.
- [`2026-07-02-MVP_SPRINT_PLAN.md`](2026-07-02-MVP_SPRINT_PLAN.md) — разбивка по неделям.
- [`2026-07-02-MVP_AGENT_QUEUE.md`](2026-07-02-MVP_AGENT_QUEUE.md) — очередь промптов для код-агентов.
- `docs/MVP_STATE_AND_ROADMAP.md` (PR #1015) — authoritative roadmap (G1–G10).
