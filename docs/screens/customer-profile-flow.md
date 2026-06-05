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

[ Закрыть ]
```

**LEGAL-REVIEW-REQUIRED:** Exact cross-border wording (включая Anthropic mention + США clarification) must be reviewed by legal/compliance before pilot ship. Draft text above is starting point.

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
