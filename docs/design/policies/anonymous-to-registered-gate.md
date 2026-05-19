# Anonymous → Registered Gate Policy

**Date:** 2026-05-19 r1
**Status:** STRATEGIC FOUNDATION — Doc #5 of 5 in Ayla-first foundation set. Defines when customer must register vs can browse anonymously.
**Reads:** [`ayla-identity-and-brand.md`](./ayla-identity-and-brand.md), [`ayla-memory-and-personalization.md`](./ayla-memory-and-personalization.md), [`ayla-emergency-fallback-policy.md`](./ayla-emergency-fallback-policy.md), [`tenant-as-provider-model.md`](./tenant-as-provider-model.md), [`customer-first-touch-and-mini-app-states.md`](./customer-first-touch-and-mini-app-states.md), memory `project_ayla_first_strategic_pivot`, Notion: AI-01 (`338b0dab-2955-8105-b117-c5db1a17633e`) — «Анонимный пользователь может задать запрос и увидеть подборку; Gate срабатывает только при нажатии «Записаться»»

> Customer can browse Ayla, ask questions, see master cards, get recommendations — all without registering. Registration triggers only when customer commits action requiring identity (booking) or wants persistent personalized experience (saved history, memory across sessions). Locks acquisition funnel to «try before commit» pattern.

---

## 0. Why this exists

### 0.1 The strategic pivot

Per memory `project_ayla_first_strategic_pivot` (locked 2026-05-19) decision #9:

> Пользователь должен смотреть, пробовать, спрашивать без регистрации. Регистрация/телефон нужны только когда: хочет записаться / хочет сохранить историю / хочет получить персональные рекомендации.

Per Notion AI-01 acceptance criteria #7: «Анонимный пользователь может задать запрос и увидеть подборку; Gate срабатывает только при нажатии «Записаться»».

### 0.2 The acquisition funnel logic

| Step | What customer does | Identity required? |
|---|---|---|
| 1 | Customer opens Ayla Mini App (cold) | NO |
| 2 | Customer types «нужен маникюр» in AI input | NO |
| 3 | Ayla shows 5 master cards | NO |
| 4 | Customer browses master profiles, prices, photos | NO |
| 5 | Customer reads reviews from other customers | NO |
| 6 | Customer asks Ayla follow-up («а вечером?») | NO |
| 7 | Customer taps «Записаться» on a master card | **YES — registration triggered** |
| 8 | Customer completes registration via MAX | YES |
| 9 | Customer confirms booking | YES (already registered) |
| 10 | Customer returns next day, asks Ayla «найди ещё мастера» | NO (registered, but anonymous chat still works) |

«Try before commit» — лочит max acquisition friction.

### 0.3 The danger if we don't draw this line

Without explicit gate logic:
- Mini App may demand registration on first open («Введи телефон чтобы продолжить») — kills acquisition funnel
- Anonymous browsing may accidentally build persistent memory (privacy violation — no consent)
- Customer's registered profile may not link with anonymous session («I asked yesterday, now you forgot»)
- Or worst: anonymous session accumulates data, registration retroactively claims it — privacy breach
- Tenant may see anonymous browsing volume as «their leads» — wrong ownership model
- Anonymous customer's data may persist indefinitely with no retention rule

### 0.4 The promise

Single source for:
- What anonymous customer can do §2
- What requires registration §3
- The gate UX moment §4
- Identity model — anonymous session → registered customer §5
- Data continuity at gate transition §6
- Ayla's behavior with anonymous user §7
- Anonymous session retention + privacy §8
- Edge cases (abandon / multi-device / cross-tenant) §9
- MAX deep-link flow integration §10
- Tenant-as-provider relationship §11
- Anti-patterns §12
- 3 NEW models, 12 endpoints, 8 events

---

## 1. Scope

### IN
- Mini App anonymous browsing
- AI chat with Ayla for anonymous users
- Master discovery + comparison anonymously
- Recommendations (lightweight) for anonymous users
- The single gate trigger — «Записаться» (booking commit)
- Secondary gate triggers §3.2 (save history, personalized recs)
- Anonymous session model + retention
- Anonymous-to-registered linking at gate moment
- Ayla's voice tone for anonymous vs registered
- MAX deep-link registration flow (per Notion)
- Multi-tenant anonymous browsing (tenant-aware, identity not required)
- 3 NEW models (`AnonymousSession`, `GateTransitionEvent`, `AnonymousMemoryDraft`)
- 12 endpoints, 8 events

### OUT
- Specific MAX OAuth implementation (engineering scope per existing identity stack)
- Anti-fraud detection on anonymous abuse — Phase 4+
- Anonymous-to-anonymous chat (out of scope — no such feature)
- Cross-platform anonymous identity (out of scope MVP; Mini App + Bot DM only)
- Anonymous customer's per-tenant analytics (tenant sees no anonymous-traffic data per Doc #4 §3.3)
- Anonymous customer reviews (cannot leave reviews without registration)
- Anonymous customer loyalty (no loyalty for anonymous; account creation required per `customer-loyalty-rewards-ux §5.1`)
- Anonymous wellness module logging (modules require registered customer per existing wellness handoffs)
- Anonymous customer access to refund disputes / privacy data export — N/A (need to be registered first)
- Anonymous-only «guest checkout» permanently (not supported; gate triggers registration always for bookings)
- Phone-only registration (without MAX) — Phase 4+ if needed for non-MAX users
- Multi-language anonymous browsing (Phase 3+ Kazakh)
- Voice anonymous browsing (Phase 2+ voice scope)

---

## 2. Strategic constraints — non-negotiable

### 2.1 Default = anonymous-friendly
Mini App opens to functional state without ANY registration prompt. Customer sees AI input field + can immediately interact. NO splash «Sign up to continue».

