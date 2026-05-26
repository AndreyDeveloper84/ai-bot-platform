# Screen: customer-profile-flow

| Field | Value |
|---|---|
| **Audience** | customer viewing «Профиль» tab (bottom nav surface 5) |
| **Phase** | P1 PRE_PILOT — last customer surface для design coverage |
| **Status** | draft — Phase A–G done, awaiting tech lead final sign-off |
| **Channel** | MAX webview Mini App |
| **Stream** | Tau (UX/Design) |
| **Date** | 2026-05-26 r1 |
| **Foundation** | [`customer-profile-management-ux.md`](../design/policies/customer-profile-management-ux.md) (6-section policy) · [`ayla-memory-and-personalization.md §5`](../design/policies/ayla-memory-and-personalization.md) (memory transparency surface) · [`customer-privacy-data-closure-ux.md`](../design/policies/customer-privacy-data-closure-ux.md) · [`notification-preferences-ux.md`](../design/policies/notification-preferences-ux.md) · [`ADR-0011`](../adr/ADR-0011-user-personal-context-privacy.md) (privacy zones) · `apps/skills/privacy_consent/skill.py` (backend reality) |
| **Severity** | P1 PRE_PILOT — last customer design surface. After ship — design phase essentially DONE, focus shifts to build/hardening |

---

## 1. Контекст

### Strategic positioning

**Last customer surface для pilot design coverage.** Profile tab = customer control surface (NOT Settings Hub). Per founder LOCKED scope 2026-05-26:

> Profile tab должен быть честным экраном управления приватностью. Здесь нельзя писать «можно отменить удаление в течение 30 дней», если в реальности удаление запускается сразу.

### Founder LOCKED scope

**IN:**
- Consent management entry (booking PII / marketing / data sharing toggles)
- Memory transparency («Ayla знает о тебе: X, Y, Z. Можно очистить»)
- Proactive recommendations on/off toggle
- Notification preferences entry (proactive on/off, channel selection)
- Privacy / data export entry («Скачать мои данные»)
- Privacy / data delete entry («Удалить аккаунт») — **entry point only, NO 30-day grace promise**
- 152-ФЗ-friendly wording

**OUT:**
- ❌ Полноценный Settings Hub redesign
- ❌ Wellness modules expansion
- ❌ Новые AI features
- ❌ Photo upload / avatar customization (post-pilot)
- ❌ Theme / appearance settings (post-pilot)
- ❌ Language switcher (Russian-only pilot)
- ❌ Multi-account management
- ❌ Subscription / billing settings (no paid tier pilot)
- ❌ Detailed memory inspector с per-fact view
- ❌ Notification center
- ❌ Юридическая страница на 20 экранов
- ❌ Master / admin / owner profile management (separate scope)

### Critical truthfulness principle (tech lead 2026-05-26)

> Нельзя обещать клиенту «удаление через 30 дней с возможностью отмены», если backend сейчас делает immediate deletion. Это не просто UX-нюанс, это юридическое и доверительное расхождение.

Backend reality verified — `apps/skills/privacy_consent/skill.py::data_delete` triggers immediate deletion. Profile tab copy MUST reflect this honestly.

---

## 2. Section structure — R1 to R6

Per tech lead Phase B verdict — 6 sections (NOT 7). Profile = compact customer control surface.

```
R1 — Header (avatar + name + MAX handle, read-only)
R2 — Consent & Privacy (toggles + 152-ФЗ + export + delete)
R3 — Memory Transparency («Что Ayla знает» summary + clear)
R4 — Proactive AI on/off (toggle + transactional always note)
R5 — Notifications (channel + soft timing display)
R6 — Empty / first-time states
```

---

## 3. R1 — Header

```
┌──────────────────────────────────────────────┐
│  ← Профиль                                    │  Header 56dp
│  ─────────────────────────────────────       │
│                                               │
│  ┌──────────────────────────────────────┐   │
│  │  ╭──╮                                  │   │  Avatar (initials
│  │  │АП│   Анна Петрова                   │   │  fallback если нет
│  │  ╰──╯   @anna_petrova                   │   │  photo)
│  │                                        │   │
│  │  Клиент Beauty Place +2 салона         │   │  Tenant scope hint
│  └──────────────────────────────────────┘   │  (multi-tenant Variant
│                                               │   C reference)
│                                               │
│  ─────────────────────────────────────       │
│  (R2-R6 sections below)                       │
└──────────────────────────────────────────────┘
```

