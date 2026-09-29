# AYLA — CURRENT ARCHITECTURE & RELEASE STATE

**Дата:** 2026-08-09 (ревизия 2 — после полного Linear reconciliation)
**Автор:** Chief Architect / Release Owner agent
**Базис:** репозитории + runtime local e2e + e2e-artifacts (04–06.08) + canon `ayla-knowledge` @ `ad5047b` + audit-отчёты AGENT_1/2/3 + **Linear (полный доступ через API)**.
**Классификация:** VERIFIED / INFERRED / CLAIMED / UNKNOWN.

> **Ревизия 2 — важная поправка.** Первая ревизия занижала состояние: она опиралась на локальный e2e-стенд и артефакты до 06.08. Linear раскрыл работу 07–08.08 на **пилотном хосте** (`api-dev.gobeauty.site`, compose-проект `ayla-bot-staging`): T-02/T-03/T-05 прошли runtime-приёмку, EventBus **включён и проверен вживую**. Локальный e2e-стенд — отстающая копия, не пилотный контур.

---

## 1. Executive Summary

**Техническая готовность Controlled Pilot достигнута. Оставшийся путь к GO — операционный, и почти весь он на стороне Product Owner.**

- Backend booking-слой доказан (12/12 live, staging validation) — VERIFIED.
- Канальный flow: Channel Gate PASS, регрессия PI-1..6 PASS — VERIFIED.
- **T-02 EventBus: controlled activation PASSED 08.08** на пилотном тенанте formula-tela (`b32a057a-…`): продюсер ровно 4 топика (`booking.created`, `booking.confirmed`, `booking.cancelled`, `appointment.rescheduled`), legacy `booking.rescheduled` исключён, consumer fail-closed с allowlist, HMAC сверен, DLQ=0, mirror сверен на create/confirm/reschedule/cancel — CLAIMED-strong (детальное evidence в DRF-954).
- **T-03**: пилотный BOT на чистом git-baseline (`4406c0c` → `e6460bd`), legacy systemd-юниты отключены, healthz/readyz 200 — CLAIMED-strong (DRF-955).
- **T-05**: privacy runtime-приёмка пройдена (confirmed delete fail-closed, PII стирается, person-level export) — CLAIMED-strong (DRF-956/959).
- **P1 service discovery (DRF-960)**: код + runtime-приёмка пройдены 08.08 23:36 (232 MasterService-ребра, live-диалог «Пенза + спортивный массаж») — CLAIMED-strong.
- **DRF-945 Pilot Readiness Validation: NEEDS_WORK** — техническая приёмка зелёная (P0/P1 = 0/0), блокируют **4 операционных пункта** (раздел 7).

Параллельно канон пережил поворот OD-MVP-1..4 (LDT выведен из critical path), шесть foundation-документов ждут Owner Final Review — не блокирует pilot.

---

## 2. System Map

| Репозиторий | Trunk | Ответственность |
|---|---|---|
| `beautygo_backend` | `dev` @ `566fe19b` (задеплоен на пилотный хост) | SoR: identity, tenants/TUR, Appointment, payments, catalog, personal context, outbox |
| `ai-bot-platform` | `dev` @ `e6460bd` (задеплоен на пилотный хост) | Канальный runtime: MAX ingress, skills, eventbus-consumers, RemoteBookingProxy, reminders |
| `ayla-ai-core` | v0.9.0 (consumers на v0.8.1 — drift) | Reusable AI library |
| `ayla-knowledge` | `main` @ `ad5047b` | Канон |
| `frontAyla` | заморожен с 09.04 | Mobile (вне pilot-контура) |

**Контуры:**
- **Пилотный хост** `api-dev.gobeauty.site` (194.87.99.126), compose `ayla-bot-staging` — BOT `e6460bd`, Backend `566fe19b`, EventBus АКТИВЕН (4 топика, тенант formula-tela), nginx → :8014.
- **Локальный e2e** — отстающая копия (BOT `f9d73af`, EventBus off); использовать для регрессий, не путать с пилотным контуром.
- Деплой BOT — bundle-workaround (DRF-891 пайплайн inert); backend — GitHub Actions по push в dev.
- Production-контур не существует (для пилота не требуется).