### 2.2 Gate triggers explicit
Only specific actions trigger registration:
- **Primary:** «Записаться» button tap (booking commit)
- **Secondary opt-in:** «Сохранить историю» / «Получать персональные рекомендации» (customer's explicit request)
- **Implicit / soft:** customer reaches limit of anonymous capability (e.g., 10 chats in a session — soft prompt, not block) Phase 3+

Customer-driven gate, not platform-pushed.

### 2.3 Anonymous customer is real customer
Per Doc #4 §2.1: salon is provider. Anonymous customer is still customer-of-Ayla; salon is still provider. Tenant cannot demand registration «to see who's looking».

### 2.4 No backend «pre-registration»
- ❌ Backend doesn't quietly create BotUser for anonymous session («ghost account»)
- ✅ Backend creates `AnonymousSession` row — separate from `BotUser`
- ✅ Registration moment is when `BotUser` is created from MAX OAuth
- ✅ `AnonymousSession` may link to `BotUser` if customer registers within session

### 2.5 Anonymous data is anonymous
Per Doc #2 §1.3: Ayla memory belongs to user. Anonymous = no user yet → no memory accumulation. Per-session ephemeral context only.

### 2.6 Anonymous session memory is short-lived
- 24 hours active (browsing context preserved in-session)
- Per-tab / device-fingerprint scoped
- Auto-purged after 24h inactivity
- NOT carried to other devices anonymously

### 2.7 Registration consent
Per `customer-privacy-data-closure-ux` + Doc #2 §12: when customer registers, explicit consent for memory accumulation. Anonymous session's draft data per §6 either:
- Discarded
- Or carried forward with customer's explicit accept

### 2.8 Voice with anonymous user same as registered
Per Doc #1 §3 — Ayla voice consistent. No «register first to get full Ayla». No condescending tone for anonymous.

### 2.9 No anonymous «memory teaching»
- ❌ Anonymous can't say «помни что я люблю утро» and have it persist
- ✅ Anonymous session can pass that to in-session reasoning, but at session end, gone
- ✅ Customer is informed: «эти предпочтения сохранятся, если зарегистрируешься»

### 2.10 No mid-action gate without explanation
When gate triggers, Ayla explains why:
- ✅ «Чтобы записаться, нужна минута на регистрацию — это безопасно и быстро через MAX»
- ❌ Silent redirect to MAX OAuth

### 2.11 Per-tenant anonymous browsing tracked aggregate
Per Doc #4 §3.3: tenant sees own salon's anonymized aggregate, including anonymous visits to their pages. NO per-anonymous-session tracking shared with tenant; aggregate only.

### 2.12 Anonymous cannot trigger emergency
Per Doc #3: emergency tiers require existing customer (refund / no-show / privacy / etc. all need account). Anonymous can ask questions but cannot open dispute (no booking yet).

### 2.13 Anonymous cannot perform side-effects on tenants
- ❌ Anonymous cannot leave reviews
- ❌ Anonymous cannot rate masters
- ❌ Anonymous cannot reserve a slot tentatively (no «hold» without identity)
- ❌ Anonymous cannot trigger no-show flag
- ✅ Anonymous can VIEW reviews, ratings, master cards

### 2.14 Anonymous → registered is one-way commit
Once registered, customer's identity persists. Cannot «de-register to anonymous» mid-session. Customer can later close account per `customer-privacy-data-closure-ux.md` — fresh anonymous browsing OK after closure.

### 2.15 Anonymous customer doesn't see Ayla memory transparency
Per Doc #2 §5: «Что Ayla знает обо мне» surface requires authenticated user. Anonymous user sees: «здесь будет твоя память Ayla, когда зарегистрируешься».

---

## 3. What requires registration

### 3.1 Primary gate trigger — «Записаться»

Per Notion AI-01 AC: customer taps «Записаться» button on master card → gate fires.

**Why this is THE primary trigger:**
- Booking commits real-world consequence (master holds slot, customer arrives at salon)
- Salon needs identity for service delivery (legal, safety, customer recognition)
- Customer's first booking commits identity reasonably

### 3.2 Secondary gate triggers (customer-initiated)

| Trigger | Customer asks via | Reason |
|---|---|---|
| Save chat history across sessions | «Сохранить нашу переписку» button in Mini App OR voice command «запомни нашу историю» | Memory persistence requires identity |
| Receive personalized recommendations | «Хочу персональные рекомендации» CTA | Personalization requires memory |
| Activate wellness module | Module tile «Подключить» | Module data requires identity per wellness handoffs |
| Set goals (wellness) | Goal-setting flow | Goals require Ayla memory |
| Use loyalty (earn / redeem) | «Бонусы» tab → «Начать накапливать» | Loyalty account requires identity |
| Bookmark master | Heart icon on master card | Persisted favorites require identity |
| Multi-device sync | Tap «Синхронизировать с другим устройством» | Identity required for cross-device |

### 3.3 Soft gate (advisory, not block)

Phase 3+ — soft prompts:
- After 10 chats in anonymous session: «Зарегистрируйся, чтобы я помнила тебя в следующий раз»
- After viewing 3 masters and asking comparisons: «Личные рекомендации точнее — если зарегистрируешься, могу помнить твои предпочтения»
- NOT blocking — customer can dismiss + continue anonymous

MVP: no soft gates — just primary + secondary explicit triggers.

### 3.4 What does NOT require registration

Comprehensive list (per §2.1, §2.13):
- Open Mini App
- Type AI input
- Get master recommendations
- View master cards (photo, rating, services)
- View prices
- View available slots
- Read other customers' reviews
- Ask Ayla follow-up questions
- Compare masters
- Filter masters by criteria (location, price range, service)
- Ask Ayla about salon (hours, address, services)
- View tenant profile pages
- Read tenant policies (refund, cancellation, etc.)

### 3.5 Tenant cannot demand earlier registration

Per Doc #4 §2.6: tenant cannot customize Ayla. Tenant cannot add «Зарегистрируйся чтобы увидеть наши услуги» gate. Tenant's content visible to anonymous customers.

---

## 4. The gate UX moment

### 4.1 Anonymous customer taps «Записаться»

Ayla intercepts, shows registration prompt:

```
┌────────────────────────────────────────┐
│ 🌸 Чтобы записаться                       │
├────────────────────────────────────────┤
│ Я подобрала Лену С. на пятницу 14:00     │
│ — отлично!                                │
│                                        │
│ Чтобы записаться, нужна минута на        │
│ регистрацию — это быстро через MAX:      │
│                                        │
│ ── ──                                    │
│ ✓ Подтверди свой MAX-аккаунт              │
│ ✓ Я запомню тебя для следующего раза     │
│ ✓ Мастера увидят, кто к ним записан      │
│                                        │
│ ── ──                                    │
│                                        │
│ [Зарегистрироваться через MAX]            │
│                                        │
│ [Назад — продолжить смотреть]             │
└────────────────────────────────────────┘
```

### 4.2 Tone of gate

- Calm, action-oriented (per Doc #1 §3.1)
- Customer's chosen master/slot mentioned by name (continuity)
- Brief explanation of why
- Reversible — «продолжить смотреть» visible

### 4.3 Secondary triggers (customer-initiated registration)

Customer explicitly taps «Сохранить историю» — Ayla replies:

```
{{customer_first_name_or_unknown}}, чтобы сохранить нашу переписку и
вернуться к ней — нужна короткая регистрация через MAX. Минута, и я
запомню тебя.

[Зарегистрироваться]   [Не сейчас]
```

«Не сейчас» — anonymous session continues; gate didn't fire.

### 4.4 MAX OAuth flow

Per Notion / existing identity stack:
1. Customer taps «Зарегистрироваться через MAX»
2. Redirect to MAX OAuth in Mini App context
3. MAX returns identity (max_username, phone if granted)
4. Backend creates `BotUser` row with MAX identity
5. Anonymous session's draft data may link per §6
6. Customer returns to Ayla — now registered

If MAX OAuth fails:
- Customer sees graceful error («что-то с MAX, можешь попробовать через минуту»)
- Anonymous session preserved
- Customer can retry

### 4.5 Post-registration acknowledgment

```
🎉 Готово! Теперь я буду помнить тебя.

Возвращаемся к Лене С. — записываю на пятницу 14:00. Подтверждаешь?

[Да, записаться]   [Передумала]
```

Continuity preserved — customer's pre-registration intent honored.

### 4.6 Customer abandons gate

If customer taps «Назад» / closes Mini App without completing:
- Anonymous session continues
- Selected master/slot tentative info preserved in session (NOT a real reservation)
- Returning next day → if session expired, customer starts fresh
- If session active (within 24h), Ayla picks up context: «помню, ты смотрела Лену С. на пятницу — посмотришь варианты?»

### 4.7 Gate retries

Customer attempts «Записаться» 3+ times without completing registration — Ayla doesn't push:

```
{{... pause without nagging ...}}

Без проблем — посмотреть и сравнить тоже полезно. Когда будешь готова
записаться — я тут.
```

NO repeated prompts. Customer's pace.

### 4.8 Per Notion AC: response time

«AI отвечает в течение ≤ 3 секунд». Includes anonymous chat.

---

## 5. Identity model — anonymous → registered

### 5.1 Two-table model

Anonymous and registered are **separate identities** in the database. Linking happens at registration moment.

```python
class AnonymousSession(models.Model):
    """Customer browsing without registration. Short-lived, ephemeral.
    Per Ayla-first decision #9 — try-before-commit acquisition funnel."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    session_token = models.CharField(max_length=128, unique=True)
    # Generated client-side or upon Mini App open, persisted in localStorage / Mini App state

    device_fingerprint = models.CharField(max_length=64)
    # SHA256 of UA + screen + locale; NOT identifying PII

    ip_audit_only = models.GenericIPAddressField()
    # Per Q-AML privacy: not displayed; audit access only

    created_at = models.DateTimeField(auto_now_add=True)
    last_active_at = models.DateTimeField(auto_now=True)
    expires_at = models.DateTimeField()
    # 24h from last_active_at; auto-purged

    # If customer converts:
    converted_to_user = models.ForeignKey(
        'identity.BotUser',
        null=True, blank=True,
        on_delete=SET_NULL,
        related_name='anonymous_sessions_converted',
    )
    converted_at = models.DateTimeField(null=True, blank=True)

    # Browsing context (NOT memory — discarded at session end OR carried forward at conversion)
    last_search_query = models.TextField(blank=True, default='', max_length=500)
    last_viewed_master_ids = models.JSONField(default=list)
    last_viewed_tenant_ids = models.JSONField(default=list)
    chat_message_count = models.IntegerField(default=0)
    masters_viewed_count = models.IntegerField(default=0)

    class Meta:
        indexes = [
            Index(fields=['session_token']),
            Index(fields=['expires_at']),  # cron scanner
            Index(fields=['converted_to_user']),
        ]
```

### 5.2 Conversion at gate moment

When customer completes registration via MAX OAuth during an anonymous session:

```python
def convert_anonymous_to_user(session_token, max_identity):
    session = AnonymousSession.objects.get(session_token=session_token)

    # Create BotUser
    user = BotUser.objects.create(
        max_username=max_identity['username'],
        phone=max_identity.get('phone'),
        ...
    )

    # Link
    session.converted_to_user = user
    session.converted_at = timezone.now()
    session.save()

    # Per §6: optionally carry forward draft context
    if has_draft_to_carry(session):
        offer_carry_forward(user, session)

    return user
```

### 5.3 Anonymous session token

- Client generates UUID on first Mini App open
- Stored in `localStorage` (Mini App) or session-cookie (web fallback)
- Token sent in API requests as `Anonymous-Session-Token: <uuid>` header (NOT auth token; doesn't grant elevated access)
- Server uses to read context, NOT identity

### 5.4 Returning anonymous customer (same device)

If customer returns within 24h on same device with same session token → Ayla picks up context:

```
С возвращением! Видела, ты смотрела маникюр — продолжим?
```

If > 24h → session expired → fresh start.

### 5.5 No cross-device anonymous linking

- Customer on phone + tablet anonymously → 2 separate sessions
- Cannot merge anonymous sessions across devices
- Only registration unifies (one BotUser, multiple devices)

### 5.6 Anonymous session in Bot DM

If customer interacts via MAX Bot DM (NOT Mini App):
- MAX bot DM identity is MAX-username (already a form of identity)
- Per Notion: bot DM users may always be at least pseudonymous via MAX
- This doc focuses on Mini App anonymous; bot DM is borderline (likely auto-registered upon first interaction since MAX identity available)
- Per Q-AN1 — TBD whether Bot DM has anonymous tier at all

---

## 6. Data continuity at gate transition

### 6.1 The challenge

Customer browses anonymously, finds master, taps «Записаться». What context carries forward?

### 6.2 Always preserved

When anonymous → registered:
- Selected master (master_id customer was about to book with)
- Selected service / slot
- Last search query context
- Active conversation continuity (Ayla doesn't say «здравствуй впервые!»)

Carries forward as part of conversion flow §4.5.

### 6.3 Offered to customer (opt-in carry forward)

If anonymous session had richer context (chat history, viewed masters, comparisons), customer is offered:

```
🎉 Зарегистрирована!

Хочешь сохранить нашу переписку и предпочтения, которые я заметила за
этот час?
✓ Чат с историей сравнения мастеров
✓ Интерес к вечернему времени
✓ Бюджет около 2500₽

[Да, сохрани]   [Нет, начнём с чистого листа]
```

If «Да» — chat history saved to `Conversation` model; preferences saved to `MemoryEntry` with source=`inferred_anonymous_carryforward`, confidence=0.50 (lower than direct customer statement per Doc #2 §4.4).

If «Нет» — anonymous session purged immediately; only the active booking continues.

### 6.4 What NEVER carries forward without consent

Even if customer says «Да, сохрани»:
- Anonymous browsing history of OTHER customers / tenants — never shared
- Anonymous behavioral inferences from very short session — discarded as noise

### 6.5 Anonymous draft memory model

```python
class AnonymousMemoryDraft(models.Model):
    """Temporary inferred preferences from anonymous browsing.
    Either carried forward to MemoryEntry on conversion (with customer consent)
    OR discarded at session expiry."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    session = models.ForeignKey(AnonymousSession, on_delete=CASCADE, related_name='memory_drafts')

    field_key = models.CharField(max_length=64)
    inferred_value = models.JSONField()
    confidence = models.DecimalField(max_digits=3, decimal_places=2, default=0.50)
    # Anonymous inferences ALWAYS confidence 0.30-0.60 (lower than registered)

    inferred_from = models.CharField(max_length=64)
    # 'chat_pattern' / 'master_view_pattern' / 'search_query_pattern'

    created_at = models.DateTimeField(auto_now_add=True)
    carried_forward = models.BooleanField(default=False)
```

### 6.6 Anonymous chat history retention

Anonymous chat is in-memory (per `Conversation` model with `is_anonymous=True` flag):
- Persisted during session
- Auto-deleted with session expiry
- NOT migrated to registered user unless §6.3 consent
- Customer can request export at gate moment («покажи всё что я писала за этот час»)

### 6.7 Gate Transition Event

```python
class GateTransitionEvent(models.Model):
    """Audit row per gate trigger / completion."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    session = models.ForeignKey(AnonymousSession, on_delete=SET_NULL, null=True, related_name='gate_events')

    TRIGGER_CHOICES = [
        ('book_attempt', 'Customer tapped Записаться'),
        ('save_history_request', 'Customer asked to save history'),
        ('personalized_recs_request', 'Customer asked for personalized'),
        ('wellness_module_activation', 'Customer trying to activate wellness'),
        ('loyalty_activation', 'Customer trying to use loyalty'),
        ('bookmark_master', 'Customer trying to bookmark'),
        ('multi_device_sync', 'Customer trying multi-device'),
        ('soft_prompt_dismissed', 'Soft prompt shown + dismissed'),
        ('soft_prompt_accepted', 'Soft prompt accepted'),
    ]
    trigger = models.CharField(max_length=64, choices=TRIGGER_CHOICES)

    OUTCOME_CHOICES = [
        ('registered', 'Customer registered'),
        ('abandoned_gate', 'Customer abandoned at gate'),
        ('postponed', 'Customer postponed («Не сейчас»)'),
        ('failed_max_oauth', 'MAX OAuth failed'),
    ]
    outcome = models.CharField(max_length=32, choices=OUTCOME_CHOICES, blank=True, default='')

    customer_carried_forward_data = models.BooleanField(default=False)
    # Per §6.3 — did customer opt-in to carry forward

    triggered_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
```

---

## 7. Ayla's behavior with anonymous user

### 7.1 Identity tone

- Ayla says «привет» on first interaction (not «привет, {{name}}»)
- No assumption of returning customer
- After 24h within session: «с возвращением» allowed (customer's session preserved)
- Across devices anonymously: fresh start each device

### 7.2 No profile questions

Per Doc #2 §10.1: NEVER on first interaction. Even more for anonymous — Ayla doesn't ask profile questions during anonymous session at all. Customer's preferences inferred from behavior only.

### 7.3 No proactive memory references

Anonymous customer Ayla doesn't say «помню, ты любишь утром» (no memory yet). Within-session context allowed: «ты только что искала маникюр — добавлю варианты с вечером?».

### 7.4 Recommendations less personalized

For anonymous:
- Ayla doesn't have UserPersonalContext to consult
- Recommendations based on stated query + general patterns
- Quality target: «не хуже чем у нового клиента после первого запроса»
- Goal: convert by demonstrating Ayla quality

For registered:
- Full UserPersonalContext consultation (per Doc #2 §13)
- Tier-aware (loyalty level) recommendations
- Goal-aware (wellness goals)

### 7.5 Ayla mentions registration benefit naturally

When relevant, Ayla can mention without nagging:

✅ «Вот 5 мастеров. Кстати — если зарегистрируешься, я запомню твои предпочтения и в следующий раз буду точнее.»

❌ Repeated prompts every reply

Per Q-AN6 — frequency cap: max 1 «registration benefit» mention per anonymous session.

### 7.6 Anonymous customer asks «ты бот?»

Per Doc #1 §6: honest answer same as registered. «Да, я AI Ayla. Помогу подобрать мастера, рассказать про процедуры — всё что нужно перед визитом.»

### 7.7 Anonymous customer asks about Ayla

«Что ты умеешь?» / «Кто ты?» — Ayla replies with capability summary appropriate to anonymous tier:

```
Я Ayla — твой AI-помощник по уходу за собой. Могу:

• Подобрать мастера под твой запрос
• Сравнить варианты по цене / времени / стилю
• Рассказать про процедуры
• Помочь с записью (для этого нужна короткая регистрация через MAX)

Что тебе интересно?
```

### 7.8 Anonymous customer asks about other features

If anonymous customer asks about features requiring registration:

✅ «Wellness модули доступны после короткой регистрации — там можно отслеживать настроение, сон, питание. Если интересно — расскажу подробнее.»

✅ «Бонусы накапливаются после первой записи — это часть программы для зарегистрированных клиентов.»

NOT condescending; just informational.

### 7.9 Anonymous customer asks about specific tenant

✅ «Формула тела — салон в центре города на Тверской. Работают с 9 до 21 в будни, 10-20 в выходные. Услуги: маникюр, педикюр, окрашивание. Хочешь, посмотрю свободные слоты на эту неделю?»

Tenant data (per Doc #4) accessible to anonymous customer for discovery.

---

## 8. Anonymous session retention + privacy

### 8.1 24-hour active session

- `AnonymousSession.expires_at = last_active_at + 24h`
- Each customer interaction resets timer
- 24h after last activity → session marked expired

### 8.2 Auto-purge cron

Nightly Celery task:
- Find sessions with `expires_at < now`
- Hard-delete the AnonymousSession + related AnonymousMemoryDraft + anonymous Conversation messages
- Audit log entry retained 30 days then anonymized

### 8.3 Per Q-AN3: session preservation for cross-device

NOT supported MVP. Phase 3+ — customer might register quickly on one device to «save context» before opening on another.

### 8.4 Audit retention

Per `customer-privacy-data-closure-ux §9.6` analog:
- AnonymousSession audit: 30 days post-expiry then anonymized
- GateTransitionEvent: 90 days for funnel analytics
- Anonymized aggregate retention indefinite

### 8.5 No PII in anonymous session

- No name (anonymous)
- IP audit-only (never displayed; per `master-device-reauth Q-MD7`)
- Device fingerprint (UA hash; no GPS, no detailed device info)
- Chat content — text only, not analyzed for PII detection during anonymous (no consent to extract)

### 8.6 What if customer leaks PII anonymously

If customer types phone / email / full name in anonymous chat:
- Ayla doesn't capture as MemoryEntry (no consent)
- Server doesn't store in PII fields
- Customer's text remains in conversation for in-session context
- At session expiry — all gone

### 8.7 Tenant cannot identify anonymous customer

Per Doc #4 §3.3: tenant sees own salon's anonymized aggregate, NOT per-anonymous-session traffic. Even own salon's traffic is anonymized («15 anonymous visits today»).

### 8.8 Founder analytics on anonymous funnel

Founder dashboard sees:
- Anonymous session count
- Gate trigger rate (% of sessions reaching «Записаться»)
- Registration completion rate
- Time-to-registration distribution
- Carry-forward acceptance rate
- All aggregated, anonymized

### 8.9 Anonymous customer cannot be deleted as «account»

Anonymous customer is NOT an account. Cannot request deletion (no account to delete). Customer can:
- Wait 24h for auto-purge
- Clear localStorage manually to reset session_token

### 8.10 GDPR / 152-ФЗ on anonymous data

Anonymous session data is not personal data per 152-ФЗ definition (no identifying information). Treated as ephemeral metadata.

If customer registers AND data carries forward per §6.3, THEN it becomes personal data under registered user's privacy regime.

---

## 9. Edge cases

### 9.1 Anonymous customer abandons mid-conversion

Customer taps «Зарегистрироваться через MAX» → MAX OAuth opens → customer closes browser/Mini App.

Outcome:
- No BotUser created (OAuth not completed)
- AnonymousSession continues until expiry
- Customer returns within 24h same device → picks up context
- Customer returns after 24h → fresh start

### 9.2 Anonymous customer changes device mid-session

Customer on phone anonymously → continues on tablet anonymously.

Outcome:
- Two separate AnonymousSessions
- No cross-device link
- Customer manually shares preferences via separate chat if wants

### 9.3 Already-registered customer logs out

Customer registered, then logs out → becomes anonymous again? NOT in MVP. Customer registered = always registered until account closure.

Per Doc #2 §11.5: customer may revoke device sessions, but identity persists. Anonymous state only for never-registered.

Phase 4+ may add «browse as guest» mode for registered users; out of scope MVP.

### 9.4 Same MAX identity, second registration attempt

Customer already registered (BotUser exists for MAX username X) opens fresh Mini App on new device → MAX OAuth → MAX returns same username.

Outcome:
- Backend detects existing BotUser
- Session linked to existing user (login, not registration)
- Customer's existing memory + bookings + everything restored

### 9.5 Multi-tenant anonymous discovery

Anonymous customer searches → results across multiple tenants per Ayla's matching. Customer can browse tenants' content freely.

Per Doc #4 §6.4: customer's tenant choice happens at booking. Anonymous can compare; tenant relationships start at first booking.

### 9.6 Anonymous customer with sensitive topic mention

Anonymous customer in chat says «я беременна, можно мне массаж?»:
- Ayla replies cautiously per `wellness-symptom-handoff §10` medical routing
- Does NOT capture as red-zone (no consent, no memory)
- In-session uses for safety filter (this query) but discards at session end
- Customer encouraged to consult doctor

If customer THEN registers (within session), customer offered:

```
Тебе показалось важным упомянуть про беременность. Если хочешь, могу
сохранить это в твою память — чтобы я автоматически избегала
противопоказанных процедур. Сохранять?

[Да, сохрани]   [Не нужно]
```

Customer's choice; if «Да» — captured as red-zone with proper consent per Doc #2 §2.3.

### 9.7 Anonymous customer reports emergency

Anonymous customer in chat: «мне плохо после процедуры» — but no booking exists (anonymous, never booked).

Outcome:
- Per Doc #3 §2.12: emergency tiers require existing customer (booking / dispute). Anonymous cannot open emergency dispute via standard flow.
- Ayla responds with health guidance + emergency contact info («103 / poison control» etc.) per `wellness-symptom-handoff §10`
- Encourages customer to register IF wants formal complaint pathway

### 9.8 Anonymous traffic spike (load / abuse)

Per Q-AN8 anti-abuse: if device fingerprint creates 100+ sessions in 24h → rate-limit at IP. Phase 4+ ML-based.

MVP: simple rate limit. Audit logged.

### 9.9 Anonymous «explore» mode by registered customer

Phase 4+ feature: registered customer wants to browse incognito (without memory accumulation that session). Adds session flag `incognito=True`. Customer explicit «browse without saving». Out of MVP scope.

### 9.10 Tenant onboards while customer browses anonymously

Customer searches for «массаж рядом» → Ayla shows tenants A, B. Tenant C onboards while customer is browsing.

Next search query results include tenant C automatically (Ayla queries fresh tenant list each search). No special handling needed.

---

## 10. MAX deep-link flow integration

### 10.1 Anonymous Mini App entry

Per existing identity stack:
- Customer opens MAX → finds @Ayla bot → opens Mini App
- OR customer scans QR code → MAX deep-link → Mini App open
- OR customer Web search «Ayla salon» → landing page → «Открыть в MAX» → Mini App

All entry paths → Mini App opens to functional state without registration.

### 10.2 MAX identity available but customer not registered

MAX user has username + (maybe) phone in their MAX account. But:
- Anonymous customer in our Mini App = customer NOT registered with Ayla
- We have no BotUser, no MemoryEntry, no settings for them
- MAX identity available in OAuth but not used until gate fires

### 10.3 Pre-fill at gate

When gate fires + customer taps «Зарегистрироваться через MAX»:
- MAX OAuth returns identity (always available because customer IS in MAX)
- Backend creates BotUser quickly (typically 1-2 seconds)
- Per Notion AI-01 AC: response time ≤ 3 seconds (gate-to-confirm)

### 10.4 Bot DM as alternative entry

Customer can also interact via @Ayla Bot DM (not Mini App):
- Same Ayla persona
- Customer's MAX username is implicit (Bot DM requires MAX identity)
- Per Q-AN1: Bot DM users may be auto-registered on first interaction (since MAX identity available) — TBD

If auto-registered, gate is implicit. No browsing anonymous via Bot DM.

If NOT auto-registered (Q-AN1 decision pending), Bot DM has anonymous tier same as Mini App.

### 10.5 Multi-app MAX (5-bot limit per `project_max_platform_capabilities`)

If user has 5 bots in their MAX org max already, opening Ayla → MAX requires removing another bot.

Anonymous Mini App still works (doesn't add bot to org). Only Bot DM requires adding the bot.

Customer informed if this constraint hits:

```
MAX позволяет добавить ограниченное число ботов. Если хочешь общаться
со мной через MAX-чат, может понадобиться убрать кого-то.
[Подробнее]
```

For anonymous Mini App: no impact. Customer can browse freely.

---

## 11. Tenant-as-provider relationship for anonymous

### 11.1 Anonymous customer searches tenant

Per Doc #4 §3.4: tenant content (services, masters, schedule, hours) accessible to anonymous customer.

### 11.2 Tenant doesn't see anonymous customer

Per Doc #4 §5.5 + §3.3: tenant analytics shows anonymized aggregate. Even own salon's traffic count is anonymized:

```
За сегодня в Формуле тела:
• 47 anonymous browsing
• 12 registered customers
• 8 bookings made
```

Tenant cannot identify any specific anonymous user.

### 11.3 Tenant cannot demand «register to view our content»

Per Doc #4 §2.6: tenant cannot customize Ayla. Cannot add «register to see prices» gate. Tenant's content is open to anonymous browsing as part of Ayla acquisition funnel.

### 11.4 Anonymous customer's «interest» in tenant

When anonymous customer views master cards / reads reviews at tenant — that's interest signal aggregated for founder analytics, NOT for tenant.

### 11.5 Anonymous customer to tenant relationship begins at first booking

Per Doc #4 §6.4: customer's tenant relationship starts at first confirmed booking. Anonymous browsing doesn't create relationship.

### 11.6 Tenant SUSPENDED affects anonymous search

If tenant SUSPENDED → not surfaced in anonymous search results. Per Doc #4 §10.2.

---

## 12. Anti-patterns

### 12.1 Registration friction

| Anti-pattern | Why bad | Correct |
|---|---|---|
| Mini App splash «Sign up to continue» on first open | §2.1 acquisition friction | Functional state immediately |
| Demand phone before any interaction | §2.1 + privacy | MAX OAuth only at gate moment |
| Gate fires on every Ayla query | §2.2 customer-driven | Only on «Записаться» + secondary opt-ins |
| Backend creates «ghost account» without customer consent | §2.4 | AnonymousSession separate from BotUser |
| Soft prompts every reply | §4.7 + §7.5 max 1 per session | One organic mention max |
| «Limited browsing — register for full Ayla» | Anti-pattern (functional regression) | Anonymous Ayla = full Ayla quality |

### 12.2 Data privacy

| Anti-pattern | Why bad | Correct |
|---|---|---|
| Capture anonymous chat to MemoryEntry without consent | §2.5 | Session-only context; no memory until consent |
| Carry forward all anonymous data on registration without opt-in | §6.3 + §2.7 | Explicit «сохранить?» offer |
| Track anonymous customer across devices | §5.5 | Per-device sessions only |
| Tenant sees anonymous browsing detail | §11.2 | Aggregated, anonymized only |
| Anonymous session retained > 24h without activity | §8.1 | Auto-purge cron |
| Anonymous customer's PII typed in chat captured | §8.6 | Not extracted; ephemeral |
| Display anonymous customer's IP | §8.5 (per Q-MD7 precedent) | Audit-only |

### 12.3 Gate UX

| Anti-pattern | Why bad | Correct |
|---|---|---|
| Silent redirect to MAX OAuth | §2.10 | Explain why before redirect |
| Customer abandons gate 3× → kick out of Mini App | §4.7 | Customer's pace |
| Gate cannot be cancelled | §4.6 | «Назад» always available |
| MAX OAuth fail blocks Mini App | §4.4 | Anonymous session preserved on fail |
| Gate hides Ayla's chat history | Continuity break | Anonymous chat continues in background |
| Multiple gates in one flow | Bad UX | Single gate at «Записаться»; secondary opt-ins separate |

### 12.4 Multi-tenant violations

| Anti-pattern | Why bad | Correct |
|---|---|---|
| Tenant restricts anonymous to «their» customers only | Doc #4 §2.6 + §11.3 | Open to all anonymous |
| Anonymous browsing identifies «their» tenant to other tenants | Privacy | Anonymous never identifies |
| Tenant demands customer register before viewing prices | §3.5 | NEVER |
| Anonymous can register «to one tenant only» | Wrong model | Customer registers to Ayla, then can book at any tenant |

### 12.5 Memory / personalization

| Anti-pattern | Why bad | Correct |
|---|---|---|
| Anonymous customer sees «Что Ayla знает обо мне» | §2.15 | Placeholder text only; requires registration |
| Anonymous memory drafts persist after session expiry | §6.5 + §8.2 | Discarded with session |
| Anonymous behavioral inferences high confidence | §6.5 | Confidence 0.30-0.60 max |
| Carry forward includes red-zone without explicit consent | §9.6 | Per-field consent at carry-forward |

---

## 13. Data models — already specified §5, §6, §7

See §5.1 `AnonymousSession`, §6.5 `AnonymousMemoryDraft`, §6.7 `GateTransitionEvent`.

Total: 3 NEW models.

---

## 14. API contracts

### 14.1 Anonymous endpoints

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/v1/anonymous/session` | Create anonymous session (returns session_token) |
| GET | `/api/v1/anonymous/session/<token>` | Retrieve session context |
| POST | `/api/v1/anonymous/session/<token>/chat` | Anonymous chat with Ayla |
| POST | `/api/v1/anonymous/session/<token>/search-masters` | Master discovery |
| GET | `/api/v1/anonymous/session/<token>/tenant/<tenant_id>` | Tenant profile view |
| POST | `/api/v1/anonymous/gate/trigger` | Customer attempts gate-protected action (returns gate UI metadata) |
| POST | `/api/v1/anonymous/gate/register-via-max` | Initiate MAX OAuth + register |
| POST | `/api/v1/anonymous/gate/carry-forward-data` | After registration, opt-in to carry context §6.3 |

### 14.2 Customer endpoints

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/v1/customer/anonymous-conversion` | Backend transition completion |

### 14.3 Founder / analytics

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/founder/anonymous-funnel-analytics` | Aggregate gate trigger / conversion / drop-off |
| GET | `/api/v1/founder/anonymous-sessions/count-active` | Real-time anonymous traffic count |

### 14.4 Internal

| Method | Path | Purpose |
|---|---|---|
| POST | `/internal/anonymous/session/purge-expired` | Cron — 24h purge §8.2 |
| POST | `/internal/anonymous/abuse-detection` | Phase 4+ rate-limit scanner |

---

## 15. Events emitted

Add to [`event-taxonomy.md`](./event-taxonomy.md) `3.21 anonymous flow domain` (NEW section):

| Trigger | Event | Notes |
|---|---|---|
| Session created | NEW: `anonymous.session_created` | device_fingerprint hash |
| Chat message | NEW: `anonymous.chat_message` | session_id, message_count |
| Master viewed | NEW: `anonymous.master_viewed` | master_id, tenant_id |
| Tenant viewed | NEW: `anonymous.tenant_viewed` | tenant_id |
| Gate triggered | NEW: `anonymous.gate_triggered` | trigger_type |
| Registration completed | NEW: `anonymous.registered` | session_id, time_in_anonymous_minutes |
| Carry-forward accepted | NEW: `anonymous.carry_forward_accepted` | fields_carried_count |
| Session expired | NEW: `anonymous.session_expired` | |

8 NEW events §15.

---

## 16. Acceptance criteria

- [ ] 3 models §5/§6/§7 (AnonymousSession, AnonymousMemoryDraft, GateTransitionEvent)
- [ ] 12 endpoints §14 (8 anonymous + 1 customer + 2 founder + 2 internal)
- [ ] Mini App opens to functional state without registration §2.1
- [ ] Anonymous AI chat with Ayla §2.1 + §7
- [ ] Master discovery, comparison, recommendations for anonymous §3.4
- [ ] Tenant content browsing for anonymous §3.4 + §11
- [ ] Primary gate trigger: «Записаться» button §3.1
- [ ] Secondary gate triggers per §3.2 (save history / personalized / wellness / loyalty / bookmark / multi-device)
- [ ] Gate UX with explanation §4.1
- [ ] MAX OAuth integration §4.4 + §10
- [ ] Anonymous → registered conversion §5.2
- [ ] Data carry-forward opt-in §6.3
- [ ] AnonymousSession 24h expiry + cron purge §8.1-8.2
- [ ] Ayla behavior with anonymous §7 (no profile questions, lighter recs, single soft prompt per session)
- [ ] Anonymous session preserved on MAX OAuth fail §4.4 + §9.1
- [ ] Cross-device = separate sessions §5.5 + §9.2
- [ ] Per Notion AC: response time ≤ 3 sec §4.8 + §10.3
- [ ] Tenant CANNOT identify anonymous customer §11.2
- [ ] Tenant CANNOT demand earlier registration §3.5 + §11.3
- [ ] No PII captured during anonymous session §8.5-8.6
- [ ] Founder analytics aggregate anonymized §8.8
- [ ] 8 events §15
- [ ] Anti-pattern review §12
- [ ] Tests: anonymous session creation + chat / gate trigger detection / MAX OAuth conversion / carry-forward opt-in / session expiry / cross-device isolation / abandon mid-OAuth / tenant data scope on anonymous browse / founder aggregate / Ayla voice tone

---

## 17. Open questions

| # | Question | Lean | Owner | Urgency |
|---|---|---|---|---|
| **Q-AN1** | Bot DM users — auto-register or anonymous tier? | TBD. If auto-register: simpler model, MAX identity always present. If anonymous tier: consistent with Mini App. Lean auto-register (since MAX identity exists, customer chose to message Ayla = implicit registration intent). | Policy + Eng | 🔴 PRE-DEPLOY |
| **Q-AN2** | Anonymous session retention — 24h correct? | YES MVP. Tune based on data — if customers commonly return at 30-48h, extend. | UX + Data | 🟢 |
| **Q-AN3** | Cross-device anonymous linking — Phase 3+? | YES Phase 3+ if value demonstrated. MVP per-device. | Eng | 🟡 |
| **Q-AN4** | Soft gate (10 chats → prompt) — Phase 3+ or MVP? | Phase 3+ MVP only explicit triggers. Don't push. | UX | 🟢 |
| **Q-AN5** | Anonymous customer mentions PII in chat — capture warning? | Phase 2+ — Ayla can say «не называй полное имя сейчас, не сохраняю». MVP just don't capture. | UX + AI | 🟡 |
| **Q-AN6** | «Registration benefit» mention frequency — once per session correct? | YES MVP §7.5. Tune if data shows higher conversion at 2× / session (unlikely). | UX | 🟢 |
| **Q-AN7** | Carry-forward UX — when offered? | At post-registration acknowledgment §4.5 + §6.3. After customer completed first booking would also be candidate. Phase 3+ optimize. | UX | 🟡 |
| **Q-AN8** | Anti-abuse: rate-limit on anonymous session per device fingerprint | 100 sessions per device per 24h MVP soft cap. Phase 4+ ML. | Eng + Anti-fraud | 🟡 |
| **Q-AN9** | Anonymous customer in wellness emergency mention — special handling? | Per §9.6: full medical routing per `wellness-symptom-handoff §10`. Cannot open formal emergency (no account). Customer encouraged to register if wants formal pathway. | Policy + Privacy | 🔴 PRE-DEPLOY |
| **Q-AN10** | Tenant SUSPENDED with anonymous customer mid-search — re-search? | Search re-evaluates each query. If tenant transitions to SUSPENDED, next query excludes. Customer's in-flight chat references tenant — Ayla mentions «временно недоступен». | UX + Eng | 🟡 |
| **Q-AN11** | Mini App localStorage cleared by customer (incognito browsing pattern) — what happens? | New session_token each open → fresh anonymous session. Privacy-protecting; no link to prior. | Eng + Privacy | 🟢 |
| **Q-AN12** | Multi-language anonymous customer — Phase 3+? | YES Phase 3+ per `ayla-identity-and-brand Q-AYL3/4`. Russian MVP. | UX + I18N | 🟢 |
| **Q-AN13** | Anonymous customer who taps «Записаться» but MAX OAuth fails 3× — what? | After 3rd fail, Ayla suggests «попробуй позже, проверь интернет, или напиши мне в MAX напрямую». No more retries forced. | UX + Eng | 🟡 |
| **Q-AN14** | Anonymous customer can leave anonymous review? | NO per §2.13. Review requires booking → requires registration. | Policy | 🟢 |
| **Q-AN15** | Anonymous → registered → close account → fresh anonymous browsing — allowed? | YES per §2.14. Customer can re-onboard at any time per `customer-privacy-data-closure-ux §2.10`. | Policy + Eng | 🟢 |
| **Q-AN16** | Anonymous-session-token persistence — localStorage forever? | localStorage until cleared OR 24h server-side expiry. Whichever first. | Eng | 🟢 |
| **Q-AN17** | Anonymous customer's «what can you do?» — list registration-gated features? | Per §7.7-7.8: mention them as «доступно после регистрации». Not blocking. | UX | 🟢 |
| **Q-AN18** | Founder-level analytics — when does anonymous-funnel-analytics become useful? | Phase 1 once we have 100+ anonymous sessions / day per tenant. Phase 0 (early MVP): basic count + completion rate. | PM + Data | 🟢 |
| **Q-AN19** | Anonymous customer in HUMAN_SUPERVISED — what? | Anonymous customer can't be in HUMAN_SUPERVISED (no account → no tier escalation). All anonymous interactions are AI_DEFAULT per `conversation-ownership-policy` (deprecated) but functionally Ayla-only. | Policy | 🟢 |
| **Q-AN20** | Anonymous browsing with Ayla mentioning «помню тебя» (returning session) — privacy? | Per §5.4: session token in localStorage = «remembering» on same device within 24h. Customer informed at first interaction what's remembered (chat history within session). On registration, full continuity. Pre-MAX no cross-device memory. | UX + Privacy | 🟢 |

---

## 18. Cross-document linkage

### Foundation set (this is final doc)
- [`ayla-identity-and-brand.md`](./ayla-identity-and-brand.md) — Doc #1
- [`ayla-memory-and-personalization.md`](./ayla-memory-and-personalization.md) — Doc #2 (no anonymous memory)
- [`ayla-emergency-fallback-policy.md`](./ayla-emergency-fallback-policy.md) — Doc #3 (anonymous cannot open emergency)
- [`tenant-as-provider-model.md`](./tenant-as-provider-model.md) — Doc #4 (tenant cannot identify anonymous)
- **This doc** — Doc #5

### Customer-side
- [`customer-first-touch-and-mini-app-states.md`](./customer-first-touch-and-mini-app-states.md) — first-touch flow now anonymous-friendly
- [`customer-loyalty-rewards-ux.md`](./customer-loyalty-rewards-ux.md) — loyalty requires registration §3.2
- [`customer-notification-controls-ux.md`](./customer-notification-controls-ux.md) — no notifications for anonymous (no MAX session for push)
- [`customer-privacy-data-closure-ux.md`](./customer-privacy-data-closure-ux.md) — anonymous re-registration after close allowed
- [`customer-refund-dispute-ux.md`](./customer-refund-dispute-ux.md) — disputes require registration
- [`customer-wellness-dashboard-ux.md`](./customer-wellness-dashboard-ux.md) — wellness requires registration

### Engineering
- `apps.identity.BotUser` — registered customer model
- New `apps.anonymous.AnonymousSession` — anonymous session model
- MAX OAuth integration per existing identity stack
- Per Doc #1 §12: internal terminology — `bot_user` code stays

### Notion
- AI-01 (`338b0dab-2955-8105-b117-c5db1a17633e`) — gate AC verbatim
- Brand Vision — anonymous discovery pattern
- PRD Ayla v2.0 — bottom nav structure

### Memory
- `project_ayla_first_strategic_pivot` decision #9
- `project_ayla_personal_ai` voice consistency

---

## 19. What this unblocks

- **Acquisition funnel optimization** — try-before-commit pattern
- **Privacy preservation** — no anonymous backend ghost accounts
- **Onboarding without friction** — Mini App opens functional
- **Multi-tenant discovery** — anonymous browses tenants freely
- **Founder analytics** — funnel metrics for product / sales
- **Tenant ecosystem clarity** — tenant cannot demand earlier registration
- **Cross-tenant boundary** — anonymous can browse multiple tenants; no tenant sees specifics
- **Foundation set complete** — Doc #5 of 5 final

## 20. What this does NOT unblock

- ❌ Anonymous emergency handling (Q-AN9 — formal escalation requires registration)
- ❌ Cross-device anonymous linking (Phase 3+)
- ❌ Anonymous review submission (per §2.13)
- ❌ Anonymous wellness module access (per §3.4 + wellness handoffs)
- ❌ Anonymous loyalty
- ❌ Voice anonymous browsing (Phase 2+)
- ❌ Multi-language anonymous (Phase 3+ Kazakh)
- ❌ «Browse as guest» mode for registered users (Phase 4+)
- ❌ Phone-only registration without MAX (Phase 4+ if needed)
- ❌ Skip Q-AN1 Bot DM auto-register vs anonymous — pre-deploy
- ❌ Skip Q-AN9 wellness emergency for anonymous — pre-deploy

---

## 21. Sign-off

| Role | Approval | Date |
|---|---|---|
| UX Architect | ✅ | 2026-05-19 |
| Identity / BotUser backend lead (MAX OAuth + AnonymousSession + conversion flow) | ☐ | |
| Mini App frontend (Mini App opens functional + gate UX + carry-forward offer) | ☐ | |
| AI prompt eng (Ayla voice for anonymous + §7 behavior rules) | ☐ | |
| Privacy / Legal (§8 anonymous data retention + §2.5 + §6.5 + Q-AN9 emergency policy) | ☐ | 🔴 PRE-DEPLOY |
| Founder (Q-AN1 Bot DM auto-register decision + anonymous funnel analytics scope) | ☐ | 🔴 PRE-DEPLOY |
| Tenant-as-provider steward (anonymous data NOT visible to tenant — §11) | ☐ | |
| Memory steward (anonymous → registered carry-forward consent + §6.3) | ☐ | |
| Emergency policy steward (anonymous cannot open emergency — §2.12 + §9.7) | ☐ | |
| Accessibility (WCAG 2.2 AA on gate UI + carry-forward offer) | ☐ | |

## Last verified
2026-05-19 (initial draft, anonymous browsing supported + single primary gate trigger «Записаться» + secondary opt-in triggers + AnonymousSession 24h + MAX OAuth conversion + data carry-forward opt-in + tenant invisibility + 3 models, 12 endpoints, 8 events — locked. Foundation Doc #5 of 5 for Ayla-first pivot. COMPLETES foundation set.)