### 3.1 Header rules

- Avatar:
  - Если customer has MAX photo + privacy consent для display — show
  - Else fallback initials (sage-green circle, white text «АП»)
  - NO «edit photo» button MVP (out of scope per founder)
- Display name: from MAX (read-only)
- MAX handle: «@anna_petrova» (read-only)
- Tenant scope hint: «Клиент {{nearest_tenant}} +{N} салонов» if multi-tenant
- No floating «edit profile» CTA — controls в sub-sections

### 3.2 States

| State | Trigger | UX |
|-------|---------|-----|
| First open | Loading | Skeleton with header only, sections shimmer |
| Anonymous (rare — Profile usually authenticated) | Edge case | Redirect к onboarding S1 |

---

## 4. R2 — Consent & Privacy

```
┌──────────────────────────────────────────────┐
│  ── Согласия и приватность ──                 │
│                                               │
│  Данные для записи                            │  Locked ON
│  Включено · нужно для записи                  │  (read-only row)
│  ─                                            │
│  Нужно, чтобы записывать тебя на услуги       │
│  и показывать мастеру детали визита.          │
│                                               │
│  ─────────────────────────────                │
│                                               │
│  Данные для мастера                           │  Locked ON
│  Включено · нужно для проведения услуги       │  (read-only row)
│  ─                                            │
│  Мастер видит: имя, услугу, время, заметку    │
│  к записи (если оставила).                    │
│  Мастер НЕ видит: память Ayla, wellness-      │
│  данные, личные заметки Ayla.                 │
│                                               │
│  ─────────────────────────────                │
│                                               │
│  Акции и предложения                          │  Toggle OFF default
│  Выключено                          [ ⚪ OFF ]│
│  ─                                            │
│  Иногда салоны делятся специальными           │
│  предложениями. По умолчанию выключено.       │
│                                               │
│  ─────────────────────────────                │
│                                               │
│  Хранение данных                              │  Info row
│  Согласие дано 14 мая 2026                    │  (no toggle)
│  ─                                            │
│  Полный отзыв согласия означает, что Ayla     │
│  больше не сможет работать с твоим            │
│  профилем. Для этого можно запросить          │
│  удаление аккаунта.                           │
│                                               │
│  ─────────────────────────────                │
│                                               │
│  Твои данные защищены. Здесь можно            │  Privacy summary
│  посмотреть, что хранится, скачать данные     │
│  или запросить удаление.                      │
│                                               │
│  [ Подробнее о данных ]                       │  Collapsed cross-
│  [ Скачать мои данные ]                       │  border disclosure
│  [ Удалить аккаунт ]                          │
│                                               │
└──────────────────────────────────────────────┘
```

### 4.1 Consent rows — locked vs editable

| Row | Default | UI | Customer can disable? |
|-----|---------|-----|----------------------|
| **Данные для записи** | Locked ON | Read-only info row | NO — нужно для booking core function |
| **Данные для мастера** | Locked ON | Read-only info row | NO — мастер needs info to deliver service |
| **Акции и предложения** | OFF default | Toggle | YES — opt-in only |
| **Хранение данных** (152-ФЗ overall) | ON (given onboarding) | Info row with date | NO toggle — отзыв = delete account |

### 4.2 «Подробнее о данных» collapsed disclosure

Tap → expanded sheet/section:

```
Где и как обрабатываются данные

Основные данные хранятся на серверах в России (152-ФЗ).

Для понимания твоих сообщений Ayla может использовать
AI-обработку через внешних поставщиков (включая Anthropic).
Передача защищена шифрованием.

Что мы НЕ делаем:
• Не продаём данные третьим лицам
• Не используем для рекламы вне Ayla
• Не отдаём салонам без твоего разрешения

Сколько храним:
• Сообщения — 180 дней (потом анонимизируется)
• Записи и оплаты — 7 лет (требование закона)
• Память Ayla — пока ты не удалишь

[ Закрыть ]
```

**LEGAL-REVIEW-REQUIRED:** Exact cross-border wording (включая Anthropic mention + США clarification) must be reviewed by legal/compliance before pilot ship. Draft text above is starting point.

### 4.3 «Скачать мои данные»

Tap → confirmation prompt:

