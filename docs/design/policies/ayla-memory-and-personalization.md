# Ayla — Memory & Personalization Policy

**Date:** 2026-05-19 r1
**Status:** STRATEGIC FOUNDATION — Doc #2 of 5 in Ayla-first foundation set. Closes Q-AYL20 PRE-DEPLOY (memory transparency UX). Blocks launch per 152-ФЗ compliance.
**Reads:** [`ayla-identity-and-brand.md`](./ayla-identity-and-brand.md), memory `project_ayla_first_strategic_pivot`, memory `project_ayla_personal_ai`, Notion: Ayla AI Персонализация (`334b0dab-2955-81d5-87cf-eaf49efd2d5b`), User Flow Управление памятью Ayla (`336b0dab-2955-819d-b36a-ee844cb472ef`), MEM-01 (`338b0dab-2955-813f-8bfc-cb167b636dc7`), PROF-01 (`338b0dab-2955-81e4-820f-c104f6c9041d`), User Flow Прогрессивное профилирование (`334b0dab-2955-816c-8c02-e48ab8d7c71e`)

> Ayla remembers. That's the moat. But memory без прозрачности = creepy AI; memory без контроля = legal violation. This doc specifies HOW Ayla collects, stores, surfaces, and lets customer manage what Ayla knows. 3-zone sensitivity framework is the operational core. Per 152-ФЗ — memory management surface is P0; without it, app rejected from App Store / Google Play + РКН fines.

---

## 0. Why this exists

### 0.1 The strategic foundation

Per [`ayla-identity-and-brand.md`](./ayla-identity-and-brand.md): **«Ayla remembers»** — это brand promise. «AI, который помнит. Всегда.»

Per memory `project_wellness_os_vector`: 10-layer Wellness Profile + cross-tenant persistence + customer-only ownership = retention moat.

Per Notion personalization architecture (`334b0dab-...`): «Персональная память как moat — чем дольше пользователь с Ayla, тем ценнее накопленная модель. Сложно скопировать, болезненно потерять.»

### 0.2 The trust dilemma

Memory creates trust + creates fear. Same data point:
- Customer says «помню, ты по понедельникам после работы» → tëпло, личное
- Customer sees «Ayla знает, что у тебя ребёнок 4 лет» → жуткое отслеживание

Difference is:
1. **Whether Ayla names the data source** — yellow-zone naming forbidden
2. **Whether customer controls what Ayla remembers**
3. **Whether customer can see the full picture**

This doc designs all three.

### 0.3 The promise