Runtime path: MAX → ingress → Redis Stream → worker → skills (keyword dispatch) → booking skill → Backend internal REST → Appointment → Outbox → publisher (HMAC) → BOT ingest → consumers → RemoteBookingProxy + reminders. LLM-оркестратор реализован, production его обходит (P3, T-10).

---

## 3. Source of Truth Map

**CANONICAL:** Constitution 2.2; Journey v1.2; Intent Model v1.0; Conversation Model v1.0 (все — 08.08); Consent Scope Registry v1.2; MVP Reset Roadmap 1.0.

**Candidate, ждут Product Owner Final Review** (переоткрыты OD-MVP-1..4 / AYLA-DEC-0063..0066): Product Essence 1.2, LDT Manifesto 1.1, Product Vision 2.1, Product Thesis 0.6, Product Principles 0.2, **MVP Scope and Release Contract 0.4** (v0.3 мёртв; три downstream-документа ещё запинены на v0.3).

**Draft:** Core Domain Model 1.3, Memory Model 1.0 (Wave 1 locked; Wave 2A +360 строк НЕзакоммичен), TSM 0.2, Capability Registry 1.2, DER 0.4, ADR-0012/13/14, AMD-001/020.

**Дефекты governance:** реестр решений расщеплён (DEC-0001..0025+0035 в Decision Log v1.9 `review`; DEC-0026..0073 в OWNER_DECISION_REGISTER v0.2 `draft`); максимум AYLA-DEC-0073; коллизия «OD-MEM-1..4» (privacy-набор AGENT_3, 05.08) vs «OD-MEM-1..7» (Memory Model, 08.08); корневой MOC `Ayla.md` устарел; AMD-020 нумерация не долечена.

---

## 4. Current Release Target

**Controlled Pilot — 3–5 реальных пользователей** — существует как milestone в Linear (отдельно от «Wave 1 — Booking Foundation», как и требуется). В каноне по имени отсутствует; операционный эквивалент Phase A0/A1. Тенант пилота: **formula-tela**, 4 мастера, 58 услуг, 232 master-service связей.

---

## 5. Current Critical Path (после reconciliation)

Технический остаток минимален; основной остаток — операционный:

**Операционные gap'ы из DRF-945 (владелец):**
1. **Pilot user roster** — списка 3–5 реальных пользователей и способа приглашения нет.
2. **Provider consent** — нет evidence, что салон/мастера formula-tela знают о пилоте и готовы принимать записи.
3. **Support model** — нет формального канала поддержки и времени реакции для пилотных пользователей.
4. **Privacy support runbook** — нет операторского пути для кейсов 502 partial / not_linked deletion (могу написать сам, нужно одобрение).

**Технический остаток (Linear NOW):**
5. **DRF-911** Booking Lookup — прогнать acceptance против активированного baseline (EventBus уже жив; DRF-954 evidence готово).
6. **DRF-925 / DRF-929 (+923/927)** Client Onboarding — consent flow и first booking нового клиента (E1-сценарий, нужен второй MAX-аккаунт).
7. **DRF-943** Targeted Regression / Evidence Reconciliation → **DRF-944 уже Done**.
8. **Re-run DRF-945** → PASS → **DRF-946 Go/No-Go** (решение владельца).

**Гигиена (не блокирует):** 3 несмерженных security-коммита backend (`7c9910d8`, `5392ac83`, `8711af52` — ветка с удалённым upstream); beat-расписание для `cleanup_deleted_users`; закоммитить untracked launch-доки и Memory Wave 2A; подтянуть локальный e2e-стенд до `e6460bd`.

---

## 6. Wave 1 Status (Linear ↔ evidence)

