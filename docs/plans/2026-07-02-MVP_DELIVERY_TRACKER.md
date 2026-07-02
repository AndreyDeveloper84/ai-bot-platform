# MVP_DELIVERY_TRACKER_2026-07

> **Назначение:** управляющий контур поверх [`2026-07-02-MVP_GAP_MAP.md`](2026-07-02-MVP_GAP_MAP.md) (v1.1). Здесь — объём (SP), этапы, контрольные даты, milestones, velocity и еженедельный delta «обгоняем / по плану / запаздываем».
>
> **✅ BASELINE (утверждён founder 2026-07-02):**
> - **Цель:** пилот на **Ayla REST = 2026-08-15**. Внутренний target «gates green / pilot-ready» = **08.08 (W5)**; **11–15.08 (W6)** — буфер/hardening перед запуском.
> - **15.07 НЕ фиксируется как Ayla-REST MVP** (нереалистично: P0/P1-блокеры по contract, safety, event_id, catalog bridge, booking flip). Если нужен показ 15.07 — только **демо/legacy YClients path**, без заявления «MVP готов».
> - **Velocity:** 2 параллельных код-агента + ежедневное ревью ≈ **35 SP/нед** (база). 3-й агент **не в базовой скорости** — подключается точечно на независимые задачи (docs / tests / eventbus / catalog audit), т.к. много пересечений bot ↔ Ayla; сначала стабилизируем контракты и safety.
> - **Остаётся оформить:** реконсиляция #1044 (тело «пилот на легаси» → 15.07 = демо, MVP = 15.08 Ayla REST); ACK ownership `UserPersonalContext` (#1055).

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
| **M7** | Pilot ready → **launch** | W5 gates green · **launch 15.08** | закрыты все gates: G-Contract, G-Event, G-Safety, G-Booking, G-Catalog, G-Notify; smoke + rollback готовы |

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
| S0A.1 | AylaUrlBuilder в bot-platform | bot | #1049 | BE | 5 | W1 | M | Ready |
| S0A.2 | AYLA_BASE_URL host-only validator | bot | #1049 | BE | 3 | W1 | M | Ready |
| S0A.3 | Убрать ad-hoc URL f-строки | bot | #1049 | BE | 5 | W1 | M | Ready |
| S0A.4 | Унификация s2s-auth/token | bot | #1050 | BE | 3 | W1 | M | Ready |
| S0B.1 | fix profile_client path+token | bot | #978 | BE | 3 | W1 | M | Ready |
| S0B.2 | fix recommendations_client path+token | bot | #1048 | BE | 3 | W1 | M | Ready |
| S0B.3 | nutrition token alignment | bot | #1050 | BE | 3 | W1 | M | Ready |
| S0C.1 | contract tests vs Ayla route-table | bot(+Ayla) | — | BE | 8 | W1 | M | Ready |
| S0C.2 | обновить contract docs/ADR | bot | #1050 | BE | 3 | W1 | M | Ready |

### Этап 2 — Event compatibility (23 SP; pilot 13) · G-Event
| ID | Задача | Repo | Issue | Owner | SP | Week | Pilot | Status |
|---|---|---|---|---|---|---|---|---|
| S05.1 | event_id 26→36 / ULID agreement | bot(/Ayla) | #1058 | BE | 5 | W1 | M | Ready |
| S05.2 | миграции + тесты dedupe/DLQ/failure | bot | #1058 | BE | 5 | W1 | M | Ready |
| S05.3 | allowlist check + no_show/revoked | bot | #946 | BE | 3 | W2 | M | Ready |
| S05.4 | retention cleanup beat | bot | #1056 | BE | 5 | W6 | D | Backlog |
| S05.5 | double-contact + MasterNotificationPrefs dispatcher | bot | #1057 | BE | 5 | W6 | D | Backlog |

