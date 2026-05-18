# Customer First-Touch + Mini App States Catalog

**Date:** 2026-05-18 r1
**Status:** Foundational — preemptive spec for Phase 1 / 4b (customer Mini App 6 screens)
**Reads:** [`product-ux-vision.md`](./product-ux-vision.md), [`core-user-states.md`](./core-user-states.md), [`user-journeys.md`](./user-journeys.md), [`conversational-ux-framework.md`](./conversational-ux-framework.md), [`information-architecture.md`](./information-architecture.md), [`attribution-policy.md`](./attribution-policy.md), [`assistant-persona.md`](./assistant-persona.md)

> Two adjacent gaps in one doc: (1) what does the customer see/hear when they FIRST arrive (cold entry from QR / Instagram / Maps / referral / etc.), and (2) what does every Mini App screen show in each of its operational states (loading / empty / error / disabled / offline). Both must conform to single-assistant voice and customer-care tone.

---

## 0. Why this exists

### The two gaps

**Gap 1 — First-touch flow**: existing customer-first-time-handoff covers AFTER first message exchange. But the FIRST message — what tone, what content, what assumed context — depends on how customer arrived. A QR-scan customer at the salon counter is in a different state than someone clicking an Instagram bio link, who's different again from a referred friend. Without explicit per-source first-touch templates, the first message is generic + off-brand at moments of highest acquisition friction.

**Gap 2 — Mini App states catalog**: 4b ships 6 Mini App screens. Each screen has 5-7 operational states (loading / success / empty / error / disabled / partial / stale / offline). Without locked patterns, each screen invents its own empty copy, loading indicator, error message → inconsistency across what should feel like one app.

### Why combined

Both belong to the same customer journey: customer arrives → Mini App loads → catalog renders → action taken. First-touch determines what state customer enters; Mini App states are what they experience throughout. Single doc keeps narrative + voice + templates cross-referenced.

---

## 1. Scope

### IN
- Entry-point catalog: 7 ways customer arrives
- First-touch templates per source (cold + warm/returning)
- Customer state classification at arrival (DISCOVERED vs resume)
- State machine: arrival → first touch → routing
- 10-state catalog for any Mini App screen (loading / success / empty / error / disabled / partial / stale / offline / sync-pending / not-found)
- Per-screen state matrix for 4b's 6 customer screens (F1 catalog / F1-detail / F2 masters / F2-detail / F3 date+time / F4 confirm / F5 success)
- Copy templates with voice anchors
- Anti-patterns
- Accessibility baseline (WCAG 2.2 AA)

### OUT
- Master + owner Mini App states (separate scope; covered partially in [`schedule-editor-wireframes.md`](./schedule-editor-wireframes.md) and [`owner-conversational-templates.md`](./owner-conversational-templates.md))
- Booking flow templates (covered in [`conversational-ux-framework.md`](./conversational-ux-framework.md) §5.1)
- Cancellation/reschedule (covered in [`customer-cancellation-reschedule-spec.md`](./customer-cancellation-reschedule-spec.md))
- First-touch for tenants not yet in MVP (multi-location, chains) — Phase 2+
- Voice messages (deferred per Q-C6)
- Push notifications outside MAX chat (MAX platform limitation)

---

## 2. Entry-point catalog

Seven ways a customer arrives at the salon's assistant. Each maps to a `start_param` on bot deeplink (per MAX bot API) or initial conversation context.

| # | Entry source | Trigger | `start_param` pattern | Acquisition friction | First-touch tone weight |
|---|---|---|---|---|---|
| 1 | **QR code** (physical) | Customer scans QR at salon reception, business card, mirror | `qr_<location_id>_<placement>` | LOW (already physical visit) | Warm-direct; assumes immediate intent |
| 2 | **Instagram bio link** | Customer taps link in salon's IG bio | `ig_bio_<campaign>` | MEDIUM (curious) | Warm-introductory; soft sell |
| 3 | **Instagram post link** (Phase 2) | Customer taps link in specific IG post | `ig_post_<post_id>` | MEDIUM-HIGH (post-driven intent) | Reference what they saw |
| 4 | **Google/Yandex Maps listing** | Customer taps «Записаться» on salon's map listing | `maps_<provider>` | HIGH (search intent) | Acknowledge map source; direct to booking |
| 5 | **Direct MAX search** | Customer types salon name in MAX, opens bot | `direct` OR no start_param | UNKNOWN (intent unclear) | Default introduction |
| 6 | **Customer referral link** | Friend forwards `shareMaxContent` per Q-CX7 | `ref_<referrer_user_id>` | LOW-MEDIUM (trust-borrowed) | Warm; acknowledge social proof |
| 7 | **Salon website button** (Phase 2) | Customer clicks «Открыть в MAX» on salon site | `web_<page_slug>` | MEDIUM-HIGH (active browser session) | Reference what page they came from |
| 8 | **CRM reactivation blast** (Phase 2) | Returning customer taps link in salon's reactivation message | `campaign_<campaign_id>_<dispatch_id>` | RETURNING (warm) | Recognition tone; not a stranger |

