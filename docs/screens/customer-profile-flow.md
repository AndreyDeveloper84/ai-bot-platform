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

## 0. Pilot scope & backend reality (2026-06-01, tech-lead recon)

> **Recon source:** code-audit of BOTH repos (bot-platform + beautygo/Ayla). This note supersedes any earlier assumption that profile-tab truthfulness rests on `data_delete = immediate` alone.

**Verified ground truth (both repos code-audited):**
- bot-platform `apps/skills/privacy_consent::data_delete` = IMMEDIATE, HARD-delete, UNCONDITIONAL — но **bot-platform-only** (ноль cross-service вызовов). Export = inline-JSON только данных bot-platform. **Нет endpoint для очистки памяти и нет таблиц `UserPersonalContext`/`MemoryEntry`** — memory-слой ещё не построен.
- beautygo (Ayla) account delete = SOFT-delete + анонимизация; **Appointments/Payments переживают удаление (FK PROTECT)**; reviews/nutrition/favorites удаляются CASCADE; **нет service-to-service delete hook**; **нет endpoint экспорта данных**.
- **Net:** две системы НЕ связаны и противоречат друг другу (hard vs soft). Одна кнопка «Удалить аккаунт» не может честно выполнить полное удаление аккаунта для пилота. Cross-service delete-контракт (**ADR-0015**) не ратифицирован и не построен.

**Decision (Variant 3 — defer):**
- **R2 delete + export** → DEFERRED для пилота. In-app entry маршрутизирует клиента в поддержку для запросов на данные (см. §4). Никаких обещаний полного удаления/выгрузки.
- **R3 memory transparency + clear** → DEFERRED для пилота. Backend memory-слой не построен — показываем coming-soon (см. §5), без фейкового data-surface и без clear-действия.
- **R1 / R4 / R5 / R6** → функционально без изменений, partial tech-lead sign-off (см. ниже).

**152-ФЗ mitigation (pilot):** право на доступ и на удаление (152-ФЗ) обрабатывается **ручным процессом оператора** во время пилота — клиента ведут в поддержку, оператор выполняет удаление в обеих системах вручную (dual-system delete). Этот процесс документируется в deployment runbook.

**Forward reference:** полноценный cross-service delete/export — post-pilot **ADR-0015 epic** (ratify контракт + service-to-service delete hook + единый export). Туда же переносится реальный memory-слой (`UserPersonalContext`/`MemoryEntry`/clear).

**Partial sign-off:** ✅ tech-lead sign-off 2026-06-01 — R1 (header), R4 (proactive toggle), R5 (notifications), R6 (states). R2 delete/export + R3 memory — DEFERRED, не подписаны для пилота.

---

## 1. Контекст

### Strategic positioning

**Last customer surface для pilot design coverage.** Profile tab = customer control surface (NOT Settings Hub). Per founder LOCKED scope 2026-05-26:

> Profile tab должен быть честным экраном управления приватностью. Здесь нельзя писать «можно отменить удаление в течение 30 дней», если в реальности удаление запускается сразу.

### Founder LOCKED scope

**IN:**
- Consent management entry (booking PII / marketing / data sharing toggles)
- Memory transparency («Ayla знает о тебе: X, Y, Z. Можно очистить») — **DEFERRED for pilot per §0 recon (memory layer не построен); coming-soon state, см. §5**
- Proactive recommendations on/off toggle
- Notification preferences entry (proactive on/off, channel selection)
- Privacy / data export entry («Скачать мои данные») — **DEFERRED for pilot per §0 recon; routes to support, см. §4**
- Privacy / data delete entry («Удалить аккаунт») — **entry point only, NO 30-day grace promise · DEFERRED for pilot per §0 recon; routes to support, см. §4**
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

### Critical truthfulness principle (tech lead 2026-05-26, updated 2026-06-01 recon)

> Нельзя обещать клиенту «удаление через 30 дней с возможностью отмены», если backend сейчас делает immediate deletion. Это не просто UX-нюанс, это юридическое и доверительное расхождение.

**Update (2026-06-01 recon):** принцип честности теперь охватывает **обе системы** (bot-platform + beautygo/Ayla), а не только `data_delete = immediate` в bot-platform. Code-audit показал: (1) две системы удаления НЕ связаны и противоречат (bot-platform hard-delete vs beautygo soft-delete + anonymize, Appointments/Payments переживают через FK PROTECT); (2) **memory-слой ещё не построен** (нет `UserPersonalContext`/`MemoryEntry`, нет clear-endpoint). Поэтому одна кнопка «Удалить аккаунт» не может честно выполнить полное удаление для пилота, а R3 не может честно показать «что знает Ayla». Profile tab copy MUST reflect this — delete/export/memory переведены в deferred/coming-soon состояние (см. §0, §4, §5).

---

## 2. Section structure — R1 to R6

Per tech lead Phase B verdict — 6 sections (NOT 7). Profile = compact customer control surface.

```
R1 — Header (avatar + name + MAX handle, read-only)                          ✅ sign-off 2026-06-01
R2 — Consent & Privacy (toggles kept · export + delete DEFERRED → support)
R3 — Memory Transparency (DEFERRED for pilot — coming-soon state)
R4 — Proactive AI on/off (toggle + transactional always note)                ✅ sign-off 2026-06-01
R5 — Notifications (channel + soft timing display)                           ✅ sign-off 2026-06-01
R6 — Empty / first-time states                                               ✅ sign-off 2026-06-01
```

---

## 3. R1 — Header

> ✅ tech-lead sign-off 2026-06-01 — функционально без изменений.

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