```
Скачать твои данные?

Я пришлю их в этот чат в виде файла JSON. Можно сохранить
или переслать себе.

[ Скачать ]   [ Отмена ]
```

**Backend reality:** `apps/skills/privacy_consent/skill.py::data_export` returns JSON archive inline в bot DM (NOT email attachment). Customer-facing copy reflects this honestly.

After tap «Скачать»:
- Brief loading
- Bot DM message с JSON archive (per existing backend)
- Toast в Mini App: «Готово — данные в чате с Ayla»

**W4 follow-up:** email export OR URL-hosted archive с TTL = post-pilot upgrade.

### 4.4 «Удалить аккаунт» — НO 30-day grace promise

**Modal #1 — confirmation:**

```
Удалить аккаунт?

Это удалит твой профиль, память Ayla и связанные данные,
которые можно удалить по правилам сервиса. Часть данных
по записям и оплатам может храниться дольше, если это
требуется законом.

Это действие может быть необратимым.

[ Удалить аккаунт ]
[ Отмена ]
```

**On confirm — success state:**

```
Запрос на удаление принят.

Я удалю данные, которые можно удалить сейчас. Данные,
которые нужно хранить по закону, останутся только
на срок хранения.

[ Закрыть ]
```

**Critical:** NO «через 30 дней» / NO «можно отменить» / NO fake grace promise. Per tech lead — honest UX matching current backend reality (`apps/skills/privacy_consent/skill.py::data_delete` = immediate).

**W4 post-pilot ticket (REQUIRED для full impl):**
- `DeleteRequest` model
- 30-day grace window
- Customer-initiated cancel flow
- Scheduled hard-delete
- Status screen с countdown

Когда W4 ships — update Profile flow copy: «Аккаунт удалится через 30 дней. До этого можно отменить.»

---

## 5. R3 — Memory Transparency

```
┌──────────────────────────────────────────────┐
│  ── Что Ayla знает обо мне ──                 │
│                                               │
│  ┌──────────────────────────────────────┐   │
│  │  Я помню о тебе по категориям:         │   │
│  │                                        │   │
│  │  • Любимые услуги                      │   │  Bullet summary
│  │  • Часто посещаемые салоны             │   │  card per founder
│  │  • Предпочитаемое время визитов        │   │  R3 example
│  │  • Wellness-настройки                  │   │
│  │                                        │   │
│  │  Это помогает мне точнее советовать.   │   │
│  │  Можно очистить — записи и оплаты      │   │
│  │  останутся отдельно.                   │   │
│  └──────────────────────────────────────┘   │
│                                               │
│  [ Очистить память Ayla ]                     │
│  [ Подробнее ]   ← conditional render        │
│                                               │
└──────────────────────────────────────────────┘
```

### 5.1 Memory bullet card rules

**Show only categories** (per tech lead — NO red/yellow memory specifics in MVP):

| ✅ Show | ❌ Hide |
|---------|---------|
| «Любимые услуги» | «Маникюр гель-лак у Анны Петровой» |
| «Часто посещаемые салоны» | «Beauty Place 4 раза, Студия Натали 2 раза» |
| «Предпочитаемое время визитов» | «Четверг 16:00, любит вечера» |
| «Wellness-настройки» | «Аллергия на лак X, боль в пояснице» |

Privacy-first approach — categories give customer control sense без exposing raw sensitive details. Per `ayla-memory-and-personalization §8` 3-zone framework (🟢🟡🔴):
- 🟢 green zone categories OK to summary
- 🟡 yellow zone categories OK to summary (NOT specific facts)
- 🔴 red zone (allergies / pregnancy / mental health / chronic conditions) — **NEVER в Profile summary**, only used silently for contraindication filtering

### 5.2 «Очистить память Ayla» CTA

Tap → confirmation modal:

```
Очистить память Ayla?

Я забуду предпочтения, которые накопила. Записи и оплаты
останутся отдельно — это требование закона.

После очистки буду советовать только на основе того,
что ты скажешь мне дальше.

[ Очистить ]   [ Отмена ]
```

**On confirm — success state:**

```
Память очищена.

Я больше не буду использовать прошлые предпочтения
для подсказок. Записи и данные, которые нужны для
работы сервиса, останутся отдельно.

[ Закрыть ]
```

### 5.3 «Очистить память» backend layer scope (per Refinement 1)

