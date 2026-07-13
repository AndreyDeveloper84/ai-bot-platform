# Admin surface — consolidation & gap-closure spec

> **Статус:** консолидация зафиксированного дизайна + вердикты по пробелам, 2026-07-13.
> **Тип:** это НЕ новый дизайн. Admin — самая зрелая поверхность приложения (8/9 экранов боевые, ~2800 строк handoff-дизайна). Этот док сводит существующие handoffs в screen-спек-слой (как у всех прочих поверхностей в `docs/screens/`), сверяет их с фактическим кодом + ADR-0009/ребрендом и выносит вердикт по реальным пробелам.
> **Поверхность:** `apps/miniapp/src/screens/admin/*` — 9 экранов (Ayla Pro Mini App, team-режим). Web-dashboard-паритет — вне этого дока.
> **Комплемент:** `master-solo-surface.md` покрывает solo (owner+admin+master в одном); этот спек — **team-половина** (Owner/Admin управляют другими мастерами). **Receptionist = conversations-роль, не management** — его приёмная поверхность post-pilot; см. §2a (Option A′, P2-team).

---

## 0. Чем этот док является и не является

- **Является:** индексом источников правды + reconcile-слоем + вердиктами build/defer по disabled-контролам + спецификацией 2 действительно непостроенных поведений + фиксацией одного латентного код-бага.
- **НЕ является:** переписыванием дизайна MM1–MM5 / internal-chat / settings-hub. Их per-screen layout, state-machine, audit-события и backend-контракты **уже зафиксированы** в handoffs (§1) — дублировать нельзя.

---

## 1. Источники правды (mapping экран → handoff → код)

| Admin-экран (код) | Route | Дизайн-источник | Зрелость кода |
|---|---|---|---|
| `AdminTeamScreen` | `/admin/team` | master-management **MM1** (roster) | боевой |
| `AdminInviteMasterScreen` | `/admin/team/invite` | master-management **MM2** (invite modal) | боевой |
| `AdminMasterDetailScreen` | `/admin/team/:masterId` | master-management **MM3** (detail/edit) | боевой |
| `AdminServicesMatrixScreen` | `/admin/services` | master-management **MM4** (services×masters) | боевой (самый полный) |
| `AdminDeactivationFlowScreen` | `/admin/team/:masterId/deactivate` | master-management **MM5** (4-step reassign) | боевой (12-action reducer) |
| `AdminAvailabilityRequestsScreen` | `/admin/availability-requests` | schedule-management S6 change-request + decisions-log **Q-M6** | боевой (единственный настоящий skeleton) |
| `AdminInternalChatListScreen` | `/admin/internal-chat` | master-admin-internal-chat **§4.1** | боевой |
| `AdminInternalChatThreadScreen` | `/admin/internal-chat/threads/:threadId` | master-admin-internal-chat **§4.2–4.8** | боевой (частично, см. §5) |
| `AdminSettingsPlaceholderScreen` | `/admin/settings` | settings-hub **SH1–SH4** | **placeholder** (by design, см. §5.1) |

**Handoff-файлы (читать как канон перед правкой admin-кода):**
- `docs/design/handoffs/2026-05-18-master-management-handoff.md` — MM1–MM5, state-machine мастер-записи, audit-события, backend-контракты.
- `docs/design/handoffs/2026-05-19-master-admin-internal-chat-handoff.md` — обе стороны team-чата.
- `docs/design/handoffs/2026-05-18-settings-hub-handoff.md` — SH1 homepage, SH2 audit, SH3 notifications, SH4 policy.
- `docs/design/handoffs/2026-05-18-schedule-management-handoff.md` — где живёт редактирование графика (MM3 линкует, не дублирует; §H).
- `docs/adr/ADR-0008-role-detection-and-staff-model.md` — модель ролей/staff.
- `docs/design/policies/solo-provider-ux.md §6` — team surface (комплемент solo).
- Permission-контракт — **два слоя, не путать:**
  - **Enforced (канон для реализации):** backend-декоратор `apps/admin_api/auth.py` (пускает `owner|admin`, всё прочее 403) + тесты `admin_api/tests/`, `internal_chat/tests/`. Это единственный действующий контракт.
  - **Design-intent (высокоуровневый):** `tenant-as-provider-model §2.10` даёт лишь 4 буллета ролей (Owner / Admin / Receptionist / Master) и **сам отсылает** к `master-mobile-handoff §8` + `master-management-handoff` matrix. §2.10 — НЕ детальная матрица; deprecated `conversation-ownership-policy §4` заменён именно этими handoff-матрицами, а не §2.10.

