# MVP_SPRINT_PLAN_2026-07

> Недельная разбивка пути до пилота. Источник объёма — [`2026-07-02-MVP_DELIVERY_TRACKER.md`](2026-07-02-MVP_DELIVERY_TRACKER.md). **Утверждено:** пилот на Ayla REST = **15.08.2026** (committed launch); **08.08 (W5) = gates-green candidate**. Velocity = 2 агента ~35 SP/нед (3-й точечно, не в базе). **SP-рамка:** baseline 155 + new scope +16 (дискавери) = current pilot **~171–177**; W6 = must-have tail + buffer.
>
> Правило: **не открывать следующий stream, пока не закрыт его gate-предшественник** (Freeze rule, GAP MAP §10). Safety закрывается ДО живого booking-пути.

---

## Week 1 — Contract Stabilization + event_id · 02.07–11.07 · ~35 SP
**Фокус:** bot-platform корректно и единообразно ходит в Ayla; событийные колонки совместимы.
**Задачи:** Волна **1A** — S0-A builder+auth (#1049/#1050) ‖ S0.5 event_id (#1058); Волна **1B** — S0-B перевод клиентов (#978/#1048) **после** S0-A; Волна **1C** — S0-C contract tests (конец W1 / начало W2, после S0-A+S0-B).
**Параллель (база 2 агента):** 1A: BE-agent-1=S0-A, BE-agent-2=S0.5; затем 1B: BE-agent-1→S0-B. **S0-B зависит от builder S0-A — НЕ параллелить с S0-A** (иначе конфликт по файлам клиентов).
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

## Week 3–4 — Catalog domain rebuild + G-CalendarSync + booking prep · 21.07–01.08 · ~70 SP (2 недели, cross-repo)
> **Рефрейм #1044:** Stream 3 вырос до 50–70 SP и стал cross-repo (тяжёлая Ayla-сторона) → растянут на W3+W4. **Не стартовать пока не закрыты 4 условия** (G-CalendarSync decision · данные Пензы · Ayla-side breakdown accepted · S3 design locked). Нужен **отдельный Ayla-агент** на S3A/S3C/S3-CAL.
**Фокус:** построить canonical bookable-каталог Ayla (ServiceTemplate→SalonService→SpecialistService) + защита от двойной брони.
**Задачи:** **S3A** Ayla domain (#200), **S3B** bot mirror re-key (#1044/#1052/#1060, удалить mysite+#1043), **S3C** pilot salon intake (draft→confirm), **S3-CAL** G-CalendarSync (Variant A/B), **S3D** contract tests; параллельно booking prep S2.1 (#1051).
**Параллель:** **Ayla-agent** = S3A + S3C + S3-CAL (beautygo_backend); BE-agent = S3B (bot mirror) + S2.1; discovery Фаза 1 (S4.1a–d) стартует только после S3B (нужен review_count/stable-id).
**Milestone:** M4 Catalog green + **M4-CAL CalendarSync green**.
**Exit criteria:** bot mirror по Ayla stable-id (mysite удалён); confirmed SpecialistService bookable с resolved duration/health; slots учитывают внешнюю занятость.
**Gate:** **G-Catalog ✅ + G-CalendarSync ✅** (оба до G-Booking).

## Week 4→5 — Booking REST + discovery · перекрытие с W4 · ~35 SP
**Фокус:** запись создаётся в Ayla, bot хранит proxy; discovery подбирает ранжированно.
**Задачи:** S2.2 (Ayla #203), S2.3/S2.4/S2.5/S2.6 (#1016), **S4.1a–d + S4.2a** (Pilot Discovery Ranking, #1018).
**Milestone:** M5 Booking green.
**Exit criteria:** cancel/reschedule идемпотентны; ayla_user_id провижинится; E2E confirm→Ayla REST→proxy; нет двойной брони (G-CalendarSync); discovery отдаёт ranked top-3.
**Gate:** G-Booking ✅ (до flip `BOOKING_VIA_AYLA_REST`; требует G-Catalog + G-CalendarSync).

## Week 5 — Discovery Фаза 1/2 финиш + gates + pilot smoke · 04.08–08.08 · ~20 SP
**Фокус:** связать нить пилота end-to-end и пройти gates.
**Задачи:** **S4.1e/f + S4.2b/c/d** (#1018/#1020) — завершение Pilot Discovery Ranking Фазы 1+2, G.1–G.5, G.7 (smoke), G.8 (rollback).
**⚠️ НЕ в пилот:** **S4.3 (availability-aware) = fast-follow**, не начинать до готовых slots/cache — иначе утащит MVP в задержку. S4.4 (персонализация, 3 слоя) — post-pilot.
**Milestone:** M6 Discovery green · **M7 Pilot-ready candidate (gates green, target 08.08)**.
**Exit criteria:** нить пилота зелёная (consent→safety→discovery→ranked→slots→Ayla booking→proxy→event→reminder→AdminTask-on-fail); smoke-скрипт проходит; rollback-план готов.
**Gate:** все gates ✅ (candidate). Хвост must-have (если есть) → W6 до committed launch.

## Week 6 — Буфер / hardening / deferred → 🚀 Pilot launch 15.08 · 11.08–15.08 · ~15–25 SP
**Фокус:** поглощение задержек + deferred-хвост; финальный smoke → запуск пилота 15.08.
**Задачи (по остатку):** хвост must-have до committed launch + deferred: S05.4 retention beat, S05.5 double-contact dispatcher, S1.6 de-drift handlers, S1.7 ConsentRecord→memory, S1.8 DOB endpoint, G.6 G-Notify, S4.3a/b availability (fast-follow), S4.4a–d персонализация (post-pilot).
**Правило:** если W1–W5 идут On Track — часть deferred поднимается в scope; если Delayed — буфер поглощает перенос, deferred уходит в post-pilot. W6 = **must-have tail + buffer** (не чистый буфер).

---

## Карта зависимостей (нельзя нарушать)
```
W1 Contract(G-Contract) ─┬─► W2 Safety(G-Safety) ──► запуск пилотного бота
                         ├─► W2 Event(G-Event) ────► flip топиков доставки
                         └─► W3-4 Catalog rebuild(G-Catalog) + CalendarSync(G-CalendarSync)
                                   └─► W4-5 Booking(G-Booking) ─► flip BOOKING_VIA_AYLA_REST
                                             └─► W5 Discovery ─► M7 Pilot-ready (15.08, At Risk)
   ⚠ Wave 2/S3 gated: G-CalendarSync decision · данные Пензы · Ayla-side breakdown · S3 design locked
```
Контроль: каждую пятницу — velocity-строка в трекере + еженедельный статус (шаблон §6 трекера).