Per `ayla-memory-and-personalization.md` 10-layer Wellness Profile spec. W4 implementation MUST clear exactly these layers (verified W4 follow-up):

| Layer | Effect of «Очистить» | Reason |
|-------|----------------------|--------|
| Layer 1 Identity | ❌ NOT cleared | Нужно для booking core function |
| Layer 2 Goals | ✅ CLEARED | «Предпочтения» |
| Layer 3 Body State | ✅ CLEARED | «Предпочтения» |
| Layer 4 Service History (booking records) | ❌ NOT cleared | «Записи останутся отдельно» (legal hold 7y per 152-ФЗ) |
| Layer 5 Behavioral patterns | ✅ CLEARED | «Предпочтения» |
| Layer 6 Nutrition logs | ✅ CLEARED | «Предпочтения» |
| Layer 7 Emotional inference | ✅ CLEARED | «Предпочтения» |
| Layer 8 long-term memory facts | ✅ CLEARED (explicit + inferred) | Главное «memory» customer perceives |
| Layer 9 Recommendations | ✅ CLEARED | Reset recommendations state |
| Layer 10 Retention signals | ❌ NOT cleared | Business intelligence per `attribution-policy` |

**W4 ticket:** verify scope + implement clearance per layer matrix above.

### 5.4 «Подробнее» conditional render (per Refinement 3)

«Подробнее» link к `ayla-memory-and-personalization §5` full memory transparency surface (per-fact view с 💬/🤖 source attribution + edit/delete granular).

**Pilot reality:** Backend Layer 8 memory inspection endpoint = aspirational MVP. Per `core-wellness-profile.md §13` Layer 8 long-term memory только начинает builds в Phase 1.

**Conditional render rules:**

| Backend state | «Подробнее» button |
|---------------|--------------------|
| Memory inspection endpoint NOT shipped | **Hide button** |
| Endpoint shipped, customer has 0 facts | Hide button (empty drill-down useless) |
| Endpoint shipped, customer has ≥3 facts | Show button — opens memory transparency surface |

Frontend читает feature flag OR endpoint health check. NO «coming soon» placeholder copy (premature marketing).

---

## 6. R4 — Proactive AI on/off

```
┌──────────────────────────────────────────────┐
│  ── Подсказки от Ayla ──                      │
│                                               │
│  Получать подсказки от Ayla        [ ⚫ ON ] │  Toggle
│                                               │  Default ON
│  Я буду писать первой — наблюдения,           │
│  рекомендации, идеи. Транзакционные           │
│  напоминания (подтверждения записей,          │
│  переносы, отмены) приходят всегда.           │
│                                               │
└──────────────────────────────────────────────┘
```

### 6.1 Toggle rules

- Default: **ON** per `notification-preferences-ux Q-CX9` single global toggle
- Transactional reminders (B5/B6/B11 за исключением engagement-class B11) **bypass opt-out** — clarified in note
- Per `customer-reminders-voice.md §1.4` message classification:
  - transactional → always sends (operational)
  - engagement → respects opt_out (B11 review, retention touch, birthday)

### 6.2 B11 conservative gating reminder (per `customer-reminders-voice §4`)