| Задача | Linear | Сверка с evidence |
|---|---|---|
| DRF-910/912/913/914 create/reschedule/cancel/conversation | Done | Совпадает (RB1.x, PI-1..6) |
| DRF-915 Reminders | Done | Совпадает (no dup/backdated, cancel clears, re-peg; #1148 принят как P2) |
| DRF-916 Booking Acceptance E2E | Done | Совпадает (на baseline 4406c0c/566fe19b) |
| DRF-942 Smoke / DRF-944 Staging Validation | Done | Совпадает |
| DRF-954 T-02 / DRF-955 T-03 / DRF-956+959 T-05 | Done | Runtime-приёмка на пилотном хосте, детальные evidence-комментарии |
| DRF-960 Service Discovery P1 | Done | Код + runtime-приёмка 08.08 |
| DRF-911 Lookup | Todo P1 | Готов к прогону |
| DRF-923/925/927/929 Onboarding | Todo | Не начаты |
| DRF-943 Regression reconciliation | Todo | Не начат |
| DRF-945 Readiness | In Progress, NEEDS_WORK | 4 операционных gap'а |
| DRF-946 Go/No-Go | Todo | Корректно не начат |
| Epic Identity (DRF-917..922) | Backlog P2 | Без NOW-метки — на пилот не блокирует (binding по runbook O05/O06) |

---

## 7. Release Blockers

**P0 = 0. Технических release-blocking P1 = 0** (по DRF-945 на baseline BOT `4406c0c`+/Backend `566fe19b`; discovery-блокер закрыт 08.08 на `e6460bd`).

**Блокируют PASS DRF-945 (операционные):** roster пилотных пользователей; provider consent; support model; privacy support runbook. Плюс незакрытые acceptance: DRF-911, DRF-925/929, DRF-943.

**Отдельное внимание владельца — 152-ФЗ юридический контур (DRF-155..159, P1, Backlog):** политика конфиденциальности, ToS, реестр ПДн, юр. заключение. Пилот впускает **реальных** пользователей; техническое стирание данных работает, но юридическая обвязка не сделана и в операционных gap'ах DRF-945 не упомянута. Рекомендую явно решить: принять риск на 3–5 доверенных пользователей или закрыть минимум (политика + согласие) до запуска.

---

## 8. Canon vs Implementation Drift

1. `BOOKING_VIA_AYLA_REST` default=False vs AYLA-DEC-0036 — на пилотных стендах ON; решение о дефолте до Public MVP (T-13).
2. Journey v1.2 CANONICAL, но ссылается на Twin-требования, отменённые MVP Scope v0.4 / OD-MVP-1 — закрыть при Owner Final Review.
3. Три документа запинены на MVP Scope v0.3.
4. Дублирующий Conversation SoR (backend `ai.*` vs bot `apps/conversations`) — P3, решение OD-IC-1 (рекомендация: bot authoritative для MVP).
5. `ayla-ai-core` v0.9.0 не доставлен consumers (оба на v0.8.1).
6. Intent: бот 6 интентов vs registry 11+UNKNOWN; production по keyword, LLM-router не подключён.
7. Memory Model Wave 2A незакоммичен; AMD-нумерация; расщеплённый реестр решений; устаревший MOC.
8. Второй outbox в nutrition (параллельная реализация).
9. Linear-гигиена: задачи с [CANCELLED] в заголовке, но в состоянии Backlog (DRF-44, DRF-54, DRF-126, DRF-164) — перевести в Canceled.

---

## 9. Cross-repo Contracts

**Стабильно:** outbox envelope (ADR-0009-реализация; сам ADR в канон не влит — OD-REQ-3); HMAC ingest-контракт (жив в production с 08.08); internal Bearer REST booking API; Personal Context API v1.0 FROZEN; intent-контракты 0.5/1.0.
**Дефицит:** TENANCY_CROSS_REPO_CONTRACT (закрыт для пилота allowlist-вариантом B); reconciliation job для mirror (G5, backlog); версии ai-core ↔ consumers; контракт Conversation SoR.

---

## 10. Linear Reconciliation (полный)

Team DRF, проект Ayla, активный Cycle 20 (заканчивается 09.08). 94 открытых задачи. Классификация:

- **A. CURRENT PILOT (открытые):** DRF-911, DRF-925, DRF-929, DRF-923, DRF-927, DRF-943, DRF-945, DRF-946 + операционные gap'ы. Epic'и DRF-904/905/906/909 In Progress.
- **B. NEXT MVP (MVP v2 — Core Value Loop):** onboarding-эпики мастера/салона (DRF-907/908 + дети), DRF-13 (AI-чат), DRF-143 (Food Scanner), DRF-230 (Memory Foundation), DRF-926/928 (Goal Collection / First Recommendation — корректно вынесены из пилота), DRF-957 (Food Scanner experimental admission — NOT READY, не блокирует core GO).
- **C. LATER:** DRF-146/147/151 (аватар), DRF-185 (платежи), DRF-226 (PersonalizationEngine Phase 6), M5-ретеншен трекеры.
- **D. ALREADY DONE:** — стейл-задач «сделано, но открыто» не обнаружено; свежие Done подтверждены evidence-комментариями. Наоборот, Linear оказался **актуальнее** локальных артефактов.
- **E/F. SUPERSEDED/OBSOLETE:** legacy-милстоуны M1–M5 (Figma/BeautyGO-эра: DRF-30/48/58/67/116/118 и др.) — большинство относится к мобильному приложению, замороженному с апреля; кандидаты на пересмотр после пилота. [CANCELLED]-задачи в Backlog — закрыть.
- **G. UNKNOWN:** DRF-241 ([Phase A.6] REST /ai/chat поверх AIConcierge, Todo C20), DRF-206 (Infrastructure Setup), DRF-184/58 (Anonymous access + Gate, In Progress C20) — активные задачи вне pilot-scope; уточнить, зачем в Cycle 20.
- **152-ФЗ (DRF-155..159)** — см. раздел 7.

MCP: сервер `linear-api` зарегистрирован в `.mcp.json` (API-ключ), заработает со следующей сессии; в этой — прямой GraphQL.

---

## 11. Risks

1. Evidence T-02/T-03/T-05 — CLAIMED (комментарии Linear, детальные и согласованные с git SHA); независимая повторная проверка пилотного хоста мною не выполнялась (нет ssh из этой сессии).
2. Один HMAC-секрет на инсталляцию (ограничение варианта B) — принятый риск с митигациями.
3. Rollback описан (снять топики + allowlist), но не отрепетирован после активации.
4. 152-ФЗ юридическая обвязка (раздел 7).
5. Bus factor: ручной bundle-деплой, один оператор.
6. Governance-хаос реестра решений — риск потери решений в следующих canon-волнах.
7. Локальный e2e-стенд отстаёт от пилотного контура — риск ложных выводов при будущих проверках (эта ревизия — пример).

---

## 12. Recommended Next Actions

1. **Владелец:** закрыть 4 операционных gap'а DRF-945 — roster, provider consent, support model (мне: написать privacy support runbook — могу сделать сам).
2. **Владелец:** решение по 152-ФЗ минимуму для пилота.
3. Прогнать DRF-911 lookup acceptance (техническая работа).
4. Прогнать DRF-925/929 onboarding acceptance (нужен второй MAX-аккаунт).
5. DRF-943 targeted regression → re-run DRF-945 → PASS.
6. **DRF-946 Go/No-Go packet** владельцу (Scope / Evidence / Risks / Blockers / Monitoring / Rollback / Recommendation).
7. Гигиена (параллельно): security-коммиты в dev; beat для hard-delete; закоммитить launch-доки и Memory Wave 2A; подтянуть локальный e2e; закрыть [CANCELLED]-задачи; Owner Final Review шести foundation-документов.

---

## 13. Owner Decisions Required

1. **Операционные gap'ы DRF-945** (roster, provider consent, support model) — блокируют pilot.
2. **152-ФЗ минимум для пилота** — принять риск или закрыть политику+согласие до запуска.
3. **Product Owner Final Review** шести foundation-документов v0.4-пакета — блокирует canon/NEXT, не pilot.
4. OD-REQ-3 (ADR-0009 в канон), развод нумерации OD-MEM-* — canon-гигиена.
5. Судьба legacy-милстоунов M1–M5 и мобильного трека — после пилота.

---

## 14. GO / NO-GO State

**NO-GO сегодня — но уже не по техническим причинам.** P0/P1 = 0/0, EventBus жив, privacy принята, discovery починен и принят. До GO осталось: 4 операционных решения владельца + 3 acceptance-прогона (lookup, onboarding, regression) + re-run DRF-945 + формальный Go/No-Go packet (DRF-946). При закрытии операционных пунктов это 1–2 фокусных дня.

После GO — наблюдение (§33): errors, booking consistency, EventBus/DLQ, reminders, false success → evidence для NEXT MVP loop (Goal → Signal → Context → Recommendation → Action → Memory → Progress; Food — первый signal).