---

## 2. Зафиксированные решения (из master-management §1 — не переоткрывать)

| # | Решение | Влияние на код |
|---|---|---|
| A | Primary surface = web-dashboard (плотность); Mini App = паритет/mobile | admin Mini App — вторичен; часть плотных экранов (settings) уходит в web |
| B | Invite = MAX bot-DM deeplink + magic-link (без пароля/SMS); email-fallback **deferred** | объясняет disabled email-radio в MM2 (§4) |
| C | Soft-archive: деактивация не удаляет; `is_active=False`+`archived_at`; reactivate 1-click | MM1/MM3/MM5 |
| D | Photo 500×500 ≤5MB JPG/PNG; initials-fallback server-side; фото **опционально** | MM3 photo; delete — backend-пробел (§4) |
| E | Фикс 4 роли (Owner/Admin/Receptionist/Master); кастомные — post-MVP | объясняет disabled role-radios (§4) |
| F | Services-master = matrix UI на MM4; влияет на bot `show_slots`/`suggest_master` | MM4 |
| G | Деактивация → обязательный reassignment (MM5) или mass-cancel с шаблоном; без silent orphaning | MM5 |
| H | **Schedule = link, не edit**: MM3 линкует в Schedule Management, не дублирует | ⚠️ код держит «Изменить график» disabled вместо navigate — см. §4 |

---

## 2a. 🟡 P2 (team, НЕ pilot) — Receptionist = conversations-роль, а не management-роль

**Решение зафиксировано 2026-07-13 (Option A′, founder + tech-lead).** Исходно это было сформулировано как «backend ошибочно 403-ит receptionist» — заземление показало, что **backend прав**, а рамка была неверной.

**Receptionist по capability-модели — роль приёма/переписки, не управления.** `apps/identity/services/role_resolver.py:172` даёт ему набор целиком про conversations:
`view_conversation_list_all · view_customer_phone_audited · send_reply_all · promote_human_locked · snooze_conversation_all · escalate_csm` — **ни одной** management-способности. Его дизайн-«дом»: `role_resolver.py:68` default_landing = `/admin/conversations` (не `/admin/team`).

**Две разные поверхности — не путать:**

| Поверхность | Гейт | Receptionist |
|---|---|---|
| Team-management (`admin_api`, MM1–MM5) | `owner\|admin` (`auth.py:147`) | **корректно исключён** — нет management-capabilities. Backend ПРАВ. |
| Conversations/приём (`/admin/conversations`) | должен быть capability-based | его дом **по дизайну** |

**Реальный дефект — узкий, фронтовый:** `App.tsx:1104` сваливает `is_receptionist` в management-`hasAdmin` → роутит в `<AdminRoutes>`→`/admin/team` (чужой дом) → 403-стена на каждый вызов. Зафиксировано тестами (`admin_api/tests/test_master_views.py:102`; `internal_chat/tests/test_admin_views.py:66`).

**⚠️ Результат обязательной проверки (2026-07-13): приёмной поверхности НЕ существует.**
- Фронт монтирует только `/master/conversations` (master-only) и `/admin/internal-chat`; экрана клиентских переписок для admin/receptionist **нет**.
- Capability-набор определён и отдаётся в `/me` (`identity/views.py:126`), но **ни одна вьюха его не потребляет** — реального capability-гейта нет.
- Фронт роутит по булям ролей; `default_landing` из `/me` **не читает** → `/admin/conversations` сейчас мёртвая метаданность.

