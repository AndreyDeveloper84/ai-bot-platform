# MVP_SPRINT_PLAN_2026-07

> Недельная разбивка пути до пилота. Источник объёма — [`2026-07-02-MVP_DELIVERY_TRACKER.md`](2026-07-02-MVP_DELIVERY_TRACKER.md). **Утверждено:** пилот на Ayla REST = **15.08.2026**; gates-green target 08.08 (W5), 11–15.08 буфер. Velocity = 2 агента ~35 SP/нед (3-й агент — точечно на независимые задачи, не в базе).
>
> Правило: **не открывать следующий stream, пока не закрыт его gate-предшественник** (Freeze rule, GAP MAP §10). Safety закрывается ДО живого booking-пути.

---

## Week 1 — Contract Stabilization + event_id · 02.07–11.07 · ~35 SP
**Фокус:** bot-platform корректно и единообразно ходит в Ayla; событийные колонки совместимы.
**Задачи:** S0A.1–4 (#1049/#1050), S0B.1–3 (#978/#1048/#1050), S0C.1–2, S05.1–2 (#1058).
**Параллель:** BE-agent-1 = S0-A/S0-B; BE-agent-2 = S0.5 (event_id) — разные forbidden-dirs, конфликтов нет.
**Milestone:** M1 Contract green (+M0 baseline готов).
**Exit criteria:** нет ad-hoc URL f-строк; все клиенты через builder + `AYLA_INTERNAL_API_TOKEN`; contract tests green; event_id совместим, dedupe/DLQ тесты зелёные.
**Gate:** G-Contract ✅ (обязательно до любого прод-трафика bot→Ayla).

## Week 2 — Safety/consent/handoff + event hardening · 14.07–18.07 · ~30 SP
**Фокус:** глобальный MAX-бот безопасен; событийный allowlist безопасен.
**Задачи:** S1.1/S1.2/S1.3/S1.4/S1.5 (#1046/#1053/#1047), S1.FE (Consent/Welcome UI — ShiroPy), S05.3 (#946).
**Параллель:** BE = safety-стрим (один стрим, P0, аккуратно); FE (ShiroPy) = consent/welcome UI, ревьюим.
**Milestone:** M2 Safety green · M3 Event green.
**Exit criteria:** global path проходит consent + safety pre_check; should_handoff создаёт AdminTask; HUMAN_HANDOFF глушит бота; red-flag/complaint/human/bookingfail тесты green; allowlist без «эмитится-без-consumer'а».
**Gate:** G-Safety ✅ (до запуска пилотного бота), G-Event ✅ (до flip топиков).

## Week 3 — Catalog bridge + booking prep · 21.07–25.07 · ~35 SP
**Фокус:** bot понимает canonical услуги/мастеров Ayla; подготовка booking.
**Задачи:** S3.1 (Ayla #200), S3.2/S3.4/S3.5/S3.6 (#1044/#1052), S2.1 (#1051).
**Параллель:** BE-agent-1 = catalog rebuild (bot+Ayla); BE-agent-2 = booking prep (slots service_id).
**Milestone:** M4 Catalog green.
**Exit criteria:** sync/rebuild из Ayla (не mysite); ayla_service_id заполнен; health-check дом на `Service`; coverage ≥ порога; slots работают с service_id.
**Gate:** G-Catalog ✅ (до health-grounded booking под флагом ON).

## Week 4 — Booking REST + marketplace E2E · 28.07–01.08 · ~35 SP
**Фокус:** запись создаётся в Ayla, bot хранит proxy; discovery подбирает ранжированно.
**Задачи:** S2.2 (Ayla #203), S2.3/S2.4/S2.5/S2.6 (#1016), S4.1 (#1018).
**Параллель:** BE-agent-1 = booking REST + flip-plan; BE-agent-2 = marketplace discovery→ranked.
**Milestone:** M5 Booking green.
**Exit criteria:** cancel/reschedule идемпотентны; ayla_user_id провижинится; E2E confirm→Ayla REST→proxy проходит; discovery отдаёт ranked top-3.
**Gate:** G-Booking ✅ (до flip `BOOKING_VIA_AYLA_REST`).

## Week 5 — Marketplace + gates + pilot smoke · 04.08–08.08 · ~20 SP
**Фокус:** связать нить пилота end-to-end и пройти gates.
**Задачи:** S4.3 (#1018), S4.6, G.1–G.5, G.7 (smoke), G.8 (rollback).
**Milestone:** M6 Marketplace green · **M7 Pilot-ready (target 08.08)**.
**Exit criteria:** нить пилота зелёная (consent→safety→discovery→top3→slots→Ayla booking→proxy→event→reminder→AdminTask-on-fail); smoke-скрипт проходит; rollback-план готов.
**Gate:** все gates ✅.

## Week 6 — Буфер / hardening / deferred → 🚀 Pilot launch 15.08 · 11.08–15.08 · ~15–25 SP
**Фокус:** поглощение задержек + deferred-хвост; финальный smoke → запуск пилота 15.08.
**Задачи (по остатку):** S05.4 retention beat, S05.5 double-contact dispatcher, S1.6 de-drift handlers, S1.7 ConsentRecord→memory, S1.8 DOB endpoint, G.6 G-Notify, S4.2/S4.4/S4.5.
**Правило:** если W1–W5 идут On Track — часть deferred поднимается в scope; если Delayed — буфер поглощает перенос, deferred уходит в post-pilot.

---

## Карта зависимостей (нельзя нарушать)
```
W1 Contract(G-Contract) ─┬─► W2 Safety(G-Safety) ──► запуск пилотного бота
                         ├─► W2 Event(G-Event) ────► flip топиков доставки
                         └─► W3 Catalog(G-Catalog) ─► W4 Booking(G-Booking) ─► flip BOOKING_VIA_AYLA_REST
                                                       └─► W4-5 Marketplace ─► M7 Pilot-ready
```
Контроль: каждую пятницу — velocity-строка в трекере + еженедельный статус (шаблон §6 трекера).
