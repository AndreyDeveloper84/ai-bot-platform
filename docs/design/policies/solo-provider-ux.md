# Solo Provider — Universal UI Policy

| Field | Value |
|---|---|
| **Date** | 2026-05-25 r1 |
| **Status** | STRATEGIC FOUNDATION — founder decision 2026-05-25 (locked) |
| **Author** | Tau (UX/Design stream) |
| **Reads** | [`ADR-0008`](../../adr/ADR-0008-role-detection-and-staff-model.md) (role detection + TenantStaff model), memory `project_solo_provider_universal_ui`, [`tenant-as-provider-model.md`](./tenant-as-provider-model.md), [`master-onboarding-m0-m7.md`](./master-onboarding-m0-m7.md), [`owner-conversational-templates.md`](./owner-conversational-templates.md), [`master-conversational-templates.md`](./master-conversational-templates.md), [`conversation-ownership-policy.md §4`](./conversation-ownership-policy.md) (5-role capability matrix), [`information-architecture.md`](./information-architecture.md) |

> Ayla Pro поддерживает self-employed solo provider (один человек = owner + admin + master в одном tenant) **как first-class scenario** с пилота 15 июля 2026. Universal UI со smart defaults — single codebase адаптируется под размер tenant + роли. Solo Olga видит понятный кабинет «для меня», team Анна-владелица видит full salon ops. Этот doc — single source for solo provider UX rendering rules.

---

## 0. Why this exists

### 0.1 The strategic decision

Founder decision 2026-05-25 (foundational, supersedes any earlier «pilot = teams only» assumption):

> Ayla Pro поддерживает self-employed / solo provider **с первого пилота** как first-class scenario. Не отдельное приложение. Не отдельный mode. Universal UI с smart defaults — один codebase адаптируется под размер tenant + роли.

Per `project_solo_provider_universal_ui` memory (locked 2026-05-25 после investigation a934b34dd9dfad2e5).

### 0.2 Why this matters for Penza pilot 15 July

Pre-research working hypothesis: beauty-провайдеры в Пензе **смешанные** — self-employed = **40-60%**. Если pilot UX игнорирует solo сценарий, мы:
- Теряем половину addressable рынка с первого дня
- Заставляем self-employed Ольгу «играть в админа salons» — wrong mental model, churn
- Создаём engineering tech debt — отдельный код для solo приходит post-pilot когда retention уже потеряна

Solo support как first-class = wider market + cleaner mental model + lower tech debt.

### 0.3 What's architecturally already done (ADR-0008)

Per ADR-0008 + verification 2026-05-25 investigation:
- **Multi-role additive semantics** уже implemented — один BotUser может одновременно держать TenantStaff(role=owner) + TenantStaff(role=admin) + CatalogMaster.linked_bot_user→self
- **`apps/tenancy/models.py:431`** `unique_together = (("tenant", "bot_user", "role"),)` — отдельные строки per role, не enum
- **`resolve_role()`** (`apps/identity/services/role_resolver.py:231-332`) уже возвращает union capabilities всех ролей одновременно
- **`/api/v1/me`** уже exposes multi-role
- Backend **90% ready**

### 0.4 The remaining gap

**`apps/miniapp/src/App.tsx:263-267`** cascade exclusive routing БЛОКИРУЕТ owner-master Ольгу от `/master/*` routes (routes assume customer XOR master XOR admin, not «all three same person»). Fix = ~50 LoC W1.

Плюс UX: smart defaults rendering (hide team-only features для solo), onboarding auto-seed, surface organization. Это design scope этого doc.

### 0.5 The promise

Single source for:
- Solo vs team definition §2
- Detection logic §3 (when tenant treated as solo)
- UI rendering rules §4 (what's hidden, what's shown)
- Solo surface design §5 (8-tab structure)
- Team surface (existing Bundle A/B) reference §6
- Onboarding for solo §7
- Voice / tone for solo Ольгу §8
- Transition: solo → team growth §9
- Backend mapping §10
- Anti-patterns §11
- Open questions §12

---

## 1. Scope

### IN
- Solo provider definition + detection logic
- Universal UI rendering rules — solo vs team
- Solo surface 8-tab structure
- Smart-default feature hiding (team-only features hidden for solo)
- Onboarding seeding для solo (auto-create TenantStaff×2 + CatalogMaster)
- Voice / tone notes for Ольгу in chat с Ayla (она же owner + admin + master)
- Solo → team transition (post-pilot edge case)
- Cross-doc «Открыть обсуждение» button conditional rendering per memory
- Backend API hint: `is_solo_provider` field on `/api/v1/me` extension
- Mini App router fix scope reference (App.tsx inclusive routing)

### OUT
- Customer-side UX (Ayla customer flows) — solo/team transparent to customer per `tenant-as-provider-model.md`
- Pricing differentiation between solo/team — same product (per memory `pricing-model-hybrid`)
- ADR-0008 role storage changes — already locked, this doc just consumes
- Engineering implementation details (routing TypeScript, migration code, etc.) — separate streams W1/Alpha
- Marketing positioning of «solo» vs «salon» — separate Marketing scope
- Multi-tenant solo (Ольга работает в 2 салонах + у себя) — out of pilot scope, Phase 2+
- Voice (TTS/STT) interactions — Phase 2+
- Family solo (Ольга + один helper мама) — treated as team if 2+ TenantStaff rows, no edge case carve-out
- Receptionist role для solo — solo doesn't have receptionist, only owner+admin+master same person

---

## 2. Definition

### 2.1 Solo provider