> **DEFERRED for pilot (§0 recon):** consent toggles остаются как есть. **Export («Скачать мои данные») и delete («Удалить аккаунт») переведены в deferred/support-routed состояние** — backend двух систем не связан, единого честного удаления/выгрузки для пилота нет. In-app entry ведёт клиента в поддержку (152-ФЗ право доступа/удаления обрабатывается ручным процессом оператора, см. §0).

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
│  удаление аккаунта через поддержку.           │
│                                               │
│  ─────────────────────────────                │
│                                               │
│  Твои данные защищены. Здесь можно            │  Privacy summary
│  посмотреть, что хранится. Чтобы скачать      │
│  данные или удалить аккаунт — напиши в        │
│  поддержку, мы всё сделаем вручную.           │
│                                               │
│  [ Подробнее о данных ]                       │  Collapsed cross-
│  [ Запросить данные ]            → поддержка   │  border disclosure
│  [ Удалить аккаунт ]             → поддержка   │
│                                               │
└──────────────────────────────────────────────┘
```

### 4.1 Consent rows — locked vs editable

| Row | Default | UI | Customer can disable? |
|-----|---------|-----|----------------------|
| **Данные для записи** | Locked ON | Read-only info row | NO — нужно для booking core function |
| **Данные для мастера** | Locked ON | Read-only info row | NO — мастер needs info to deliver service |
| **Акции и предложения** | OFF default | Toggle | YES — opt-in only |
| **Хранение данных** (152-ФЗ overall) | ON (given onboarding) | Info row with date | NO toggle — отзыв = запрос удаления через поддержку |

> Consent toggles (rows above) — **без изменений, остаются в scope пилота**. Изменены только export/delete entries ниже.

### 4.2 «Подробнее о данных» collapsed disclosure

Tap → expanded sheet/section. **Expanded pre-draft per #947 — REQUIRES legal sign-off before pilot ship. Current shipped copy (`DisclosureSheet.tsx`) is the smaller r1 starting point; legal verdict drives whether to expand to r2 below.**

#### 4.2.1 r1 — shipped copy (`DisclosureSheet.tsx`)

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

[ Закрыть ]
```

#### 4.2.2 r2 — Tau pre-draft (for legal review)

```
Где и как обрабатываются данные

Основные данные хранятся на серверах в России —
так требует 152-ФЗ.

Чтобы понимать твои сообщения, Ayla отправляет их в
AI-сервис от компании Anthropic (США). Передача
защищена шифрованием TLS 1.3, ответ возвращается тоже
шифрованным. Anthropic обрабатывает текст в момент
ответа и не хранит твою переписку у себя.

Что мы делаем со ВНЕШНИМИ передачами:
• Передаём только текст сообщения — без имени,
  телефона и других контактов
• Шифруем передачу TLS 1.3
• Не передаём фото из дневника питания на Anthropic —
  распознавание блюд работает внутри российского
  контура

Что мы НЕ делаем:
• Не продаём данные никому со стороны
• Не используем твою переписку для рекламы
• Не отдаём салонам ничего, кроме того, что нужно
  для записи (имя, услуга, время)
• Не показываем мастеру память Ayla или wellness-логи

Сколько храним:
• Сообщения с Ayla — 180 дней, потом анонимизируется
• Записи на услуги и оплаты — 7 лет (требует ФЗ-2300-1
  и ФЗ-54 — закон не даёт удалить раньше)
• Память Ayla про тебя — пока ты сама не удалишь
• Аудит факта удаления — храним по закону, но без
  твоего имени

Согласие можно отозвать. Полный отзыв = удаление
аккаунта (это можно сделать в разделе «Удалить
аккаунт»). Частичный — управляй переключателями
выше.

[ Закрыть ]
```

#### 4.2.3 LEGAL_REVIEW_REQUIRED — explicit asks for legal/compliance (#947)

Pre-draft r2 above is **Tau starting point** for legal. Please verify and mark up each of the following points:

1. **Anthropic + USA framing**
   - r2: «AI-сервис от компании Anthropic (США)»
   - Question: is the country mention required at this level of disclosure, or sufficient to say «иностранный сервис» with detail in privacy policy? 152-ФЗ ст.12 (transborder transfer) interpretation.
   - Alternative phrasing to consider: «зарубежный AI-сервис (Anthropic, США)» — more precise about who and where.

2. **TLS 1.3 mention**
   - r2: «шифрованием TLS 1.3»
   - Question: do we want a specific protocol version in customer-facing copy (commits us technically) or generic «современное шифрование»?
   - Tau lean: generic is friendlier; specific is more credible to a technical reviewer.

3. **Anthropic non-retention claim**
   - r2: «Anthropic обрабатывает текст в момент ответа и не хранит твою переписку у себя»
   - Question: does this match actual Anthropic Data Processing Agreement / our contract terms? If we have zero-retention tier, this is honest; if we use default retention, this overpromises.
   - **MUST verify with vendor contract before shipping.**

4. **Photo separation claim**
   - r2: «Не передаём фото из дневника питания на Anthropic — распознавание блюд работает внутри российского контура»
   - Question: is this true at pilot ship (food scanner uses internal vision pipeline, no Anthropic photo path)? If photo path adds Anthropic post-pilot, this becomes a breach — needs updating before that change.
   - Tau lean: ship the claim if true at pilot; flag a follow-up to legal if photo pipeline architecture changes.

5. **«никому со стороны» replacement for «третьим лицам»**
   - Per adversarial CR (Profile Phase B PR agent `a93d90bebc68bba10`): «третьим лицам» reads as §14 legal jargon
   - r2: «Не продаём данные никому со стороны»
   - Question: is «никому со стороны» legally equivalent to «третьим лицам» under 152-ФЗ? Or does the legal term need to stay?
   - Tau lean: prefer the friendly phrasing if compliance allows.