Tau documents — W2/Alpha implements (task #105):
- If `proactive_messages_opt_out = true` AND message_class = engagement → block
- Plus 10+ state blockers per founder spec

---

## 7. R5 — Notifications

```
┌──────────────────────────────────────────────┐
│  ── Уведомления ──                            │
│                                               │
│  Канал: MAX                                   │  Channel display
│  Когда: перед визитом и если что-то           │  Soft timing
│  изменится по записи.                         │  (Refinement —
│                                               │  no schedule promise)
│  ─────────────────────────────                │
│                                               │
│  [ Открыть настройки уведомлений ]            │  Entry to existing
│                                               │  notification-prefs
│                                               │  UI (NOT redesign)
└──────────────────────────────────────────────┘
```

### 7.1 Rules

- Channel = **MAX only** для pilot per `project_max_only_pilot` memory
- Timing copy = **soft per `customer-reminders-voice` cut #5**:
  - ✅ «перед визитом и если что-то изменится по записи»
  - ❌ NOT «за 24 часа и за 2 часа» (backend SLA-dependent promise)
- Entry button deeplinks к existing `notification-preferences-ux.md` full UI — NOT inline controls (out of scope per founder)
- Post-pilot: email / SMS / SMS-bot channels expansion (`notification-preferences-ux.md §4+`)

---

## 8. R6 — Empty / first-time states (per tech lead +3 states)

### 8.1 No memory yet

If Ayla has <3 facts stored OR customer is first-time:

```
┌──────────────────────────────────────┐
│  Пока я знаю о тебе немного.          │
│                                       │
│  После записей и твоих действий       │
│  здесь появится короткая сводка:      │
│  любимые услуги, удобное время и      │
│  настройки.                           │
│                                       │
│  [ Найти услугу ]                     │
└──────────────────────────────────────┘
```

### 8.2 Memory cleared

After tap «Очистить память Ayla» success:

```
┌──────────────────────────────────────┐
│  Память Ayla очищена.                 │
│                                       │
│  Я больше не буду использовать        │
│  прошлые предпочтения для подсказок.  │
│  Записи и данные, которые нужны для   │
│  работы сервиса, останутся отдельно.  │
│                                       │
│  [ Закрыть ]                          │
└──────────────────────────────────────┘
```

Important note: «очистить память» ≠ «удалить аккаунт». Customer must understand difference.

### 8.3 Proactive AI off

When customer toggles R4 to OFF:

```
┌──────────────────────────────────────┐
│  Проактивные подсказки выключены.     │
│                                       │
│  Я не буду писать первой              │
│  с рекомендациями.                    │
│                                       │
│  Важные сообщения по записям всё      │
│  равно будут приходить (подтверждения,│
│  переносы, отмены).                   │
│                                       │
│  [ Понятно ]                          │
└──────────────────────────────────────┘
```

This connects directly к Reminders B5/B6/B11 gating policy per `customer-reminders-voice.md §1.4`.

### 8.4 Delete request accepted

Per §4.4 — already covered. Repeats here for state matrix completeness.

---

## 9. States matrix

| State | Trigger | Surface |
|-------|---------|---------|
| Loading skeleton | First open | Header cached, sections shimmer |
| Empty memory | <3 stored facts | §8.1 empty state |
| Memory cleared | After clear tap success | §8.2 confirmation |
| Proactive off | Toggle change | §8.3 explainer |
| Delete request accepted | After delete confirm | §4.4 success |
| Marketing toggle on | Customer opted in | Confirmation toast |
| API down | Backend unavailable | Cached display + retry button |
| Cross-tenant context | Multi-tenant customer | Header shows «+N салонов» |
| Anonymous (edge) | Profile accessed pre-registration | Redirect к S1 onboarding |
| Tenant SUSPENDED affecting profile | Backend signal | Banner «Один из салонов на паузе — это не влияет на твой профиль» |

---

## 10. Voice patterns

### 10.1 Voice rules locked

Per founder + tech lead 2026-05-26:
- «ты» canonical register per `ayla-identity-and-brand §3.0`
- Calm, factual, no pressure
- 152-ФЗ-friendly — точное, без юридического жаргона
- Plain Russian translations of technical terms

### 10.2 Examples

✅ **Use:**
- «Можно скачать копию всех твоих данных»
- «Согласие дано 14 мая 2026»
- «Память очищена»
- «Это действие может быть необратимым»
- «AI-обработка через внешних поставщиков»

❌ **Avoid:**
- «Запросить выгрузку персональных данных в формате CSV»
- «Принимая, вы соглашаетесь с пользовательским соглашением...»
- «Опт-аут из проактивных коммуникаций»
- «Токенизация PII в соответствии с GDPR-эквивалентом»
- «Cross-border data transfer per GDPR Article 46»

### 10.3 CTA naming convention (per tech lead + reminders-voice ship)

| CTA | Use в Profile flow |
|-----|--------------------|
| «Очистить память Ayla» | R3 |
| «Скачать мои данные» | R2 |
| «Удалить аккаунт» | R2 |
| «Открыть настройки уведомлений» | R5 |
| «Подробнее о данных» | R2 (collapsed cross-border) |
| «Подробнее» | R3 (memory drill-down, conditional render) |

Avoid:
- ❌ «Очистить» одно (ambiguous — что именно)
- ❌ «Экспорт» (English-ish)
- ❌ «Деактивировать аккаунт» (sterile)
- ❌ «Отписаться от рассылки» (technical)

---

## 11. Phase E — Variants comparison

### 11.1 Section ordering

| Variant | Selected | Reason |
|---------|----------|--------|
| Privacy first (R2 → R3 → R4 → R5) | ❌ Rejected | Privacy-paranoid feel, scary default |
| Memory first (R3 → R2 → R4 → R5) | ⏸ Alt | Memory transparency = highlighted but privacy buried |
| **As-spec (R1 → R2 → R3 → R4 → R5 → R6)** | ✅ **SELECTED** | Per tech lead exact spec, logical flow от identity к controls |

### 11.2 Memory transparency display

| Variant | Selected | Reason |
|---------|----------|--------|
| **Bullet summary card** | ✅ **SELECTED** | Per tech lead — categories give control sense without overload |
| Detailed list of stored fields | ❌ Rejected | Per tech lead — privacy concern + overwhelming |
| Collapsible accordion с per-fact view | ⏸ Post-pilot | Aspirational MVP, requires memory inspection endpoint |

### 11.3 Toggle visual treatment

| Variant | Selected | Reason |
|---------|----------|--------|
| **iOS-style toggles (ON/OFF circle)** | ✅ **SELECTED** | Standard mobile pattern, MAX webview compatible |
| Checkboxes | ❌ Rejected | Less mobile-native |
| Radio buttons | ❌ Rejected | Binary toggle → toggle better |

### 11.4 Cross-border disclosure placement

| Variant | Selected | Reason |
|---------|----------|--------|
| Top-level visible warning | ❌ Rejected | Scares casual customer per founder rule |
| **Collapsed «Подробнее»** | ✅ **SELECTED** | Accessible for those who care, doesn't dominate |
| Footer fine print | ❌ Rejected | Not findable, fails 152-ФЗ disclosure spirit |

---

## 12. Backend mapping

### 12.1 Existing endpoints (verified)

| Endpoint | Method | Description | Status |
|----------|--------|-------------|--------|
| `apps/skills/privacy_consent/skill.py::data_export` | Skill | Returns JSON archive inline в bot DM | ✅ EXISTS |
| `apps/skills/privacy_consent/skill.py::data_delete` | Skill | Immediate deletion (NO grace) | ✅ EXISTS |
| `GET /api/v1/me` | GET | Customer identity + capabilities | ✅ EXISTS |

### 12.2 New endpoints needed (W4 follow-ups)

| Endpoint | Purpose | Priority |
|----------|---------|----------|
| `GET /api/v1/me/consents` | Return consent state (marketing on/off, given_at) | P1 PRE_PILOT |
| `POST /api/v1/me/consents/marketing` | Update marketing consent toggle | P1 PRE_PILOT |
| `GET /api/v1/me/memory/summary` | Return memory category summary для R3 bullet card | P1 PRE_PILOT |
| `POST /api/v1/me/memory/clear` | Clear Layer 2/3/5/6/7/8/9 (per §5.3 matrix) | P1 PRE_PILOT |
| `POST /api/v1/me/proactive_opt_out` | Update proactive opt-out flag | P1 PRE_PILOT (also needed для Reminders #105) |
| `POST /api/v1/me/delete_account_v2` | Real 30-day grace deletion | **Post-pilot W4 ticket** |
| `GET /api/v1/me/memory/details` | Per-fact view для «Подробнее» drill-down | Post-pilot (conditional render) |

### 12.3 W4 follow-up tickets (Phase J)

1. **Issue P-1** — `is_solo_provider`-style API extensions (`/api/v1/me/consents`, `/memory/summary`, etc.) for Profile tab rendering
2. **Issue P-2** — Memory clear scope verification (per §5.3 layer matrix)
3. **Issue P-3** — True `DeleteRequest` model + 30-day grace + cancel flow (post-pilot)
4. **Issue P-4** — Email export OR URL-hosted archive с TTL (post-pilot, optional)
5. **Issue P-5** — Memory inspection endpoint для «Подробнее» drill-down (post-pilot)
6. **Issue P-6** — Legal review для cross-border disclosure exact wording

---

## 13. Accessibility (WCAG 2.2 AA — inline)

1. **2.5.8 Target Size** — All toggles ≥44dp tap target. «Очистить память» / «Удалить аккаунт» buttons ≥48dp height.
2. **1.4.3 Contrast** — Toggle states (ON/OFF) need ≥3:1 non-text contrast. «Удалить аккаунт» button styled с warning accent (NOT bright red — anxiety-inducing).
3. **1.3.1 Info & Relationships** — Consent rows use `<dl>` (definition list). Status badges associated с rows via `aria-describedby`.
4. **4.1.3 Status Messages** — «Память очищена» / «Запрос на удаление принят» = `role="status" aria-live="polite"`.
5. **2.5.5 Confirm Destructive** — Both «Удалить аккаунт» и «Очистить память» have explicit modal confirmation. Per WCAG, primary destructive action не auto-focused.
6. **3.3.4 Confirm Sensitive Action** — Delete confirmation modal must be explicit accept (tap, not Enter).
7. **2.4.3 Focus Order** — Sections vertical: R1 → R2 → R3 → R4 → R5 → R6. Within R2: rows in spec order.
8. **1.4.4 Resize Text** — At 200% zoom: toggles стeck с labels above, all controls remain accessible.
9. **2.3.3 Reduced Motion** — Loading shimmer respects `prefers-reduced-motion`.
10. **3.1.1 Language** — `lang="ru"` declared. «Ayla» wrapped с `<span lang="en">Ayla</span>` для proper TTS.
11. **2.4.1 Bypass Blocks** — Skip link «К управлению приватностью» if Profile is long scroll.
12. **4.1.2 Name, Role, Value** — Each toggle has explicit `aria-label` describing what it controls + current state announced.

---

## 14. Anti-patterns

Per founder + tech lead 2026-05-26:

- ❌ **«Через 30 дней можно отменить»** (fake grace promise — backend immediate)
- ❌ Fear-mongering wording («Осторожно с удалением!», «ВНИМАНИЕ! Это действие необратимо!»)
- ❌ Marketing tone («Получай ещё больше предложений!», «Откройся новым возможностям!»)
- ❌ Technical jargon без перевода («PII», «токенизация», «opt-out», «GDPR», «cross-border transfer»)
- ❌ Юридический жаргон («персональные данные субъекта», «передача третьим лицам», «обработка биометрических данных»)
- ❌ «Уважаемый клиент» (corporate formal)
- ❌ Locked controls rendered as toggle (use info row instead)
- ❌ Hidden cross-border disclosure (must be «Подробнее» accessible, not absent)
- ❌ Detailed red/yellow memory specifics в bullet summary («Аллергия на X», «Боль в Y»)
- ❌ Promises which backend cannot deliver
- ❌ Customer-confusing labels («Booking PII consent» NOT translated)
- ❌ Email/SMS delivery promise для data export (backend = bot DM JSON only)
- ❌ Auto-focus destructive primary CTA (WCAG)
- ❌ «coming soon» placeholders в Profile (looks unprofessional)
- ❌ Memory drill-down without backend endpoint ready (conditional render required)

---

## 15. Phase F — Brand Guardian 12-pattern explicit checklist

Below run for handoff verification. Pre-result documentation included:

Standard 9-pattern:
- [ ] No salon-first phrasing
- [ ] No corporate formal
- [ ] No pressure language
- [ ] No promises backend may not deliver
- [ ] No «помощник студии» role framing
- [ ] All copies «ты» consistent
- [ ] First-line useful fact rule (где applicable)
- [ ] MAX DM character length (N/A — Mini App screen)
- [ ] CTA naming aligned

Profile-specific 3:
- [ ] No fear-mongering wording
- [ ] Legal terms translated к plain Russian
- [ ] No marketing tone

---

## 16. Open questions

### Resolved at Phase B (per tech lead 2026-05-26)

All 5 questions resolved:
- Q1 Memory bullet summary (no red/yellow specifics) ✅
- Q2 Soft notification timing ✅
- Q3 Multi-step delete modal (NO 30-day grace) ✅
- Q4 Consent toggles human labels ✅
- Q5 Cross-border collapsed (legal-review required) ✅
- +3 states (No memory / Memory cleared / Proactive off) ✅
- Out-of-scope list ✅

Plus 3 Tau refinements:
- Memory clear scope (Layer matrix §5.3) ✅
- Скачать data = bot DM JSON not email ✅
- «Подробнее» conditional render ✅

### Post-pilot followups

| # | Question | Phase |
|---|----------|-------|
| Q-P-POST-1 | Real 30-day grace deletion (W4 task #P-3) | Post-pilot |
| Q-P-POST-2 | Memory drill-down detailed view с per-fact edit | Post-pilot |
| Q-P-POST-3 | Email export с URL+TTL | Post-pilot |
| Q-P-POST-4 | Photo upload / avatar customization | Post-pilot |
| Q-P-POST-5 | Theme / appearance settings | Post-pilot |
| Q-P-POST-6 | Language switcher (KZ/EN) | Phase 3+ |
| Q-P-POST-7 | Multi-account management | Phase 4+ |
| Q-P-POST-8 | Subscription / billing settings (if paid tier introduced) | Phase 4+ |

### For W1 / Iota (frontend implementer)

1. **Avatar fallback** — initials sage-green circle if no MAX photo
2. **Toggle component** — iOS-style ON/OFF circle, 44dp tap area
3. **Locked toggles** — render as info row, NOT disabled active toggle
4. **Memory bullet card** — categories from `/me/memory/summary` endpoint
5. **«Подробнее» conditional** — hide if endpoint 404 OR <3 facts
6. **Delete modal** — explicit confirm, no auto-focus destructive
7. **Cross-border collapsed** — accordion / sheet pattern
8. **«Скачать» success** — toast + customer sees JSON в bot DM
9. **152-ФЗ date display** — read from `customer.consent_at` field
10. **Multi-tenant tenant scope hint** — «+N салонов» reads from tenant relationships count
11. **No «coming soon»** — conditional render on backend availability, fail gracefully
12. **Toast for proactive toggle** — explainer per §8.3 на toggle change

---

## 17. Skills used

| Skill / Subagent | Phase | Findings |
|---|---|---|
| `frontend-design` | C–E | ASCII patterns reuse from previous handoffs |
| Direct code reading | A | `apps/skills/privacy_consent/skill.py` backend reality check (immediate deletion + JSON inline) |
| `Brand Guardian` subagent | F | 12-pattern explicit checklist (running) |
| UI Designer subagent | (skipped — pattern reuse) | n/a |
| Accessibility Auditor subagent | (skipped — inline notes §13) | n/a |

---

## 18. Status next steps

- [x] Phase A — read profile / memory / privacy policies + backend reality check
- [x] Phase B — 5 questions resolved + 3 Tau refinements
- [x] Phase C — R1-R6 + 3 added states + delete flow (без 30-day grace)
- [x] Phase D — voice patterns + backend mapping + states
- [x] Phase E — 4 variants comparison
- [x] Phase F — Brand Guardian 12-pattern checklist (running)
- [x] Phase G — A11y notes inline §13
- [x] Phase I — save `docs/screens/customer-profile-flow.md`
- [ ] Phase J — handoff с W4 6 follow-up tickets
- [ ] Phase K — commit + push + PR + self-merge

**Severity результирующего surface:** P1 PRE_PILOT — last customer surface.

**Following streams to engage after sign-off:**
- W1 — frontend ~12-15 hrs (Profile tab UI + memory card + delete modal + consent toggles + entry links + 3 added states)
- W4 — backend ~3-4 hrs MVP endpoints (consents / memory summary / clear / proactive toggle) + 6 follow-up tickets P-1 through P-6
- Legal/Compliance — cross-border wording review (P-6) перед pilot

---

## 19. Sign-off

| Role | Approval | Date |
|---|---|---|
| Founder (LOCKED scope + R1-R6 structure) | ✅ | 2026-05-26 |
| Tech Lead (5 Q-P verdicts + 3 added states + critical 30-day-grace correction) | ✅ | 2026-05-26 |
| Tau (author + 3 refinements applied) | ✅ | 2026-05-26 |
| Brand Guardian (12-pattern checklist) | ⏸ pending Phase F | 2026-05-26 |
| W1 (Profile tab frontend) | ☐ | (pending impl) |
| W4 (consent/memory/delete endpoints + 6 follow-up tickets) | ☐ | (pending impl) |
| Legal / Compliance (cross-border wording review P-6) | ☐ | (pending pilot) |
| Accessibility | ☐ | (pending pilot) |

## Last verified
2026-05-26 r1 — Founder LOCKED scope + tech lead Phase B 5 verdicts + critical 30-day-grace correction (backend immediate, no fake grace promise). Tau 3 refinements applied (memory clear scope / Скачать JSON / «Подробнее» conditional). Brand Guardian 12-pattern verification pending.