### MVP coverage
Sources 1, 2, 5, 6 ship Phase 1. Sources 3, 4, 7, 8 ship Phase 2+ as integrations come online.

---

## 3. Customer state classification on arrival

System looks up customer record by `(tenant_id, max_user_id)` tuple before deciding first-touch template.

```
                          ┌──────────────────┐
                          │  Customer arrives │
                          │  via entry source │
                          └────────┬──────────┘
                                   │
                          lookup (tenant, max_user_id)
                                   │
                ┌──────────────────┼──────────────────┐
                │                  │                  │
            no record       record exists,        record exists,
                            state = NEW           state ≠ NEW
                │                  │                  │
                ▼                  ▼                  ▼
         create record       assign state         resume current
         state = NEW         per source intent    state; warm tone
                │                  │                  │
                ▼                  ▼                  ▼
        DISCOVERED          DISCOVERED OR        depends on state
        (default)           EXPLORING            (PROBLEM_SEEKING /
                            (if start_param      READY_TO_BOOK /
                            implies specific     POST_VISIT /
                            service)             ACTIVE_REGULAR / etc.)
```

### State assignment rules

| Source + record state | Assigned state | Reasoning |
|---|---|---|
| Any source, NO record | DISCOVERED | First touch, no prior context |
| Source 1 (QR at counter), no record | DISCOVERED | Physical visit hints intent but doesn't define service |
| Source 3 (IG post specific service), no record | EXPLORING (with `goal_hint=service_X`) | Post-driven means specific service interest |
| Source 4 (Maps), no record | EXPLORING (with `goal_hint=booking`) | Map search = transactional intent |
| Source 6 (referral), no record | DISCOVERED (with `attribution_metadata.referred_by=customer_X`) | Trust borrowed but no specific service intent |
| Source 8 (CRM reactivation), record exists with AT_RISK_DRIFTING | Resume AT_RISK_DRIFTING | Don't reset; honor existing state |
| Any source, record state = ACTIVE_REGULAR | Resume ACTIVE_REGULAR | Returning customer; don't re-introduce |
| Any source, record state = HUMAN_LOCKED conversation in flight | Resume conversation; don't trigger first-touch | Admin owns this; AI silent on entry |

### Why this matters for attribution
Per [`attribution-policy.md`](./attribution-policy.md), `attribution_metadata` populated with `entry_source` + `first_seen_source` (if new) drives downstream analytics on which channels acquire customers. Marketing dashboards filter by entry source.

---

## 4. First-touch templates (per source)

### Voice anchors (consistent across all templates)
Per [`conversational-ux-framework.md`](./conversational-ux-framework.md) and [`assistant-persona.md`](./assistant-persona.md):
- Warm + Calm + Premium-but-accessible
- No emoji in opener (§8 of conversational-ux-framework)
- Single-assistant identity preserved («помощник студии», never «бот»)
- Length: ≤ 3 sentences

### 4.1 Source 1 — QR code (physical placement at salon)

**Template:**
```
Здравствуйте. Я помощник студии «{{salon_name}}» — отвечу на вопросы по услугам, помогу записаться или подобрать.

С чего удобнее начать?

[Подобрать услугу]  [Посмотреть прайс]  [Записаться сразу]
```