**Self-employed beauty/wellness provider — one person who IS the business.**

Examples:
- Ольга, мастер маникюра, работает дома или в арендованном кабинете
- Андрей, массажист, выезжает на дом или принимает в одной комнате студии
- Катя, бровист, work from her own room в studio (но catalog отдельный от других мастеров studio)

**Technical definition:**
- ОДИН BotUser
- Имеет `TenantStaff(role=owner)` + `TenantStaff(role=admin)` записи — две отдельные строки в `tenancy_tenantstaff` per ADR-0008 Decision 2
- Имеет `CatalogMaster.linked_bot_user → self` — она же мастер в своём же каталоге
- ЕДИНСТВЕННЫЙ TenantStaff row в tenant (если посчитать distinct bot_user_id), плюс ЕДИНСТВЕННЫЙ CatalogMaster.linked_bot_user

**НЕ делаем:**
- ❌ Три отдельных BotUser («Olga-owner» / «Olga-admin» / «Olga-master»)
- ❌ Полю-enum «provider_type = solo | salon»
- ❌ Отдельный tenant type «SoloTenant» (один Tenant model для всех scales)

**Per ADR-0008 Decision 3** — Multi-role additive: capabilities union'ятся, frontend role chip показывает highest privilege, but customer access + master delivery + admin management — все работают одновременно.

### 2.2 Team tenant

**Salon / studio with 2+ distinct people в TenantStaff.**

Examples:
- Studio с владелицей Татьяной + admin Анной + 3 мастерами (Карина, Лена, Юля)
- Salon с founder Олегом (owner+admin himself) + 2 мастерами (Sergei + Mark) → STILL team (Олег + Sergei + Mark = 3 distinct people)
- Edge: Татьяна owner + Анна admin + 0 мастеров (catalog empty) → team (2 staff people, even without masters)

**Technical definition:**
- ≥2 distinct `bot_user_id` values across active `TenantStaff` rows for that tenant
- ИЛИ
- ≥2 distinct `CatalogMaster.linked_bot_user` values (multiple masters even if same owner)
- ИЛИ both

### 2.3 Edge cases

| Scenario | Classification | Rationale |
|----------|----------------|-----------|
| Solo Olga invited a master, master accepted | **Team** | Threshold = 2+ distinct people now |
| Solo Olga invited a master, invite pending (not accepted) | **Still Solo** | Invite не = membership |
| Solo Olga's tenant имеет TenantStaff(role=master) for another person but `linked_bot_user IS NULL` | **Solo (template-only mode)** | Pre-PR-203 legacy; master not yet linked to a BotUser. Per ADR-0008 Decision 2 master detection requires `linked_bot_user`, not just TenantStaff row |
| Olga deactivated her only invited master (cooperative offboarding) | **Reverts to Solo** | Active staff count back to 1 |
| Tenant имеет owner + admin (2 different people) but 0 catalog masters | **Team** | Per §2.2 — 2+ TenantStaff = team |
| Tenant имеет owner only (no admin, no masters separate) | **Solo** | Same person owner-only is solo (admin role auto-implied or absent — depends on onboarding seed pattern §7) |

---

## 3. Detection

### 3.1 The `is_solo_provider` API hint

**Backend computes server-side, exposes via `/api/v1/me` response payload.**

```json
{
  "user_id": "...",
  "tenant": { "id": "...", "name": "..." },
  "primary_role": "owner",
  "capabilities": [...],
  "is_solo_provider": true,    // NEW field
  ...
}
```

**Computation logic (Alpha owns implementation):**

```python
def is_solo_provider(tenant: Tenant) -> bool:
    active_staff_users = set(
        TenantStaff.objects
        .filter(tenant=tenant, deactivated_at__isnull=True)
        .values_list("bot_user_id", flat=True)
    )
    active_masters = set(
        CatalogMaster.objects
        .filter(tenant=tenant, deactivated_at__isnull=True, linked_bot_user__isnull=False)
        .values_list("linked_bot_user_id", flat=True)
    )
    distinct_people = active_staff_users | active_masters
    return len(distinct_people) == 1
```

**Caveats:**
- Compute on every `/api/v1/me` request — cheap (single tenant scope, indexed). Не cache aggressive чтобы solo→team transition было прозрачно
- Solo→team transition unhides team features automatically per §9
- Frontend reads hint, applies render rules per §4. **Capability checks остаются server-authoritative per ADR-0008 Decision 4** — `is_solo_provider` это UX hint, не security gate

### 3.2 Frontend usage

Mini App reads `is_solo_provider` on `/api/v1/me`:
- `true` → render Solo surface §5
- `false` → render Team surface §6 (Bundle A/B existing)

State stored in `RoleContextProvider` Mini App level. Re-fetched on:
- App boot
- After team-changing operations (invite accepted, master deactivated)
- Stale-while-revalidate 30 sec

### 3.3 Server-side enforcement boundary

**Important per ADR-0008 Decision 4:** capability gates server-side never read `is_solo_provider`. They read capabilities. A solo Olga `has_capability("manage_team")` returns true только если у неё owner+admin roles (which она does), regardless of `is_solo_provider`. Hiding «Команда» tab — это UX choice, not authorization choice.

If solo Olga somehow taps Manage Team URL directly (bookmarked from web), backend allows the request (she has permission). UI then shows empty state «Пока ты одна. Когда приведёшь мастера — расскажу как пригласить».

---

## 4. UI rendering rules

### 4.1 Three categories of features