Single source for:
- 3-zone sensitivity operational rules §2 (extends Doc #1 §8)
- `UserPersonalContext` data model §3
- 3 data sources with weights §4 (explicit / behavioral / contextual)
- Memory transparency surface «Что Ayla знает обо мне» §5 (P0, 152-ФЗ)
- Inference vs explicit data — UI distinction §6
- Memory operations: view / edit / delete / reset §7
- Chat-side memory commands §8 («покажи что знаешь», «забудь X»)
- Cross-tenant persistence rules §9
- Progressive profiling — Ayla asks ≤ 1 question/session §10
- Retention policy per zone §11
- 152-ФЗ compliance + export §12
- LLM prompt construction with zone respect §13
- Anti-patterns §14
- 4 NEW models, 18 endpoints, 12 events

---

## 1. Scope

### IN
- `UserPersonalContext` model — Ayla's memory of user, cross-tenant
- Per-field zone tagging (🟢🟡🔴) + zone enforcement at API + LLM prompt levels
- 3 data sources tracked with attribution (явные / поведенческие / контекстуальные)
- Memory transparency surface in Mini App («Что Ayla знает обо мне»)
- Per-field source attribution (💬 «сам сказал» / 🤖 «AI вывела»)
- Edit (explicit data only) + delete (all) + 5-sec undo + full reset (типу «удалить» confirmation)
- Chat-side memory commands («покажи что знаешь», «забудь X», «забудь всё» → redirect to settings)
- Progressive profiling rules — ≤ 1 question/session, ≥ 24h gap, 2 skip → 30d pause, NEVER first interaction
- Behavioral pattern extraction (Celery daily, no questions to customer)
- Contextual signal extraction (Claude API structured during natural chat)
- Cross-tenant memory persistence with per-tenant exception list
- Retention per zone (green indefinite, yellow indefinite, red 90d unused → auto-delete)
- 152-ФЗ export + data subject rights
- Memory access audit log
- 12 NEW events for event-taxonomy

### OUT
- LLM model selection (`324b0dab-...`)
- Wellness module data (covered by per-module handoffs; Ayla memory references them but doesn't duplicate)
- Tenant-side analytics on customer behavior (per-tenant booking data is tenant-scoped, NOT Ayla memory; see Doc #4 tenant-as-provider)
- Booking history (lives in `BookingRequest`; Ayla memory references via lightweight summary)
- Voice / TTS / STT (Phase 2+)
- Multi-language inference (Phase 3+ Kazakh)
- ML-based persona drift detection (Phase 2+)
- Memory export to other platforms (data portability standard; we export JSON, customer's responsibility from there)
- Customer-to-customer memory sharing (out of scope; privacy)
- Family / shared accounts memory (Phase 4+)
- Cross-language memory translation (Phase 3+)
- Anti-fraud detection on profile manipulation — Phase 4+

---

## 2. 3-zone sensitivity framework — operational

Per [`ayla-identity-and-brand.md §8`](./ayla-identity-and-brand.md) — strategic introduction. Here are **operational rules** for engineering + UX.

### 2.1 🟢 Зелёная зона

**Definition:** facts user has explicitly stated OR Ayla has reasonably inferred from openly stated context, that are safe to reference back to user.

**Examples:**
- Рабочий адрес («работаю на Тверской»)
- Район проживания / любимый район
- Спортзал, любимые места
- Любимое время суток («предпочитаю вечером»)
- Бюджет на услугу
- Любимый мастер / любимый салон
- Диета general («не ем мясо»)
- Жизненные события (свадьба, отпуск, командировка)
- Предпочтения по услугам («только без отдушек»)
- Любимая температура / музыка / процедуры

**Operational rules:**
- ✅ Ayla can reference verbatim in chat: «нашла рядом с твоим офисом», «знаю, ты предпочитаешь вечером»
- ✅ Stored in plain DB, no encryption requirement
- ✅ Anonymized analytics OK (founder dashboards: «N% customers prefer evening slots»)
- ✅ Can be used in recommendation logic openly
- ✅ Listed in memory transparency surface with full context

### 2.2 🟡 Жёлтая зона

**Definition:** data Ayla knows or has inferred that, if surfaced explicitly, would feel surveillant / intrusive.

**Examples:**
- Наличие детей (inferred from booking patterns + chat context)
- Marital status / relationship presence (inferred or stated casually)
- Занятость pattern («очень занята по утрам» as inferred reality)
- Режим дня specifics (sleep schedule, peak hours)
- Информация о партнёре / семье
- Behavioral patterns (booking cadence, no-show frequency)
- Emotional state inferred from message tone
- Spending pattern

**Operational rules:**
- ✅ Ayla **uses** the data to inform reasoning + recommendations
- ❌ Ayla **NEVER names** the data or its source in chat:
  - ❌ «Знаю, что у тебя ребёнок»
  - ❌ «Вижу, ты занята по утрам»
  - ❌ «По твоим эмоциям заметила, что ты расстроена»
- ✅ Acts on the knowledge silently:
  - «Вот несколько вечерних слотов — подойдёт?» (uses kids-knowledge to skip morning)
  - «По твоей цели спокойствие — массаж может быть кстати» (uses stress signal silently)
- ✅ Storage: encrypted DB field
- ❌ Default analytics events **excluded** from yellow-zone field values
- ✅ Listed in memory transparency surface but with «AI вывела» 🤖 tag
- ⚠ Aggregated analytics OK **only with explicit customer consent** (Phase 3+ if pursued; out of scope MVP)

### 2.3 🔴 Красная зона

**Definition:** medically / legally / emotionally sensitive data with absolute privacy requirement.

**Examples:**
- Беременность
- Хронические заболевания
- Mental health diagnoses / states
- Медицинские противопоказания (specific medical contraindications)
- Sensitive relationship information (abuse, divorce-in-progress)
- Drug / alcohol history
- Surgery / hospitalization history
- Sexual orientation (not for service recommendation)

**Operational rules:**
- ✅ Ayla uses **strictly for service contraindication filtering** (e.g., pregnancy → skip rejuvenation lasers, certain oils, hot stones)
- ❌ Ayla **NEVER references in chat without explicit customer initiation**:
  - ❌ «Учитывая твою беременность — могу предложить...» (if customer hasn't brought it up in this session)
  - ❌ Auto-recommend pregnancy-specific services
  - ✅ Customer says «можно мне X при беременности?» → Ayla cautious answer + medical routing per [`wellness-symptom-handoff §10`](../handoffs/2026-05-19-wellness-symptom-handoff.md)
- ✅ Storage: `is_sensitive=True` flag + encrypted at-rest
- ❌ NEVER in analytics events (no value, no aggregate, no founder dashboards)
- ✅ Access logged separately to `RedZoneAccessLog` model
- ✅ Listed in memory transparency surface with explicit «Только локально, для безопасных рекомендаций» label
- ✅ Retention: 90 days unused → auto-delete (per Notion personalization spec)
- ✅ Hidden by default in memory surface — explicit reveal required (per §5.6)
- ✅ Deletion requires confirmation + warns about service contraindication loss

### 2.4 Zone determination

When new fact is captured, system tags zone:
- **Explicit user statement of medical fact** → 🔴 red
- **Inferred medical fact** (from symptom log, food log, conversation tone) → 🔴 red
- **Explicit statement of family / relationship / behavioral state** → 🟡 yellow
- **Inferred family / relationship / behavioral pattern** → 🟡 yellow
- **Everything else explicit or inferred** → 🟢 green

Edge cases (ambiguous):
- «Я веган» → 🟢 green (dietary preference, openly shareable)
- «У меня аллергия на лак X» → 🟢 green (service-relevant, safety positive)
- «У меня panic attacks» → 🔴 red (mental health)
- «Беременна на 5 неделе» → 🔴 red
- «Купила квартиру» → 🟢 green (life event)
- «Развожусь» → 🟡 yellow (relationship, sensitive but informational)

Per Q-AML1 — zone classification can be manually overridden by founder (cannot be by customer or admin). Audit logged.

---

## 3. Data model — `UserPersonalContext`

### 3.1 The model

```python
class UserPersonalContext(models.Model):
    """Ayla's memory of one user. Cross-tenant; lives independently of any salon.

    Per Ayla-first pivot 2026-05-19: memory belongs to user, not salon.
    Memory survives customer's relationship with any particular tenant.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField(
        'identity.BotUser',
        on_delete=models.CASCADE,
        related_name='ayla_memory',
        help_text="BotUser this memory belongs to. CASCADE because Ayla's "
                  "memory dies with the user account per 152-ФЗ erasure."
    )

    # Counters for analytics
    explicit_facts_count = models.IntegerField(default=0)
    inferred_facts_count = models.IntegerField(default=0)
    red_zone_count = models.IntegerField(default=0)

    last_behavioral_extraction_at = models.DateTimeField(null=True, blank=True)
    last_contextual_extraction_at = models.DateTimeField(null=True, blank=True)
    last_question_to_user_at = models.DateTimeField(null=True, blank=True)
    last_full_reset_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
```

### 3.2 `MemoryEntry` — individual facts

```python
class MemoryEntry(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    context = models.ForeignKey(UserPersonalContext, on_delete=CASCADE, related_name='entries')

    ZONE_CHOICES = [
        ('green', '🟢 Зелёная — можно ссылаться'),
        ('yellow', '🟡 Жёлтая — использовать молча'),
        ('red', '🔴 Красная — только для безопасности'),
    ]
    zone = models.CharField(max_length=8, choices=ZONE_CHOICES, db_index=True)

    SOURCE_CHOICES = [
        ('explicit_chat', 'Customer said in chat'),
        ('explicit_form', 'Customer entered in form'),
        ('inferred_behavioral', 'Behavioral pattern extraction'),
        ('inferred_contextual', 'Contextual signal extraction (Claude)'),
        ('inferred_booking_history', 'Inferred from booking patterns'),
        ('inferred_wellness_module', 'Inferred from wellness module data'),
        ('founder_override', 'Founder manual zone change'),
    ]
    source = models.CharField(max_length=32, choices=SOURCE_CHOICES)

    field_key = models.CharField(max_length=64)
    # Standardized vocabulary: 'work_address', 'preferred_time_evening',
    # 'has_children', 'pregnant', 'gym_name', etc.
    # Subset of allowed keys per `apps.ayla.memory.vocabulary`.

    field_value = models.JSONField()
    # Plain DB for green-zone, encrypted at app-layer for yellow + red.
    # Application-layer encrypt/decrypt in services.py.

    confidence = models.DecimalField(max_digits=3, decimal_places=2, default=1.00)
    # 1.00 for explicit; 0.30-0.95 for inferred per heuristic

    evidence_summary = models.TextField(blank=True, default='', max_length=500)
    # Brief audit explanation. For explicit: "Сказала в чате 19 мая".
    # For inferred: "Из 5 последних бронирований 4 были вечером после 18:00".

    last_used_at = models.DateTimeField(null=True, blank=True)
    # Updated when Ayla actually uses this fact in a recommendation / chat.
    # Red-zone retention rule: 90 days unused → auto-delete.

    captured_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    is_active = models.BooleanField(default=True)
    deleted_at = models.DateTimeField(null=True, blank=True)
    deleted_by = models.CharField(max_length=16, blank=True, default='')
    # 'customer', 'auto_retention', 'founder', 'cascade_account_close'

    class Meta:
        indexes = [
            Index(fields=['context', 'zone', 'is_active']),
            Index(fields=['context', 'field_key']),
            Index(fields=['zone', 'last_used_at']),  # retention scanner
        ]
        constraints = [
            UniqueConstraint(
                fields=['context', 'field_key'],
                condition=Q(is_active=True),
                name='memory_entry_unique_active_field_per_context',
            ),
        ]
```

### 3.3 `MemoryAccessLog` — audit

```python
class MemoryAccessLog(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    context = models.ForeignKey(UserPersonalContext, on_delete=CASCADE, related_name='access_logs')

    ACTION_CHOICES = [
        ('entry_created', 'Fact captured'),
        ('entry_used_in_chat', 'Ayla used fact in reply'),
        ('entry_used_in_recommendation', 'Used in recommendation'),
        ('entry_used_in_contraindication_filter', 'Red-zone safety filter applied'),
        ('entry_viewed_by_customer', 'Customer viewed in memory surface'),
        ('entry_edited_by_customer', 'Customer edited explicit fact'),
        ('entry_deleted_by_customer', 'Customer deleted'),
        ('entry_deleted_auto_retention', 'Auto-deleted by retention scanner'),
        ('full_memory_export_generated', '152-ФЗ export'),
        ('full_memory_reset_by_customer', 'Customer typed «удалить»'),
        ('zone_changed_founder_override', 'Founder reclassified zone'),
    ]
    action = models.CharField(max_length=64, choices=ACTION_CHOICES)
    entry = models.ForeignKey(MemoryEntry, null=True, blank=True, on_delete=SET_NULL, related_name='+')
    actor_type = models.CharField(max_length=16)
    # 'customer', 'ayla_system', 'founder', 'auto_cron'
    metadata = models.JSONField(default=dict, blank=True)
    at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [Index(fields=['context', '-at'])]
```

### 3.4 `RedZoneAccessLog` — separate audit for red-zone

```python
class RedZoneAccessLog(models.Model):
    """Separate from MemoryAccessLog per §2.3 — red-zone needs stricter audit
    for compliance review."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    context = models.ForeignKey(UserPersonalContext, on_delete=CASCADE, related_name='red_zone_access_logs')
    entry = models.ForeignKey(MemoryEntry, on_delete=CASCADE, related_name='+')

    PURPOSE_CHOICES = [
        ('contraindication_filter', 'Skip-list service due to contraindication'),
        ('customer_initiated_query', 'Customer brought up topic, Ayla responded'),
        ('founder_audit_review', 'Founder compliance review'),
        ('legal_hold', 'Legal subpoena / data subject request'),
    ]
    purpose = models.CharField(max_length=64, choices=PURPOSE_CHOICES)
    initiating_actor = models.CharField(max_length=32)
    accessed_at = models.DateTimeField(auto_now_add=True)
```

---

## 4. 3 data sources with weights

Per Notion personalization architecture: Ayla learns from 3 sources with explicit weights. This is engineering's source-of-truth for how facts enter memory.

### 4.1 Explicit chat (~30%)

Customer literally says it.

**Example:** «У меня аллергия на лак X»

**Capture:** Claude API structured extraction at conversation-end OR after meaningful intent recognition. Extracts:
- Field key: `service_allergies`
- Field value: `["lacquer_x"]`
- Zone: green (safety-positive)
- Source: `explicit_chat`
- Confidence: 1.00
- Evidence: «Сказала 19 мая в чате»

**Capture point:** post-message LLM extraction (Claude structured output). Background task, not blocking customer reply.

### 4.2 Explicit form (~5%, MVP)

Customer fills profile fields explicitly.

**Capture:** direct field write. Source: `explicit_form`. Zone per §2.4 mapping. Confidence: 1.00.

### 4.3 Behavioral patterns (~50%)

Inferred from observed behavior. Most powerful source.

**Examples:**
- 5 last bookings all evening → `preferred_time_evening` (green, confidence 0.80)
- Booking cadence every 28 days → `service_cycle_monthly` (green, confidence 0.90)
- 3 no-shows in 90 days → admin-side pattern flag (NOT in Ayla memory; per `customer-no-show-policy §8`)
- Wellness module water log → daily hydration pattern → green if 80%+ logged
- Sleep logs consistently <6h → yellow `sleep_deficit_pattern`
- Mood logs trending negative for 14d → yellow `mood_trend_negative` (NOT red — mood module separately handles)

**Capture:** Celery daily task per Notion. Runs nightly per user:
1. Pull last 30 days of bookings + wellness logs + interactions
2. Compute patterns per allowlist
3. Compare to existing memory entries
4. Write new / update / age out
5. Log to `MemoryAccessLog` action=`entry_created`

**Important:** behavioral extraction creates NO question to customer. Per Notion: «Этот флоу не генерирует уведомлений».

### 4.4 Contextual signals (~20%)

Mid-conversation extraction via Claude.

**Example:** customer mentions «надо успеть до садика» → contextual signal `has_children`, zone yellow, confidence 0.85.

**Capture:** Claude API structured output during turn:
- Run on every chat turn post-MVP; sample on MVP (cost)
- Extracts structured signals from natural conversation
- Validates against vocabulary allowlist
- Writes to `MemoryEntry` with source `inferred_contextual`

### 4.5 Sources NOT used

- Browser fingerprint / IP / device → out of scope; not stored in Ayla memory
- Location (background GPS) → never; «when in use» only per Notion travel-time
- Social network public data → out of scope MVP
- Cross-platform tracking → out of scope MVP

### 4.6 Source attribution UI

Per Notion: each `MemoryEntry` shown to customer in memory surface displays source icon:

| Source | Icon | Label |
|---|---|---|
| `explicit_chat` | 💬 | «Сказала в чате {{date}}» |
| `explicit_form` | 📝 | «Указала в профиле {{date}}» |
| `inferred_behavioral` | 🤖 | «Ayla заметила из твоих записей» |
| `inferred_contextual` | 🤖 | «Ayla поняла из разговора» |
| `inferred_booking_history` | 🤖 | «Ayla заметила паттерн в записях» |
| `inferred_wellness_module` | 🤖 | «Ayla заметила из модуля {{module}}» |
| `founder_override` | ⚠ | «Команда отметила вручную» |

---

## 5. Memory transparency surface

Per Notion: «Что Ayla знает обо мне» is P0 — blocks launch per 152-ФЗ Article 14 (right to access).

### 5.1 Entry point

Mini App «Профиль» → section «Что Ayla знает обо мне». Also accessible via:
- Bot DM «покажи что знаешь обо мне» (NLU triggers)
- Mini App settings → memory section
- 152-ФЗ data export link

### 5.2 Home — what customer sees

```
┌────────────────────────────────────────┐
│ 🧠 Что Ayla знает обо мне                │
├────────────────────────────────────────┤
│ Всё, что Ayla помнит про тебя — на этом │
│ экране. Можешь посмотреть, изменить или │
│ удалить любую запись.                    │
│                                        │
│ ── Откуда я знаю ──                      │
│ 💬 Сказала сама — 12 фактов              │
│ 🤖 Ayla заметила — 18 фактов              │
│                                        │
│ ── Темы ──                                │
│                                        │
│ 🟢 Открытые (24)                          │
│   Адрес работы, любимое время, бюджет,  │
│   диета, любимые мастера, гастрономия...│
│   [Посмотреть]                            │
│                                        │
│ 🟡 Личные (6)                             │
│   Семейные обстоятельства, занятость,    │
│   паттерны...                            │
│   ⓘ Эти данные Ayla использует молча,    │
│   не упоминает в чате                    │
│   [Посмотреть]                            │
│                                        │
│ 🔴 Чувствительные (1) — скрыто           │
│   ⚠ Только для безопасных рекомендаций. │
│   Не упоминается в чате.                 │
│   [Показать]                              │
│                                        │
│ ── Действия ──                            │
│ [📤 Скачать всю историю (152-ФЗ)]         │
│ [🗑 Удалить всю память]                    │
└────────────────────────────────────────┘
```

### 5.3 Per-zone list view

Tap «Посмотреть» (green example):

```
┌────────────────────────────────────────┐
│ ← 🟢 Открытые знания                      │
├────────────────────────────────────────┤
│ Адрес работы                              │
│ 💬 «Тверская улица, 14»                   │
│    Сказала сама 12 марта                  │
│    [Изменить]                              │
│                                        │
│ Любимое время                              │
│ 🤖 «Вечер после 18:00»                    │
│    Ayla заметила из последних 5 записей  │
│    [Удалить]                               │
│                                        │
│ Бюджет на услугу                          │
│ 💬 «До 2500 ₽»                            │
│    Указала в профиле                      │
│    [Изменить]                              │
│                                        │
│ Любимый мастер                            │
│ 🤖 «Анна Петрова в Формуле тела»          │
│    Ayla заметила: 8 записей подряд       │
│    [Удалить]                               │
│                                        │
│ Диета                                      │
│ 💬 «Не ем мясо»                           │
│    Сказала 5 апреля                       │
│    [Изменить]                              │
│                                        │
│ ...                                      │
└────────────────────────────────────────┘
```

### 5.4 Per-zone list — yellow example

```
┌────────────────────────────────────────┐
│ ← 🟡 Личные                                │
├────────────────────────────────────────┤
│ ⓘ Эти данные Ayla использует молча,      │
│   не упоминает в чате. Например, знает  │
│   о детях — поэтому предлагает          │
│   вечерние слоты, но не говорит         │
│   «знаю, что у тебя ребёнок».           │
│                                        │
│ Семейные обстоятельства                  │
│ 🤖 «Возможно, есть дети»                  │
│    Ayla поняла из фразы про садик       │
│    [Удалить]                               │
│                                        │
│ Занятость                                  │
│ 🤖 «Очень занята по утрам»                │
│    Ayla заметила: 0 утренних записей    │
│    за 90 дней                             │
│    [Удалить]                               │
│                                        │
│ ...                                      │
└────────────────────────────────────────┘
```

### 5.5 Per-zone list — red example (hidden by default)

Tap «Показать» → reveal screen with extra confirmation:

```
┌────────────────────────────────────────┐
│ 🔴 Чувствительные данные                  │
├────────────────────────────────────────┤
│ Эти данные Ayla хранит только для       │
│ безопасности — чтобы не предлагать      │
│ услуги с противопоказаниями.             │
│                                        │
│ Ayla никогда не упоминает их в чате,    │
│ даже не намёкнёт. Они хранятся          │
│ зашифрованными и автоматически          │
│ удаляются через 90 дней без             │
│ использования.                            │
│                                        │
│ [Хорошо, показать]                        │
└────────────────────────────────────────┘
```

After confirm:

```
┌────────────────────────────────────────┐
│ ← 🔴 Чувствительные                       │
├────────────────────────────────────────┤
│ Состояние здоровья                        │
│ 💬 «Беременность, 12 недель»              │
│    Сказала 1 мая                          │
│    Auto-delete: через 87 дней без        │
│    использования                          │
│    [Удалить сейчас]                        │
│                                        │
│ ── ⚠ Если удалишь ──                     │
│ Ayla больше не будет автоматически       │
│ исключать процедуры с противопоказаниями.│
│ Это твой выбор.                          │
└────────────────────────────────────────┘
```

### 5.6 Per-entry detail (tap row)

```
┌────────────────────────────────────────┐
│ ← Любимое время                           │
├────────────────────────────────────────┤
│ Зона: 🟢 Открытая                         │
│ Источник: 🤖 Ayla заметила                │
│                                        │
│ Что записано:                            │
│ Вечер после 18:00                        │
│                                        │
│ Откуда:                                   │
│ Из 5 последних бронирований 4 были      │
│ вечером после 18:00. Ayla сделала       │
│ обобщение 12 мая.                        │
│                                        │
│ Как используется:                         │
│ Когда подбираю слоты — предлагаю        │
│ сначала вечерние                          │
│                                        │
│ ── Действия ──                            │
│ [🗑 Удалить]                              │
│                                        │
│ ⓘ Inferred-данные нельзя редактировать  │
│   — только удалить. Если хочешь чтобы   │
│   Ayla узнала иначе — расскажи в чате.  │
└────────────────────────────────────────┘
```

Per §6.1: inferred data = read-only / delete-only. Explicit data = editable.

### 5.7 Edit explicit data

For 💬 explicit-source entries, tap [Изменить] opens edit form:

```
┌────────────────────────────────────────┐
│ ← Адрес работы                            │
├────────────────────────────────────────┤
│ Что Ayla помнит:                          │
│ Тверская улица, 14                        │
│                                        │
│ Изменить на:                              │
│ [Тверская улица, 14_____________]        │
│                                        │
│ [Сохранить]   [Отмена]                    │
└────────────────────────────────────────┘
```

Save → updates `field_value`, increments `updated_at`, writes `MemoryAccessLog` action=`entry_edited_by_customer`.

### 5.8 5-second undo (snackbar)

After delete or edit, snackbar appears bottom:

```
┌────────────────────────────────────────┐
│ ✅ "Адрес работы" удалён [Отменить]      │
└────────────────────────────────────────┘
```

5 seconds visible. Tap «Отменить» → reverts. Otherwise persists.

### 5.9 Hard reset — «Удалить всю память»

```
┌────────────────────────────────────────┐
│ ⚠ Удалить всю память Ayla                │
├────────────────────────────────────────┤
│ Это сбросит всё, что Ayla знает о тебе. │
│ Бронирования и оплаты сохранятся (это   │
│ обязательно по закону), но всё личное   │
│ — что любишь, чего избегаешь, какие     │
│ мастера — Ayla забудет.                  │
│                                        │
│ Ayla станет как при первой встрече.     │
│ Это безвозвратно.                         │
│                                        │
│ Чтобы подтвердить, введи слово           │
│ «удалить»:                                │
│ [_____________________________]        │
│                                        │
│ [Удалить навсегда]   [Передумала]         │
└────────────────────────────────────────┘
```

Per Notion: requires typing word «удалить» (anti-misclick). Triggers `MemoryAccessLog` action=`full_memory_reset_by_customer`. Preserves: `BookingRequest`, `Payment`, audit logs. Deletes: all `MemoryEntry` (active + soft-deleted), `RedZoneAccessLog` references.

### 5.10 152-ФЗ export

«📤 Скачать всю историю» button → generates JSON dump of all `MemoryEntry` + access logs (per §12). Customer's right to data portability.

---

## 6. Inference vs explicit data — UI distinction

### 6.1 Edit rules

| Source | Editable? | Deletable? |
|---|---|---|
| `explicit_chat` 💬 | ✅ Yes | ✅ Yes |
| `explicit_form` 📝 | ✅ Yes | ✅ Yes |
| `inferred_behavioral` 🤖 | ❌ No (only delete) | ✅ Yes |
| `inferred_contextual` 🤖 | ❌ No | ✅ Yes |
| `inferred_booking_history` 🤖 | ❌ No | ✅ Yes |
| `inferred_wellness_module` 🤖 | ❌ No | ✅ Yes |
| `founder_override` ⚠ | ❌ No | ✅ Yes (but founder gets notification) |

**Why inferred is delete-only:** If customer wants Ayla to know something different, customer says it in chat (becomes explicit data). Letting customer edit AI's inference creates ambiguity — was it inferred or stated? Audit clarity wins.

### 6.2 If customer wants to «teach» Ayla

Customer types in chat: «На самом деле я предпочитаю утро, не вечер.»

Ayla:
1. Captures as new `MemoryEntry` with source=`explicit_chat`, supersedes old inferred entry (sets old to `is_active=False`, deleted_by=`auto_supersede`)
2. Replies acknowledging: «Поняла, утренние слоты в приоритете теперь»
3. Audit logs both events

### 6.3 Delete then re-infer behavior

If customer deletes inferred entry, the underlying behavioral data isn't deleted (bookings stay). Next nightly Celery extraction may re-infer same fact.

Per Q-AML5: if customer deletes same field 3 times in 90 days → behavioral extractor adds field to user's «do-not-re-infer» list. Audit logged. Customer can clear list via settings.

---

## 7. Memory operations

### 7.1 View

`GET /api/v1/customer/memory` → list all active `MemoryEntry` for user.
Filters: `?zone=green`, `?source=explicit_chat`, `?field_key=preferred_time`.

`GET /api/v1/customer/memory/<entry_id>` → detail view per §5.6.

### 7.2 Edit

`PATCH /api/v1/customer/memory/<entry_id>` → only for explicit-source entries; 400 if inferred.

```json
{ "field_value": "Тверская улица, 14" }
```

### 7.3 Delete (soft)

`DELETE /api/v1/customer/memory/<entry_id>` → sets `is_active=False`, `deleted_at=now()`, `deleted_by='customer'`. Within 5 sec, customer can `POST /undo` to revert.

### 7.4 Undo

`POST /api/v1/customer/memory/<entry_id>/undo` → if `deleted_at` within 5 seconds, set `is_active=True`, `deleted_at=NULL`. Otherwise 410 Gone.

### 7.5 Hard reset

`POST /api/v1/customer/memory/reset` with body `{ "confirmation": "удалить" }`. Validates literal text. Cascades delete of all `MemoryEntry` for this user. Preserves `BookingRequest` / `Payment` / audit per §5.9. Triggers `MemoryAccessLog` + `customer.memory.full_reset` event.

### 7.6 Export

`POST /api/v1/customer/memory/export` → generates ZIP/JSON, links to background job per [`customer-privacy-data-closure-ux §4`](./customer-privacy-data-closure-ux.md).

### 7.7 Founder override (Phase 3+)

`POST /api/v1/founder/memory/<entry_id>/zone-override` — founder reclassifies zone for compliance review. Audit logged. Customer notified silently in next interaction.

---

## 8. Chat-side memory commands

Per Notion: customer can manage memory via natural chat.

### 8.1 «Покажи что знаешь обо мне»

Ayla replies with summary + link to full memory surface:

```
Помню, что ты предпочитаешь вечернее время, любимый мастер — Анна в
Формуле тела, бюджет около 2500₽. Открыть полный список — [Что я знаю
обо мне].
```

Mentions only green-zone facts. Yellow + red NOT mentioned in chat summary.

### 8.2 «Забудь {{field}}»

If customer says «забудь мой адрес работы», Ayla:
1. Identifies matching `MemoryEntry` (field_key=`work_address`, active)
2. Soft-deletes
3. Replies confirmation: «Готово, забыла адрес работы.»
4. Audit logged

If ambiguous («забудь про маникюр»), Ayla asks clarification: «Маникюр как услугу или конкретные предпочтения? Покажу что помню — [список].»

### 8.3 «Забудь всё»

Ayla **does NOT execute** mass deletion in chat. Per Notion: redirect to settings.

```
Это серьёзный шаг. Удаление всей памяти доступно в настройках — [Что
Ayla знает обо мне → «Удалить всё»]. Там нужно ввести слово «удалить»
для подтверждения, чтобы случайно не нажать.
```

Rationale: mass delete is irreversible; require explicit UI confirmation flow, not chat-side.

### 8.4 «Забудь про беременность» (red-zone)

Per §2.3: red-zone deletion is allowed in chat OR settings, but Ayla warns about contraindication loss:

```
Поняла. Перед тем как удалить — учти: я больше не буду автоматически
исключать процедуры, которые могут не подойти при беременности.
Уверена?

[Да, удалить]   [Передумала]
```

If confirmed, delete + audit.

### 8.5 «Что про меня знают другие?»

If customer asks about cross-tenant / multi-party visibility:

```
В чате со мной — только мы. Студии не видят то что мы обсуждаем, не
видят твои заметки и предпочтения. Они видят только свои записи и
итоговые услуги. Подробнее — [Приватность].
```

Links to [`customer-privacy-data-closure-ux.md`](./customer-privacy-data-closure-ux.md).

---

## 9. Cross-tenant memory persistence

### 9.1 The rule

**Ayla's memory is one entity per user**, not per (user, tenant). When customer is at Salon A and tells Ayla «not ем мясо», Ayla also knows this when customer interacts with Salon B's services.

### 9.2 What persists cross-tenant

- All `MemoryEntry` rows
- Preferences (green / yellow / red — all)
- Wellness profile layers (per `core-wellness-profile.md` — Ayla's, not tenant's)
- AI memory transcripts (Ayla's chat history with customer)
- Goal definitions (per `customer-wellness-goal-setting-ux.md`)

### 9.3 What is per-tenant (NOT in Ayla's memory)

- Booking records (`BookingRequest` rows per tenant)
- Loyalty balance + tier (per [`customer-loyalty-rewards-ux.md Q-CL13`](./customer-loyalty-rewards-ux.md))
- Reviews customer left for tenant's masters
- Refund disputes per tenant
- No-show pattern (per `customer-no-show-policy-ux §2.8`)
- Tenant-specific consent log entries (e.g., notification prefs per tenant per `customer-notification-controls-ux §7`)

### 9.4 «My memory at Tenant X» — does not exist

There's no «my Ayla profile at Salon A» concept. There's «my Ayla» (one) + «my bookings at Salon A» (subset of total bookings).

### 9.5 If customer closes account at one tenant

Per [`customer-privacy-data-closure-ux.md §10`](./customer-privacy-data-closure-ux.md): customer closes per-tenant account → tenant-side data deleted/anonymized. Ayla memory **stays** (it's customer's, not tenant's). Customer's bookings at other tenants unaffected.

### 9.6 If customer closes Ayla account entirely

Per `customer-privacy-data-closure-ux §7`: hard-delete cascades:
- All `MemoryEntry` deleted
- All `UserPersonalContext` deleted
- All `MemoryAccessLog` anonymized 30 days then deleted
- All `RedZoneAccessLog` anonymized

Tenant-side data anonymized per §9.3 — booking_id stays for audit but customer_id → null.

### 9.7 Multi-tenant scenarios memory uses

When Ayla considers green-zone fact «любимый мастер — Анна в Формуле тела» for a Salon B query:
- Mentions: «У тебя есть любимый мастер Анна в Формуле тела. В Lounge — хочешь попробовать новенького или подобрать кого-то с похожим стилем?»
- DOES NOT auto-recommend «давай к Анне в Формулу тела» when customer asked about Lounge — respects customer's tenant choice

When considering yellow-zone fact «возможно есть дети» across tenants:
- Uses silently everywhere (skip morning slots regardless of which tenant customer browses)
- Never name source

When considering red-zone fact «беременна»:
- Skip-list contraindicated services at ANY tenant
- Never name source proactively at any tenant

---

## 10. Progressive profiling rules

Per Notion (PROF-01 + User Flow Прогрессивное профилирование) — strict guardrails for when Ayla asks user questions.

### 10.1 The 4 rules

1. **Never on first interaction.** Customer's first session with Ayla = zero profile questions. Per «никаких вопросов на первом взаимодействии».
2. **≤ 1 profile question per session.** Session = conversational thread within 30-min window of activity. If Ayla asks one question, no more questions until next session.
3. **≥ 24 hours between profile questions.** Even if sessions span days.
4. **2 skips of same data type → 30-day pause** on that specific question. Tracked in `UserProfilingState` (model §10.6).

### 10.2 What counts as profile question

Profile questions are those aimed at learning persistent facts:
- «А где ты обычно работаешь?»
- «Любишь больше вечером или утром?»
- «Какие процедуры тебе ближе?»
- «Какой у тебя любимый стиль?»

NOT profile questions (no rate-limit applies):
- «Какое время удобно?» (booking flow)
- «Какие даты подходят?» (booking flow)
- «Согласна перенести на пятницу?» (operational)

### 10.3 Organic embedding

Per Notion: «Вопрос должен быть встроен в ответ Ayla как продолжение разговора, а не как отдельное сообщение-опрос. Пользователь не чувствует что его „допрашивают“.»

✅ «Поищу 3 варианта поближе к тебе. А ты обычно где-то в центре или ближе к окраине?»
❌ «У меня есть вопрос. Где ты работаешь?» (separate poll-like message)

### 10.4 Skip handling

If customer doesn't answer or says «не хочу говорить»:
- Increment skip count for that field_key in `UserProfilingState`
- If skip count == 2 → set `paused_until = now + 30 days` for that field_key
- Ayla never re-asks that question for 30 days

### 10.5 Skip ≠ explicit «нет такого»

If customer says «у меня нет любимого района» — that's an answer (NOT-applicable). Capture as `MemoryEntry` with field_value=`null` + `explicitly_not_applicable=True`. Don't re-ask.

### 10.6 `UserProfilingState` model

```python
class UserProfilingState(models.Model):
    user = models.OneToOneField('identity.BotUser', on_delete=CASCADE, related_name='ayla_profiling_state')
    last_question_at = models.DateTimeField(null=True, blank=True)
    last_question_field_key = models.CharField(max_length=64, blank=True, default='')
    field_skip_counts = models.JSONField(default=dict)
    # {'work_address': 2, 'gym_name': 1}
    field_paused_until = models.JSONField(default=dict)
    # {'work_address': '2026-06-19T00:00:00Z'}
```

### 10.7 Session boundary

Session = continuous interaction within 30-min of last user message. After 30-min gap = new session. Mid-session in-app navigation doesn't reset session.

### 10.8 Trust prerequisite

Even when rate-limits permit, Ayla evaluates «is customer warm enough?»:
- Not on first 2 sessions
- Not during emotionally-loaded conversation (per sentiment signal)
- Not immediately after customer refused something
- Yes if customer expressed positive engagement recently

### 10.9 Customer can disable

`PATCH /api/v1/customer/memory/profiling-preferences`:
```json
{ "allow_questions": false }
```

If `false`, Ayla never asks profile questions. All memory still builds from behavioral + contextual sources (those don't ask).

---

## 11. Retention policy per zone

### 11.1 Green-zone

- Indefinite retention while user account active
- No auto-delete based on age
- Customer manual delete only
- Survives tenant changes

### 11.2 Yellow-zone

- Indefinite retention while user account active
- No auto-delete based on age
- Customer manual delete only
- Aggregated analytics opt-in only (Phase 3+)
- Encrypted at-rest in DB
- Audit logged separately from green

### 11.3 Red-zone

Per Notion: «retention 90 дней неиспользования → авто-удаление».

- Default 90 days unused → auto-delete
- «Unused» = `last_used_at` not updated within 90 days
- Customer can extend via setting (Phase 3+ if needed)
- Encrypted at-rest
- `RedZoneAccessLog` separate audit
- Hard-delete after 90d unused; soft-delete first to allow recovery 7 days
- Customer notified silently in next interaction (no surface, just removed)

### 11.4 Soft-delete window

All zones: soft-delete preserves entry for 7 days, then hard-delete by cron. Allows 152-ФЗ data subject re-request or accidental delete recovery.

### 11.5 Auto-deletion cron

Nightly Celery task:
- Scan red-zone entries: `last_used_at < now - 90 days` AND `is_active=True` → soft-delete + audit
- Scan soft-deleted (any zone): `deleted_at < now - 7 days` → hard-delete
- Scan superseded entries: keep audit summary, hard-delete value

### 11.6 Full account closure

Per [`customer-privacy-data-closure-ux.md §7.7`](./customer-privacy-data-closure-ux.md):
- All MemoryEntry hard-deleted immediately
- All MemoryAccessLog anonymized after 30 days
- All RedZoneAccessLog anonymized after 30 days
- Aggregate counters anonymized

---

## 12. 152-ФЗ compliance

### 12.1 Right to access (Article 14)

Customer can view all memory entries via §5 memory transparency surface. Full export available per §7.6.

### 12.2 Right to erasure (Article 21)

- Per-entry delete via §7.3
- Full memory reset via §7.5
- Full account closure per `customer-privacy-data-closure-ux`

### 12.3 Right to rectification (Article 18)

- Explicit-source entries: edit directly §7.2
- Inferred entries: delete + tell Ayla correct version (becomes new explicit entry) §6.2

### 12.4 Audit + access logs

`MemoryAccessLog` retains 365+ days per compliance. `RedZoneAccessLog` retains 7 years per medical data conventions.

### 12.5 Data export format

JSON / CSV / PDF per [`customer-privacy-data-closure-ux §4`](./customer-privacy-data-closure-ux.md). Includes:
- All active MemoryEntry (zone, field_key, field_value, source, captured_at, evidence_summary)
- All deleted entries (last 30 days)
- All MemoryAccessLog (last 365 days)
- Excludes RedZoneAccessLog raw details (anonymized aggregate only)

### 12.6 Breach notification

Out of scope MVP. Phase 2+ `breach-notification-policy.md` if needed.

### 12.7 No analytics on red-zone

Per §2.3: red-zone field values NEVER enter analytics events. Tracking event `customer.memory.entry_created` fires with `zone=red` (fact of capture) but not `field_key` or `field_value`.

### 12.8 Cross-border data

Out of scope MVP. Russia-only. Phase 3+ international expansion separate.

---

## 13. LLM prompt construction with zone respect

Engineering concern, surfaced here for spec alignment.

### 13.1 Green-zone in prompt

LLM gets full data: «User's preferred time: evening after 18:00. User's budget: up to 2500₽. User's favorite master: Anna at Формула тела.» Can reference verbatim.

### 13.2 Yellow-zone in prompt

LLM gets sanitized inference: «User has constraints in mornings (do not propose morning slots).» NOT «User has a child (which is why they can't morning).»

LLM prompt instruction: «Yellow-zone facts inform reasoning but MUST NOT be quoted to user.»

### 13.3 Red-zone in prompt

LLM gets only **service contraindication tags**: «Skip-list for this user: [pregnancy_contraindicated, allergen_lacquer_x].»

LLM prompt instruction: «Red-zone facts are silent filters. NEVER mention to user unless user explicitly initiates the topic in current turn.»

### 13.4 If customer brings up red-zone

Customer: «можно мне делать массаж при беременности?»

LLM gets prompt extension: «User has surfaced topic [pregnancy]. You may respond cautiously and recommend doctor consultation per medical routing protocol.»

Then Ayla replies per `wellness-symptom-handoff §10` medical routing.

### 13.5 Prompt prefix template

Every Ayla LLM call includes prefix:

```
You are Ayla. You remember the user.

Green-zone facts (can reference openly):
{{green_facts_serialized}}

Yellow-zone constraints (use silently, never mention source):
{{yellow_constraints_serialized}}

Red-zone filters active (silent skip-list only):
{{red_filter_keys_only}}

Voice rules: per ayla-identity-and-brand.md §3.
Question rules: per ayla-memory-and-personalization.md §10.
```

### 13.6 Audit per LLM call

Each LLM invocation logs:
- Which `MemoryEntry` were included in prompt
- Which zones served
- Response generated
- Whether yellow-zone leaked into response (post-hoc detection — Phase 2+ ML)

---

## 14. Anti-patterns

### 14.1 Zone violations

| Anti-pattern | Why bad | Correct |
|---|---|---|
| Yellow-zone source naming («Знаю, что у тебя ребёнок») | §2.2 trust violation | Use silently; offer evening |
| Red-zone surfacing without ask («Учитывая беременность, не предлагаю X») | §2.3 sensitive data must be silent | Skip-list applies; never mentioned |
| Red-zone in analytics events with values | §12.7 hard rule | Fact-only events; no values |
| Customer can edit AI's inferred fact | §6.1 ambiguity | Delete + restate as explicit |
| Auto-recategorize green → yellow without audit | Trust violation | Founder override + audit only §7.7 |
| Cross-tenant naming of facts («Помню, что у тебя в Формуле тела…») | OK if tenant-relevant | Acceptable; tenant is just context |
| Mentioning yellow facts to admin/tenant | §2.2 privacy | Yellow stays customer-only |

### 14.2 Surface violations

| Anti-pattern | Why bad | Correct |
|---|---|---|
| Memory surface hidden 4 menus deep | 152-ФЗ accessibility | Profile → top-level entry §5.1 |
| No source attribution per entry | Trust foundation | 💬 / 🤖 icons §4.6 |
| No undo on delete | Accidental loss | 5-sec snackbar §5.8 |
| Mass delete via single tap | Risk of misclick | Type «удалить» required §5.9 |
| Inferred facts as editable | §6.1 | Delete-only |
| Hide red-zone reveal completely | Customer's right to see | Reveal with extra confirmation §5.5 |
| Memory surface mixed with booking history | Confusion | Separate; bookings in own tab |

### 14.3 Progressive profiling violations

| Anti-pattern | Why bad | Correct |
|---|---|---|
| Profile question on first interaction | Anti-onboarding §10.1 | Never |
| > 1 question per session | Customer fatigue §10.1 | Max 1 |
| Re-ask after skip immediately | Pressure | 30-day pause after 2 skips §10.4 |
| Separate poll-message «У меня вопрос:» | Chatbot tell §10.3 | Embed organically |
| Question during emotional moment | Bad timing §10.8 | Wait for warmer turn |
| Question when customer disabled it | §10.9 | Honor preference |

### 14.4 Memory operation violations

| Anti-pattern | Why bad | Correct |
|---|---|---|
| Edit endpoint accepts inferred entries | §6.1 | 400 error |
| Delete without `MemoryAccessLog` | Audit gap | Always logged |
| «Забудь всё» executed in chat | Risk of accidental data loss | Redirect to settings §8.3 |
| Auto-re-infer immediately after deletion | Customer's wish ignored | Add to do-not-re-infer list after 3 deletes §6.3 |
| Full reset preserves nothing | 152-ФЗ requires booking history retention | Preserve BookingRequest + Payment §5.9 |

### 14.5 Cross-tenant violations

| Anti-pattern | Why bad | Correct |
|---|---|---|
| Per-tenant memory entry («like_evening_at_tenant_A») | Wrong model §9.1 | One memory per user; tenant is context |
| Cross-tenant memory shared with tenant | Privacy §9.3 | Tenant never sees Ayla memory |
| Tenant analytics on customer's preferences | Privacy violation | Aggregate-only Phase 3+ opt-in |
| Aggregate yellow-zone data shared across tenants | §2.2 + §9.3 | Customer-only |

---

## 15. Data flow examples

### 15.1 Explicit fact capture

Turn 1 (booking flow):
- Customer: «У меня аллергия на лак Vinilux»
- Ayla: «Поняла, исключаю Vinilux из вариантов. Подобрала 3 мастера с другими марками.»
- Background: Claude API extracts → MemoryEntry created (field_key=`service_allergies`, value=`['vinilux']`, zone=green, source=`explicit_chat`, confidence=1.00, evidence=«Сказала 19 мая в чате»)
- MemoryAccessLog: action=`entry_created`

Turn N (later, at different tenant):
- Customer: «Хочу маникюр в Lounge»
- Background: LLM prompt construction pulls service_allergies → skip Vinilux at Lounge too
- Ayla: «Нашла 2 мастера, у которых нет Vinilux в инструментах. Анна Г. и Лена П.»
- MemoryAccessLog: action=`entry_used_in_recommendation`, entry=allergies_id
- `last_used_at` updated

### 15.2 Behavioral inference

Nightly Celery task:
- Pull last 30 days bookings for user X
- Compute: 5 of last 6 bookings were after 18:00
- Existing entry: none for `preferred_time_evening`
- Create new MemoryEntry: field_key=`preferred_time_evening`, value=`{after: "18:00", confidence: 0.83}`, zone=green, source=`inferred_behavioral`, confidence=0.83, evidence=«Из 6 последних бронирований 5 были после 18:00»
- MemoryAccessLog: action=`entry_created`

Next interaction:
- Customer: «Запиши на эту неделю»
- Ayla considers preferred_time_evening (just inferred) + works it into recommendation
- Ayla: «Нашла 3 варианта на эту неделю — вторник 19:00, среда 20:00, пятница 18:30. Подойдёт?»

### 15.3 Contextual signal extraction

Customer message: «На пятницу не получится — забираю младшего из садика»

Claude API structured extraction at message-end:
- Detects: family situation indicator («младшего», «садика»)
- Inferred: has_children = true, has_young_child = true
- Zone: yellow (family situation)
- Source: `inferred_contextual`
- Confidence: 0.90 (multiple signals in one message)
- Evidence: «Из фразы „забираю младшего из садика“ 22 мая»

MemoryAccessLog: action=`entry_created`

Future interactions: Ayla never mentions kids. Skips morning-rush slots (~7-9 AM). Acts silently.

### 15.4 Red-zone with safety filter

Customer message: «Я беременна 12 недель. Можно мне массаж?»

Claude extraction:
- field_key=`pregnancy_status`, value=`{trimester: 1, weeks: 12, stated_at: "..."}`
- Zone: red
- Source: `explicit_chat`
- Confidence: 1.00
- Evidence: «Сказала прямо 1 мая»

Ayla immediate response (per §8.4 + wellness-symptom routing):
- «Поздравляю! Для массажа при беременности первого триместра — лучше уточнить у врача. Из безопасных: лимфодренаж лица. Хочешь подберу мастера?»
- Skip-list activated for: hot-stone, deep-tissue, certain oils, lasers, rejuvenation

`RedZoneAccessLog`: purpose=`customer_initiated_query`

Two weeks later:
- Customer: «Хочу омолаживающую процедуру»
- Background: red-zone filter checks pregnancy → skip-list includes laser rejuvenation
- Ayla: «Нашла 2 мягкие процедуры — гидропилинг и витаминная маска. Подойдёт?»
- DOES NOT say «учитывая беременность» — uses filter silently
- `RedZoneAccessLog`: purpose=`contraindication_filter`

---

## 16. API contracts

### 16.1 Customer endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/customer/memory` | List own entries (filters: zone, source, field_key) |
| GET | `/api/v1/customer/memory/<id>` | Per-entry detail §5.6 |
| PATCH | `/api/v1/customer/memory/<id>` | Edit explicit entry §7.2 |
| DELETE | `/api/v1/customer/memory/<id>` | Soft-delete §7.3 |
| POST | `/api/v1/customer/memory/<id>/undo` | 5-sec undo §7.4 |
| POST | `/api/v1/customer/memory/reset` | Hard reset (requires confirmation) §7.5 |
| POST | `/api/v1/customer/memory/export` | 152-ФЗ export §7.6 |
| GET | `/api/v1/customer/memory/access-log` | View own audit log §12.4 |
| PATCH | `/api/v1/customer/memory/profiling-preferences` | Disable questions §10.9 |
| POST | `/api/v1/customer/memory/red-zone/reveal` | One-time reveal confirmation §5.5 |
| GET | `/api/v1/customer/memory/red-zone` | Red-zone list (requires reveal session) |

### 16.2 Founder endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/founder/memory/zone-override-queue` | Pending zone reclassification |
| POST | `/api/v1/founder/memory/<id>/zone-override` | Reclassify zone with audit reason §7.7 |
| GET | `/api/v1/founder/memory/compliance-audit` | RedZoneAccessLog summary per tenant |

### 16.3 Internal

| Method | Path | Purpose |
|---|---|---|
| POST | `/internal/memory/extract-behavioral` | Cron daily — behavioral inference |
| POST | `/internal/memory/extract-contextual` | Per-turn Claude call |
| POST | `/internal/memory/retention-scan` | Cron — red-zone 90d cleanup §11.5 |
| POST | `/internal/memory/hard-delete-soft-deleted` | Cron — 7d soft-delete cleanup |
| POST | `/internal/memory/llm-prompt-build` | Build LLM prompt with zone respect §13 |

---

## 17. Events emitted

Add to [`event-taxonomy.md`](./event-taxonomy.md) `3.18 ayla memory domain` (NEW section):

| Trigger | Event | Notes |
|---|---|---|
| Entry created | NEW: `customer.memory.entry_created` | zone, source (NOT value if yellow/red) |
| Entry used (chat/rec) | NEW: `customer.memory.entry_used` | zone, purpose (chat / rec / filter) |
| Entry edited | NEW: `customer.memory.entry_edited` | source (only explicit allowed) |
| Entry deleted | NEW: `customer.memory.entry_deleted` | deleted_by, zone |
| Entry auto-deleted retention | NEW: `customer.memory.auto_deleted_retention` | zone (red-zone 90d) |
| Full memory reset | NEW: `customer.memory.full_reset` | entries_deleted_count |
| Export generated | NEW: `customer.memory.exported` | format |
| Profile question asked | NEW: `customer.memory.question_asked` | field_key |
| Profile question skipped | NEW: `customer.memory.question_skipped` | field_key, skip_count |
| Profile question paused (2 skips) | NEW: `customer.memory.field_paused` | field_key |
| Zone override (founder) | NEW: `customer.memory.zone_overridden` | old_zone, new_zone |
| Red-zone reveal | NEW: `customer.memory.red_zone_revealed` | session_id |

12 NEW events §17.

---

## 18. Acceptance criteria

- [ ] 4 models §3 (UserPersonalContext, MemoryEntry, MemoryAccessLog, RedZoneAccessLog) + UserProfilingState §10.6
- [ ] 18 endpoints §16 (11 customer + 3 founder + 5 internal)
- [ ] 3-zone enforcement at:
  - [ ] API level (yellow/red field values filtered in responses based on caller)
  - [ ] LLM prompt construction §13 (yellow sanitized, red filter-only)
  - [ ] Analytics events §12.7 (red values never logged)
- [ ] Memory transparency surface §5 (home, per-zone list, per-entry detail)
- [ ] Source attribution UI §4.6 (💬/🤖/📝/⚠ icons)
- [ ] Inference = delete-only enforcement §6.1
- [ ] 5-sec undo snackbar §5.8
- [ ] Hard reset with «удалить» typed confirmation §5.9
- [ ] Red-zone reveal flow §5.5
- [ ] Chat commands §8 («покажи», «забудь X», «забудь всё» → redirect)
- [ ] Behavioral inference Celery daily §4.3
- [ ] Contextual signal extraction §4.4
- [ ] Progressive profiling rules §10 (4 rules + skip tracking + session boundary + customer opt-out)
- [ ] Cross-tenant persistence §9 + per-tenant scoping §9.3
- [ ] Retention scanners §11.5 (red-zone 90d, soft-delete 7d cleanup)
- [ ] 152-ФЗ export §12.5 includes all required data
- [ ] Audit logs §3.3 + §3.4 (general + red-zone separate)
- [ ] PII rules: yellow/red never in analytics; audit logged
- [ ] Founder zone override flow §7.7 with audit
- [ ] 12 events §17
- [ ] Tests: per-zone field tagging / explicit-vs-inferred edit rules / undo within 5s / hard reset preserves BookingRequest / red-zone 90d auto-delete / progressive profiling 4-rule enforcement / cross-tenant persistence / cross-tenant filtering (red-zone applies at any tenant) / LLM prompt sanitization (yellow not quoted) / chat commands (показать / забудь / забудь всё redirect) / 152-ФЗ export completeness

---

## 19. Open questions

| # | Question | Lean | Owner | Urgency |
|---|---|---|---|---|
| **Q-AML1** | Zone classification — manual founder override allowed? | YES per §2.4. Audit logged. Customer NOT notified (would expose backend logic). | Founder + Privacy | 🟢 |
| **Q-AML2** | Zone vocabulary — fixed allowlist or extensible? | Fixed allowlist MVP per `apps.ayla.memory.vocabulary` — prevents tag explosion. Extensible Phase 3+ via vocabulary management. | Eng + AI | 🟡 |
| **Q-AML3** | Inferred fact confidence threshold — store < 0.50 confidence? | NO — threshold 0.50. Below = noise. Configurable per vocabulary entry Phase 3+. | Eng | 🟢 |
| **Q-AML4** | Behavioral extraction frequency — nightly or more often? | Nightly MVP per Notion. More often Phase 3+ if data shows value. | Eng | 🟢 |
| **Q-AML5** | Customer deletes same inferred field 3 times — do-not-re-infer list? | YES per §6.3. Customer can clear list in settings. | Policy + Eng | 🟡 |
| **Q-AML6** | Wellness module integration — wellness data IS memory or REFERENCES memory? | References. Wellness modules own their data (mood/water/etc.). Ayla memory has lightweight derived facts (e.g., `sleep_deficit_pattern`) that REFERENCE wellness data without duplicating. | Eng | 🟢 RESOLVED 2026-05-20 |
| **Q-AML7** | LLM prompt audit storage — keep all prompts or sample? | Sample MVP (1 in 100 per user). Full audit Phase 3+ if quality concern. Cost driver. | Eng + AI quality | 🟡 |
| **Q-AML8** | Customer < 18 — memory collection at all? | Per existing wellness rules: minors can use Ayla for booking but NO wellness-module data + NO behavioral inference + ONLY explicit facts customer states. Red-zone fully disabled for minors. | Privacy + Legal | 🟢 RESOLVED 2026-05-20 |
| **Q-AML9** | Session boundary — 30-min correct? | 30-min MVP per §10.7. Tune based on conversation pattern data. | UX | 🟢 |
| **Q-AML10** | Auto-supersede vs explicit conflict resolution | Auto-supersede MVP §6.2. Phase 3+ may surface «Я раньше думала X, теперь говоришь Y — что верно?» if conflict significant. | UX | 🟡 |
| **Q-AML11** | Red-zone retention 90 days unused — tune? | 90d MVP per Notion. Could shorten to 60 if data abuse concern; longer 180 if customers complain about loss. | Policy + Privacy | 🟢 |
| **Q-AML12** | Customer can extend red-zone retention manually? | NO MVP (don't surface; reduces complexity). Phase 3+ if needed. | Policy + UX | 🟢 |
| **Q-AML13** | Memory export format — JSON only or also human-readable? | JSON (machine) + PDF (human) per [`customer-privacy-data-closure-ux §4.2`](./customer-privacy-data-closure-ux.md). | Eng | 🟢 |
| **Q-AML14** | Multi-tenant Ayla — one MemoryEntry across all tenants? | YES per §9.1. Tenant is context not partition. | Eng | 🟢 |
| **Q-AML15** | Cross-tenant analytics on memory patterns — founder-level? | YES with anonymization — founder sees «X% of customers have evening preference». NEVER per-customer named. | Privacy + Founder | 🟡 |
| **Q-AML16** | Founder zone override notification to customer? | NO MVP — would expose backend logic. Audit only. Phase 3+ may surface in memory entry source («команда отметила вручную»). | Privacy + Policy | 🟢 |
| **Q-AML17** | Hallucination defense in extraction — what if Claude extracts wrong fact? | Confidence threshold §Q-AML3 + customer can delete + audit. Phase 2+ post-hoc validation against booking patterns. | AI quality | 🟢 RESOLVED 2026-05-20 |
| **Q-AML18** | Memory access by Ayla itself — does Ayla need to «remember» it accessed? | Cumulative `last_used_at` update is enough MVP. Per-access trace via MemoryAccessLog. | Eng | 🟢 |
| **Q-AML19** | Voice (Phase 2+) memory commands — same UX as chat? | YES. Voice transcribed → same chat-side commands § 8 apply. | UX (Phase 2+) | 🟢 |
| **Q-AML20** | Tone of voice for memory chat replies — same Ayla voice? | YES per `ayla-identity-and-brand §3.1`. Calm, action-oriented, no apology for «I remembered», honest about deletion («забыла»). | UX | 🟢 |

---

## 20. Cross-document linkage

### Foundation set
- [`ayla-identity-and-brand.md`](./ayla-identity-and-brand.md) — Doc #1 (identity / voice / 3-zone introduction)
- **This doc** — Doc #2 (memory operational + 3-zone enforcement)
- `ayla-emergency-fallback-policy.md` — TO WRITE: Doc #3
- `tenant-as-provider-model.md` — TO WRITE: Doc #4
- `anonymous-to-registered-gate.md` — TO WRITE: Doc #5

### Wellness OS integration
- [`core-wellness-profile.md`](./core-wellness-profile.md) — 10-layer profile is Ayla's, lives in this memory model
- `wellness-input-modules.md` — modules' data referenced by MemoryEntry §Q-AML6
- All wellness module handoffs — capture into Ayla memory via `inferred_wellness_module` source

### Privacy
- [`customer-privacy-data-closure-ux.md`](./customer-privacy-data-closure-ux.md) — full account closure cascades
- [`customer-notification-controls-ux.md`](./customer-notification-controls-ux.md) §8 — consent log retention align

### LLM operational
- `ai-quality-observability.md` — forbidden phrase enforcement + quality gates
- LLM model docs (Notion `324b0dab-...`) — model selection

### Engineering
- `apps.identity.BotUser` — owns memory
- `apps.ayla.memory.vocabulary` — TO BUILD: allowlist of field_keys per zone
- `apps.ayla.memory.services` — extraction + retention + LLM prompt build
- `apps.ayla.memory.subscribers` — booking/wellness events → behavioral inference

### Notion source docs
- Ayla AI Персонализация (`334b0dab-2955-81d5-87cf-eaf49efd2d5b`)
- User Flow Управление памятью Ayla (`336b0dab-2955-819d-b36a-ee844cb472ef`)
- MEM-01 152-ФЗ (`338b0dab-2955-813f-8bfc-cb167b636dc7`)
- PROF-01 (`338b0dab-2955-81e4-820f-c104f6c9041d`)
- User Flow Прогрессивное профилирование (`334b0dab-2955-816c-8c02-e48ab8d7c71e`)

### Memory
- `project_ayla_first_strategic_pivot`
- `project_ayla_personal_ai`
- `project_wellness_os_vector` — wellness OS = Ayla's offering, fed by this memory layer

---

## 21. What this unblocks

- **Q-AYL20 PRE-DEPLOY from Doc #1** — memory transparency UX specified
- **152-ФЗ compliance for App Store / Google Play launch**
- **Ayla retention moat operational** — every interaction enriches user model
- **Wellness OS layer activation** — wellness modules can feed memory (via `inferred_wellness_module` source)
- **Personalization at depth** — Ayla can use 3-zone facts without privacy violation
- **Cross-tenant Ayla** — memory persists per `ayla-first-strategic-pivot`
- **Progressive profiling without spam** — 4-rule guardrails locked
- **LLM prompt sanitization** — zone-aware prompt construction
- **Customer trust foundation** — transparent + controllable

## 22. What this does NOT unblock

- ❌ Voice memory commands (Phase 2+)
- ❌ Cross-language inference (Phase 3+ Kazakh)
- ❌ Multi-tenant cross-aggregation of preferences for tenants
- ❌ ML persona drift detection
- ❌ Customer-to-customer memory sharing
- ❌ Family / shared accounts
- ✅ Q-AML6 / Q-AML8 / Q-AML17 — resolved 2026-05-20 (founder confirmed provisional); implementation tickets unblocked

---

## 23. Sign-off

| Role | Approval | Date |
|---|---|---|
| UX Architect | ✅ | 2026-05-19 |
| AI prompt eng (Ayla memory access in LLM prompts + §13 sanitization + Q-AML17 hallucination) | ☐ | 🟢 Q-AML17 resolved 2026-05-20 |
| Privacy / Legal (152-ФЗ compliance §12 + §2 3-zone + Q-AML8 minor + Q-AML15/16 founder access) | ☐ | 🟢 Q-AML8 resolved 2026-05-20 |
| Mini App frontend (memory transparency surface §5 + edit/delete flows + red-zone reveal) | ☐ | |
| Wellness OS steward (Q-AML6 wellness integration boundary) | ☐ | 🟢 Q-AML6 resolved 2026-05-20 |
| Brand owner / Founder (Q-AYL voice for memory replies + Q-AML1 zone override authority) | ☐ | |
| Engineering (behavioral extraction Celery + contextual Claude API + retention scanners + cross-tenant model) | ☐ | |
| AI quality steward (per-LLM-call audit + Q-AML7 sampling rate + Q-AML17) | ☐ | 🟢 Q-AML17 resolved 2026-05-20 |
| Accessibility (WCAG 2.2 AA on memory surface) | ☐ | |

## Last verified
2026-05-19 (initial draft, Ayla memory model + 3-zone enforcement + transparency surface + progressive profiling + cross-tenant persistence + 152-ФЗ compliance + LLM prompt sanitization — locked. Foundation Doc #2 of 5 for Ayla-first pivot. Closes Q-AYL20 from Doc #1.)