**Variant — repeat scan within 24h** (customer scanned same QR twice in a day — they're physically at salon, probably indecisive):
```
Вы только что были у нас на связи. Если что-то осталось непонятно — спросите, я рядом.
```

**Forbidden:**
- ❌ «Привет!» — too casual for first contact
- ❌ «Сканировали QR? Отлично!» — over-acknowledging the channel
- ❌ Emoji on first line

### 4.2 Source 2 — Instagram bio link (generic IG entry)

**Template:**
```
Здравствуйте. Я помощник студии «{{salon_name}}» — отвечу на вопросы по услугам и помогу записаться.

Что вас интересует?

[Услуги]  [Подобрать мастера]  [Записаться]
```

**Forbidden:**
- ❌ «Подписывайтесь на наш Instagram!» — circular cross-promo
- ❌ Reference IG content customer might not have seen

### 4.3 Source 3 — Instagram post (Phase 2)

If `start_param` encodes post → service mapping:
```
Здравствуйте. Видела(а), что вас заинтересовал {{service_short_name}}.

Расскажу подробнее или сразу подберём время?

[Подробнее о {{service_short_name}}]  [Подобрать время]  [Другие услуги]
```

If post→service mapping absent (generic post link):
Falls back to source 2 template.

**Forbidden:**
- ❌ «Помню вашу историю просмотров» — privacy creepy and untrue
- ❌ Hard-sell of the specific service («лучшая услуга нашей студии!»)

### 4.4 Source 4 — Maps listing (Phase 2)

**Template:**
```
Здравствуйте. Помощник студии «{{salon_name}}» — рада, что нашли.

Подсказать что-то по услугам или сразу записать?

[Что есть в каталоге]  [Записаться]
```

**Forbidden:**
- ❌ Push «оставьте отзыв на картах!» on first touch — wrong moment

### 4.5 Source 5 — Direct MAX search (most generic)

**Template:** see Source 2 — same as IG bio link. Generic, no acknowledgement of channel.

### 4.6 Source 6 — Customer referral link

**Template:**
```
Здравствуйте. Я помощник студии «{{salon_name}}» — рада знакомству.

{{referrer_first_name}} поделилась с вами — расскажу про наши услуги или сразу подберём?

[Услуги]  [Подобрать время]
```

**Variant — if Wellness Profile from referrer indicates likely interest** (Phase 3+; e.g., referrer's Layer 2 Goals match common patterns for first-time visitor):
Falls back to base template; don't speculate on customer's goals based on referrer's data (privacy).

**Forbidden:**
- ❌ «{{referrer_first_name}} рекомендует {{specific_service}}» — referrer didn't recommend specific service, that's projection
- ❌ Promise referrer-bonus mechanics («у вас и у {{referrer}} +500 бонусов!») — Q-CX7 says silent tracking only

### 4.7 Source 7 — Salon website button (Phase 2)

If `web_<page_slug>` indicates intent (e.g., `web_pricing` = customer was on pricing page):
```
Здравствуйте. Видела(а), вы смотрели наши цены.

Подсказать по конкретной услуге или сразу записать?

[Спросить по услуге]  [Записаться]
```

If `web_main` (just main page):
Falls back to source 5 generic.

### 4.8 Source 8 — CRM reactivation blast (Phase 2)

Customer is RETURNING. Different tone entirely — see §5 returning-customer flow.

```
{{customer_first_name}}, давно не виделись. {{contextual_hook}}

Если что-то нужно — рядом, как обычно.

[Записаться]  [Не сейчас]
```

This is per [`conversational-ux-framework.md`](./conversational-ux-framework.md) §5.3.1 reactivation template — referenced here for entry-source completeness.

---

## 5. Returning-customer flow (any source)

If customer record exists with state ≠ NEW, regardless of entry source:

### 5.1 State = ACTIVE_REGULAR
**Template:**
```
С возвращением, {{customer_first_name}}. {{contextual_acknowledge}}

Что нужно?

[Записаться как обычно]  [Что-то новое]  [Спросить]
```

`contextual_acknowledge` examples:
- If usual visit cycle just due: «Похоже, как раз время для следующего {{usual_service}}»
- If during off-cycle: «(no contextual line — keep brief)»

### 5.2 State = POST_VISIT (recent completion, 1-7 days)
**Template:**
```
{{customer_first_name}}, как ощущения после {{last_service}}?
```

Open-ended check-in. Customer reply drives next move per [`conversational-ux-framework.md`](./conversational-ux-framework.md) §5.

### 5.3 State = AT_RISK_DRIFTING / DORMANT
Per [`conversational-ux-framework.md`](./conversational-ux-framework.md) §5.3.1 — reactivation tone applies regardless of entry source.

### 5.4 State = HUMAN_LOCKED in active conversation
**No first-touch message.** Customer enters Mini App or DM, sees their existing conversation. Admin owns it. AI silent until admin releases per [`conversation-ownership-policy.md`](./conversation-ownership-policy.md).

### 5.5 State = ANY other (EXPLORING / PROBLEM_SEEKING / READY_TO_BOOK)
Resume conversation in that state's tone (per [`conversational-ux-framework.md`](./conversational-ux-framework.md) §2 state→tone matrix). Don't re-introduce assistant.

---

## 6. State machine: arrival → first touch → routing

```
┌────────────────────┐
│  Customer arrives  │
│   via deeplink     │
└─────────┬──────────┘
          │
          ▼
┌────────────────────────┐
│  Resolve source +      │
│  fetch/create record   │
│  (assign initial state) │
└──────────┬─────────────┘
           │
           ▼
┌────────────────────────────────┐
│  Emit `conversation.started`   │
│  event with entry_source       │
└──────────┬─────────────────────┘
           │
           ▼
┌────────────────────────────────┐
│  Choose template:              │
│   if new → §4 per-source       │
│   if returning → §5 per-state  │
│   if HUMAN_LOCKED → silent     │
└──────────┬─────────────────────┘
           │
           ▼
┌────────────────────────────────┐
│  Send first-touch message      │
│  via bot DM (text + buttons)   │
└──────────┬─────────────────────┘
           │
           ▼
┌────────────────────────────────┐
│  Wait for customer reply       │
│  Customer enters one of:       │
│   - Mini App (if button taps   │
│     deep-link into Mini App)   │
│   - Continues DM with text     │
│   - Silent (no reply)          │
└────────────────────────────────┘
```

### Silent-on-arrival case
Customer opens bot, sees first-touch, doesn't reply for 24h → no follow-up. Customer's state persists (DISCOVERED or whatever assigned). They can return any time; AI doesn't re-introduce on second open within 7 days.

After 7 days of silence post-arrival without reply → state transitions DISCOVERED → DORMANT-LIGHT (lighter than full DORMANT; allows one gentle revival touch). Phase 2+ refinement (Q-FT9).

---

## 7. Mini App states catalog (universal)

Every customer-facing Mini App screen exists in one of these 10 states at any time. Each state has a design pattern + copy template + behavior.

### 7.1 Loading (fetching data)
**When**: initial render OR refetch after user action
**Visual pattern**: skeleton placeholders mimicking the eventual content shape (NOT spinner unless data structure unknown)
**Copy**: none in skeleton body. If load > 5s: subtle text fade-in («Подождите ещё немного…»)
**Behavior**:
- 0-200ms: nothing visible (avoid skeleton flash)
- 200ms-5s: skeleton visible
- 5s-15s: skeleton + soft inline note
- > 15s: error state with retry

**ASCII pattern for catalog list:**
```
┌──────────────────────────────────┐
│ ▒▒▒▒▒▒▒▒▒▒                       │  ← header skeleton
├──────────────────────────────────┤
│ ▒▒▒▒▒▒▒▒▒▒                       │
│ ▒▒▒▒▒▒                            │
│                                  │
│ ▒▒▒▒▒▒▒▒▒▒                       │
│ ▒▒▒▒▒▒                            │
└──────────────────────────────────┘
```

### 7.2 Success (data loaded, content rendered)
**When**: HTTP 200 with expected payload
**Visual pattern**: actual content per screen design
**Copy**: per-screen — see §8

### 7.3 Empty (loaded successfully but no data)
**When**: HTTP 200 with empty payload (e.g., no services in catalog, no available slots on date)
**Visual pattern**: centered icon + 1-line copy + relevant CTA

**Copy templates per screen** (see §8 per-screen):
- F1 (catalog): «У {{salon_name}} пока нет услуг в каталоге. Спросите у студии напрямую.» → CTA «Написать студии» (opens DM)
- F2 (masters): «У этой услуги пока нет доступных мастеров.» → CTA «Другие услуги»
- F3 (slots): «На {{date}} свободного времени нет. Ближайшее: {{nearest_alternative_date}}» → CTA «Перейти на {{nearest}}»
- Bookings list: «У вас нет активных записей.» → CTA «Записаться»

**Forbidden:**
- ❌ Generic «No data» / «Пусто»
- ❌ Sad-face emoji / 😢
- ❌ «Извините» on every empty — empty isn't apology-worthy by default

### 7.4 Error (load failed)

Three subtypes:

#### 7.4.1 Network error
**When**: fetch fails (offline, DNS, etc.)
**Copy**:
```
Не получилось загрузить. Проверьте интернет и попробуйте снова.

[Попробовать снова]
```

#### 7.4.2 Server error (5xx)
**When**: backend returns 500-series
**Copy**:
```
Что-то у нас не получается прямо сейчас.

[Попробовать снова]   [Сообщить студии]
```

«Сообщить студии» → routes to HUMAN_LOCKED tier per [`conversation-ownership-policy.md`](./conversation-ownership-policy.md). Customer goes to bot DM with pre-filled «У меня не работает Mini App: {{screen_id}}» admin-side context.

#### 7.4.3 Permission denied (403)
**When**: customer-side permission issue (rare for customer surfaces)
**Copy**:
```
Этот раздел сейчас недоступен.
```
Plus context-aware secondary CTA («вернуться в каталог», etc.)

### 7.5 Disabled (action not allowed by business rules)
**When**: target entity exists but action blocked (e.g., booking past `max_advance_days`, service archived, master archived)
**Visual pattern**: greyed-out element with inline explanation
**Copy templates**:
- Past slot: «Это время уже прошло.» (no CTA)
- Archived service: «Эта услуга временно недоступна.» → CTA «Похожие услуги»
- Archived master: «Этот мастер сейчас не принимает.» → CTA «Другие мастера»
- Beyond max_advance window: «Запись на эту дату пока недоступна. Открывается за {{N}} дней до {{date}}.» → CTA «Напомнить когда откроется»

### 7.6 Partial (some data loaded, some failed)
**When**: aggregate fetch (e.g., catalog + masters + slots) where some sub-requests succeed
**Visual pattern**: show what loaded; subtle banner at top
**Copy** (banner):
```
Часть данных не загрузилась. [Обновить]
```
Tap → retries failed sub-requests only.

**Critical**: don't block UI. Customer can interact with what loaded.

### 7.7 Stale (cached data, server unreachable)
**When**: offline + cached data available for read; ages > 5min
**Visual pattern**: persistent banner at top
**Copy**:
```
Данные могут быть устаревшими. [Обновить]
```

### 7.8 Offline (no network, no cache)
**When**: offline + no usable cache
**Visual pattern**: full-screen offline state
**Copy**:
```
Нет интернета.

Откроется, как только появится связь.
```
No retry button — auto-retries on connection restore.

### 7.9 Sync-pending (offline action queued)
**When**: customer made an action (e.g., cancel booking) while offline, queued for sync
**Visual pattern**: toast at bottom + small dot on relevant element
**Copy** (toast):
```
Изменения сохранятся, как появится связь.
```
After 60s offline still: persistent banner per Q-SW12 pattern.
After 5min: stronger warning.
After 24h: drop with notification.

### 7.10 Not found (specific entity 404)
**When**: customer navigates to URL with stale ID (deep link to deleted service, etc.)
**Copy**:
```
Не нашлось. Возможно, удалили.

[Вернуться]
```

«Вернуться» → previous valid screen OR home if no history.

---

## 8. Per-screen state matrix (4b's 6 screens)

### Screen F1 — Catalog list (services)

| State | Visual | Copy | Behavior |
|---|---|---|---|
| Loading | 5-6 skeleton cards (category headers + service rows) | — | per §7.1 |
| Success | Full catalog with category sections | content + standard nav | tap service → F1-detail |
| Empty | Centered icon + copy | «У {{salon_name}}» pattern §7.3 | CTA «Написать студии» |
| Network error | Per §7.4.1 | retry button | full retry on tap |
| Server error | Per §7.4.2 | retry + «Сообщить студии» | escalate path |
| Partial | Some categories loaded | banner per §7.6 | tap banner retries failed |
| Stale | Banner per §7.7 | — | manual refresh |
| Offline | Per §7.8 | — | auto-retry on connect |

### Screen F1-detail — Service detail

| State | Visual | Copy | Behavior |
|---|---|---|---|
| Loading | Skeleton: service info + masters strip + price | — | per §7.1 |
| Success | Full detail, masters carousel, price, duration, description, CTA «Подобрать время» | — | tap CTA → F2 (filtered by service) |
| Disabled (service archived) | Greyed content + banner | «Эта услуга временно недоступна» | CTA «Похожие услуги» |
| Not found (404) | Per §7.10 | — | back to F1 |
| Network/server error | Per §7.4 | — | retry / escalate |

### Screen F2 — Masters list (filtered by service)

| State | Visual | Copy | Behavior |
|---|---|---|---|
| Loading | Skeleton 3-4 master cards | — | per §7.1 |
| Success | Filtered list of masters who do this service | — | tap → F2-detail |
| Empty | Centered + copy | «У этой услуги пока нет доступных мастеров» | CTA «Другие услуги» |
| Partial | Some masters loaded, some failed | banner | per §7.6 |
| Network/server error | Per §7.4 | — | — |

### Screen F2-detail — Master detail

| State | Visual | Copy | Behavior |
|---|---|---|---|
| Loading | Skeleton: photo + name + bio + services list + CTA | — | per §7.1 |
| Success | Full profile, services chips, CTA «Подобрать время с {{master_first_name}}» | — | tap → F3 |
| Disabled (master archived) | Greyed + banner | «Этот мастер сейчас не принимает» | CTA «Другие мастера» |
| Not found | Per §7.10 | — | back to F2 |

### Screen F3 — Date picker + time grid

| State | Visual | Copy | Behavior |
|---|---|---|---|
| Loading (date strip) | Skeleton 14-day strip | — | — |
| Loading (slots for selected date) | Skeleton grid 6-8 cells | — | — |
| Success | Date strip + slots grid for selected date | — | tap slot → F4 |
| Empty (no slots on date) | Per §7.3 | «На {{date}} свободного времени нет. Ближайшее: {{date_X}}» | tap date_X — auto-navigate |
| Disabled past dates | Greyed in date strip | (none — visual only) | unclickable |
| Disabled past slots | Greyed cells | — | unclickable |
| Disabled (beyond max_advance) | Date strip ends visibly + banner | «Запись на эту дату пока недоступна. Открывается за {{N}} дней» | CTA «Напомнить когда откроется» |
| Network/server error | Per §7.4 | — | — |

### Screen F4 — Confirmation

| State | Visual | Copy | Behavior |
|---|---|---|---|
| Success (form view) | Pre-filled summary: service / master / date+time / customer (if known) / price + duration | — | confirm CTA |
| Submitting (after confirm tap) | Button spinner + disabled state | — | wait for response |
| Server 200 (success) | → transitions to F5 | — | — |
| Server 409 (slot taken — race) | Modal: «Этот слот только что заняли» | «Ближайшее свободное у {{master}}: {{nearest_alt}}. Записать?» | per Q-CONV-RACE (referenced [customer-cancellation-reschedule §10.1](./customer-cancellation-reschedule-spec.md)) |
| Server validation error (400) | Inline error under field | per-field copy («Не выбрано время») | block submit |
| Server 5xx | Toast + retry button | «Не получилось. Попробуем ещё раз?» | retry |
| Network error | Toast | per §7.4.1 | retry |

### Screen F5 — Success

| State | Visual | Copy | Behavior |
|---|---|---|---|
| Success (only state) | Centered checkmark + booking summary | per [conversational-ux-framework §5.1.5](./conversational-ux-framework.md) — never «✅ Вы записаны!», prefer «Готово — {{date}} в {{time}}, у {{master}}. Напомню за день и за час до визита.» | CTA «Открыть запись» / «На главную» |

**Forbidden on F5:**
- ❌ Marketing exuberance («Поздравляем с записью!»)
- ❌ Immediate cross-sell («Добавим ещё услугу со скидкой?»)
- ❌ «Поделитесь с другом!» CTA on success (out of moment)

---

## 9. Loading state design principles

### Skeleton vs spinner
- **Skeleton ALWAYS preferred** when content structure is predictable (catalog, list, form)
- **Spinner only when** structure unknown OR very short load (button submit)
- **Never both** in same view

### Timing
- 0-200ms: nothing (avoid flash if data comes back fast)
- 200ms-5s: skeleton
- 5-15s: skeleton + subtle inline note
- 15s+: transition to error state with retry

### Skeleton anatomy
- Match eventual content shape (header height, card sizing, count of items)
- Use background colour `var(--surface-2)` per design tokens
- Gentle shimmer animation (0.5Hz, low opacity)
- Don't over-animate (drains battery, looks frantic)

### Specific patterns
- List: 5-6 skeleton rows
- Card grid: 4-6 skeleton cards in grid
- Form: skeleton labels + skeleton input boxes
- Image: skeleton block with aspect ratio preserved

---

## 10. Empty state design principles

### When empty is normal (not error)
- New tenant has no catalog yet — expected
- Customer has no bookings — expected
- Date with no available slots — expected (master fully booked, day off)

### Pattern: icon + 1-line copy + relevant CTA

```
┌──────────────────────────────────┐
│                                  │
│            [icon]                │
│                                  │
│      У вас нет активных записей  │
│                                  │
│         [Записаться]              │
│                                  │
└──────────────────────────────────┘
```

### Icon choice
- Mini App-native icon set (no decorative «sad face»)
- Functional + neutral (calendar / list / clock)
- No emoji

### Copy rules
- Past tense fact: «У вас нет активных записей»
- NOT future-oriented complaint: «Записи отсутствуют — добавьте»
- NOT apologetic: «К сожалению, услуг пока нет»
- NOT marketing: «Самое время записаться!»

### CTA rules
- Specific verb + outcome: «Записаться», «Посмотреть услуги»
- One CTA primary, optional one secondary
- If nothing actionable from customer side, no CTA (just informational empty)

---

## 11. Error state design principles

### Tone
Calm + Functional + brief. Never panic.

### Information hierarchy per error type

| Error type | What customer needs to know | What to hide |
|---|---|---|
| Network | «Проверьте интернет» | error codes, retry counts |
| 5xx server | «У нас не получается» | stack traces, request IDs |
| 4xx client | What's wrong with input | server internals |
| 404 not found | «Не нашлось» | URLs, IDs |
| 409 conflict | «Только что заняли» | concurrent transaction details |

### Forbidden
- ❌ «Error 500: Internal Server Error» raw
- ❌ Request IDs in customer view (logging only)
- ❌ Stack traces ever
- ❌ «Свяжитесь с администратором (support@…)» — provide in-app path instead
- ❌ Modal blocking after each error (toast preferred for transient)

### Retry behavior
- Network errors: auto-retry once silently after 2s; if still fails, show error UI
- 5xx errors: NEVER auto-retry (could be expensive); show user button
- 4xx client errors: don't retry; user must fix input

---

## 12. Offline + sync-pending special considerations

### What works offline (per Mini App platform capabilities)
- Read access to cached: own profile, last-fetched catalog, last-fetched bookings
- Queue: cancel booking, reschedule, update profile

### What does NOT work offline
- Fresh catalog / slot search (always needs server)
- Booking creation (slot resolver requires server)
- AI Q&A (requires LLM call)

### Sync queue behavior
- On reconnect: queue plays in submission order
- Conflict during sync (slot taken in meantime, booking already cancelled by another path): show user toast with what happened
- Customer can review queued actions in Settings → «Несинхронизированное» (Phase 2+)

### Banners
- 60s offline: subtle bottom toast «Без сети»
- 5min offline: persistent banner at top
- 24h queued action unsync'd: drop + notification

---

## 13. Accessibility (WCAG 2.2 AA baseline)

### Contrast
- All text ≥ 4.5:1 against background (regular text)
- Large text (≥ 18pt) ≥ 3:1
- Interactive controls ≥ 3:1 against surrounding

### Touch targets
- Minimum 44×44 CSS pixels for tap targets
- 8px spacing between adjacent targets

### Focus order
- Logical reading order on every screen
- Skeleton states focusable only after content renders
- Error toasts focusable, dismissible via keyboard

### Screen reader
- Skeleton: `aria-busy="true"` on container
- Empty state: descriptive `aria-label` («Каталог пуст»)
- Error: `role="alert"` on error message
- Disabled CTA: `aria-disabled="true"` + tooltip explaining why
- Sync banners: `aria-live="polite"`

### Motion
- Respect `prefers-reduced-motion` — disable shimmer, reduce transitions
- Skeleton animation: max 0.5Hz cycle
- Never auto-scroll content

### Language
- `lang="ru"` on Mini App root
- All copy in clear standard Russian — no slang, no untranslated English

### Localization-safe layouts
- Text can grow 30% without breaking layout (German/Russian word lengths vary)
- Time/date strings localizable
- Currency formatting per locale

---

## 14. Anti-patterns (cross-screen)

| Anti-pattern | Why bad | Correct |
|---|---|---|
| Spinner on every load regardless of structure | Generic, slow-feeling | Skeleton matching content shape |
| «Loading...» text always visible | Cluttered | Skeleton speaks for itself |
| Sad emojis on empty states (😢 / 😞) | Infantilizing | Neutral icon + plain copy |
| «Извините» on every error | Apology fatigue | Reserve «извините» for AI's actual mistakes |
| Modal for transient errors | Disruptive | Toast for transient, modal for blocking only |
| Auto-retry on 5xx without user knowledge | Hides problem; can amplify | User-initiated retry only |
| First-touch message that asks 3 questions | Customer overwhelmed | 1 hook + max 2-3 CTAs |
| First-touch with emoji opener | Wrong tone | No emoji on opener |
| First-touch in different tone for returning customer | Disorienting | Recognition + warm; not «как новый клиент» |
| Different empty copy on same conceptual emptiness across screens | Inconsistent UX | Per-screen specific copy (catalog vs bookings) but consistent tone |
| Long error messages with technical detail | Customer-hostile | One sentence; tech detail in logs |
| Push «Поделитесь приложением!» on F5 success | Hijacks goal moment | F5 is for goal completion, not virality push |
| First-touch mentions other entry sources | Confusing meta-narrative | Stay in current channel context |
| Cross-channel push («тоже скачайте наш Instagram!») on first MAX touch | Channel-hopping disrespect | Engage where customer is |

---

## 15. Localization

### MVP
- RU only
- Address forms: «Вы»
- Currency: ₽ with thin space
- Numbers: thin space separator
- Date format in UI: «18 мая, ср»; in compact lists: «18.05»
- Time: 24h

### Phase 4+
- Per-language template re-author (don't auto-translate)
- Layouts that breath 30% text expansion
- RTL-safe (Phase 5+ for Arabic markets)

---

## 16. Open questions

| # | Question | Lean | Owner | Urgency |
|---|---|---|---|---|
| **Q-FT1** | First-touch message — sent immediately on bot open OR after 1-2s delay (avoid feeling pre-scripted)? | Immediate but with «typing...» indicator for 1s before message appears — feels more human, less canned | UX | 🟢 |
| **Q-FT2** | Should the first-touch message include customer's name if known from MAX profile? | YES if known + state ≠ DISCOVERED. NO on DISCOVERED (don't reveal we have their name before they introduce themselves — feels surveillant) | UX + Privacy | 🟡 |
| **Q-FT3** | Repeat same QR scan within 24h — should we even send any message? | One message (per §4.1 variant); after that, silent for 24h | UX | 🟢 |
| **Q-FT4** | Phase 2 IG post linkage — how is post→service mapping captured? Manual per IG post in dashboard? | Manual MVP — tenant adds links in IG bio with predefined start_param mapping; v1.2+ auto-detection via IG API | PM + Eng | 🟢 |
| **Q-FT5** | If `start_param` is malformed/unknown — fallback or silent error? | Fallback to source 5 generic; log + alert engineering; never expose to customer | Eng | 🟢 |
| **Q-FT6** | Referral attribution — does referrer get loyalty credit only if referred customer books, or just on arrival? | Per Q-CX7: only on booking (silent tracking); arrival alone doesn't credit. Add to `attribution_metadata.referred_by` at first touch + `loyalty_referral_triggered_at` on booking. | PM | 🟡 |
| **Q-FT7** | If customer arrives via referral but already has record in tenant (was customer of theirs earlier) — do we acknowledge referrer? | NO — they're already our customer; referral is moot. Process as returning customer per §5. | UX | 🟢 |
| **Q-FT8** | Should first-touch always use bot DM, or can it open Mini App directly (deeplink)? | Bot DM always first — establish persona + identity. Then customer taps button to enter Mini App. Direct-to-Mini-App skips the trust-building moment. | UX | 🟡 |
| **Q-FT9** | DORMANT-LIGHT state between DISCOVERED-with-no-reply and full DORMANT — is it warranted or overcomplicated? | Try without it MVP. If analytics shows confused customer ressurection from 7-30-day silence, add later. | UX | 🟢 |
| **Q-FT10** | Source 8 CRM reactivation — if customer already in HUMAN_LOCKED conversation when blast arrives, suppress blast? | YES suppress (per [conversation-ownership-policy](./conversation-ownership-policy.md) HUMAN_LOCKED takes priority over any AI proactive). | PM | 🟡 |
| **Q-MAS1** | Skeleton minimum display time — 200ms enough to avoid flash, or 300ms safer? | 200ms MVP per industry standard; revisit if user testing shows flicker | UX | 🟢 |
| **Q-MAS2** | Should we have a different empty state for «catalog has 0 services because new tenant» vs «catalog filter returned nothing»? | Different copy: first = «У {{salon_name}} пока нет услуг»; second = «По вашему запросу ничего не нашлось» (with «сбросить фильтры» CTA). | UX | 🟢 |
| **Q-MAS3** | 5xx error «Сообщить студии» CTA — should it open admin DM or open «Ошибка в Mini App» pre-filled message? | Pre-filled message «У меня не работает Mini App, экран X» — gives admin context; reduces friction | UX + Eng | 🟢 |
| **Q-MAS4** | Offline queue conflict resolution — if customer cancelled offline then sync-time it was already cancelled by another path — what does UI show? | Toast «Эта запись уже отменена. Если что не так — напишите студии» + dismiss queued action | UX | 🟡 |
| **Q-MAS5** | Auto-retry once on network error — silent or visible to customer? | Silent on first attempt (200ms-2s); if fails again, show error UI explicitly | UX | 🟢 |
| **Q-MAS6** | Per-language skeleton shapes — for RTL languages should we mirror skeleton layout? | YES Phase 5+ (with full RTL support); MVP RU LTR only | UX | 🟢 |
| **Q-MAS7** | Should disabled states have a tooltip explaining why disabled (e.g., «record beyond 60 days»)? | YES on tap (mobile) / hover (desktop); accessibility win | UX | 🟢 |
| **Q-MAS8** | Stale data threshold — 5 min for «stale» banner or longer? | 5min for transactional data (slots), 30min for catalog (less change-sensitive); per data category | Eng + UX | 🟢 |
| **Q-MAS9** | Sync queue persistence — survives Mini App close? Browser refresh? | Persists 24h via localStorage; cleared on explicit sync OR after 24h timeout | Eng | 🟡 |
| **Q-MAS10** | Maximum sync queue depth — should we cap at N actions? | Cap at 5 queued mutations; over → reject with «слишком много несохранённых изменений, подключитесь к сети» | Eng | 🟢 |

---

## 17. Cross-document linkage

- [`product-ux-vision.md`](./product-ux-vision.md) — single-assistant identity preserved across all first-touch templates
- [`core-user-states.md`](./core-user-states.md) — state assignment rules §3 map to state taxonomy
- [`user-journeys.md`](./user-journeys.md) — first-touch initiates each of 3 journeys depending on source/state
- [`conversational-ux-framework.md`](./conversational-ux-framework.md) — voice anchors + §5.1 (booking confirm) referenced in F5 success
- [`information-architecture.md`](./information-architecture.md) — Mini App 5 surfaces; states catalog applies across all
- [`assistant-persona.md`](./assistant-persona.md) — voice envelope; all first-touch templates conform
- [`attribution-policy.md`](./attribution-policy.md) — `attribution_metadata` populated with entry_source + first_seen_source for analytics
- [`event-taxonomy.md`](./event-taxonomy.md) — `conversation.started` event with entry_source payload; `customer.created` if new record
- [`conversation-ownership-policy.md`](./conversation-ownership-policy.md) — HUMAN_LOCKED state suppresses first-touch (§3 rule)
- [`customer-cancellation-reschedule-spec.md`](./customer-cancellation-reschedule-spec.md) — F4 409 conflict pattern shared
- [`schedule-editor-wireframes.md`](./schedule-editor-wireframes.md) Q-SW12 — offline queue pattern shared
- [`../handoffs/2026-05-18-customer-first-time-handoff.md`](../handoffs/2026-05-18-customer-first-time-handoff.md) — adjacent first-visit experience after first-touch
- [`../handoffs/2026-05-18-marketing-campaigns-handoff.md`](../handoffs/2026-05-18-marketing-campaigns-handoff.md) — source 8 CRM reactivation linkage

---

## 18. What this unblocks

- **Phase 1 / 4b implementation** — all 6 customer Mini App screens have locked first-touch + state designs
- **Per-source attribution** — backend can correctly classify entry_source in `attribution_metadata`
- **Customer recognition** — returning customer flow §5 covers ACTIVE_REGULAR / POST_VISIT / AT_RISK / HUMAN_LOCKED resume
- **Cold acquisition channels** — QR, IG, Maps each have appropriate first-touch tone
- **Universal Mini App state patterns** — engineering applies §7 catalog to any future screen
- **Loading skeleton consistency** — design tokens + timing rules per §9
- **Error tone consistency** — calm functional voice per §11; never panic, never tech-leak
- **Offline + sync UX** — §12 patterns shared with master/owner screens
- **Accessibility baseline** — WCAG 2.2 AA in §13 applies to all customer surfaces

## 19. What this does NOT unblock

- ❌ Booking flow templates (covered in [`conversational-ux-framework.md`](./conversational-ux-framework.md))
- ❌ Cancellation/reschedule (covered in [`customer-cancellation-reschedule-spec.md`](./customer-cancellation-reschedule-spec.md))
- ❌ Master + owner Mini App states (separate scope)
- ❌ Phase 2+ entry sources (IG post mapping, Maps integration, web button, CRM reactivation) — patterns defined but launches gated on integrations
- ❌ Multi-language (Phase 4+)
- ❌ Voice messages (Q-C6 deferred)
- ❌ Push notifications outside MAX chat (MAX platform limitation)
- ❌ Skip persona-conformance linter on first-touch generated messages — every template must pass

---

## 20. Sign-off

| Role | Approval | Date |
|---|---|---|
| UX Architect | ✅ | 2026-05-18 |
| Mini App frontend lead | ☐ | |
| Backend (entry_source + attribution_metadata writes) | ☐ | |
| AI prompt engineering (first-touch templates) | ☐ | |
| Accessibility (WCAG 2.2 AA compliance review) | ☐ | |
| Privacy / Legal (Q-FT2 name disclosure ruling) | ☐ | |

## Last verified
2026-05-18 (initial draft, customer first-touch + Mini App states locked for 4b)