**Значит вариант «построить read-only management-матрицу» отвергнут** (строил бы не то — receptionist не нужны management-экраны). Принятый путь:

1. **Пилотный фикс (маленький, FE):** снять `is_receptionist` из management-`hasAdmin` (`App.tsx:1104`) и роутить receptionist в **безопасный fallback** (`NoRoleScreen`-стиль «скоро / веб»), **НЕ** в несуществующий `/admin/conversations`. Убирает 403-стену.
2. **Backend management:** оставить `owner|admin` как есть — уже корректно.
3. **Post-pilot эпик:** построить приёмную поверхность receptionist — FE-роут `/admin/conversations` + экран + backend, **потребляющий** `view_conversation_list_all` / `view_customer_phone_audited` (с audit-on-reveal из ADR-0008 TODO `role_resolver.py:121`).
4. **MM1 read-only roster** для receptionist (из handoff) — опционально, часть post-pilot эпика; для работоспособности роли не требуется.

**Приоритет: 🟡 P2-team, НЕ пилот-блокер** — solo/YClients-пилот receptionist'а не содержит (solo = один активный человек). Admin management-поверхность боевая для **Owner/Admin**; receptionist-приём — post-pilot.

---

## 3. Drift-reconcile vs ADR-0009 / ребренд (handoffs датированы 2026-05-18/19 — ДО ADR-0009 05-20)

Handoffs верны по существу, но содержат устаревшие foundation-ссылки. Фиксируем, чтобы имплементатор не пошёл по мёртвым ссылкам:

| Устаревшая ссылка в handoff | Актуально |
|---|---|
| master-management §2 → `project_single_assistant_identity` («master's identity is still the single assistant») | **DEPRECATED 2026-05-19** → `[[project_ayla_personal_ai]]`. Суть сохраняется: admin-UI **внутренний**, клиент не видит «Master X joined»; но формулировка «single assistant» неактуальна. |
| master-management «permission matrix → `conversation-ownership-policy §4`» | §4 deprecated 2026-05-19. Design-матрица переехала в `master-mobile-handoff §8` + `master-management-handoff` (`tenant-as-provider §2.10` — лишь высокоуровневый указатель на них, см. §2). **Действующий** гейт — `admin_api/auth.py` = `owner|admin` только. ⚠️ Receptionist «read-only» из handoffs бэкендом НЕ реализован (§2a). |
| Общий тон «admin = Ayla Pro provider tool» | Соответствует ADR-0009: это AI/runtime-поверхность bot-platform, не транзакционный домен Ayla. Никаких прямых записей в booking/payment/catalog Ayla — admin-мутации идут через bot-platform admin-эндпоинты (`/api/v1/admin/*`), не в таблицы Ayla. |

**Вывод:** дизайн-решения остаются в силе; правки — только косметические foundation-ссылки в handoffs (оставляю владельцам handoff-доков, anti-touch), но screen-спек фиксирует актуальную матрицу как канон.

---

## 4. «Скоро»-контролы — вердикт build / defer

Все disabled-контролы гейтятся рантайм-ролями + hardcoded `disabled` с тултипом «Скоро» (feature-флагов `VITE_*` в admin-коде **нет**). Каждый сверен с зафиксированным решением:

| Контрол (экран) | Тултип | Спроектирован? | Вердикт | Обоснование |
|---|---|---|---|---|
| «Изменить график» (MM2 invite, MM3 detail) | «Доступно после создания» / disabled | ДА — Decision **H** («link, не edit») | **Отдельная зависимость (НЕ дёшево)** | ⚠️ Целевого admin-маршрута расписания в Mini App **нет** (`App.tsx` монтирует только `/master/schedule` и `/solo/schedule`, не `/admin/schedule`). Разбить: (1) MM2-form — оставить disabled; (2) MM2-success/MM3 — включать navigate **только после** появления admin schedule-route; (3) сам route + проверка доступа к чужому расписанию — отдельная задача (backend+frontend), не «wire link». |
| «Эффективность» секция (MM3) | «Скоро — нужна аналитика-секция backend» | ДА — §2 ref: LINK на analytics-dashboard `?master_id=` | **Build (wire link) ИЛИ defer** | Если analytics-dashboard существует — это LINK, не stub. Если dashboard не в пилоте → defer явно. |
| Смена роли (Owner/Admin/Receptionist radios, MM2/MM3) | «Скоро — для Owner» | Частично — Decision **E** (4 роли фикс) | **Defer (post-pilot)** | Назначение одной из 4 ролей — валидно, но team-only. Solo-пилот → N/A. Kept disabled OK. |
| Смена контакта / email-radio (MM2/MM3) | «Скоро — высокий риск» / «пока используйте MAX» | Decision **B** (email-fallback deferred) | **Defer (post-pilot)** | Консистентно с B. Смена контакта = высокий риск (перенос magic-link identity) — сознательно отложено. |
| Delete фото (MM3) | disabled — нет DELETE endpoint | Decision **D** (initials-fallback) | **Defer (backend-gated)** | Нужен `DELETE …/photo/`. Низкий приоритет — replace покрывает 95% случаев. |
| Resend / cancel invite (MM3) | «нужен бот-DM трекинг» | НЕТ (MM2 покрывает только issue) | **Defer (post-pilot)** | Не в зафиксированных решениях. Обходной путь: повторный invite. v1.1. |
| «Открыть профиль» на success (MM2) | «Доступно после MM3 detail screen» | — | **Build (stale tooltip)** | MM3 **уже существует** — тултип устарел. Просто включить navigate → `/admin/team/:id`. |

**Пилотный срез:** для solo/YClients-пилота в Пензе (solo = owner+admin+master в одном) единственный дешёвый build-пункт — **stale «Открыть профиль» tooltip** (§4, MM3 уже существует). Schedule-link — НЕ дешёвый (отдельная зависимость, выше). Остальное — team-функции post-pilot. Реальную (узкую) точку входа solo в admin-поверхность см. §8.

---

## 5. Действительно непостроенные спроектированные поведения

### 5.1 Settings — namespace mismatch (НЕ баг, зафиксировать как by-design)

- settings-hub проектирует богатый `/settings/*` набор (SH1 homepage / SH2 audit / SH3 notifications / SH4 policy + cross-module роуты) — **web-dashboard-first** (Decision A).
- Код монтирует `AdminSettingsPlaceholderScreen` на `/admin/settings` (Mini App) — чистый ComingSoon «Скоро здесь будут настройки салона».
- **Вердикт: консистентно.** Rich settings живут в web-dashboard; Mini App-таб — намеренный placeholder, чтобы у нижнего таба был destination во время MM5-раскатки. Docblock экрана прямо это заявляет.
- **Пробел (мелкий):** placeholder не имеет loading/empty/error (статичный render) — это ок для ComingSoon.
- **Решение (зафиксировано, единое — устраняет ранний разнобой):** rich settings (SH1 homepage / SH2 audit / SH4 policy + billing/loyalty/integrations) остаются **web-dashboard-only**. Mini App-таб получает **максимум** own-account + own-notifications (SH3 own) — и то опционально. **Полный Mini App parity settings — НЕ build-пункт этого спека, а отдельный post-pilot эпик** со своим scope и контрактами. В итоге (§10) фигурирует именно так, не как «rich Mini App settings».

### 5.2 Internal-chat resolve-действия (§4.2 / §4.6) — спроектированы, НЕ построены

`AdminInternalChatThreadScreen` имеет close-thread + sign-as-self + optimistic send + 409-recovery, но НЕ имеет topic-typed resolve-действий из handoff:

| Handoff-поведение | Построено? | Вердикт |
|---|---|---|
| §4.2/§4.6 «закрыть спор» (earnings_dispute → resolve `EarningDispute` + close) | ❌ только read-only linked-label | **Build post-pilot** (нужен EarningDispute domain — вероятно Ayla) |
| §4.2/§4.6 «согласовать отпуск» (leave_request → approve/reject `MasterLeaveRequest` + close) | ❌ | **Build post-pilot** (пересекается с AvailabilityRequests — не дублировать) |
| §4.6 review_concern → mark «addressed» | ❌ | Defer post-pilot |
| §4.3 multi-admin assignment («закреплено за …») | ❌ | Defer (team-only, solo N/A) |
| §4.4 topic-tag override | ❌ | Defer |
| §4.8 auto-close inactive (14d) | ❌ (беклог-задача) | Defer (backend beat) |

**Пилот:** internal-chat целиком team-функция ([[project_cross_doc_buttons_post_pilot]] — master↔admin через internal-chat). Для solo-пилота — **N/A**; resolve-действия строим post-pilot вместе с доменами EarningDispute / MasterLeaveRequest (проверить ownership по ADR-0009 — вероятно Ayla-домен, тогда bot вызывает REST).

---

## 6. Латентный код-баг (найден при заземлении — вынести в follow-up)

`AdminAvailabilityRequestsScreen`: `useMemo` (`reasonCount`) вызывается **после раннего `return`** → условный хук, нарушение rules-of-hooks (React может рассинхронить порядок хуков между рендерами). Плюс результат `reasonCount` тут же `void`-ится (мёртвый код, «retained for future inline counts»).

**Вердикт:** PRE_MERGE-класс для React-корректности, но не эксплойт/не биллинг → **FOLLOW_UP**. Фикс: поднять `useMemo` над всеми ранними `return` либо удалить мёртвый memo. Вне scope UX-спека — завести код-тикет.

---

## 7. Аудит состояний (house-standard: skeleton / empty / error / content / submitting)

Из 8 боевых экранов состояния покрыты, но **настоящий skeleton** есть только у `AvailabilityRequests` и `InternalChatList` (`ListSkeleton`); MM1–MM4 используют текстовые callout'ы («Загружаем команду…»). Это косметический долг, не пробел функциональности.

| Экран | skeleton | empty | error | submitting | 409-recovery |
|---|---|---|---|---|---|
| MM1 Team | текст | ✅ filter-aware | ✅ StateError | ✅ | — |
| MM2 Invite | — (soft-fail) | n/a | ✅ inline+banner+offline | ✅ | idempotent |
| MM3 Detail | текст | 404 | ✅ | ✅ per-field | ✅ 409 |
| MM4 Matrix | текст | ✅ 2 вида + filtered | ✅ | ✅ | ✅ 409 preserve-edits |
| AvailabilityRequests | ✅ real | ✅ filter-aware | ✅ | ✅ | overlap-conflict |
| MM5 Deactivation | текст | by-design skip | ✅ | ✅ | ✅ 409 bounce |
| InternalChatList | ✅ real | ✅ | ✅ 403 no-retry | n/a | — |
| InternalChatThread | текст | ✅ empty-thread | ✅ 404/403/generic | ✅ optimistic | ✅ 409 reload |

**Вердикт:** консистентность skeleton'ов — **NICE-to-have post-pilot** (заменить текстовые callout'ы на `Skeleton` в MM1–MM4). Не блокер.