6. **Retention specifics**
   - 180 days for messages: matches `STRICT_TENANT_REFUSE` runbook + memory layer policy. ⚠ **See §4.2.5 retention audit — code-level grep shows policy exists but no anonymizer job implemented.**
   - 7 years for bookings/payments: matches consumer-protection law minimum (ФЗ-2300-1 ст.10) + accounting law. ⚠ **See §4.2.5 — Alpha-side reality not verified by Tau.**
   - Memory «пока ты не удалишь»: matches `apps/skills/privacy_consent` skill behavior
   - Audit «храним по закону»: matches 152-ФЗ ст.18 + ст.21
   - Question: are these numbers accurate AND legally minimal (we're not retaining longer than necessary)?

7. **Tone**
   - Tau wrote in Ayla voice (warm, «ты», first-person where natural). This may need to shift to more formal legal voice for the cross-border section specifically.
   - Question: does legal want this rewritten in third-person formal Russian («Ayla обрабатывает...»), or is first-person acceptable in this context?

8. **Withdrawal mechanics**
   - r2: «Полный отзыв = удаление аккаунта»
   - Question: under 152-ФЗ ст.9 ч.5, must we offer a more granular withdrawal path (per-purpose consent)? Or is the locked-on/toggle-off + full-delete combination sufficient?

#### 4.2.4 Process

1. Tau shipped pre-draft (r2 above) merged into spec — this is the **starting point**, NOT shipped customer copy
2. Legal reviews + marks up via PR comment on this file OR direct edit of §4.2.2
3. After legal verdict → §4.2.1 shipped copy updated to final wording, `LEGAL_REVIEW_REQUIRED` flag removed
4. W4 updates `apps/miniapp/src/components/DisclosureSheet.tsx` with final wording
5. P-6 gate (pre-pilot ship) — verify final wording in production build

**Until legal verdict ships:** r1 (§4.2.1) is what customers see in current shipped build. Whether to ship r2 expansion vs keep r1 is `pilot-pre-ship-blocked` on legal verdict.

#### 4.2.5 Retention reality audit — Tau findings 2026-06-02 (#950)

> **Acceptance check per #950:** "Confirm with Alpha (Ayla backend) + W2 (bot-platform retention jobs) that 180d / 7y numbers match production reality."
>
> Tau ran a code-level grep audit on `ai-bot-platform` (this repo). Findings below — **Alpha + W2 MUST confirm or correct before pilot ship.**

##### Finding R-1 — 180-day message anonymization: POLICY EXISTS, BACKEND JOB NOT FOUND ⚠

- **Policy source:** `docs/design/policies/conversation-ownership-policy.md` §6 — "Retention: 180 days"
- **Customer claim (shipped r1 + pre-draft r2):** «Сообщения с Ayla — 180 дней, потом анонимизируется»
- **Code reality (2026-06-02 grep):**
  - `apps/conversations/tasks.py::purge_old_ai_drafts` exists — but scope is `AiDraft` (operator-side AI drafts), NOT customer Message rows. Retention = 30 days, not 180.
  - No `timedelta(days=180)` found anywhere в `apps/`.
  - No `anonymize_message` / `purge_old_messages` task found.
- **Risk:** Customer told "180 days then anonymized" — backend has no anonymizer job → messages retained INDEFINITELY. Variant 3 truthfulness violation per adversarial CR P8.
- **Action required (W2):** either
  - **(a)** implement the 180-day message anonymizer job before pilot ship (matches policy + customer copy), OR
  - **(b)** Tau rewrites customer copy to reflect actual retention (likely «храним столько, сколько работаем с тобой» + honest manual-delete path), OR
  - **(c)** mark feature as `policy-only-not-shipped` and drop the customer-facing 180-day promise. Tau lean: **(a) preferred**; **(c) acceptable** if (a) doesn't fit pilot scope; **(b) is fallback** if neither (a) nor (c) works.

##### Finding R-2 — 7-year booking/payment retention: REQUIRES ALPHA CONFIRMATION ⚠

- **Customer claim:** «Записи на услуги и оплаты — 7 лет (требует ФЗ-2300-1 и ФЗ-54 — закон не даёт удалить раньше)»
- **Code reality (2026-06-02 grep on bot-platform):** booking/payment retention LIVES IN AYLA DJANGOPROJECT (beautygo), not this repo. Per ADR-0009 split-domain — bot-platform does not own this data.
- **Risk:** if Ayla side has a shorter retention OR auto-deletes earlier OR (worse) retains LONGER than 7 years without legal basis — customer copy is false.
- **Action required (Alpha):** confirm beautygo `Appointment` + `Payment` retention is:
  - At least 7 years (per ФЗ-2300-1 ст.10 + ФЗ-54 ст.4.7)
  - NOT longer than 7 years for anonymized fields (over-retention without basis = 152-ФЗ ст.5 ч.7 violation)
  - Specifically check: is there a scheduled purge job at 7y+1d? If not, this is an over-retention risk.

##### Finding R-3 — Memory retention "пока не удалишь": CHECK STRICT_TENANT_REFUSE INTERACTION ⚠

- **Customer claim (r2 only):** «Память Ayla про тебя — пока ты сама не удалишь»
- **Code reality:** `apps/identity/UserPersonalContext` lives indefinitely. Customer hard-delete via `apps/skills/privacy_consent` clears it immediately.
- **Edge case:** what if customer's TenantUserRelationship is removed (tenant offboarding) but customer doesn't delete account? Per memory `strict-tenant-refuse-soak`, STRICT_TENANT_REFUSE may surface scoping issues — confirm memory persists even when tenant link breaks.
- **Action required (W4 / Alpha):** confirm memory lifecycle is purely customer-controlled, not tenant-controlled.

##### Finding R-4 — Audit retention "храним по закону, но без имени": CONFIRM 30-DAY ANONYMIZATION ✅

- **Customer claim (r2 only):** «Аудит факта удаления — храним по закону, но без твоего имени»
- **Code reality (2026-06-02):** `apps/audit/tasks.py` shows `retention_days==30` in tests (audit row anonymized after 30 days). Matches policy `customer-privacy-data-closure-ux.md` Q-CP14.
- **Risk:** ✅ LOW — code matches policy. Customer-facing claim is honest. No action.

##### Summary — what blocks pilot ship

| Finding | Risk | Owner | Blocker tier |
|---|---|---|---|
| R-1: 180-day message anonymizer not implemented | HIGH (false promise) | W2 | **pilot-blocking-hard** |
| R-2: 7-year retention not verified Alpha-side | MEDIUM (potential false promise) | Alpha | **pilot-blocking-soft** |
| R-3: memory lifecycle vs tenant-offboarding edge | LOW (corner case) | W4 + Alpha | **follow-up** |
| R-4: audit 30-day anonymization | ✅ matches | — | none |

**Tau recommendation:** treat R-1 as P-7 blocker (same tier as #949 SUPPORT_DEEPLINK wiring). R-2 needs Alpha sign-off as part of P-6 legal review (#947). Without R-1 fix, customer copy in r1 §4.2.1 (which is SHIPPED) is already dishonest — either implement (a), or rewrite (b), or drop (c) before pilot opens.

#### 4.2.6 Tech-lead resolution 2026-06-02 — path (b) + path (a) deferred post-pilot

Tech lead reviewed §4.2.5 findings and chose **(b) now + (a) post-pilot** as recommendation to founder (founder verdict pending):

- **Now (b):** rewrite customer copy to reflect actual retention behavior — «храним, пока активен аккаунт; удаляются, когда удаляешь аккаунт». This is honest (`apps/skills/privacy_consent/skill.py::data_delete` already implements customer-controlled deletion), 152-ФЗ-defensible through the right-to-erasure path, and ships immediately without blocking W2 (who is loaded on #842 PII critpath).
- **Post-pilot (a):** implement actual 180-day message anonymizer for proper retention limitation. Owned by W2 — separate ticket, not pilot-blocker.
- Tau writes voice → legal confirms wording as part of #947 → W1 ships corrected `DisclosureSheet.tsx` (one-line copy patch, anti-touch-safe).

**Tau path-(b) draft copy** (replaces «Сколько храним» block in both r1 §4.2.1 + r2 §4.2.2 «Сколько храним» section):

```
Сколько храним:
• Сообщения с Ayla — пока активен твой аккаунт. Когда
  удалишь аккаунт — удаляются вместе с ним.
• Записи на услуги и оплаты — 7 лет (этого требует закон,
  записи и оплаты не удаляются раньше).
• Память Ayla про тебя — пока ты не очистишь или не
  удалишь аккаунт.
• Аудит факта удаления — храним по закону, но без
  твоего имени.
```

**Voice rules applied (path-(b)):**

| Element | Rule | Status |
|---|---|---|
| «пока активен твой аккаунт» | Honest scope — matches `data_delete` skill reality | ✅ |
| «удалишь аккаунт — удаляются вместе с ним» | Cause-effect framing, customer agency surfaced | ✅ |
| «закон не удаляет раньше» | Inverted from «нельзя удалить» — honest about WHY 7y is fixed | ✅ |
| «пока ты не очистишь или не удалишь» | Two-path customer control — clear/delete | ✅ |
| «ты» throughout | Register canon | ✅ |
| No «анонимизируется» / «потом» promise | Removes the dishonest claim entirely | ✅ |
| No backend-overpromise language («автоматически удалим», «через X дней») | Pattern 4 avoided | ✅ |
| Audit retention same as r2 (✅ R-4 matches policy) | No change needed | ✅ |

**Voice rules deliberately NOT used:**
- ❌ «храним только пока необходимо» (legally weasel — what is «necessary»?)
- ❌ «удалим автоматически если ты долго не пользуешься» (would be partial (a) — not shipping)
- ❌ «соблюдаем GDPR-стандарты» (vendor-speak, не applicable RU)
- ❌ «безопасно храним» (security claim без backing)

**Path-(b) handoff:**

| Recipient | Action | Owner | Status |
|---|---|---|---|
| Founder | Approve (b)+later vs (a)-in-pilot tradeoff | founder | ⚠ pending verdict |
| Legal | Confirm (b) wording as 152-ФЗ-defensible (part of #947 review pass) | legal advisor | ⚠ pending |
| W1 | Ship `DisclosureSheet.tsx` retention-block copy update once founder + legal verdicts land | W1 | ⚠ blocked on founder verdict |
| W2 | NO action pilot-side. Post-pilot ticket: 180-day message anonymizer (path (a)). | W2 | post-pilot queue |

**If founder picks (a)-in-pilot instead:** keep r1/r2 copy as-is, W2 must implement 180-day anonymizer before pilot ship. Tau prepares NO additional voice — current 180-day copy stands as-written.

**Until founder verdict ships:** customer in production sees r1 §4.2.1 (the dishonest «180 дней потом анонимизируется» version). Verdict urgency = P-7 (matches #949 SUPPORT_DEEPLINK).

### 4.3 «Запросить данные» — DEFERRED for pilot (export via support)

> **Было:** «Скачать мои данные» → inline-JSON в bot DM. **Стало (§0 recon):** export откладывается. Bot-platform export отдаёт только свои данные (нет данных Ayla/beautygo, нет единой выгрузки). Для пилота честнее не обещать in-app выгрузку, а вести в поддержку.

Tap → entry sheet (routes to support, NO in-app download):

```
Нужна копия твоих данных?

Я не делаю выгрузку прямо здесь — пока это запрос через
поддержку. Напиши нам, и оператор подготовит твои данные
по закону (152-ФЗ, право на доступ).

[ Написать в поддержку ]   [ Отмена ]
```

- Copy **НЕ обещает** мгновенную in-app выгрузку, файл JSON в чат или скачивание.
- «Написать в поддержку» → открывает support-канал (deeplink / support chat).
- 152-ФЗ право на доступ обрабатывается ручным процессом оператора (см. §0).

**Post-pilot:** единый cross-service export — часть ADR-0015 epic (§0 forward reference).

### 4.4 «Удалить аккаунт» — DEFERRED for pilot (delete via support)

> **Было:** in-app immediate hard-delete с success-state. **Стало (§0 recon):** delete откладывается. Code-audit: bot-platform hard-delete и beautygo soft-delete НЕ связаны (нет cross-service hook), Appointments/Payments в beautygo переживают через FK PROTECT. Одна кнопка не выполняет полное удаление честно. Для пилота — вести в поддержку, оператор делает dual-system delete вручную. **NO 30-day grace promise сохраняется** (founder-locked).

Tap → entry sheet (routes to support, NO in-app deletion):

```
Хочешь удалить аккаунт?

Удаление пока оформляется через поддержку — так я уверена,
что уберу данные во всех системах правильно. Напиши нам,
и оператор всё сделает по закону.

Часть данных по записям и оплатам может храниться дольше,
если это требует закон.

[ Написать в поддержку ]   [ Отмена ]
```

- Copy **НЕ обещает**: стирание аккаунта в один тап, «удалить всё», полное удаление, мгновенную обработку, 30-дневный grace.
- «Написать в поддержку» → открывает support-канал.
- Оператор выполняет удаление в bot-platform + beautygo вручную (dual-system delete, документировано в deployment runbook, см. §0).
- 152-ФЗ право на удаление обрабатывается этим ручным процессом во время пилота.

**Post-pilot (ADR-0015 epic):** ратифицировать cross-service delete-контракт, построить service-to-service delete hook, затем (отдельно) реальный `DeleteRequest` + 30-day grace + cancel flow. Тогда — вернуть in-app поток и обновить copy.

### 4.5 SupportEntrySheet voice — Brand Guardian pass on shipped copy (2026-06-02, per tech-lead pickup directive)

Tau ran a 12-pattern Brand Guardian pass on shipped `SupportEntrySheet.tsx::COPY` post-merge of PR #954. Three presets reviewed: `export` (§4.3), `delete` (§4.4), `notifications` (§7).

| Preset | Verdict | Notes |
|---|---|---|
| `export` (§4.3) | ✅ PASS | Honest scope «не делаю выгрузку прямо здесь»; first-person Ayla; «ты»; 152-ФЗ legal citation is credible without being scary; SLA promise absent (correct — operator owns SLA). One «нам» borderline-acceptable as Ayla+support team collective, NOT salon-side framing. |
| `delete` (§4.4) | ✅ PASS | Honest cross-service explanation «во всех системах правильно»; retention preview §2; NO 30-day grace; NO fear-mongering; «я уверена» first-person. One «нам» borderline-acceptable. |
| `notifications` (§7 route) | ⚠ MINOR — refine post-pilot | Double «мы»-weight («мы соберём» + «нам») reads slightly more team-side than other presets. Not Variant-3-truthfulness violation, but voice-tone-asymmetric within the three presets. Acceptable for pilot ship; queue voice patch post-pilot. Suggested rewrite: «Пока этим занимается поддержка — оператор соберёт настройки вручную. Напиши, что хочешь поменять.» |

**Patterns explicitly checked and PASSED across all three presets:**

1. AI cliché («unleash potential») — none
2. Vendor speak («next-generation») — none
3. Salon-side framing («мы», «наш сервис») — borderline «нам» but contextually team-collective, acceptable
4. Backend overpromise — none (no SLA promises, no 30-day grace)
5. Pre-mature marketing («скоро будет!») — none in SupportEntrySheet; mitigated in ComingSoonCard (see §5.bis)
6. Urgency manipulation — none
7. Apology («к сожалению…») — none
8. Sterile UI text («Загрузка…», «Ошибка!») — none
9. Medical/diagnostic — n/a in this surface
10. Gamification — none
11. Empty data dashes — none
12. Anti-empathy ED-anxiety — n/a in this surface

**Ship verdict:** SupportEntrySheet copy is ✅ pilot-ready. Notification preset has minor «мы» asymmetry — file as post-pilot voice patch, NOT pilot-blocker.

---

## 5. R3 — Memory Transparency — DEFERRED for pilot (coming-soon)

> **DEFERRED for pilot (§0 recon).** Backend memory-слой **не построен**: нет таблиц `UserPersonalContext`/`MemoryEntry`, нет endpoint summary и нет clear-endpoint. Показывать «Что Ayla знает» data-surface или кнопку «Очистить память» сейчас — это фейковый UI без backend. Вместо этого — короткое coming-soon состояние. **Никакого fake data-surface, никакого clear-действия.**

```
┌──────────────────────────────────────────────┐
│  ── Что Ayla помнит ──                        │
│                                               │
│  ┌──────────────────────────────────────┐   │
│  │  Скоро я смогу показать тебе здесь,     │   │  Coming-soon
│  │  что я о тебе помню — любимые услуги,   │   │  card (no data,
│  │  удобное время, настройки — и дать      │   │  no clear action)
│  │  это очистить.                          │   │
│  │                                        │   │
│  │  Пока этот раздел готовится. Я не       │   │
│  │  показываю лишнего и не делаю вид,      │   │
│  │  что уже всё умею.                      │   │
│  └──────────────────────────────────────┘   │
│                                               │
│  (нет действий — раздел в разработке)         │
│                                               │
└──────────────────────────────────────────────┘
```

### 5.0.bis ComingSoonCard — Brand Guardian pass on shipped copy (2026-06-02)

Shipped copy lives in `apps/miniapp/src/components/ComingSoonCard.tsx`. Brand Guardian 12-pattern pass result:

| Element | Pattern check | Verdict |
|---|---|---|
| «Скоро я смогу показать…» | Pattern 5 (premature marketing) | ⚠ mitigated — see secondary line |
| «любимые услуги, удобное время, настройки» | Concrete preview, not vague «много возможностей» | ✅ honest specificity |
| «и дать это очистить» | Concrete control action surfaced | ✅ |
| «Пока этот раздел готовится» | Honest «not yet shipped» — not «временно недоступно» (apology) | ✅ |
| «Я не показываю лишнего и не делаю вид, что уже всё умею» | Explicit anti-vendor-speak meta-honesty | ✅ Brand Guardian reference quality |
| First-person Ayla («я смогу», «я не делаю вид») | Voice rule | ✅ |
| «ты» («тебе», «о тебе») | Register rule | ✅ |
| No emoji | Calm placeholder, not celebratory | ✅ |
| No CTA («Подписаться на уведомления!») | No marketing engagement loop | ✅ |
| No timeline («доступно с июля!») | Pattern 4 (backend overpromise) avoided | ✅ |

**Verdict:** ✅ PASS, reference-quality honesty. The «Скоро» pattern-5 risk is fully mitigated by the secondary line's explicit anti-vendor-speak («не делаю вид, что уже всё умею»). This is the model copy for any other future deferred-state surface in MVP — treat it as the template.

**Ship verdict:** ComingSoonCard is pilot-ready, no changes recommended.

### 5.1 Coming-soon rules

- **НЕ показывать** список категорий памяти как реальные данные — backend не отдаёт их.
- **НЕ показывать** кнопку «Очистить память Ayla» — clear-endpoint не существует.
- Один спокойный coming-soon блок, без фейковых данных, без действий.
- Tone: честный, «ты», без маркетинга («Скоро…», а не «Уже умею всё!»).

### 5.2 Post-pilot scope (ADR-0015 epic / memory layer build)

Когда memory-слой построен (`UserPersonalContext`/`MemoryEntry` + summary + clear endpoints), R3 возвращается к полной transparency-поверхности. Сохранённый дизайн полного состояния (bullet-card по категориям, 3-зонный 🟢🟡🔴 framework, clear-scope по слоям, «Подробнее» drill-down) перенесён в post-pilot и описан ниже как **целевое (не пилотное) состояние** — для справки реализатора, НЕ для рендера в пилоте.

#### Target (post-pilot, NOT pilot) — memory bullet card + clear

> Ниже — целевой дизайн для post-pilot, когда backend memory-слой готов. В пилоте НЕ рендерится.

Bullet summary card (только категории, без red/yellow specifics):

| ✅ Show | ❌ Hide |
|---------|---------|
| «Любимые услуги» | «Маникюр гель-лак у Анны Петровой» |
| «Часто посещаемые салоны» | «Beauty Place 4 раза, Студия Натали 2 раза» |
| «Предпочитаемое время визитов» | «Четверг 16:00, любит вечера» |
| «Wellness-настройки» | «Аллергия на лак X, боль в пояснице» |

Per `ayla-memory-and-personalization §8` 3-zone framework (🟢🟡🔴): 🟢/🟡 категории OK to summary (NOT specific facts); 🔴 red zone (allergies / pregnancy / mental health / chronic conditions) — **NEVER в Profile summary**, only used silently for contraindication filtering.

«Очистить память Ayla» (target post-pilot) — clear matrix по слоям:

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

«Подробнее» drill-down (per-fact view с 💬/🤖 source attribution) — также post-pilot, conditional render on memory inspection endpoint. Эти потоки — часть post-pilot memory-слоя (ADR-0015 epic / §0 forward reference).

---

## 6. R4 — Proactive AI on/off

> ✅ tech-lead sign-off 2026-06-01 — функционально без изменений.

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

> ✅ tech-lead sign-off 2026-06-01 — функционально без изменений.

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

> ✅ tech-lead sign-off 2026-06-01 — функционально без изменений (memory-cleared state ниже относится к post-pilot memory-слою, §5).

### 8.1 No memory yet

> **Pilot note (§0):** R3 в пилоте — coming-soon (§5), без data-surface. Этот empty state относится к post-pilot memory-слою.

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

> **Pilot note (§0):** clear-памяти в пилоте недоступно (clear-endpoint не построен). Этот state относится к post-pilot memory-слою.

After tap «Очистить память Ayla» success (post-pilot):

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

### 8.4 Delete request — routed to support (pilot)

> **Pilot note (§0):** in-app delete отложен; entry ведёт в поддержку (§4.4). Прежний in-app success-state («Запрос на удаление принят») возвращается post-pilot вместе с ADR-0015 delete-контрактом.

---

## 9. States matrix

| State | Trigger | Surface |
|-------|---------|---------|
| Loading skeleton | First open | Header cached, sections shimmer |
| Memory section (pilot) | R3 viewed | §5 coming-soon card (no data, no clear) |
| Empty memory (post-pilot) | <3 stored facts | §8.1 empty state |
| Memory cleared (post-pilot) | After clear tap success | §8.2 confirmation |
| Proactive off | Toggle change | §8.3 explainer |
| Export request (pilot) | «Запросить данные» tap | §4.3 support entry sheet |
| Delete request (pilot) | «Удалить аккаунт» tap | §4.4 support entry sheet |
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
- «Можно скачать копию всех твоих данных» (post-pilot, когда export вернётся)
- «Согласие дано 14 мая 2026»
- «Память очищена» (post-pilot)
- «Это действие может быть необратимым»
- «AI-обработка через внешних поставщиков»
- «Удаление пока оформляется через поддержку» (pilot)
- «Скоро я смогу показать тебе здесь, что помню» (pilot R3 coming-soon)

❌ **Avoid:**
- «Запросить выгрузку персональных данных в формате CSV»
- «Принимая, вы соглашаетесь с пользовательским соглашением...»
- «Опт-аут из проактивных коммуникаций»
- «Токенизация PII в соответствии с GDPR-эквивалентом»
- «Cross-border data transfer per GDPR Article 46»

### 10.3 CTA naming convention (per tech lead + reminders-voice ship)

| CTA | Use в Profile flow |
|-----|--------------------|
| «Написать в поддержку» | R2 export + delete (pilot, deferred) |
| «Запросить данные» | R2 export entry (pilot) |
| «Удалить аккаунт» | R2 (pilot — routes to support) |
| «Открыть настройки уведомлений» | R5 |
| «Подробнее о данных» | R2 (collapsed cross-border) |
| «Очистить память Ayla» | R3 (post-pilot only — не рендерится в пилоте) |
| «Подробнее» | R3 (post-pilot memory drill-down) |

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
| **Bullet summary card** | ✅ **SELECTED (post-pilot target)** | Per tech lead — categories give control sense without overload. **Pilot: DEFERRED → coming-soon (§5), memory layer не построен.** |
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

### 12.0 Recon-corrected reality (2026-06-01)

| Capability | bot-platform | beautygo (Ayla) | Cross-service | Pilot decision |
|-----------|--------------|-----------------|---------------|----------------|
| Account delete | immediate HARD-delete, unconditional, **bot-platform-only** | SOFT-delete + anonymize; Appointments/Payments survive (FK PROTECT); reviews/nutrition/favorites CASCADE | **NONE** (no service-to-service hook; ADR-0015 not ratified/built) | **DEFERRED → support (manual dual-system delete)** |
| Data export | inline-JSON, bot-platform data only | **no export endpoint** | none | **DEFERRED → support** |
| Memory summary / clear | **no endpoint; no `UserPersonalContext`/`MemoryEntry` tables (layer unbuilt)** | n/a | n/a | **DEFERRED → coming-soon (§5)** |

### 12.1 Existing endpoints (verified — but NOT wired for pilot deletion/export)

| Endpoint | Method | Description | Status |
|----------|--------|-------------|--------|
| `apps/skills/privacy_consent/skill.py::data_export` | Skill | Returns JSON archive inline в bot DM — **bot-platform data only, no Ayla data → NOT used in-app for pilot (deferred to support)** | ✅ EXISTS (not wired) |
| `apps/skills/privacy_consent/skill.py::data_delete` | Skill | Immediate hard-delete — **bot-platform-only, no cross-service hook → NOT used in-app for pilot (deferred to support)** | ✅ EXISTS (not wired) |
| `GET /api/v1/me` | GET | Customer identity + capabilities | ✅ EXISTS |

### 12.2 New endpoints needed (W4 follow-ups)

| Endpoint | Purpose | Priority |
|----------|---------|----------|
| `GET /api/v1/me/consents` | Return consent state (marketing on/off, given_at) | P1 PRE_PILOT |
| `POST /api/v1/me/consents/marketing` | Update marketing consent toggle | P1 PRE_PILOT |
| `POST /api/v1/me/proactive_opt_out` | Update proactive opt-out flag | P1 PRE_PILOT (also needed для Reminders #105) |
| `GET /api/v1/me/memory/summary` | Return memory category summary для R3 bullet card | **Post-pilot (memory layer unbuilt — ADR-0015 epic)** |
| `POST /api/v1/me/memory/clear` | Clear memory layers (per §5.2 matrix) | **Post-pilot (memory layer unbuilt)** |
| Cross-service delete contract + hook | Wire bot-platform ↔ beautygo dual-system delete | **Post-pilot (ADR-0015 epic)** |
| Cross-service unified export | Single export across both systems | **Post-pilot (ADR-0015 epic)** |

### 12.3 W4 follow-up tickets (Phase J)

1. **Issue P-1** — `is_solo_provider`-style API extensions (`/api/v1/me/consents`, etc.) for Profile tab rendering (consent toggles + proactive — IN scope for pilot)
2. **Issue P-2 (post-pilot)** — **ADR-0015 epic:** ratify cross-service delete contract + build service-to-service delete hook (bot-platform hard-delete ↔ beautygo soft-delete)
3. **Issue P-3 (post-pilot)** — Memory layer build (`UserPersonalContext`/`MemoryEntry` + summary + clear endpoints), then R3 transparency surface + clear scope (§5.2 matrix)
4. **Issue P-4 (post-pilot)** — Unified cross-service data export (replaces deferred bot-DM JSON)
5. **Issue P-5 (post-pilot)** — Real `DeleteRequest` + 30-day grace + cancel flow (after ADR-0015 delete hook lands)
6. **Issue P-6** — Legal review для cross-border disclosure exact wording
7. **Issue P-7 (pilot)** — Support-routed delete/export: deployment runbook procedure for manual dual-system delete (152-ФЗ access/erasure during pilot)

---

## 13. Accessibility (WCAG 2.2 AA — inline)

1. **2.5.8 Target Size** — All toggles ≥44dp tap target. «Написать в поддержку» / support-entry buttons ≥48dp height.
2. **1.4.3 Contrast** — Toggle states (ON/OFF) need ≥3:1 non-text contrast. «Удалить аккаунт» entry styled с warning accent (NOT bright red — anxiety-inducing).
3. **1.3.1 Info & Relationships** — Consent rows use `<dl>` (definition list). Status badges associated с rows via `aria-describedby`.
4. **4.1.3 Status Messages** — Support-entry confirmations = `role="status" aria-live="polite"`.
5. **2.5.5 Confirm Destructive** — «Удалить аккаунт» entry opens explicit support sheet (no silent action). Per WCAG, primary action не auto-focused.
6. **3.3.4 Confirm Sensitive Action** — Delete entry sheet must be explicit accept (tap, not Enter).
7. **2.4.3 Focus Order** — Sections vertical: R1 → R2 → R3 → R4 → R5 → R6. Within R2: rows in spec order.
8. **1.4.4 Resize Text** — At 200% zoom: toggles стeck с labels above, all controls remain accessible.
9. **2.3.3 Reduced Motion** — Loading shimmer respects `prefers-reduced-motion`.
10. **3.1.1 Language** — `lang="ru"` declared. «Ayla» wrapped с `<span lang="en">Ayla</span>` для proper TTS.
11. **2.4.1 Bypass Blocks** — Skip link «К управлению приватностью» if Profile is long scroll.
12. **4.1.2 Name, Role, Value** — Each toggle has explicit `aria-label` describing what it controls + current state announced.

---

## 14. Anti-patterns

Per founder + tech lead 2026-05-26 (updated 2026-06-01 recon):

- ❌ **«Через 30 дней можно отменить»** (fake grace promise — founder-locked, остаётся запрещено)
- ❌ **Обещание полного удаления аккаунта в один тап в пилоте** (две системы не связаны — §0; вести в поддержку)
- ❌ **Обещание in-app выгрузки/скачивания данных в пилоте** (export deferred — §4.3; вести в поддержку)
- ❌ **Fake «Что Ayla знает» data-surface или clear-кнопка без backend** (memory-слой не построен — §5)
- ❌ Fear-mongering wording («Осторожно с удалением!», «ВНИМАНИЕ! Это действие необратимо!»)
- ❌ Marketing tone («Получай ещё больше предложений!», «Откройся новым возможностям!»)
- ❌ Technical jargon без перевода («PII», «токенизация», «opt-out», «GDPR», «cross-border transfer»)
- ❌ Юридический жаргон («персональные данные субъекта», «передача третьим лицам», «обработка биометрических данных»)
- ❌ «Уважаемый клиент» (corporate formal)
- ❌ Locked controls rendered as toggle (use info row instead)
- ❌ Hidden cross-border disclosure (must be «Подробнее» accessible, not absent)
- ❌ Detailed red/yellow memory specifics в bullet summary («Аллергия на X», «Боль в Y») — post-pilot target rule
- ❌ Promises which backend cannot deliver
- ❌ Customer-confusing labels («Booking PII consent» NOT translated)
- ❌ Email/SMS delivery promise для data export
- ❌ Auto-focus destructive primary CTA (WCAG)

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
- Q1 Memory bullet summary (no red/yellow specifics) ✅ — **note: superseded for pilot, R3 deferred (§0/§5)**
- Q2 Soft notification timing ✅
- Q3 Multi-step delete modal (NO 30-day grace) ✅ — **note: pilot delete deferred to support (§4.4); NO 30-day grace still holds**
- Q4 Consent toggles human labels ✅
- Q5 Cross-border collapsed (legal-review required) ✅
- +3 states (No memory / Memory cleared / Proactive off) ✅
- Out-of-scope list ✅

Plus 3 Tau refinements:
- Memory clear scope (Layer matrix §5.2) ✅ — post-pilot target
- Скачать data = bot DM JSON not email ✅ — **superseded: export deferred to support for pilot (§4.3)**
- «Подробнее» conditional render ✅ — post-pilot

### Resolved at recon (2026-06-01 tech-lead)

- Q-R-1 Cross-service delete reality → two systems unwired & disagree (hard vs soft) → **DEFER delete to support (Variant 3)** ✅
- Q-R-2 Export reality → bot-platform-only inline JSON, no Ayla export → **DEFER export to support** ✅
- Q-R-3 Memory layer reality → not built (no tables/endpoints) → **DEFER R3 to coming-soon** ✅
- Q-R-4 152-ФЗ during pilot → manual operator dual-system delete via support, runbook-documented ✅

### Post-pilot followups

| # | Question | Phase |
|---|----------|-------|
| Q-P-POST-1 | ADR-0015: cross-service delete contract + service-to-service hook | Post-pilot |
| Q-P-POST-2 | Memory layer build (`UserPersonalContext`/`MemoryEntry` + summary + clear) → R3 transparency | Post-pilot |
| Q-P-POST-3 | Unified cross-service data export | Post-pilot |
| Q-P-POST-4 | Real 30-day grace deletion + cancel flow (after ADR-0015 hook) | Post-pilot |
| Q-P-POST-5 | Memory drill-down detailed view с per-fact edit | Post-pilot |
| Q-P-POST-6 | Photo upload / avatar customization | Post-pilot |
| Q-P-POST-7 | Theme / appearance settings | Post-pilot |
| Q-P-POST-8 | Language switcher (KZ/EN) | Phase 3+ |
| Q-P-POST-9 | Multi-account management | Phase 4+ |

### For W1 / Iota (frontend implementer)

1. **Avatar fallback** — initials sage-green circle if no MAX photo
2. **Toggle component** — iOS-style ON/OFF circle, 44dp tap area
3. **Locked toggles** — render as info row, NOT disabled active toggle
4. **R2 export/delete** — render as **support-entry** sheets (§4.3/§4.4), NO in-app download/delete; «Написать в поддержку» deeplinks support channel
5. **R3 memory** — render **coming-soon card only** (§5), NO data list, NO clear button
6. **Cross-border collapsed** — accordion / sheet pattern
7. **152-ФЗ date display** — read from `customer.consent_at` field
8. **Multi-tenant tenant scope hint** — «+N салонов» reads from tenant relationships count
9. **No «coming soon»** except R3 (R3 IS the sanctioned coming-soon per §5)
10. **Toast for proactive toggle** — explainer per §8.3 на toggle change

---

## 17. Skills used

| Skill / Subagent | Phase | Findings |
|---|---|---|
| `frontend-design` | C–E | ASCII patterns reuse from previous handoffs |
| Direct code reading | A | `apps/skills/privacy_consent/skill.py` backend reality check (immediate deletion + JSON inline) |
| Cross-repo code audit | recon 2026-06-01 | bot-platform + beautygo/Ayla audited: delete unwired (hard vs soft), export bot-platform-only, memory layer unbuilt → Variant 3 defer |
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
- [x] Recon 2026-06-01 — cross-repo audit → Variant 3 (defer R2 delete/export + R3 memory)
- [x] Phase I — save `docs/screens/customer-profile-flow.md`
- [ ] Phase J — handoff с follow-up tickets (P-1 pilot scope; P-2…P-5 post-pilot ADR-0015/memory; P-7 runbook)
- [ ] Phase K — commit + push (NO PR per recon instruction)

**Severity результирующего surface:** P1 PRE_PILOT — last customer surface.

**Following streams to engage after sign-off:**
- W1 — frontend (Profile tab UI + consent toggles + support-entry sheets + R3 coming-soon + entry links + states)
- W4 — backend (consents / proactive toggle endpoints for pilot) + runbook manual dual-system delete (P-7)
- Legal/Compliance — cross-border wording review (P-6) перед pilot
- Post-pilot — ADR-0015 epic (cross-service delete contract + hook) + memory layer build

---

## 19. Sign-off

| Role | Approval | Date |
|---|---|---|
| Founder (LOCKED scope + R1-R6 structure) | ✅ | 2026-05-26 |
| Tech Lead (5 Q-P verdicts + 3 added states + critical 30-day-grace correction) | ✅ | 2026-05-26 |
| Tech Lead (recon: Variant 3 — defer R2 delete/export + R3 memory; sign-off R1/R4/R5/R6) | ✅ partial | 2026-06-01 |
| Tau (author + 3 refinements applied) | ✅ | 2026-05-26 |
| Brand Guardian (12-pattern checklist) | ⏸ pending Phase F | 2026-05-26 |
| W1 (Profile tab frontend) | ☐ | (pending impl) |
| W4 (consent/proactive endpoints + runbook manual delete) | ☐ | (pending impl) |
| Legal / Compliance (cross-border wording review P-6) | ☐ | (pending pilot) |
| Accessibility | ☐ | (pending pilot) |

**Sign-off note (2026-06-01):** R1 (header), R4 (proactive toggle), R5 (notifications), R6 (states) — ✅ tech-lead signed off. R2 delete/export + R3 memory — DEFERRED for pilot (not signed off as shippable in-app); revisit post-pilot per ADR-0015 epic.

## Last verified
2026-06-01 — Cross-repo recon (tech-lead): bot-platform `data_delete` = immediate hard-delete but bot-platform-only (no cross-service calls), export = inline-JSON bot-platform-only, no memory endpoints/tables (layer unbuilt); beautygo soft-delete + anonymize with Appointments/Payments surviving (FK PROTECT), no delete hook, no export. Two systems unwired & disagree → **Variant 3: defer R2 delete/export + R3 memory to post-pilot (ADR-0015 epic)**; route in-app entries to support; 152-ФЗ via manual operator dual-system delete (runbook). NO 30-day grace promise retained (founder-locked). R1/R4/R5/R6 tech-lead signed off. Supersedes 2026-05-26 r1 (which anchored truthfulness only on `data_delete = immediate`).
