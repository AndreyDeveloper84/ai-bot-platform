# Memory Foundation Design — UserPersonalContext как ров пилота

> v0.1 DRAFT · 2026-07-03 · для ревью founder перед декомпозицией в стрим.
> Решение: память В ПИЛОТ как единственный ров (окно 12–18 мес до Яндекса). Строим **настоящий фундамент**, не заглушку; активируем консервативно. B2B-монетизация (клиент бесплатен) → ров = удержание клиента через накопленный контекст.
> Связано: аддендум `2026-07-03-MVP_RESEARCH_ADDENDUM.md`, #1055 (identity), #1046 (consent), Ayla #187 (internal user API).

## 1. Принцип

**Foundation, not stub.** Строим реальную доменную модель памяти так, чтобы все источники, зоны деликатности, шифрование и кросс-поверхностное использование **подключались без переделки**. В пилоте АКТИВИРУЕМ узкий безопасный слой (explicit green-zone + surfacing), остальное — plug-in на том же фундаменте.

**North Star:** «AI, который помнит». Switching cost = плотность накопленного контекста. Метрика гонки: context fill rate ≥5 полей за первый месяц.

## 2. Текущее состояние (по коду — на чём строим)

**Ayla (`beautygo_backend`, владелец памяти):**
- ✅ `UserPersonalContext` — модель, green-зона (preferred_districts, preferred_time_slots, price_range_min/max, favorite_masters, busy_days, workplace_district, min_rating_preference), `last_asked_at`, `skipped_questions`. Ключ = Ayla `User`.
- ✅ `personalization_engine.should_ask_question` — **8 anti-spam правил** (cooldown 24ч, skip×2→пауза 30д, not-first, explain-why…).
- ✅ Behavioral-источник: `personal_context_inference.infer_user_patterns` (Celery), events (`emit_question_answered/skipped`).
- ✅ `GET/PATCH /users/me/personal-context/` (DRF-174).
- 🟡 `personal_context_views` (skip / delete-field / wipe) — **код есть, НЕ подключён** (wired только minimum GET/PATCH).
- ❌ Зоны yellow/red, шифрование at-rest, contextual-extraction WRITE.