| Category | Description | Solo | Team |
|----------|-------------|------|------|
| **Core** | Always shown — base operations needed by any provider | ✅ Visible | ✅ Visible |
| **Team-only** | Multi-people coordination — moot для solo | ❌ Hidden | ✅ Visible |
| **Solo-adapted** | Core feature но render adapts (smaller defaults, different copy) | ✅ Adapted | ✅ Full |

### 4.2 Feature matrix

| Feature / surface | Solo | Team | Notes |
|---|---|---|---|
| Мой день dashboard | ✅ Core | ✅ Core | Solo: «мои визиты сегодня». Team: «команда + я» |
| Список записей | ✅ Core | ✅ Core | |
| Клиенты | ✅ Core | ✅ Core | Solo: only Ольгины customers. Team: filter by master |
| Услуги и цены | ✅ Core | ✅ Core | Solo: единый каталог Ольги. Team: catalog + per-master mapping |
| Расписание | ✅ Core | ✅ Core | Solo: own working hours + exceptions. Team: per-master + exceptions |
| Доходы | ✅ Core | ✅ Core | Solo: «мои за месяц». Team: «по мастерам + общая» |
| Отзывы | ✅ Core | ✅ Core | Solo: «ко мне». Team: «по мастерам + общая» |
| AI-помощник (chat с Ayla) | ✅ Core | ✅ Core | Solo: owner+master voice merge §8. Team: owner или master separately |
| **Команда** (master list / invite / deactivate) | ❌ Hidden | ✅ Team-only | Empty surface для solo if dirлк to /admin/team |
| Approval queue (отпусков / времени-off requests) | ❌ Hidden | ✅ Team-only | Solo не подаёт sebe requests |
| Master ↔ Admin internal chat | ❌ Hidden | ✅ Team-only | Solo single-person, internal channel moot |
| Approve time-off | ❌ Hidden | ✅ Team-only | Solo just blocks own calendar directly |
| Offboarding workflow | ❌ Hidden | ✅ Team-only | Solo deactivate herself = close tenant flow (separate) |
| Invite / deactivate master | ❌ Hidden | ✅ Team-only | Solo grows to team → unhide §9 |
| Cross-doc «Открыть обсуждение» buttons | ❌ Hidden | ✅ Team-only (conditional) | Per memory: до завершения 48h research — solo всегда скрыто, team может показывать если low scope. Если conditional impl растёт — defer to post-pilot |
| Earnings by master | ❌ Hidden | ✅ Team-only | Solo: единая «доходы» строка без breakdown |
| Review discussion (master responds to review) | ❌ Hidden | ✅ Team-only | Solo: review reply без internal-discussion intermediate step |

### 4.3 «Smart default» principle

UI hides team-only features **silently** для solo — никаких grayed-out buttons, никаких «Upgrade to Team» tooltips. Solo Olga **не должна знать** что team features existaют — иначе она чувствует «обрезанное приложение, плачу за full version меньше».

For solo: feature simply doesn't exist в навигации. If she ever invites master → §9 transition — features появляются естественно.

---

## 5. Solo surface design

### 5.1 Solo navigation structure

8 tabs, bottom navigation parity с customer Mini App pattern:

```
┌──────────────────────────────────────────────┐
│ [Mini App content area]                       │
├──────────────────────────────────────────────┤
│  📋   📅   👥   💼   ⏰   💰   ⭐   💬       │
│ День  Записи Клиенты Услуги Расп Доходы Отз AI│
└──────────────────────────────────────────────┘
```

Tabs (left → right):
1. **📋 Мой день** — landing default, agenda + quick actions для today
2. **📅 Записи** — full booking calendar / list view
3. **👥 Клиенты** — customer roster + history
4. **💼 Услуги и цены** — single catalog (own services)
5. **⏰ Расписание** — working hours + exceptions + day-off
6. **💰 Доходы** — earnings summary
7. **⭐ Отзывы** — review list + reply
8. **💬 AI-помощник** — chat с Ayla (analytics queries, schedule changes, etc.)

**Production:** Lucide icons (calendar-check / calendar / users / briefcase / clock / wallet / star / message-circle) per anti-slop scan. ASCII здесь emoji proxy.

### 5.2 Why 8 tabs (not 5 like customer)