### Этап 3 — Global MAX safety/consent/handoff (45 SP; pilot 31) · G-Safety · P0
| ID | Задача | Repo | Issue | Owner | SP | Week | Pilot | Status |
|---|---|---|---|---|---|---|---|---|
| S1.1 | consent-гейт на global path (backend) | bot | #1046 | BE | 5 | W2 | M | Ready |
| S1.FE | Consent/Welcome UI под маркетплейс | bot | #1046 | FE | 5 | W2 | M | Ready |
| S1.2 | safety pre_check до discovery | bot | #1053 | BE | 5 | W2 | M | Ready |
| S1.3 | should_handoff → AdminTask | bot | #1047 | BE | 5 | W2 | M | Ready |
| S1.4 | HUMAN_HANDOFF: бот молчит | bot | #1047 | BE | 3 | W2 | M | Ready |
| S1.5 | тесты suicide/red-flag/complaint/human/bookingfail | bot | — | BE | 8 | W2 | M | Ready |
| S1.6 | de-drift двух MAX-хендлеров | bot | #1053 | BE | 8 | W6 | D | Backlog |
| S1.7 | ConsentRecord → memory_writer | bot | #1054 | BE | 3 | W2 | D | Backlog |
| S1.8 | DOB/is_adult endpoint (Ayla) | Ayla | #202 | BE | 3 | W6 | D | Backlog |

### Этап 4 — Catalog bridge + health-grounding (34 SP; pilot 18) · G-Catalog
| ID | Задача | Repo | Issue | Owner | SP | Week | Pilot | Status |
|---|---|---|---|---|---|---|---|---|
| S3.1 | catalog rebuild model (Ayla) | Ayla | #200 | BE | 8 | W3 | M | Ready |
| S3.2 | заполнять ayla_service_id / stable-id | bot | #1044 | BE | 5 | W3 | M | Ready |
| S3.3 | заполнять ayla_user_id (masters) | bot | #1044 | BE | 5 | W3 | D | Backlog |
| S3.4 | убрать дубль CatalogMaster.ayla_user_id | bot | #1052 | BE | 3 | W3 | M | Ready |
| S3.5 | canonical-дом requires_health_check | Ayla | #1044 | BE | 5 | W3 | M | Ready |
| S3.6 | coverage check ayla_service_id ≥ threshold | bot | #1044 | BE | 3 | W3 | M | Ready |
| S3.7 | seed каталога Пензы (разово) | Ayla | #1044 | BE | 5 | W3 | D | Backlog |

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
| S4.0 | `review_count` в mirror (поле + событие синка) — prereq trust-score | bot(+Ayla) | — | BE | 3 | W4 | M | Backlog |
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

### ACK / Post-pilot MEM (не в пилоте, P2)
| ID | Задача | Repo | Issue | SP | Когда | Status |
|---|---|---|---|---|---|---|
| #1055 | UserPersonalContext ownership/name collision — declared(Ayla)/inferred(bot), Вариант B | — | #1055 | — | ACK | **ACK latent** |
| ACK.1 | cross-ref docstring в bot-модели (разрешено pre-pilot) | bot | #1055 | 1 | W1–2 | Ready |
| MEM-1 | Define declared vs inferred memory boundary | — | — | 3 | post-pilot | Backlog |
| MEM-2 | Decide end-state A/B (default B) | — | — | 2 | post-pilot | Backlog |
| MEM-3 | Rename / migrate if needed | оба | — | 8 | post-pilot | Backlog |

**Итоги:** pilot must-have ≈ **177 SP** (дискавери-скоуп вырос +16: Stream 4 Фаза 1+2 детализированы) · deferred ≈ **95 SP** · полный объём ≈ **255 SP**.

## 5. Velocity tracking (заполнять еженедельно)

| Week | Planned SP | Completed SP | Carried-over | New scope | Blocked | Delta (Compl−Plan) | Статус |
|---|---|---|---|---|---|---|---|
| W1 | 35 | — | — | — | — | — | — |
| W2 | 30 | — | — | — | — | — | — |
| W3 | 35 | — | — | — | — | — | — |
| W4 | 35 | — | — | — | — | — | — |
| W5 | 20 | — | — | — | — | — | — |
| W6 | 15–25 | — | — | — | — | — | — |

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
Gate-прогресс: G-Contract [ ] G-Event [ ] G-Safety [ ] G-Booking [ ] G-Catalog [ ] G-Notify [ ]
```

## 7. Связанные документы
- [`2026-07-02-MVP_GAP_MAP.md`](2026-07-02-MVP_GAP_MAP.md) — что готово/частично/риск, release gates, freeze rule.
- [`2026-07-02-MVP_SPRINT_PLAN.md`](2026-07-02-MVP_SPRINT_PLAN.md) — разбивка по неделям.
- [`2026-07-02-MVP_AGENT_QUEUE.md`](2026-07-02-MVP_AGENT_QUEUE.md) — очередь промптов для код-агентов.
- `docs/MVP_STATE_AND_ROADMAP.md` (PR #1015) — authoritative roadmap (G1–G10).