**Bot (`ai-bot-platform`):**
- ✅ `BotUser.ayla_user_id` (UUID) — **bridge бот-личность ↔ Ayla-юзер** уже есть (set из событий / `/api/v1/users/{id}`). Глобальный бот = sentinel-tenant BotUser.
- ✅ `ConsentRecord (tenant, bot_user, consent_type, granted, withdrawn_at, document_version)` — consent-примитив (#1046).
- ❌ Concierge не спрашивает/не применяет память; `persona` app = BrandVoiceConfig (тон), НЕ user-память.

**ai-core (`ayla-ai-core`):**
- ✅ `context.py` — context_builder для кандидатов-мастеров (tool-calls).
- ❌ Инъекции personal-context в промпты concierge нет.

## 3. Целевая архитектура

```
Клиент в MAX/Telegram DM
   │  диалог
   ▼
ai-bot-platform concierge (ayla-ai-core orchestrator)
   │  1) на входе: тянет personal-context (read) → инъекция в промпт (surfacing)
   │  2) в конце сессии: should_ask_question() → 1 вопрос органично
   │  3) contextual-extraction фактов из реплик (write, plug-in)
   ▼  BotUser.ayla_user_id ──────────────► Ayla internal API (#187)
                                              GET/PATCH/skip/delete/wipe personal-context
                                              ▼
                                        UserPersonalContext (Ayla, владелец)
                                        зоны 🟢🟡🔴 + шифрование at-rest
                                        источники: explicit / behavioral(Celery) / contextual
   consent gate: ConsentRecord.consent_type ∈ {memory_green, memory_yellow, memory_red}
```

**Владение:** Ayla — единственный источник истины памяти. Бот НЕ хранит user-факты локально, только читает/пишет через internal API по `ayla_user_id`. (152-ФЗ: одно место хранения, одно место удаления.)

## 4. Модель зон деликатности

| Зона | Что | Сбор | Хранение | В GET по умолчанию | Retention |
|------|-----|------|----------|--------------------|-----------|
| 🟢 **Зелёная** | адрес работы, бюджет, любимый мастер, район, цель, диета | explicit + behavioral, consent = light/implicit | обычное | да | бессрочно (до wipe) |
| 🟡 **Жёлтая** | наличие детей, занятость, паттерны | молча (behavioral/contextual), **не называть источник** | **шифрование at-rest** | да, но без источника | бессрочно |
| 🔴 **Красная** | беременность, хронические заболевания | только с явным consent | **шифрование + отдельный access-log** | **НЕТ** (только по явному запросу) | **90 дней** |

Реализация: поле `zone` на каждый атрибут (enum) + `EncryptedField` для yellow/red + отдельная таблица `RedZoneAccessLog`.

## 5. Разбивка scope: BUILD / ACTIVATE / PLUG-IN

### 🔨 BUILD сейчас (фундамент, чтобы не переделывать)
- Доменная модель `UserPersonalContext` + **zone-тэги на поля** + **шифрование at-rest** (yellow/red) с день-1.
- **Identity-bridge**: резолв `BotUser(sentinel-tenant).ayla_user_id` → Ayla UserPersonalContext; endpoint чтения/записи для бота (расширить #187 internal API).
- **152-ФЗ endpoints**: подключить готовые `skip / delete-field / wipe` (personal_context_views) + `RedZoneAccessLog`.
- **Consent-типы**: `memory_green / memory_yellow / memory_red` в ConsentRecord.
- **Surfacing-контракт**: ai-core context_builder принимает personal-context, инъекция в системный промпт concierge.

### ▶️ ACTIVATE в пилоте (консервативно)
- Explicit **зелёная зона**: concierge вызывает `should_ask_question` → 1 вопрос/сессия → PATCH; применяет в подборе.
- Surfacing зелёной зоны в диалоге («помню, любишь Анну, бюджет до 2000»).
- 152-ФЗ прозрачность как видимая фича (skip/удалить/стереть) — УТП доверия.
- Behavioral-инференс (Celery) — включить (уже есть), пишет зелёную/жёлтую.

### 🔌 PLUG-IN позже (без переделки фундамента)
- Contextual-extraction WRITE из чата (structured extraction).
- **Активный сбор красной зоны** — за явным consent-flow (#1046 зрелый).
- Кросс-тенантная персонализация на глобальном боте (полный G2).

## 6. Зависимости (переезжают в пилот-критпуть)
- **#1055** — UserPersonalContext на глобальной личности (bridge). Основа есть (`ayla_user_id`), нужен резолв для sentinel-tenant + read/write контракт.
- **#1046** — consent-гейт: минимум `memory_green` (light) в пилот; yellow/red consent — plug-in.
- **Ayla #187** — internal user API расширить чтением/записью personal-context по `ayla_user_id` (сервис-токен, без клиентского JWT).

## 7. Декомпозиция в задачи (черновые оценки)

### M-A — Ayla domain (beautygo_backend) — критпуть
- M-A1 `UserPersonalContext += zone` на поля + `EncryptedField` (yellow/red) + миграция. **5 SP**
- M-A2 подключить skip/delete-field/wipe endpoints + `RedZoneAccessLog` (152-ФЗ). **3 SP**
- M-A3 internal API (#187): read/write personal-context по `ayla_user_id` (сервис-токен). **5 SP**
- M-A4 включить Celery-beat behavioral-инференс + метрики (fill/answer/usage/skip rate). **3 SP**

### M-B — Bot concierge (ai-bot-platform)
- M-B1 резолв `BotUser.ayla_user_id` (sentinel-tenant) + клиент к Ayla personal-context API. **5 SP**
- M-B2 concierge: на входе тянет контекст → инъекция; в конце `should_ask_question`→вопрос→write. **5 SP**
- M-B3 consent-типы memory_* в ConsentRecord + гейт зелёной зоны. **3 SP**

### M-C — ai-core (ayla-ai-core)
- M-C1 context_builder принимает personal-context → surfacing в системный промпт. **3 SP**
- M-C2 (plug-in) contextual-extraction WRITE из реплик. **5 SP (post-pilot)**

**Пилот-итого:** ~32 SP (без M-C2/красной зоны). Ощутимо давит на 15.08 — учесть в velocity.

## 8. Открытые вопросы
1. `EncryptedField` — библиотека/подход (django-cryptography / pgcrypto / app-level)? Влияет на поиск по полю. **[открыто]**
2. ~~Consent зелёной зоны~~ → **РЕШЕНО (v1.0 §8.2):** в пилоте активируем `personal_data` + `memory_green`; yellow/red — без активного сбора.
3. Метрика context fill rate — порог gate. **РЕШЕНО частично (v1.0 §15.2):** ≥5 green-фактов/30д на активного, ≥40% активных с 5+ полями, skip ≤30%, usage ≥60%, surfacing в первых 3 сессиях. Где считать (Ayla analytics) — уточнить.
4. ~~Глобальная личность~~ → **РЕШЕНО (v1.0 §7.3, founder):** один **глобальный `ayla_user_id`** на человека; tenant-связи отдельно (`TenantUserRelationship`), НЕ дробить память.

## 8.1. Выравнивание с founder-архитектурой v1.0 (2026-07-03)
v1.0 (`handoffs/ayla_foundational_architecture_memory_platform_v1.md`) — верхний источник. Обогащает эту модель, дотянуть:
- **`MemoryFact`** вместо плоских полей: `key/value/zone/source/confidence/status/first_seen/last_seen/last_used/expires_at/consent_scope/evidence_ref` (§7.5).
- **Confidence** (0–1) + осторожная формулировка по порогу (high→«обычно вечером», low→«уточню») (§7.7).
- **Lifecycle:** Detected→Validated→Confirmed→Used→Updated→Archived→Deleted (§7.8).
- **5 источников** (explicit/behavioral/conversational/transactional/external); в пилоте write = explicit+behavioral+**transactional** (booking-история — ценнейший), conversational-write = plug-in (§7.6).
- **Memory Graph** — post-pilot слой (§7.9), но модель фактов не должна ему мешать.
- **M-GATE-1..6** (v1.0 §18.3) как гейты стрима.

## 9. DoD пилота
- [ ] Модель с зонами + шифрование yellow/red в проде.
- [ ] Бот в DM спрашивает 1 поле зелёной зоны органично (8 правил) и **применяет** в следующем подборе.
- [ ] Surfacing: диалог явно ссылается на память («помню, что…»).
- [ ] 152-ФЗ: пользователь может пропустить вопрос / удалить поле / стереть всё; красная зона не в GET по умолчанию.
- [ ] Identity-bridge: контекст сохраняется на `ayla_user_id`, переживает сессию.
- [ ] Метрика fill rate считается.