---

## 8. Пилотная релевантность (честно)

Solo/YClients-пилот, Пенза. `isSolo = me.is_solo_provider === true` истинно **только при ровно одном активном человеке** в тенанте (`App.tsx:1110`). Как только появляется 2-й активный человек — тенант уже НЕ solo, и роутинг переключается на team (`UnifiedAdminMasterRoutes` `:1138` / `AdminRoutes` `:1141`). Поэтому «если у салона появится 2-й мастер» — это **team-readiness, а не solo-pilot** (моя прежняя формулировка была самопротиворечива — исправлено).

**Единственная реальная точка входа solo в admin-поверхность** — bootstrap-кейс (`App.tsx:1120-1128`): свежесозданный solo-тенант, где owner создал себя, но ещё НЕ добавлен мастером (`solo=true, admin=true, master=false`). Гвард `isSolo && hasMaster` (`:1135`) его не ловит → он проваливается в `hasAdmin → AdminRoutes` и видит admin team-экран, **чтобы добавить себя мастером**. В этом узком сценарии MM1/MM2 реально нужны.

- **Пилот-релевантно (build сейчас):** stale «Открыть профиль» tooltip (§4) — дёшево. Bootstrap-путь MM1→MM2 (добавить себя мастером) — уже боевой.
- **Team-readiness (НЕ solo-pilot blocker):** schedule-link (§4, отдельная зависимость), approval-queue, internal-chat, roster-управление несколькими мастерами — активируются, когда тенант перестаёт быть solo.
- **Пост-пилот (team-only):** смена ролей/контакта, resend-invite, internal-chat resolve §5.2, multi-admin assignment.
- **Receptionist (§2a, Option A′, P2-team):** пилотный FE-фикс — убрать из management-роутинга в безопасный fallback (приёмной поверхности пока нет); полноценный `/admin/conversations` + capability-backend — post-pilot эпик.
- **Код-гигиена (в любой момент):** hooks-баг §6; skeleton-консистентность §7.