Customer Mini App имеет 5 tabs per `information-architecture.md` — customer mental model = смотреть/выбрать/записаться. Provider mental model = manage множество concurrent threads (today's bookings + future schedule + clients + earnings). 8 tabs = MAX UI lib limit, splits provider concerns по mental categories.

Если 8 чувствуется много на 360dp viewport — fallback на **scroll-overflow tab bar** (Telegram WebApp pattern). Не сжимаем в 5 (теряет clarity).

### 5.3 Single dashboard, не split

**NOT this:**
- ❌ «Команда» tab + «Мой профиль» tab as two separate destinations
- ❌ Sidebar split «Команда сверху / Мои инструменты снизу»

**Yes this:**
- ✅ One coherent dashboard view per tab. Solo Ольга видит «свой кабинет», team Татьяна видит «свой кабинет с командой внутри».

«Мой день» tab показывает Ольге её agenda. Team Татьяне — она показывает её personal agenda PLUS team-agenda summary (если у Татьяны есть master link to herself, иначе только team summary).

### 5.4 Empty states matter

Solo customer journey day 1 — почти все вкладки empty:
- Записи: «Первая запись будет здесь.»
- Клиенты: «Накопится после первых записей.»
- Доходы: «Считаю с первой оплаты.»
- Отзывы: «Появятся после визитов.»

**Empty states воспринимаются как promise, не как «пусто».** Каждый speaks first-person Ayla voice. CTA где applicable — например «Услуги и цены» empty state даёт `[ Добавить первую услугу ]`.

### 5.5 «Мой день» landing — solo-specific layout

```
┌──────────────────────────────────────────────┐
│  Доброе утро, Оля 🌿                          │
│  Сегодня 3 записи · ближайшая через 40 мин   │
│                                               │
│  ── Ближайшая ──                              │
│  ┌──────────────────────────────────────┐   │
│  │  10:30 · Маникюр гель-лак · 90 мин    │   │
│  │  Анна Петрова · +7 ••• 14 67           │   │
│  │  [ Сообщить что готова ]               │   │
│  │  [ Перенести ]                         │   │
│  └──────────────────────────────────────┘   │
│                                               │
│  ── Сегодня ──                                │
│  10:30 Анна Петрова · маникюр                 │
│  13:00 Мария Сидорова · педикюр               │
│  16:00 Олег Иванов · мужской маникюр          │
│                                               │
│  ── Быстрые действия ──                       │
│  [ + Добавить запись вручную ]                │
│  [ Закрыть день раньше ]                      │
│  [ Спросить Ayla ]                            │
│                                               │
│  ── На этой неделе ──                         │
│  Пн Вт Ср Чт Пт Сб Вс                         │
│  ●● ●●● ●● ●●●● ●● ●● —    18 visits          │
│                                               │
└──────────────────────────────────────────────┘

NO «команда» section. NO «approval queue» banner.
NO master selector (она же мастер). NO admin chrome separation.
```

---

## 6. Team surface design

### 6.1 Existing Bundle A/B kept as-built

Per memory: **«Keep как built — нет breaking changes.»**

Team surface = existing implementation per `master-onboarding-m0-m7.md` + `2026-05-18-master-mobile-handoff.md` + Bundle A/B Mini App work shipped through W1.

**Bundle A (master mobile):** today/week view, conversations subset, profile editor, change-requests
**Bundle B (admin):** master CRUD + approval queue + cross-doc buttons + internal chat

### 6.2 Additional features visible (vs solo)

Per §4.2 matrix — team tenant adds:
- Команда tab (master roster + invite + roles)
- Approval queue для отпусков (admin perspective)
- Internal chat master↔admin
- Cross-doc «Открыть обсуждение» buttons (conditional — see §11.2)
- Earnings by master (breakdown)
- Review discussion thread

### 6.3 Owner+admin same person, team имеет others

Edge: Татьяна = owner + admin AND имеет 3 masters. Татьяна's surface = team mode (multiple people in tenant). Татьяна's «Мой день» PERSONALLY shows owner agenda — она lookalike role chip = owner; capabilities = owner ∪ admin. Если она ALSO master (`linked_bot_user`), её day view также includes her personal session bookings.

Если Татьяна = owner+admin **без** master link (она managing only, не делает services) — «Мой день» pure managerial view: «команда сегодня · ближайшие visits · revenue today».

### 6.4 No team-mode toggle for solo

Solo Olga не должна иметь «Переключиться в team mode» toggle. Single source of truth — `is_solo_provider`. Если она reach state где team features нужны — она invites a master → real team transition happens (§9).

---

## 7. Onboarding consideration

### 7.1 Solo detection on tenant creation

When new tenant created (Phase 4c salon-onboarding flow per `2026-05-17-salon-onboarding-handoff.md`):

**Step:** «С чего ты сегодня?» — self-employed detection.

Options presented:
- `[ Я работаю одна / один ]` — solo path
- `[ Я владею салоном с командой ]` — team path
- `[ Команда придёт позже, начну сама ]` — hybrid (solo now, team-ready)

**All three paths run the same backend auto-seed:**
- Create BotUser (already exists from MAX OAuth)
- Create `TenantStaff(role=owner)` for this BotUser
- Create `TenantStaff(role=admin)` for **same BotUser** (multi-role per ADR-0008)
- Create `CatalogMaster(linked_bot_user=self)` if «работаю одна» OR «начну сама»

**Why auto-seed all 3 roles for solo path:**
Olga не должна manually promote herself to admin / register as master. Per ADR-0008 Decision 6: customer access is never gated by staff role + multi-role is additive. Auto-seeding = invisible to user, она просто видит «мой кабинет готов».

**Ownership (per tech lead correction 2026-05-25, ADR-0009 §5 hard rule):**

Все 3 модели (`TenantStaff`, `CatalogMaster`, `BotUser`) live в bot-platform repo (`apps/tenancy/`, `apps/catalog/`, `apps/identity/`). Per ADR-0009 §5 — Alpha (Ayla djangoproject) НЕ может writing в bot-platform models. Поэтому:

- **W4 owns** atomic transaction service: `apps/identity/services/solo_onboarding.py` (new file)
- **W1 wires** UI onboarding flow that calls W4 service
- **Alpha role minimal** — может emit event `provider.relationship_formed` after seeding, если Ayla side needs to know (cross-domain event consumer pattern)

### 7.2 First-time UX

```
┌──────────────────────────────────────────────┐
│                                               │
│           ☀                                   │
│                                               │
│   Готовлю твой кабинет — пара минут          │
│                                               │
│   ┌──────────────────────────────────────┐  │
│   │  ✓ Профиль создан                     │  │
│   │  ✓ Кабинет открыт                     │  │
│   │  ✓ Расписание заведено по умолчанию   │  │
│   │  ○ Скоро добавишь услуги              │  │
│   └──────────────────────────────────────┘  │
│                                               │
└──────────────────────────────────────────────┘

After 2-3 sec → land на solo Мой день.
```

**Voice:** «Готовлю твой кабинет» — first-person Ayla, present action. Checklist показывает progress. Ничего не упоминает «owner / admin / master» mechanics — Olga не должна знать о ролях.

### 7.3 Auto-seed roles invisible to user

В `/api/v1/me` response для Ольги после onboarding:
```json
{
  "primary_role": "owner",   // highest privilege per ADR-0008 Decision 3
  "all_roles": ["owner", "admin", "master"],   // optional debugging
  "is_solo_provider": true,
  ...
}
```

Ольга НЕ видит «у тебя 3 роли» где-либо. Эта complexity лежит backend-side. Mini App рендерит **«Ольга, мастер, владеешь Студия Ольги»** в profile chip — three roles compress в одну identity.

### 7.4 Hybrid path («команда придёт позже»)

Same backend auto-seed as «работаю одна». Frontend = solo surface (is_solo_provider = true).

Difference — `is_solo_provider` flips к false автоматически когда Ольга invites first master who accepts. До тех пор identical solo UX. Onboarding hint «команда придёт позже» хранится в analytics flag «expected_growth=team», но не влияет на rendering.

### 7.5 Onboarding back-fill для existing tenants

**Tenants creates before this policy (Phase 4c soft launch tenants):**
- Migration runs `is_solo_provider` computation against existing data
- Если результат = solo → re-fetch `/api/v1/me` next time user opens — UI flips on its own (no force-logout)
- No data migration of TenantStaff/CatalogMaster — already в правильной shape per ADR-0008

---

## 8. Voice / tone considerations

### 8.1 Solo Ольга при общении с Ayla

Existing policies (`owner-conversational-templates.md` + `master-conversational-templates.md`) split tone:
- **Owner-tone:** partner-style, business insights («твои визиты выросли на 18%»)
- **Master-tone:** functional, daily ops («Анна на месте, ждёт первого клиента»)

Для solo Olga оба контекста merge. Voice adaptation:

| Context | Voice register | Example |
|---------|----------------|---------|
| Olga asks about business («сколько записей сегодня?») | Owner-tone (partner-style, factual) | «Сегодня 3 записи. Самая ближайшая в 10:30 — Анна на маникюр гель-лак.» |
| Olga gets customer-arrival ping («Анна пришла, ждёт у двери») | Master-tone (functional, action) | «Анна Петрова на месте — готова принимать?» |
| Olga asks Ayla to «спросить customer something» | Owner-tone (Ayla drafts message in customer's voice) | «Какой текст Анне?» → drafts message → Olga reviews |
| Olga reports issue with customer no-show | Empathetic + factual hybrid | «Поняла. Запишу no-show. Если повторится — расскажу когда удобно ей перенести.» |
| Olga onboards (first time after auto-seed) | Owner-tone, welcoming | «Готово, кабинет твой. С чего начнём?» |

### 8.2 «Команда» wording eliminated

Per memory: «Команда» = manager-language, alienating для solo. Eliminate wherever possible.

| Anti-pattern (team voice for solo) | Solo voice |
|-----------------------------------|------------|
| «Прислала ли мне отдых заявку?» | «Закроем выходной?» |
| «Команда сегодня» | «У тебя сегодня» |
| «Coordination with team members» (any UI string) | not present |
| «Approve your time-off» (Olga has no one to approve from) | feature hidden entirely per §4.2 |

### 8.3 Solo identity language

Ольга в Profile chip = «**Ольга** · Мастер маникюра · Студия Ольги»

- Имя first, не «Ольга Иванова, владелица студии Ольги» (corporate-speak)
- Service specialty прежде чем organizational role
- «Студия Ольги» — наследует имя владелицы. Tenant name = often «{name}» pattern для solo

NOT: «Ольга Иванова, генеральный директор ООО Студия Ольги» — нет места в Ayla brand для bureaucratic titles.

### 8.4 Customer-facing voice — same Ayla

Customer (Анна) interactions с Ayla **identical** независимо от solo vs team tenant. Per `tenant-as-provider-model.md`: customer interacts с Ayla, salon = third-party reference. Solo Ольга is referenced same как team Татьяна — «у Ольги в Студии Ольги свободно завтра в 15:00».

No leakage of solo/team distinction в customer-facing copy.

---

## 9. Transition: solo → team growth

### 9.1 The transition trigger

Solo Olga invites first master via Phase 4c master invite flow (per `master-onboarding-m0-m7.md`).

When invited master **accepts** (M0 → M1 stage transition):
- New BotUser linked OR existing BotUser linked → CatalogMaster row created с `linked_bot_user`
- Tenant теперь имеет 2 distinct people (Olga + new master)
- `is_solo_provider` flips to `false`

### 9.2 Mini App UX during transition

**On master acceptance:**

Ольгa's Mini App на next `/api/v1/me` refresh (within 30 sec polling OR on tab refocus):
- `is_solo_provider` = false
- Team features auto-unhide (Команда tab появляется in nav, approval queue exists, etc.)

**Smooth animation:** new tab «Команда» fade-in (не jarring pop). Ольга видит:

```
Toast (3 sec):
╭──────────────────────────────────╮
│  Карина приняла приглашение.      │
│  Теперь вы вдвоём — добавила      │
│  раздел «Команда».                │
╰──────────────────────────────────╯
```

Toast tappable → opens новый «Команда» tab. Если Ольга не tap — toast dismisses, tab остаётся в nav.

### 9.3 Solo-only data persistence

Все Ольгины данные as solo (catalog, schedule, clients, earnings) сохраняются. No migration. Team mode просто **expands** UI surface — old data still accessible через свои tabs.

«Доходы» tab teamversion shows breakdown by master. Ольга видит «моя строка + Карина строка» — её solo earnings становятся одной из две master rows.

### 9.4 Team → solo regression

Edge case: Ольга deactivates Карину (cooperative offboarding). Now tenant имеет only Ольгу again.

- `is_solo_provider` flips back to `true`
- Team features auto-hide
- Ольга's UI returns to solo surface

**No data loss** — Карина's archived records (past visits, reviews etc.) сохраняются accessible через filter «архив мастеров» в Команда... которой больше нет в nav!

Edge resolution: when `is_solo_provider` flips back to true, преамбула banner на «Мой день» on first reload:

```
ℹ Карина деактивирована. Её прошлые записи сохранены в Доходы → Архив.
   Если приведёшь нового мастера — расскажу.
   [ Понятно ]
```

Banner dismisses, solo surface continues. История accessibility preserved через alternative path («Доходы → Архив» tab footer).

### 9.5 Pricing implications

**None per memory `pricing-model-hybrid`** + founder decision: same product solo / team. No tier change при transition.

---

## 10. Backend mapping

### 10.1 New API surface

| Endpoint | Field | Computed from | Notes |
|----------|-------|---------------|-------|
| `GET /api/v1/me` | `is_solo_provider: bool` | `count(distinct active people in tenant) == 1` per §3.1 | NEW field. Frontend reads on app boot + on tab refocus |
| `GET /api/v1/me` | `all_roles: [...]` (optional, debugging) | Existing per ADR-0008 | Frontend uses primary_role for chip; capability set for menu |
| Existing: `GET /api/v1/me` | `primary_role` | Highest priv per ADR-0008 Decision 3 | Unchanged |
| Existing: `GET /api/v1/me` | `capabilities` | Per ADR-0008 + role_resolver | Unchanged |

### 10.2 Existing models (no schema changes)

Per ADR-0008 + investigation 2026-05-25 — все необходимые tables exist:
- `tenancy_tenantstaff` — multi-role per ADR-0008 Decision 2 (unique_together constraint)
- `catalog_catalogmaster.linked_bot_user` — OneToOne к BotUser per ADR-0008 Decision 2

### 10.3 Mini App router change (P0 для pilot)

**`apps/miniapp/src/App.tsx:263-267`** cascade exclusive routing fix:

Current (broken для solo):
```typescript
if (role === "customer") return <CustomerRoutes />;
else if (role === "master") return <MasterRoutes />;
else if (role === "admin") return <AdminRoutes />;
// owner-master Olga blocked from /master/* — она routed к /admin only
```

Fix (~50 LoC W1):
```typescript
// Inclusive — render routes for ALL roles user has
return (
  <>
    {capabilities.includes("view_customer_self") && <CustomerRoutes />}
    {capabilities.includes("master_self") && <MasterRoutes />}
    {capabilities.includes("admin_*") && <AdminRoutes />}
  </>
);
// Olga renders все три stacks; router resolves correct screen per URL
```

Detail W1 stream owns. Этот doc только references что fix needed.

### 10.4 Onboarding seeding (P0)

Per §7.1 — Phase 4c salon-onboarding flow auto-creates 2 TenantStaff rows + 1 CatalogMaster для solo path.

**Ownership corrected 2026-05-25 per ADR-0009 §5 hard rule:**
- **W4 owns** atomic transaction service `apps/identity/services/solo_onboarding.py` (~2-3 days)
- **W1 wires** UI onboarding flow that calls W4 service
- **Alpha** role minimal — only `is_solo_provider` API field computation if it lives in miniapp_api views; otherwise W4 also computes server-side

Reasoning: `TenantStaff` / `CatalogMaster` / `BotUser` все в bot-platform repo (`apps/tenancy/` / `apps/catalog/` / `apps/identity/`). Alpha (Ayla djangoproject) не может writing в bot-platform models per ADR-0009 split-domain architecture rule #5.

### 10.5 Solo unified surface (P1 polish)

Mini App reads `is_solo_provider`, conditionally renders nav tabs. ~6-8 hours W1 (per founder coordination note).

---

## 11. Anti-patterns

### 11.1 НЕ делаем

- ❌ Отдельное приложение «Ayla Pro Solo» — нарушает universal codebase principle
- ❌ «Solo mode / Team mode» toggle в settings — Olga не должна выбирать, detection automatic
- ❌ Просить Olga «promote yourself to master» — auto-seed handles
- ❌ Маркетинговый pricing tier «Solo $X / Team $Y» — same product per memory
- ❌ «Coming soon: Team features» promos для solo — premature upsell, alienates
- ❌ Grayed-out / disabled buttons для hidden team features — silent hide, не teasing
- ❌ Onboarding asking «как тебя называть в системе» — Olga имеет MAX identity, имя приходит оттуда
- ❌ Showing «Team» tab as empty placeholder («приведи мастеров чтобы заполнить») — пустой surface = bad UX, лучше не показывать вообще
- ❌ Separate Stories / Help docs для solo vs team — single docs adapt voice depending on `is_solo_provider` flag in support context
- ❌ Force pre-registration of «business type» before tenant creation — onboarding §7.1 self-employed detection comes mid-flow naturally

### 11.2 Cross-doc «Открыть обсуждение» buttons — conditional

Per memory `solo-provider-universal-ui` + earlier memory `cross-doc-buttons-post-pilot` (superseded):

| Tenant type | Behavior | Reason |
|---|---|---|
| Solo | **Hidden always** | Internal discussion moot — Olga is sole participant. Button leads nowhere meaningful |
| Team | **Conditionally shown** | Если low scope implementation. Если scope grows — defer to post-pilot |
| Post-48h research | **Re-evaluate** | If team penetration высокая → full impl. Else stay conditional |

W1 stream owns the conditional gate implementation. Solo always hidden = simple. Team conditional = depends on implementation cost.

### 11.3 Mode confusion

Solo Olga'у никогда НЕ должны увидеть:
- «Switch to admin view» (нет admin view — все в одном)
- «Open as master» (она и есть master)
- «Manage team» (no team to manage)
- «Approve someone's request» (no one to approve)

If she somehow reaches a URL для team feature (bookmark from web, deep-link in stale email), graceful empty state:
```
«Пока ты одна. Когда приведёшь мастера —
расскажу как пригласить. [ Назад ]»
```

---

## 12. Open questions

| # | Question | Lean | Owner | Urgency |
|---|----------|------|-------|---------|
| Q-SOLO-1 | Onboarding self-employed detection — explicit choice («работаю одна») vs implicit (если она не invited masters в первые 7 days) vs hybrid? | ✅ **Hybrid APPROVED** (tech lead 2026-05-25) — explicit choice на onboarding step (primary) + auto-fallback to solo if no master invites within 7 days (safety net) | PM + UX | ✅ resolved |
| Q-SOLO-2 | First-time UX «Готовлю твой кабинет» — actual processing time или artificial delay? | Real time — auto-seed runs <500ms backend; show checklist 2-3 sec via animation для perceived progress, не real wait | Eng + UX | 🟢 |
| Q-SOLO-3 | If solo Olga имеет tenant name = default «Студия Ольги», offer rename в onboarding? | YES — после tenant create, soft prompt «Назвать иначе?» в Profile tab onboarding. Default usable если skip | UX | 🟢 |
| Q-SOLO-4 | Solo Olga's «Команда» empty state if she navigates direct URL — show or 404? | Empty state with explanation + CTA, not 404. URL access не должно ломаться | UX | 🟢 |
| Q-SOLO-5 | Solo → team transition toast — sticky или auto-dismiss? | Auto-dismiss 3 sec + tab появление animation. Toast не sticky (не блокирует workflow) | UX | 🟢 |
| Q-SOLO-6 | Team → solo regression edge — preserve archived staff data accessible via where? | «Доходы → Архив» sub-tab + filter «архивные мастера». Banner once on transition explains | UX + Eng | 🟢 |
| Q-SOLO-7 | Multi-tenant solo (Olga работает в Studia Karina master + own tenant solo)? | Out of pilot scope. Phase 2+ requires separate identity model design | Founder | 🟢 deferred |
| Q-SOLO-8 | Voice mode (TTS) для solo? | Phase 2+ same as team — no solo-specific voice scope | UX | 🟢 deferred |
| Q-SOLO-9 | Onboarding seeding atomic? If TenantStaff(owner) creates but TenantStaff(admin) fails — partial state? | ✅ **Atomic transaction REQUIRED** (tech lead 2026-05-25). Ownership corrected per ADR-0009 §5 — **W4 owns** atomic service `apps/identity/services/solo_onboarding.py` (NOT Alpha — bot-platform models live в bot-platform). W1 wires UI flow. Alpha optional minimal role | W4 (primary), W1 (UI wire), Alpha (event emit if applicable) | ✅ resolved |
| Q-SOLO-10 | `is_solo_provider` flips during active session — Mini App reloads / refreshes / animations smoothly? | Stale-while-revalidate 30s polling. Smooth fade-in animation для new tabs. No force-reload | Eng + UX | 🟢 |
| Q-SOLO-11 | 48-hour Penza research deferred to: when finalized? | Research outside this doc's scope. If solo % высокая (>50%) — confirm direction. Если <30% — reconsider | Founder | 🟢 (informs post-pilot refinement) |
| Q-SOLO-12 | Cross-doc buttons на team tenant — final lean: ship conditional или defer? | ✅ **Full impl DEFERRED post-pilot** (tech lead 2026-05-25). Conditional gating infrastructure (`is_solo_provider` field) ships с P1 solo surface work — zero marginal scope. Buttons themselves не build для пилота | Tech Lead | ✅ resolved |
| Q-SOLO-13 | Solo Olga добавляет «admin assistant без master service» (она hires receptionist) — что произойдёт? | Tenant классифицируется как team (2 distinct people now). Receptionist role per ADR-0008 already supported. Solo features hidden, team features unhide. Edge tested separately | Eng | 🟢 |

---

## 13. Acceptance criteria (cross-doc enforcement)

Любая UX работа на admin / master surfaces must satisfy:

- [ ] Reads `is_solo_provider` from `/api/v1/me` per §3
- [ ] Hides team-only features per §4.2 matrix when `is_solo_provider = true`
- [ ] No grayed-out / disabled team buttons для solo (silent hide)
- [ ] No «Switch to team mode» toggles
- [ ] Solo Olga's identity chip merges owner+master roles per §8.3
- [ ] Voice copies (Ayla → Olga) merge owner+master tones per §8.1
- [ ] No «команда» wording в solo-visible copy per §8.2
- [ ] Empty states speak first-person Ayla, not «функция недоступна»
- [ ] Onboarding auto-seeds 2 TenantStaff + 1 CatalogMaster atomically (§7.1)
- [ ] Solo → team transition unhides UI без force-reload (§9.2)
- [ ] Team → solo regression preserves archived data accessible (§9.4)
- [ ] Cross-doc «Открыть обсуждение» buttons hidden для solo (§11.2)

---

## 14. Cross-document linkage

### Foundation
- [`ADR-0008`](../../adr/ADR-0008-role-detection-and-staff-model.md) — Role detection model. **This doc consumes ADR-0008 decisions.**
- Memory `project_solo_provider_universal_ui` — founder decision context (2026-05-25)

### Affects (re-frame required)
- [`master-onboarding-m0-m7.md`](./master-onboarding-m0-m7.md) — M0-M7 stages assume team context. Solo path runs same stages but auto-completes M0 (invite_sent → invite_accepted) self-invite skip
- [`2026-05-17-salon-onboarding-handoff.md`](../handoffs/2026-05-17-salon-onboarding-handoff.md) — Add «self-employed detection» step §7.1
- [`owner-conversational-templates.md`](./owner-conversational-templates.md) + [`master-conversational-templates.md`](./master-conversational-templates.md) — Voice merges for solo §8.1
- [`information-architecture.md`](./information-architecture.md) — Solo surface Bundle reference added §5

### Consumed by (downstream implementers)
- **W4** — Atomic auto-seed service `apps/identity/services/solo_onboarding.py` (§7.1, §10.4) per ADR-0009 §5 ownership rule. Optional: server-side `is_solo_provider` computation if W4 also owns миniapp_api views path
- **W1** — `App.tsx` inclusive routing (§10.3), `is_solo_provider` UI render rules (§4), solo surface tabs (§5), wires onboarding UI flow to W4 service (§7.1), cross-doc gating infrastructure (§11.2)
- **Alpha** — Role minimal. ONLY если `is_solo_provider` API computation lives in Ayla djangoproject; otherwise zero scope. Optional emit `provider.relationship_formed` event consumer side
- **Tau** — Future master-side mockups must include solo path per §13 acceptance

### Strategic
- [`tenant-as-provider-model.md`](./tenant-as-provider-model.md) — Solo is one tenant configuration. Salon brand visible to customer same way regardless of solo/team
- [`ayla-identity-and-brand.md`](./ayla-identity-and-brand.md) — Ayla voice consistent customer-side regardless of provider scale

### Engineering
- `apps/identity/services/role_resolver.py::resolve_role` — already supports multi-role per ADR-0008
- `apps/tenancy/models.py::TenantStaff` — `unique_together` constraint enables multi-role rows
- `apps/catalog/models.py::CatalogMaster.linked_bot_user` — OneToOne enables «Olga is also a master»
- `apps/miniapp/src/App.tsx` — inclusive routing fix (~50 LoC W1)

---

## 15. What this unblocks

- **Solo provider pilot inclusion** — Olga может зарегистрироваться, работать, принимать customers, получать payments с пилота 15 July
- **Universal UI design pattern** — single codebase для всех provider scales, lower long-term tech debt
- **Onboarding seeding automation** — Alpha + W1 знают что auto-create per role
- **Cross-doc button policy** — solo always hidden simplifies that polish
- **Future master mockups by Tau** — must include solo lens, this doc = source of truth
- **48-hour research outcome integration** — research result (% solo в Penza) doesn't change direction, only informs polish priority

## 16. What this does NOT unblock

- ❌ Multi-tenant solo (Olga master at Studia Karina + own tenant) — Phase 2+
- ❌ Family solo (Olga + non-master helper) — defer (counts as team currently)
- ❌ Pricing differentiation — same product per memory
- ❌ Voice / TTS / STT — Phase 2+ orthogonal scope
- ❌ Customer-side awareness of solo vs team — by design transparent
- ❌ Cross-tenant inference («одна и та же Olga в 3 tenants») — ADR-0008 cross-tenant isolation preserved
- ❌ Marketing positioning «Ayla for Solos» — Marketing scope, this is product policy

---

## 17. Sign-off

| Role | Approval | Date |
|---|---|---|
| Founder (foundational decision) | ✅ | 2026-05-25 |
| UX Architect | ☐ | (pending review) |
| Tau (this doc's author) | ✅ | 2026-05-25 |
| Tech Lead (W4 + W1 + Alpha coordination, ownership correction per ADR-0009 §5) | ✅ | 2026-05-25 (r1 approved with ownership corrections applied) |
| W4 (atomic auto-seed service `apps/identity/services/solo_onboarding.py`) | ☐ | (pending implementation §7.1 + §10.4) |
| W1 (router fix + solo surface UI + onboarding UI wiring) | ☐ | (pending implementation §10.3 + §10.5 + UI wire to W4 service) |
| Alpha (optional minimal role) | ☐ | (pending if `is_solo_provider` computation lives Ayla-side) |
| Engineering (multi-role server semantics — already exists per ADR-0008) | ✅ | 2026-05-19 (per ADR-0008 sign-off) |

## Last verified
2026-05-25 r1 — Initial draft from Tau per founder coordination 2026-05-25. **Tech lead approved 2026-05-25 with ownership corrections applied** (§7.1 + §10.4 + §14 Consumed by — Alpha→W4 per ADR-0009 §5 hard rule). Q-SOLO-1 hybrid approach approved. Q-SOLO-9 ownership corrected. Q-SOLO-12 cross-doc buttons full impl deferred post-pilot (conditional gating infrastructure piggy-backs P1 solo surface zero marginal scope).
