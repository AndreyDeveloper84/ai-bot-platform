# Runbook: Pilot Deployment PART 3.1 + 3.2 — Master + Admin Mini App Smoke Tests

> Status: **draft**
> Last exercised: _never_ (pre-pilot)
> Target completion sprint: 2026-07-15 pilot launch (Penza, salon «Формула тела»)
> Owner: W1 (Delta — master/admin Mini App)

## Purpose

Manual smoke tests для master + admin Mini App после deploy в Пензе 2026-07-15. Запускаются operator'ом в окне T+0 → T+1h после launch (per [`project-pilot-deployment-runbook-scope`](../../../.claude/memory/project_pilot_deployment_runbook.md) PART 3). Цель: подтвердить что критичные user flow работают live before объявления pilot открытым для real masters.

## Trigger / when to run

- T+0 (immediately after deploy команды из PART 2 — server-deployment.md)
- T+0 to T+1h окно (per scope memory)
- Manually re-run при любом suspect-regression report от founder/support в первые 7 дней (per PART 4 monitoring)
- Manually re-run после rollback (per PART 6)

## Prerequisites

Перед запуском любого теста проверь что выполнены:

- [ ] Deploy завершён согласно PART 2 (server-deployment.md). `gh deploy:status` returns OK.
- [ ] Миграции applied: `python manage.py showmigrations conversations master_api admin_api catalog notifications` → все ✓.
- [ ] **Redis connected:** `python manage.py check --deploy` → no errors. PR #625 startup assertion fires если CACHES misconfigured.
- [ ] **Celery workers running:** `systemctl status ai-bot-workers@*` → all active. Workers нужны для auto-trigger + DM dispatch + Beat job (PII purge).
- [ ] **Feature flags:**
  - `AI_DRAFTS_AUTO_TRIGGER_ENABLED=true` (default `False` per PR #584 — должен быть выставлен в `true` для pilot, иначе auto-trigger flow в 3.1.4 не сработает).
  - `IDLE_ACTIVE_DRAFT_SUPPRESS_WINDOW_SECONDS=60` (default; per PR #700).
  - `STRICT_TENANT_REFUSE=False` (log-only mode per Alpha runbook #589; flip 2026-05-28 → перед pilot должен быть `True`).
- [ ] **Test fixtures:**
  - 1 test master (имя «Test M1», МАX account с принятой invite) — приготовлен через Django shell или /admin/team/invite перед launch.
  - 1 test admin (МАX account с role=admin) в том же tenant.
  - 1 test customer (MAX account, не связан с tenant).
  - Тест-tenant создан через Django admin OR существует в Пензе с подсадными `is_test=True` (verify в DB перед launch).
- [ ] **MAX channel verified:** test MAX DM от `@ai_bot` arrives within 5 секунд of trigger. Verify через `manage.py shell` + `send_message(test_master.linked_bot_user.max_chat_id, "smoke test")`.

Без любого из этих prerequisites — НЕ запускать smoke tests. Зафиксируй gap в incident-response.md, дозаполнить prerequisites, потом возвращаться сюда.

---

## PART 3.1 — Master mini app smoke tests

> Quote: «Master sees their CUSTOMER conversations» — master-mobile §M5 line 556-630. M5 list имеет 5 sections including `awaiting_master / ai_drafted / ai_handling / resolved / other` (PR #656 для ai_drafted counter).

### Шаг 3.1.1 — Master может открыть приложение

**Действия:**

1. На устройстве test master: открыть мессенджер MAX → меню → найти бота `@ai_bot` (или ссылку из invite DM, если master ещё не linked).
2. Если first launch: тап на invite link от admin (формат `https://ai-bot-platform.gobeauty.site/?invite=<token>`) → Mini App открывается на `MasterOnboardingScreen` → проходит Step 1 (claim) → Step 2 (accept) → Step 3 (bio + photo, optional).
3. Если master уже linked (existing pilot session): открыть Mini App из меню MAX → SessionToken в `DeviceStorage('master_token')` валидируется → landing on M1 dashboard.

**Ожидаемый вывод:**

- Master landing на `MasterDashboardScreen` (`/master/dashboard`).
- Header показывает реальное имя мастера + специализацию из CatalogMaster row.
- Видны секции «Сегодня» / «Эта неделя» / «Уведомления» (точная структура per master-mobile §M1).
- Bottom-nav 4 tabs: 🏠 Дом / 📅 Расписание / 💬 Диалоги / 👤 Профиль.
- Браузерная консоль (chrome devtools если test через web fallback) НЕ показывает 4xx/5xx network errors.

**Если не сработало:** см. PART 5.1 секция «Master Mini App» (когда PART 5 готова; пока — log location `/var/log/ai-bot-platform/master_api.log`, grep `auth.init_data` для invite-claim issues или `auth.session_token` для existing-session issues).

---

### Шаг 3.1.2 — Master видит сегодняшние записи

**Действия:**

1. Из master Mini App: тап на bottom-nav «📅 Расписание».
2. Лендит на `MasterScheduleScreen` (PR #401 + PR #467) с дефолтным day view (per master-mobile §M3 lines 380-460).
3. Проверить «Сегодня» — должны быть видны booking cards из YClients sync или manually-created bookings в Пензе на сегодня.
4. Тап на booking card → должен открыться M6 detail OR booking-specific view (depends на conversation linkage).

**Ожидаемый вывод:**

- Day view показывает hour grid с slot blocks.
- Каждый booking card содержит: время начала, имя клиента (first name only per §M5 line 615), название услуги, длительность. **NO price visible** (master не видит цену per §M5 line 615 + ownership-policy §4).
- Slot styling correct: regular booking (white), active (red dot + «● 14:30 — Мария И.»), окно («свободно · 60 мин»), outside work hours (grayed).
- Empty day shows «Свободный день. Отдыхайте.»

**Если не сработало:**

- Если booking cards missing хотя booking есть в БД: log `master_api.tasks` grep `schedule_query` для timezone issues. См. PART 5.1 «Schedule rendering» (placeholder).
- Если все cards показывают «—»: probable scheduling resolver bug, см. `apps/scheduling/services/resolver.py`.

---

### Шаг 3.1.3 — Master может ответить admin'у во внутреннем chat

> Note: это master ↔ admin team chat per master-admin-internal-chat handoff (PR #600), NOT customer chat. Это «Со студией» канал.

**Действия:**

1. Из master Mini App: bottom-nav «👤 Профиль».
2. Прокрутить до секции «━━ НАСТРОЙКИ ━━» → тап «Со студией [N] ›» (N = unread count).
3. Лендит на `MasterInternalChatListScreen` (`/master/internal-chat`).
4. Тап «+ Открыть обсуждение» → topic picker sheet с 7 опциями (Спор по выплатам / Отпуск / Отзыв клиента / Изменение графика / Уход / Общее / Прочее).
5. Выбрать «Общее» → form «Опишите вопрос…» → ввести «Smoke test message ${TIMESTAMP}» → submit.
6. Лендит на `MasterInternalChatThreadScreen` нового thread'а. Сообщение `Smoke test message ${TIMESTAMP}` виден right-aligned (master's own).
7. Подождать ≤30 секунд → admin отвечает (см. parallel test 3.2.8).
8. Ответ admin appears left-aligned с display sender «Студия» (default per §2.7).

**Ожидаемый вывод:**

- Thread создан successfully (HTTP 201 на `POST /api/v1/internal-chat/master/threads/`).
- Master's сообщение immediately виден после submit (optimistic UI).
- Admin's reply prepends sender «Студия» (если admin не signed) or «Студия — Натали» (если admin signed).
- NO customer names visible в этом канале (§2.11 enforcement).

**Если не сработало:**

- 401 на POST: invite token expired or master_token не передаётся. Очистить DeviceStorage + re-onboard (тест 3.1.1).
- 400 на topic: проверить что topic enum в `apps/internal_chat/models.py::TopicChoices` соответствует frontend list.
- Admin не видит thread: см. test 3.2.8 escalation path.

---

### Шаг 3.1.4 — «Предложен ответ (N)» counter работает (M5)

> Validates PR #656 + auto-trigger PR #584 + #700 suppress + #715 acted log. End-to-end AI drafts pilot loop.

**Действия:**

1. **Setup** (perform once before this test): test customer (MAX account not linked to tenant) пишет сообщение в conversation с test master через MAX direct DM bot path → inbound webhook → `record_message` hook → Celery `auto_generate_draft_for_inbound` task triggers → LLM call (~2-5 секунд) → `AiDraft.status=ACTIVE` created.
2. На master Mini App: bottom-nav «💬 Диалоги» → лендит на `MasterConversationsScreen`.
3. В верхней части списка должна быть section header «━━ ПРЕДЛОЖЕН ОТВЕТ (1) ━━━━━━━» (или больше если несколько drafts).
4. Card в этой секции показывает: customer first name + reason chip + preview text «Помощник предложил черновик — посмотрите» (verbatim per spec §M5 line 584-585).
5. Тап на card → M6 detail (`MasterConversationDetailScreen`).
6. Видна draft card с header «✨ Предложенный ответ» + содержимое от AI + 3 buttons:
   - `[Отправить от себя]`
   - `[Отредактировать]`
   - `[Пусть помощник ответит]`
7. Тап `[Отправить от себя]` → confirm sheet → POST send-as-me → assistant message появляется в conversation thread (right-aligned, sender «Помощник:»). Draft card vanishes.

**Ожидаемый вывод:**

- Section header «ПРЕДЛОЖЕН ОТВЕТ (1)» visible на M5.
- Draft card render correct на M6.
- После send-as-me: assistant message visible в thread, draft.status → `SENT_AS_MASTER` (verify через Django shell), `content = ""` immediately (PR #540 Blocker #5 Layer 1).
- В worker logs INFO line: `master_api.tasks.auto_draft.acted conv=... draft=... action=sent_as_master draft_age_seconds=... trigger_age_seconds=...` (PR #715).

**Если не сработало:**

- M5 не показывает «ПРЕДЛОЖЕН ОТВЕТ» section: проверить `AI_DRAFTS_AUTO_TRIGGER_ENABLED=true` (env var, default `False`). Without flag → auto-trigger не fire, drafts не создаются.
- Section visible но counter zero: проверить `apps/master_api/services/conversations.py::_section_for` — verify subquery annotation `_has_active_draft` populates правильно.
- Draft card не render на M6: проверить `MasterConversationDetailScreen.tsx` consumes `ai_draft` field from `GET /master/conversations/<id>/` response.
- Send-as-me возвращает 409 `draft_already_acted`: race с auto-trigger replace OR test master ранее уже action'нул draft (refresh + retry).

См. PART 5.1 секция «AI drafts» (placeholder).

---

### Шаг 3.1.5 — Master запрашивает изменение графика (admin approval loop)

> Validates master→admin operational loop через PR #521 (admin endpoints) + PR #522 (admin UI) + PR #539 amendments (Celery DM dispatch).

**Действия:**

1. На master Mini App: «📅 Расписание» → выбрать дату → длинный тап на slot → `Помечу как недоступно` sheet.
2. Или alternative path: M3 → секция «Запросить отгул» (если есть в UI) → form с reason_class enum (`vacation` / `sick_leave` / `day_off` / `event`) + `reason_text` (≤200 char) + date range → submit.
3. POST `/api/v1/master/availability` → 201 + ScheduleChangeRequest row в `pending` status.
4. **Switch to admin side:** open admin Mini App от test admin account → tab «📅» или nav card «Запросы графика [1]» → лендит на `AdminAvailabilityRequestsScreen` (PR #522).
5. Видеть pending request card с master name + reason_class chip + date range + reason text.
6. Тап `[✓ Одобрить]` → confirm sheet «Одобрить запрос ... на ...? Это пометит дни как нерабочие.» → `[Подтвердить]`.
7. POST `/api/v1/admin/availability-requests/<id>/approve/` → atomic + materialize ScheduleException + status=APPROVED + Celery task enqueue для MAX DM (PR #539).
8. Master получает MAX DM от `@ai_bot` в течение 30 секунд: «Ваш запрос на смену расписания на ... одобрен. Готово.»

**Ожидаемый вывод:**

- Request appears в admin queue immediately.
- After approve: card перемещается в «Решены» tab с verdict «✓ Одобрено • Test A1 • now».
- MAX DM прибывает мастеру в течение 30 секунд (Celery worker должен process task).
- Master M3 schedule shows запрошенные даты как «выходной» (gray-shaded с label).

**Если не сработало:**

- 409 `overlap_conflict` на admin approve: master row lock fired correctly (PR #539 amendment); существует pre-existing approved exception на этих датах. Verify через `ScheduleException.all_tenants.filter(master=test_master)`.
- MAX DM не приходит: проверить Celery worker logs `apps.admin_api.tasks.dispatch_master_decision_dm` — может быть `MaxAPIError` (autoretry up to 3 times per PR #539). См. PART 5.1 «Celery DM dispatch».
- Idempotency lock fired (SETNX `master_dm_sent:<request_id>:approve` exists): DM дубликат blocked, проверь что master НЕ получил duplicate.

---

### Шаг 3.1.6 — Master выходит из аккаунта (M8 logout)

**Действия:**

1. Master Mini App → «👤 Профиль» → секция «━━ НАСТРОЙКИ ━━» → «Настройки приложения ›» → лендит на `MasterSettingsScreen` (`/master/settings`).
2. Тап `[Выйти из аккаунта]` (single destructive button).
3. Confirm sheet: «Выйти из аккаунта? Чтобы вернуться, потребуется новое приглашение от админа.» → `[Выйти]`.

**Ожидаемый вывод:**

- `removeDeviceStorage('master_token')` clears the session token (PR #607).
- `window.location.replace('/')` reloads root.
- Master лендит на `MasterOnboardingScreen` Step 1 (claim) — onboarding gate fires because token missing.
- DevTools localStorage check: `localStorage.getItem('max:master_token')` → null.

**Если не сработало:**

- После reload landing на M1 (not onboarding): DeviceStorage НЕ cleared. Проверить `apps/miniapp/src/lib/max-sdk.ts::removeDeviceStorage` существует AND fallback `window.localStorage.removeItem('max:' + key)` fired.
- Stale session cookie или auth header: ensure NO cookies stored для master session (test cookies-free).

---

## PART 3.2 — Admin mini app smoke tests

> Quote: «Master ↔ admin-team (not per-individual-admin)» — master-admin-internal-chat handoff §2.4. Admin UI privileges follow ADR-0009 (Ayla split-domain) + `require_admin_role` (PR #405).

### Шаг 3.2.1 — Admin может просмотреть salon dashboard

> Note: literal «salon dashboard» screen не существует в коде. Под этим тестом подразумеваю «admin home landing» — combined view: MM1 masters roster visible + admin nav cards с counters.

**Действия:**

1. Test admin account открывает admin Mini App (`https://ai-bot-platform.gobeauty.site/admin/`).
2. Auth gate runs: init-data validation → role lookup → admin OR owner OR receptionist.
3. Лендит на `AdminTeamScreen` (`/admin/team`).

**Ожидаемый вывод:**

- Header «Команда» + master count chip.
- Roster cards visible (≥1 master в test tenant).
- Каждый card: avatar + master name + role chip + status indicator + services count + recent-activity timestamp.
- Nav cards в верхней части (added в PR #522 + PR #606):
  - «📅 Запросы графика [N]» — N = pending availability requests.
  - «💬 Чаты с мастерами [N]» — N = active master-admin threads.
- Bottom nav (per `AdminTabBar.tsx`) с tabs.

**Если не сработало:**

- 403 на admin endpoints: test admin user НЕ имеет role=admin OR role=owner. Verify через Django shell: `TenantStaff.objects.get(user=test_admin, tenant=test_tenant).role`.
- Empty roster: либо tenant действительно пустой (создать masters через MM2 invite), либо tenant_scope filter bug (см. PART 5.1 «admin tenancy»).
- Receptionist seeing admin-only cards: permission gate bug, должен hide или disable cards.

---

### Шаг 3.2.2 — Admin одобряет/отклоняет master schedule change

> Note: admin не редактирует master schedule напрямую — master proposes via M3 → admin approves via PR #521. Этот тест = admin сторона теста 3.1.5.

**Действия:**

1. Pre-seed: запустить тест 3.1.5 → pending ScheduleChangeRequest существует.
2. Admin Mini App → nav card «Запросы графика [N]» (или `/admin/availability-requests`) → лендит на `AdminAvailabilityRequestsScreen`.
3. Filter chips: «● Ожидают» / «Решены» / «Все» (default Ожидают).
4. Видеть card test master'а с reason_class + date range.
5. Тап `[✗ Отклонить]` (для смоук-теста противоположной branch'и от 3.1.5).
6. Modal с textarea «Почему отклоняете?» + counter (1-500 char) → ввести «Smoke test rejection ${TIMESTAMP}» → `[Отклонить]`.
7. POST `/api/v1/admin/availability-requests/<id>/reject/` → status=REJECTED + Celery DM dispatch.

**Ожидаемый вывод:**

- Card перемещается в «Решены» tab с verdict «✗ Отклонено • Test A1 • now — Smoke test rejection ...».
- Master получает MAX DM в течение 30 секунд: «Запрос на смену расписания на ... отклонён. Причина: Smoke test rejection ${TIMESTAMP}. Спросите у Карины уточнить.»
- Master M3 schedule: запрошенные даты НЕ помечены как нерабочие (rejection не materialize ScheduleException).

**Если не сработало:**

- Reject без reason returns 400: validation working correctly, верни reason.
- Reject длиннее 500 char returns 400: validation working.
- Idempotency: re-reject already-rejected request returns 409 `already_decided` (PR #521 + #539 amendments).

---

### Шаг 3.2.3 — Admin видит uncategorized reviews для модерации

**Действия:**

*N/A для pilot launch.*

**Ожидаемый вывод:**

*Reviews moderation UI не существует на момент pilot launch.* Backend models могут быть в `apps/reviews/` (verify через `ls apps/reviews/` — если нет, reviews ещё не построен) но admin-side UI deferred per master-reviews-feedback handoff. Pilot 2026-07-15 ships без этой функции.

**Если не сработало:**

Этот test marked as **N/A** до пост-пилота. Когда reviews UI лендит — обновить runbook:
- 3.2.3 actions: открыть `/admin/reviews` → лента модерации.
- Спецификация в `docs/design/handoffs/2026-05-19-master-reviews-feedback-handoff.md`.

См. также tracking issue если уже filed (`gh issue list --label "reviews" --label "post-pilot"`).

---

### Шаг 3.2.4 — Admin может revoke tenant relationship (manual abuse)

**Действия:**

*N/A для admin Mini App.* Это flow живёт только через Django admin (`/django-admin/tenancy/tenantuserrelationship/`):

1. Operator → SSH в production → `python manage.py shell`
2. Найти запись: `rel = TenantUserRelationship.all_tenants.get(user__phone='+7...', tenant=test_tenant)`
3. Revoke: `rel.revoked_at = timezone.now()` + `rel.revoked_reason = 'manual abuse via smoke test ${TIMESTAMP}'` + `rel.save()`
4. Verify: master/admin try open Mini App → auth gate denies → onboarding gate fires.

Alternative: Django admin UI на `/django-admin/tenancy/tenantuserrelationship/<id>/change/` → checkbox или action button (если есть в admin.py).

**Ожидаемый вывод:**

- После revoke: relationship.is_active returns False.
- Auth gate (`require_master_init_data` или `require_admin_role`) returns 403 на любой запрос от этого user'а.
- На UI: master видит onboarding gate (нет valid relationship), admin видит «Эта страница только для администраторов» (PR #522 receptionist-style banner).

**Если не сработало:**

- Mini App все ещё работает после revoke: caching layer возможно ещё держит auth state. Force restart workers (`systemctl restart ai-bot-workers@*`) OR ждать 60 секунд для cache TTL expiry.
- Django admin UI не показывает «revoke» action: добавить admin action в `apps/tenancy/admin.py` (отдельная PR, не в scope этого runbook).

См. PART 5.1 секция «manual abuse response» когда PART 5 готова.

---

### Шаг 3.2.5 — Emergency fallback tier handling (4 cases)

> Per `conversation_ownership_tiers` memory: 4 invisible system fallback tiers — `payment_dispute / booking_conflict / integration_error / legally_sensitive` — handled в admin UI invisibly. AI stops; admin queue surfaces.

**Действия:**

*Этот test = backend-only smoke verification.* Эти тиры handle'аются автоматически без admin Mini App UI (per ayla-first strategic pivot — invisible fallback). Smoke procedure:

1. **payment_dispute trigger:** test customer disputes payment via YooKassa (если pilot включает payments) — ИЛИ manually trigger в Django shell:
   ```python
   from apps.conversations.models import Conversation
   from apps.conversations.services import promote_to_human_locked
   conv = Conversation.all_tenants.get(id=<test_conv>)
   promote_to_human_locked(conv, reason_class='payment_dispute', tier_locked_reason_text='smoke')
   ```
2. Verify: `conv.tier == HUMAN_LOCKED`, AI не отвечает дальше в этой conversation (запросы returns 403 `conversation_locked`).
3. Verify admin notification: проверить что admin получил MAX DM mention OR notification slug emitted в logs (`grep tier_promoted_to_human_locked`).
4. Repeat для остальных 3 тиров: `booking_conflict`, `integration_error`, `legally_sensitive`.

**Ожидаемый вывод:**

- Каждый из 4 тиров triggers correctly через service layer.
- AI silent в HUMAN_LOCKED conversations.
- Admin notification reaches admin team (либо MAX DM либо log entry для off-band channel).
- Master M6 view shows banner «⚠ Этот диалог требует внимания администратора» (master-mobile §M6 line 690-694).
- Compose box отсутствует для master + customer в HUMAN_LOCKED tier.

**Если не сработало:**

- Tier flip works but AI продолжает отвечать: bug в `apps/orchestrator/pipeline.py` — должен check `conversation.tier == HUMAN_LOCKED` перед dispatching skills. См. PART 5.1 «orchestrator tier respect».
- Admin notification не приходит: outbox event `conversation.tier_promoted_to_human_locked` может быть не consumed. Проверить eventbus DLQ.
- Master compose box visible в HUMAN_LOCKED: frontend bug в `MasterConversationDetailScreen.tsx` — должен hide composer based на `tier === 'HUMAN_LOCKED'`.

---

### Шаг 3.2.6 — Admin invite нового мастера (MM2 end-to-end)

> Validates PR #506 invite flow + master-onboarding flow.

**Действия:**

1. Admin Mini App → `/admin/team` → header `[+ Пригласить]` button → лендит на `AdminInviteMasterScreen`.
2. Заполнить: phone `+79991234567` (test phone) + name «Test M2» + role `master` + services (≥1 из existing) → `[Создать приглашение]`.
3. POST `/api/v1/admin/masters/invite` → 201 + invite token generated + MAX DM dispatched к invited phone (если MAX account registered).
4. Verify: test M2 master account получает MAX DM с invite link в течение 30 секунд: «Test A1 приглашает вас в команду {salon_name}. Откройте: {invite_link}».
5. Test M2 master тапает invite link → Mini App открывается → `MasterOnboardingScreen` Step 1 → видит preview profile → принимает → linked.

**Ожидаемый вывод:**

- Invite сreated successfully.
- MAX DM с invite link arrives.
- Master claim flow works end-to-end.
- После accept: M2 master visible в admin roster (`/admin/team`) с status=ACTIVE.

**Если не сработало:**

- MAX DM не приходит: invited phone не registered в MAX OR send_message endpoint failed (см. PART 5.1 «MAX DM dispatch»).
- Invite token expired: tokens по default expire через 7 дней (verify в `CatalogMaster.invite_expires_at`). Re-generate invite.
- Role enum mismatch: «master» / «admin» / «receptionist» — verify в `apps/catalog/models.py::CatalogMaster.Role`.

---

### Шаг 3.2.7 — Admin отвечает мастеру в internal chat

> Admin side теста 3.1.3.

**Действия:**

1. Admin Mini App → tab «💬 Чаты» OR nav card «Чаты с мастерами [N]» → лендит на `AdminInternalChatListScreen` (PR #606).
2. Filter chips: «● Ожидают» / «Решены» / «Архив».
3. Видеть thread созданный в тесте 3.1.3 (sender=Test M1, topic=Общее, last message «Smoke test message ${TIMESTAMP}»).
4. Тап на thread → `AdminInternalChatThreadScreen` → видны messages.
5. В composer написать «Admin reply smoke test ${TIMESTAMP}».
6. **Optional signature toggle:** check «Подписаться как Test A1» (default OFF per §2.7 — master видит «Студия»; ON — master видит «Студия — Test A1»).
7. Send.

**Ожидаемый вывод:**

- Thread visible в admin queue с master's name + topic chip + last message preview.
- After send: admin's message appears right-aligned в admin view.
- Master видит reply в течение 30 секунд (либо через polling либо при reload M5).
- Sender display correct: «Студия» (signature OFF) или «Студия — Test A1» (signature ON).
- §2.7 privacy invariant сохранён: master НЕ видит admin's individual name unless explicitly signed.

**Если не сработало:**

- Thread не visible: проверить tenant scoping admin endpoints (`apps/internal_chat/views.py::admin_thread_list`).
- Reply не доходит до master: verify outbox push (если есть) OR проверить что master polls list endpoint regularly (M5 не имеет realtime push, относится на refresh).
- Cross-tenant leak: admin от tenant A видит threads от tenant B — это PRE_MERGE blocker, immediately stop pilot + escalate.

---

### Шаг 3.2.8 — Receptionist+master role combination (unified surface gating)

> FOLLOW_UP из PR #753 / issue #747. Sanity check that the inclusive
> routing wrapper (`UnifiedAdminMasterRoutes`) does NOT leak
> owner-only actions to a dual-role receptionist+master user. The
> `me` prop threads through `adminRouteElements(me)` to each admin
> screen, so the same `me.is_owner` gating that fires in
> `AdminRoutes` should fire identically inside the unified wrapper —
> this test verifies that empirically.

**Действия:**

1. Test user provisioned с `TenantStaff.role = receptionist` AND linked `CatalogMaster` (i.e. dual-role: receptionist+master, NOT owner, NOT admin).
2. Open Mini App → boot cascade hits `hasAdmin && hasMaster` → UnifiedLanding chooser visible (или auto-redirect to last-chosen surface если localStorage уже содержит выбор).
3. Tap «🏢 Салон» → land on `/admin/team`.
4. Open any master detail screen (`/admin/team/:masterId`) → verify owner-only action buttons (Деактивировать, Реактивировать, и т.п.) показывают `disabled` state + `title` tooltip «Только владелец может деактивировать» / «Только владелец может восстановить» (см. `AdminMasterDetailScreen.tsx` lines 949-961).
5. На `/admin/team` (list view) — tap «Деактивировать»/«Восстановить» возле любого мастера → buttons disabled с tooltip «Только владелец, скоро» (см. `AdminTeamScreen.tsx` lines 540-551).
6. Switch surface — back to `/`, tap «👤 Мой профиль мастера» → land on `/master/dashboard`. Verify master surface работает: schedule, conversations, profile все доступны.
7. Defence-in-depth: если получится bypass UI (e.g. via direct API call) — backend должен return 403 with `not_authorized` slug.

**Ожидаемый вывод:**

- Padlock-style disabled buttons visible on owner-only actions on BOTH `/admin/team` list view AND `/admin/team/:masterId` detail view.
- Tooltip «Только владелец…» shows on hover/tap of disabled buttons.
- Master surface (`/master/*`) fully accessible — receptionist can see her own schedule / conversations / profile.
- localStorage key `max:unified_last_surface` updates когда user navigates into admin or master subtree (single top-level listener in `UnifiedAdminMasterRoutes` — see #746).
- Backend POST attempts to owner-only endpoints (e.g. `POST /api/v1/admin/masters/{id}/deactivate`) return `403 not_authorized` if invoked by receptionist (defence in depth).

**Если не сработало:**

- Если padlock missing / button enabled: UI permission gate bug — `me.is_owner` check либо hardcoded ожидающий `true`, либо `me` prop не дошёл до screen через unified wrapper. Проверить `adminRouteElements(me)` сигнатуру.
- Если backend 200 (NOT 403) on owner-only action invoked by receptionist: критическая security проблема — escalate P0 immediately.
- Если switching surfaces ломает state (e.g. master surface не загружается после захода в admin): inspect `useLocation` listener в `UnifiedAdminMasterRoutes` — pathname prefix matching должен корректно различать `/admin/` vs `/master/`.

---

## Verification (overall)

Pilot готов declare «live» если все mandatory tests прошли:
- 3.1.1 ✓ 3.1.2 ✓ 3.1.3 ✓ 3.1.4 ✓
- 3.2.1 ✓ 3.2.2 ✓ 3.2.6 ✓ 3.2.7 ✓

Шаг 3.2.8 — mandatory IF pilot has any receptionist+master users provisioned;
N/A otherwise (single-role staff only).

Если 3.2.3 / 3.2.4 / 3.2.5 marked N/A — acceptable (documented above; deferred features).

Если ЛЮБОЙ mandatory test fails:
1. STOP pilot rollout.
2. Capture full evidence (screenshots, logs, error messages).
3. Escalate per Escalation Contacts ниже.
4. Consider rollback per PART 6 if not fixable in ≤ 1 hour.

## Escalation contacts

| Severity | Who | How to reach |
|---|---|---|
| P0 (any mandatory test fail + cannot rollback) | Tech lead | MAX DM @tech_lead OR phone |
| P1 (mandatory test fail, rollback viable) | W1 stream owner | MAX DM @w1_owner |
| P2 (N/A test now-applicable post-pilot) | Tech lead via async | Linear ticket с label `pilot-retrospective` |
| Vendor (MAX DM dispatch failing) | MAX support | TBD per PART 1 prerequisites |

## Post-mortem template

После любого non-trivial smoke test failure заполнить:

- **Что произошло:** какой test step failed.
- **Trigger:** какое действие операторa или backend event привёл к проблеме.
- **Expected vs Actual:** ожидаемый vs реальный output.
- **Detect/Mitigate/Resolve timing:** когда заметили, когда пофиксили, когда подтвердили green.
- **Учились:** причина (config / code / data / external).
- **Action items:** owner + deadline.

## Changelog

- _2026-05-25_ — W1 (Delta) — initial draft PART 3.1 + 3.2 with 6 master tests + 7 admin tests (4 mandatory + 2 extras для каждой части; 3 marked N/A pending feature build OR Django-admin-only).

## Cross-reference

- PART 1 (PRE-DEPLOYMENT) — owner Alpha + W4 (env vars + migrations). Smoke tests assume PART 1 prerequisites met.
- PART 2 (DEPLOYMENT) — owner tech lead. Smoke tests assume PART 2 commands executed.
- PART 4 (MONITORING) — owner W2. Smoke tests don't repeat metric polling; см. PART 4 dashboards.
- **PART 5.1 (TROUBLESHOOTING) — пока не написана.** Когда лендит — обновить все «См. PART 5.1 секция X» placeholders ниже в runbook'е links.
- PART 6 (ROLLBACK) — owner tech lead. Если smoke test fails irrecoverably — invoke PART 6.

Master/admin specs reference:
- `docs/design/handoffs/2026-05-18-master-mobile-handoff.md` — M0-M8 spec.
- `docs/design/handoffs/2026-05-18-master-management-handoff.md` — MM1-MM5 spec.
- `docs/design/handoffs/2026-05-19-master-admin-internal-chat-handoff.md` — Co Студией / Чаты с мастерами spec.
- `docs/runbooks/m6-auto-draft-suppress-tuning.md` — AI drafts observability + tuning protocol.