---

## 9. Governance / scope

- **Домен:** bot-platform AI/runtime (ADR-0009). Admin-мутации — только через `/api/v1/admin/*` bot-platform эндпоинты; **никаких прямых записей** в booking/payment/catalog Ayla.
- **Voice:** team-чат = операционный тон тенанта, **НЕ Ayla-персона** (internal-chat handoff §2.2); admin-UI внутренний, клиент событий не видит.
- **Anti-touch:** этот док — screen-спек-слой; правки самих handoffs (устаревшие foundation-ссылки §3) оставляю владельцам handoff-доков. `apps/*` не трогаю — вердикты §4/§5/§6 = вход для имплементатора.
- **Out of scope:** web-dashboard-паритет; кастомные роли (Decision E post-MVP); loyalty/billing settings (post-MVP); домены EarningDispute/MasterLeaveRequest (проверить Ayla-ownership).

---

## 10. Итог

| Пункт | Вывод |
|---|---|
| Общая зрелость | Admin = самая зрелая поверхность: 8/9 экранов боевые (для **Owner/Admin**), дизайн в 3 handoffs. НЕ «не продумано». |
| Что писать НЕ надо | Новый дизайн MM1–MM5 / chat / settings — уже зафиксирован. |
| Receptionist (P2-team) | §2a Option A′ (зафиксировано): conversations-роль, не management. Пилот — FE-фикс (убрать из management-`hasAdmin` → safe fallback; приёмной поверхности нет). Backend management owner\|admin оставить. `/admin/conversations` + capability-backend — post-pilot эпик. Не пилот-блокер (solo без receptionist). |
| Реальный build-пункт (пилот) | Один дешёвый: stale «Открыть профиль» tooltip §4 (MM3 существует). Bootstrap-путь solo MM1→MM2 уже боевой (§8). |
| Team-readiness (НЕ solo-pilot) | schedule-link §4 (**отдельная зависимость** — admin schedule-route не существует); approval-queue; roster-управление. |
| Post-pilot (team-only) | chat resolve §5.2; смена ролей/контакта §4; resend-invite; multi-admin assignment. |
| Settings | rich settings — **web-only**; Mini App ≤ account/notifications; полный parity — **отдельный эпик**, не build-пункт (§5.1). |
| Код-баг | AvailabilityRequests conditional `useMemo` §6 → FOLLOW_UP код-тикет. |
| Долг-гигиена | skeleton-консистентность MM1–MM4 §7 → NICE post-pilot. |
| Drift | handoff foundation-ссылки (single-assistant / conversation-ownership §4) устарели. Enforced permission-канон = `admin_api/auth.py` (owner\|admin); design-intent = handoff-матрицы (`tenant-as-provider §2.10` — лишь указатель). (§2, §3) |

---

## Last verified

2026-07-13 — создан как consolidation & gap-closure слой над master-management / internal-chat / settings-hub handoffs. Заземлён на фактический код `apps/miniapp/src/screens/admin/*` (9 экранов) + `lib/admin-api.ts` + `lib/internal-chat-api.ts`.
