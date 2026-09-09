# Домен памяти: обещано документами × построено в коде

**Дата:** 2026-08-22
**Метод:** чтение канонических документов (`ayla-knowledge`) и кода `ai-bot-platform@origin/dev` (`ce9c298`), выборочно `beautygo_backend@origin/dev` (`38679bb`). Только чтение.
**Предшественник:** `docs/AUDIT_MEMORY_DOMAIN.md` (2026-08-19/20) — его выводы не перепроверялись целиком, проверялось, что изменилось.

Классы доказательности: **VERIFIED** — прочитано в коде, указан файл:строка · **INFERRED** — выведено из кода · **CLAIMED** — так написано в документе/докстринге, кодом не подтверждено · **UNKNOWN**.

Источники обещаний (далее по сокращениям):

| Код | Документ |
|---|---|
| **MDC** | `ayla-knowledge/05 Architecture/Ayla Memory Domain Contract.md` (v1.0, approved, AYLA-DEC-0081) |
| **CRC** | `ayla-knowledge/05 Architecture/Ayla Context Resolution Contract.md` (v1.0, approved) |
| **MP** | `ayla-knowledge/05 Architecture/Ayla Memory and Context Migration Plan.md` (v1.0.3) |
| **CSR** | `ayla-knowledge/06 Safety and Governance/Consent Scope Registry.md` (v1.4) |
| **DL** | `ayla-knowledge/02 Strategy/Ayla Decision Log.md` — AYLA-DEC-0023, AYLA-DEC-0024 |
| **RS** | `ai-bot-platform/docs/specs/memory-entry-schema.md` (репозиторный спек, 2026-05-22) |
| **RP** | `ai-bot-platform/docs/design/policies/ayla-memory-and-personalization.md` (репозиторная политика) |

---

## 0. Итог в пяти строках

1. **Замер от 20.08 был мягче реальности, а не жёстче.** Он писал, что «единственный живой путь записи — три диетические регулярки». На пилоте не жив и он: путь записи упирается в согласие `PERSONAL_DATA`, которое выдаётся только онбордингом, а онбординг выключен флагом `GLOBAL_BOT_ONBOARDING` (default `false`), закреплённым freeze-правилом. Ноль строк `MemoryEntry` — не совпадение, а конструкция.
2. **С 20.08 в домен памяти приехал ровно один коммит** — `4ce069e` (шаги 2–3.5). `live-shadow-stage1` (PR #1221) к домену памяти отношения не имеет: это intent/routing-паритет, `MemoryEntry` он не трогает вообще.
3. **Приехавшее — колонки без исполнителей.** Из 11 новых полей 7 не пишет никто и никогда; остальные 4 пишет одна строка, которая на пилоте не выполняется; читает их — ноль путей.
4. **Три обещания контракта нарушены не «ещё не сделано», а «сделано иначе»:** запись не иммутабельна, `provenance` допускает третье значение `NULL`, supersession отсутствует — старая противоречащая строка остаётся `active` навсегда.
5. **Самое дорогое не в списке шагов миграции:** `ClientProfile` (RFM, отток, лояльность — выведенные ночным пересчётом) уезжает в промпт модели под именем `long_term` мимо зон, согласия, provenance и аудита. Это второй persistent-memory store, запрещённый OR-MEM-1, и он не описан ни в одном документе домена.

---

## 1. Что изменилось с 20.08

### 1.1. Приехало (VERIFIED)

`git log origin/dev --since=2026-08-19 -- apps/identity apps/orchestrator apps/persona apps/consent` — в домене памяти **один** коммит:

**`4ce069e` (2026-08-21 11:39) «Memory Domain Contract steps 2-3.5»**, 16 файлов, +1561:

- миграции `0015` (11 полей), `0016` (backfill lifecycle/времени), `0017` (`provenance`), `0018` (backfill provenance);
- `apps/identity/models.py` +150 — поля и enum'ы `STATUS_CHOICES`, `SUPERSESSION_REASON_CHOICES`, `PROVENANCE_CHOICES`;
- `apps/identity/services/memory_key_policy.py` (новый, 130 строк) — реестр `key → cardinality` и read-side разрешение конфликтов;
- `apps/identity/services/memory_writer.py` +23 — канонический штамп при записи;
- `apps/identity/services/memory_inferred.py` +8 — пометка `deprecated`;
- `apps/orchestrator/memory_block.py`, `apps/persona/memory_surface.py` — подключение key-policy;
- ~930 строк тестов.

**Важно:** шаг 1 миграционного плана помечен «**ВЫПОЛНЕН 2026-08-19**» (MP:59). На `origin/dev` read-side фикс конфликтов появился **2026-08-21** внутри того же `4ce069e` — отдельного коммита от 19.08 в истории `memory_block.py` / `memory_surface.py` нет (`git log origin/dev -- ...` даёт следующим `48b5d81` от 2026-08-03). VERIFIED.

### 1.2. Не изменилось (VERIFIED)

- `_check_minor_protection()` по-прежнему **безусловно бросает** — `apps/identity/services/memory_writer.py:74-78`. Тела условия, флага, HTTP-вызова нет. Жёлтая и красная зоны отбрасываются в 100% случаев, как и было.
- `resolve_context` — **0 вхождений во всём репозитории**. Единого входа нет.
- `MemoryProposal` — 1 вхождение, и то в докстринге `memory_inferred.py:7`. `DecisionRecord` — **0 вхождений**. Ни модели, ни класса.
- `RedZoneReader` — **0 продовых вызывающих** (`apps/identity/services/red_zone_reader.py:73`, вызовы только из тестов).
- Чтение зелёной зоны и успешная запись зелёного факта **не аудируются**. Аудит есть только на удаление (`memory_deleter.py:64,96`) и на отказ записи по DOB (`memory_writer.py:111`).

### 1.3. Shadow Stage 1 — не про память (VERIFIED)

PR #1221 (`6d968ee`, 20 файлов, +1824/−65) не трогает `apps/identity`, не добавляет миграций и моделей. `apps/orchestrator/shadow_turn.py` читает `load_snapshot` (Redis + `ClientProfile`), вызывает `classify`, `pre_check` и сравнивает решение о ветке с legacy. `MemoryEntry` в shadow-пути **не читается и не пишется**; тест `test_shadow_enabled_full_path_no_mutations` (`test_shadow_turn.py:233-301`) это фиксирует.

Два уточнения, которых нет в отчёте об активации:

- «side-effect-free» означает «не пишет в БД», но **не** «без внешних эффектов»: каждый сэмплированный ход делает реальный LLM-вызов (`shadow_turn.py:207-209`) — деньги и латентность;
- отсутствие записи в БД держится **только на `SURFACES=global`**: при `tenant is not None` `classify` уходит через `PIITokenizingProvider` → `write_audit(...)` → INSERT в `AuditLog` (`apps/llm/pii_protected_provider.py:78,187,312`). Расширение surfaces ломает свойство молча.

Событий 0 — ожидаемо и по конструкции: `ORCHESTRATOR_SHADOW_SAMPLE_RATE=0.01`, дефолт в репозитории `0.0` (`config/settings/base.py:1736-1757`), метрик нет вообще — только `logger.info` со строками `orchestrator.shadow.attempted` / `.completed` (`shadow_turn.py:380,395`).

---

## 2. Почему на пилоте ноль строк — цепочка целиком

Это главный ответ, и он не в схеме, а в одной переменной окружения. VERIFIED по коду, INFERRED по состоянию пилота.

```
MAX global webhook
  → GlobalMaxHandler.handle            apps/channels/handlers.py:83
  → _handle_global_max_event_inner     apps/channels/max/handler.py:903
  → record_explicit_green_facts        apps/orchestrator/memory/personal_context.py:91
      ├─ if not can_store_green_memory(bot_user): return 0      ← :55
      │     └─ has_global_consent(bot_user, PERSONAL_DATA)      apps/consent/memory.py:55
      │           └─ ConsentRecord создаётся только здесь:
      │              record_global_consent                      apps/consent/services.py:197
      │                └─ _record_consent_journal               apps/channels/max/global_onboarding.py:329
      │                  └─ run_onboarding_turn                 global_onboarding.py:281
      │                    └─ if settings.GLOBAL_BOT_ONBOARDING apps/channels/max/handler.py:727
      │                          GLOBAL_BOT_ONBOARDING = ... "false"  config/settings/base.py:1376
      └─ extract_green_facts → 3 регулярки                      apps/persona/memory_extract.py:47-62
      └─ write_entry                                            apps/identity/services/memory_writer.py:197
```

Второй способ выдать согласие — `apps/consent/services.py:53 def grant(...)` — **0 продовых вызывающих**. DRF-вью согласий в `apps/consent/` нет вовсе (нет ни `views.py`, ни `urls.py`).

И флаг закреплён не продуктовым решением, а инженерным правилом:

> **Freeze rule:** нет новых transactional-доменов в bot-platform; нет новых фич; **нет флипов флагов** (`BOOKING_VIA_AYLA_REST`, `OUTBOX_EXTERNAL_DELIVERY_TOPICS`, `CERTIFICATE_PAYMENT_ENABLED`, `GLOBAL_BOT_ONBOARDING` остаются default)
> — `ai-bot-platform/docs/plans/2026-07-02-AGENT_OPERATING_RULES.md:10`

**Ни MDC, ни MP, ни CRC, ни CSR не упоминают ни `GLOBAL_BOT_ONBOARDING`, ни онбординг вообще** (`grep` по трём архитектурным документам — 0 вхождений; в CSR слово `personal_data` встречается **0 раз**). То есть выключатель, который держит весь домен мёртвым, не описан ни в одном документе, который этот домен описывает.

**Следствие для планирования.** Шаги 5–8 миграционного плана строятся поверх слоя, который на пилоте не выполняет ни одной строки. Ноль строк `MemoryEntry` — это не «мало данных», это «путь не проходим», и оба бэкфилла ничего не тронули именно поэтому.

---

## 3. Таблица расхождений

Классы: **НЕТ** — не построено · **МЁРТВО** — построено, не исполняется · **ИНАЧЕ** — построено иначе · **СВЕРХ** — построено сверх документа.

| # | Обещано (документ) | Построено | Класс |
|---|---|---|---|
| 1 | `resolve_context(subject_id, tenant_id, consumer, purpose, ...) -> ContextEnvelope` — единый вход (CRC:56-60, OR-MEM-5 MDC:104-106) | 0 вхождений в репозитории. 5 независимых consumers, 4 ORM-сайта | **НЕТ** |
| 2 | `MemoryProposal` — «единственный путь, которым inference может стать памятью» (MDC:182-201, DEC-0023 п.2) | 1 вхождение — в докстринге. Модели нет | **НЕТ** (контракт сам это признаёт) |
| 3 | `DecisionRecord` (MDC:203-219) | 0 вхождений | **НЕТ** |
| 4 | `tenant_id` в `MemoryEntry` (MDC:129) | Поля нет. `source_tenant_id` — по собственному help_text «Informational — NOT a scoping boundary» | **НЕТ** |
| 5 | `category` из whitelist AYLA-DEC-0023 (MDC:130) | Поля нет. `kind` — UX-группировка («Categorical bucket for UX (Bonuses tab grouping)») | **НЕТ** |
| 6 | Типизированный `value` (`type`/`payload`/`display_text`), «свободный текст как единственная форма запрещён» (MDC:131, DEC-0024 п.1) | `content` — Fernet-шифрованный JSON `{key, value}` без схемы и валидатора; ключ достаётся `content.get("key")` | **НЕТ** |
| 7 | `MemoryCategoryPolicy` — machine-readable registry (DEC-0024 п.7) | Нет. Есть `_KEY_CARDINALITY = {"diet": "single"}` — один ключ | **НЕТ** |
| 8 | Аудит каждого retrieval, включая deny (CRC:166 §4 п.5); события `memory_fact_written` / `memory_fact_used` (CSR §5.7) | 0 строк аудита и 0 лог-строк на чтение зелёного; успешная запись не аудируется. `RedZoneAccessLog.ACCESS_WRITE` объявлен, никогда не создаётся | **НЕТ** |
| 9 | `preference_memory` — CSR-scope для persistent preference write (CSR:439-487, MP шаг 7) | Не является `ConsentType`. Оба вхождения — help_text-прозa на поле `consent_scope` | **НЕТ** |
| 10 | `MemoryAccessLog` (RP §3.3) | Только в документе, 0 в коде | **НЕТ** |
| 11 | Ночные TTL-свипы жёлтой и красной зон (RS §5, §13.2) | Джобы нет. `expires_at` и `ttl_days` не читает ни один запрос | **НЕТ** |
| 12 | Канонический штамп при записи: `status`, `provenance`, `effective_from`, `updated_at`, `expires_at` (MP v1.0.2, MDC §3.1) | Код есть — `memory_writer.py:186-195`. На пилоте не выполняется ни разу (см. §2) | **МЁРТВО** |
| 13 | Yellow/red persistent memory — fail-closed до activation gate (MDC:388-397, OR-MEM-6) | `_check_minor_protection` бросает всегда (`memory_writer.py:74-78`). Вокруг: RLS, `RedZoneAccessLog` 7 лет, 3 CHECK, AST-линтер в CI | **МЁРТВО** (по замыслу — но апарат защищает пустоту) |
| 14 | `RedZoneReader` — «действующий прототип этого контракта» (CRC:219-223) | 0 продовых вызывающих; `MemoryEntryAdmin` существует только внутри докстринга | **МЁРТВО** |
| 15 | 7 полей: `purpose_tags`, `consent_scope`, `evidence_refs`, `derivation_method`, `source_event_id`, `superseded_by`, `supersession_reason` (MDC:134-145) | Колонки есть. **Не пишет никто, не читает никто.** `superseded_by` и `supersession_reason` — по 1 вхождению в репозитории (объявление в `models.py`) | **МЁРТВО** |
| 16 | `source_event_id` — идемпотентность записи (DEC-0024 п.9) | `unique=True` на колонке, которая всегда `NULL` → не гарантирует ничего | **МЁРТВО** |
| 17 | Read-side разрешение конфликтов (MP шаг 1, MDC §6.3) | `memory_key_policy` работает, но подключён к **2 из 5** путей чтения | **МЁРТВО (частично)** |
| 18 | `record_inferred_green_facts` — 147 строк, 10 тестов | 0 продовых вызывающих; **новое с 20.08** — помечен `deprecated` (`memory_inferred.py:3-9`) | **МЁРТВО** (честно объявлено) |
| 19 | Ветка декларированных предпочтений Ayla + `memory_ask.py` | Гейт `memory_green` (`apps/identity/services/personal_context.py:71`) никогда не выдаётся в проде → блок памяти всегда `""` | **МЁРТВО** |
| 20 | `provenance` — «Ровно `user_stated \| user_confirmed_inference`» (MDC:133) | Поле nullable; legacy `source` остался рядом; **DB-констрейнта, связывающего их, нет**. `inferred`/`signal` строки несут `provenance=NULL` — де-факто третье значение | **ИНАЧЕ** |
| 21 | «Иммутабельна: изменение значения — только через supersession, никакого update-in-place» (MDC:117-119) | `promote_zone` делает `save(update_fields=["sensitivity_zone","consent_at"])` (`memory_writer.py:243-248`) и не трогает `updated_at`. `memory_deleter.py:51-61` обновляет на месте и **не выставляет `status`** → soft-deleted строка остаётся `status='active'` | **ИНАЧЕ** |
| 22 | Explicit correction → атомарно `old.superseded` + `new.active` (MDC:265-268, DEC-0024 п.4) | Всегда `.create()`. Старая противоречащая строка остаётся `active` навсегда; противоречие прячется только на чтении и только для 2 из 5 consumers | **ИНАЧЕ** |
| 23 | MP шаг 1 «ВЫПОЛНЕН 2026-08-19», «применён в `memory_block.py` и `memory_surface.py`» (MP:59-64) | На `origin/dev` — 2026-08-21, внутри `4ce069e`; логика лежит в третьем модуле, который оба вызывают | **ИНАЧЕ** (бухгалтерия) |
| 24 | MP v1.0.3: «Step 4 выполнен в beautygo_backend: projection builder, `PersonalContextProposal`, флаги `PERSONAL_CONTEXT_INFERENCE_TARGET` / `PERSONAL_CONTEXT_PROJECTION_SOURCE`, parity-инструмент» (MP:242-250) | В `beautygo_backend` этих строк **нет ни на `origin/dev`, ни на одном remote-ref** (`git grep` по всем `refs/remotes/` — 0). `REPORT_MEMORY_STAGE1_ACTIVATION.md §5` описывает тот же `PersonalContextProposal` как незакоммиченный черновик в брошенном клоне, который «не переносить, заархивировать» | **ИНАЧЕ** — два документа противоречат друг другу |
| 25 | MP шаг 2/3: «миграция обратима», «backfill идемпотентен и ограничен новыми полями» (MP:85-86, 105-107) | Вперёд — да, идемпотентно. Назад — `0016.revert_step2_fields` делает **безфильтровый** `MemoryEntry.objects.update(status=None, effective_from=None, updated_at=None, expires_at=None)` по всей таблице, включая строки, записанные после миграции | **ИНАЧЕ** |
| 26 | Два основания согласия для одной зелёной памяти (зафиксировано аудитом 20.08 как осознанное расхождение; OD-MEM-4 MDC:408 объявил `memory_green` deprecated) | Не изменилось: локальная запись гейтится `PERSONAL_DATA`, чтение деклараций Ayla — `memory_green`. Deprecated-гейт остаётся единственным живым на стороне Ayla | **ИНАЧЕ** |
| 27 | OR-MEM-1: «Параллельный persistent-memory store не создаётся» (MDC:81-83); DEC-0023 п.2: inference сам по себе памятью не становится; CSR §10.1: «no persistent inferred signals» (CSR:1110) | `ClientProfile` (RFM-сегмент, lifecycle, tier, recency, churn_risk, favorite_service — «All fields are **derived**», пересчёт ежедневно) читается `coordinator.load_snapshot` (`coordinator.py:88-104`) в поле `long_term` класса `MemorySnapshot` и инжектится в промпт классификатора (`intent_router.py:390-399`). Без зоны, согласия, provenance, TTL и аудита. В документах домена — не упомянут | **СВЕРХ** |
| 28 | — | Два независимых чтения одной и той же зелёной памяти в одном ходу как два блока промпта: `render_current_personal_context` (`handler.py:809`) и `build_concierge_memory_block` (`handler.py:820`). **У первого нет consent-гейта вообще**, у второго есть | **СВЕРХ** |
| 29 | `apps/consent/memory.py:17-19`: «this module deliberately does NOT introduce ConsentRecord.memory_* types» | `apps/consent/models.py:75-77` определяет `MEMORY_GREEN`, `MEMORY_YELLOW`, `MEMORY_RED`. Докстринг живого гейта — ложный | **СВЕРХ** (молчаливое решение + ложный докстринг) |
| 30 | CSR §10.1: для MVP Phase 1 «persistent memory отключена (**не активирована технически**, не только по политике)» (CSR:1080) | Технически зелёная запись включена по умолчанию (`CONCIERGE_MEMORY_ENABLED=true`, `base.py:1384`); фактически мертва по другой причине (§2). Совпадение с документом — случайное | **ИНАЧЕ** |
| 31 | AST-guard на прямой ORM `MemoryEntry` вне Memory Service (MP шаг 6) | Не срок. Но: `tools/lint/import_boundaries.py` уже содержит аналог для `BookingRequest` (`:236`), а `red_zone_guard.py` в CI ловит только литерал `'red'` и явно разрешает `MemoryEntry.objects.filter(...)` (`:29`) | **НЕТ** (по плану — позже) |

**Счёт:** 31 расхождение — **11 «не построено»**, **8 «построено, не исполняется»**, **9 «построено иначе»**, **3 «построено сверх документа»**.

---

## 4. Существенное — по разделам

### 4.1. Схема есть, писателя нет, читателя нет

Из 29 полей `MemoryEntry` (`apps/identity/models.py:622-988`) enum'ы `STATUS_CHOICES` (`:698-704`), `SUPERSESSION_REASON_CHOICES` (`:712-717`) и `PROVENANCE_CHOICES` (`:727-730`) совпадают с контрактом **дословно**. `confidence` действительно отсутствует — но не потому, что что-то это запрещает, а потому что его никто не добавил.

Единственная строка в проде, которая пишет канонические поля, — `memory_writer.py:186-195`, и она условная:

```python
canonical: dict[str, Any] = {}
if source == MemoryEntry.SOURCE_EXPLICIT:
    write_ts = timezone.now()
    canonical = {
        "status": MemoryEntry.STATUS_ACTIVE,
        "provenance": MemoryEntry.PROVENANCE_USER_STATED,
        "effective_from": write_ts,
        "updated_at": write_ts,
        "expires_at": (write_ts + timedelta(days=ttl_days) if ttl_days is not None else None),
    }
```

Ни один продовый вызывающий не передаёт `ttl_days` → `expires_at` был бы `NULL` даже при живой записи. VERIFIED.

Читателей новых полей — ноль. Единственный запрос к зелёной памяти (`memory_reader.py:99-106`) фильтрует по `soft_deleted_at` и `delete_requested_at`, **не по `status` и не по `expires_at`**, и сортирует по `created_at`. То есть истёкшая запись и заменённая запись видны всем читателям одинаково.

Индексов на `status`, `expires_at`, `provenance`, `superseded_by` нет — будущий «активные записи» или «свип по истечению» пойдут без индекса. VERIFIED.

### 4.2. Иммутабельность и supersession — обещаны, не реализованы, и это уже баг

Контракт (MDC:117-119): «Иммутабельна: изменение значения — только через supersession (§6), никакого update-in-place».

Реальность:

- запись значения — всегда `.create()` (`memory_writer.py:197`), старая строка остаётся `status='active'`, `soft_deleted_at IS NULL`;
- `promote_zone` (`:243-248`) меняет `sensitivity_zone` и `consent_at` **на месте**, `updated_at` не трогает — то есть `updated_at` перестаёт означать «время последнего перехода состояния», как требует MDC:138;
- `memory_deleter.py:51-61` выставляет `delete_requested_at`/`soft_deleted_at`/`deletion_reason` и **не выставляет `status`** → после бэкфилла `0016` удалённая строка остаётся `status='active'`. Ни один CHECK этого не ловит: три существующих констрейнта (`0007_user_personal_context.py:73-108`) не касаются ни одного поля шага 2.

Модуль `memory_key_policy.py:5-7` описывает это открыто и честно:

> «a changed fact (vegan → keto) lands as a NEW live row and the old row stays live until the targeted supersession lifecycle ships»

Read-side фикс закрывает противоречие **для двух из пяти** потребителей. Остальные три показывают оба факта:

- `apps/persona/memory_commands.py:184` — команда «покажи, что знаешь обо мне» отдаёт пользователю и `vegan`, и `keto`;
- `apps/identity/services/privacy.py:380` — выгрузка по 152-ФЗ отдаёт оба;
- write-side дедуп (`memory/personal_context.py:71`, `memory_inferred.py:108`) читает сырьё.

Так что баг из §2.4 замера 20.08 **не закрыт** — он частично замаскирован на двух поверхностях из пяти.

### 4.3. `provenance` — третье значение, которого контракт не допускает

MDC:133 требует ровно два значения. В коде:

- `source` (`models.py:754-760`) остался — non-nullable, indexed, 3 значения. Это осознанное решение: комментарий `models.py:719-724` фиксирует «the legacy `source` field stays legacy runtime metadata and is NOT re-purposed as canonical provenance (owner/architect ruling)»;
- `provenance` (`:936-947`) — nullable, 2 значения, `default=None`;
- **констрейнта между ними нет.** БД допускает `source='inferred' AND provenance='user_stated'`. Инвариант живёт только в фильтре миграции `0018` и в одном `if` в райтере.

Бэкфилл `0018` намеренно оставляет `inferred`/`signal` строки с `provenance=NULL` (правильно по OR-MEM-3: молчаливая конвертация запрещена). Но результат — что `NULL` становится рабочим третьим значением, которого нет в контракте, и никто не обязан его когда-либо разрешить. VERIFIED.

### 4.4. Аудит: удаление видно, чтение и запись — нет

CRC:166 (инвариант 5): «каждый retrieval — audit event, включая deny».

Факт:

| Операция | Аудит |
|---|---|
| Чтение зелёной памяти | **нет** — `memory_reader.py` и `memory_key_policy.py` не импортируют логгер вообще |
| Успешная запись зелёного факта | **нет** — только `logger.info` со счётчиком в `memory/personal_context.py:110` |
| Отказ записи в жёлтую/красную | есть — `RedZoneAccessLog`, `ACCESS_WRITE_REJECTED_DOB` |
| Чтение красной зоны | есть — но 0 продовых вызывающих |
| Удаление / forget-all / экспорт | есть — `memory_deleter.py:64,96`, `privacy.py:399,531` |

Аудитор по 152-ФЗ на вопрос «когда Ayla читала память этого человека» получает **ничего**. Это не «ещё не сделали шаг 5» — это уже действующий разрыв: память читается в проде на каждом ходу (`handler.py:809,820`), а следа нет.

### 4.5. `ClientProfile` как вторая память — самое дорогое и самое незамеченное

Это единственная находка, которой нет ни в замере от 20.08, ни в одном из документов домена.

`apps/identity/models.py:359-362`:

> «Computed RFM/LTV/risk/tier snapshot per bot_user … All fields are **derived** by services in `apps.identity.services` … the row is recomputed daily by `recompute_profiles_daily` (P7) + on `booking_completed` signal (P8).»

`apps/orchestrator/memory/coordinator.py:88-104` кладёт из него `rfm_segment`, `lifecycle_stage`, `loyalty_tier`, `recency_days`, `frequency_visits`, `churn_risk`, `favorite_service_id` в поле `long_term` структуры `MemorySnapshot`. `apps/orchestrator/intent_router.py:390-399` превращает это в строки промпта:

```python
if long_term.get("rfm_segment"):
    context_hints.append(f"customer segment: {long_term['rfm_segment']}")
if long_term.get("lifecycle_stage"):
    context_hints.append(f"lifecycle: {long_term['lifecycle_stage']}")
if long_term.get("loyalty_tier"):
    context_hints.append(f"tier: {long_term['loyalty_tier']}")
```

Что это нарушает:

- **OR-MEM-1** (MDC:81-83): «Параллельный persistent-memory store не создаётся». Он создан — persistent, выведенный, ежедневно пересчитываемый, и используется как контекст модели.
- **AYLA-DEC-0023 п.2** (DL:988-995): «Inference никогда не становится persistent memory самостоятельно». Здесь inference и есть содержимое, и оно доезжает до модели без confirmation.
- **CSR §10.1** (CSR:1110): режим MVP Phase 1 включает `no persistent inferred signals`. Сигналы есть и работают.
- **MDC §12 / OR-MEM-6**: жёлто-красный gate требует одновременно authorization, consent, sensitivity gate, audit. `ClientProfile` не проходит ни один из четырёх, потому что он вообще вне контура.

Формально можно возразить, что `ClientProfile` — не `MemoryEntry` и не Memory Fact. Ровно так же формально можно возразить про `NutritionProfile` — и MDC §11 (:377-386) специально выносит его в отдельный Privacy/Legal perimeter именно потому, что «не Memory Fact» не означает «не проблема». `ClientProfile` такого разбора не получил вообще: он не упомянут ни в MDC, ни в CRC, ни в CSR, ни в замере 20.08.

Stage 1 shadow добавил к этому вторую точку потребления: `shadow_turn.py:201` тоже зовёт `load_snapshot` и тоже отправляет снимок в LLM.

### 4.6. Единого входа нет, и разброс шире, чем зафиксировано документом

CRC §8 (:239-252) перечисляет **четыре** legacy read paths. Фактически потребителей зелёной памяти **пять**, и деление проходит не там:

| Потребитель | Через key-policy | Consent-гейт |
|---|---|---|
| `persona/memory_surface.render_current_personal_context` (`handler.py:809`) | да | **нет** |
| `orchestrator/memory_block.build_concierge_memory_block` (`handler.py:820`) | да | да (`memory_block.py:115`) |
| `persona/memory_commands` (`handler.py:761`) | нет | — |
| `identity/services/privacy` (экспорт + каскад) | нет | — |
| write-side дедуп (`memory/personal_context.py:71`, `memory_inferred.py:108`) | нет | — |

То есть в одном ходу зелёная память читается **дважды**, инжектится в промпт **двумя разными блоками**, и один из двух блоков не гейтится согласием вообще. Этого нет ни в одном документе.

`purpose` не принимает ни один путь. `purpose_tags` в БД есть, help_text прямо признаёт: «Resolver enforcement is a later step — not implemented here».

### 4.7. Документы, которые расходятся друг с другом

1. **MP v1.0.3 против REPORT_MEMORY_STAGE1_ACTIVATION §5.** Первый: «Step 4 … выполнен в beautygo_backend». Второй: тот же `PersonalContextProposal` — «незакоммиченные правки … архитектурно превзойдён … не переносить, заархивировать». В `beautygo_backend` нет ни одного из трёх маркеров Step 4 ни на одном remote-ref. VERIFIED. Итог: шаг 4 в плане помечен выполненным, кода нет в общей линии, а параллельный отчёт называет тот же код мусором.
2. **RS против MDC.** Репозиторный спек `docs/specs/memory-entry-schema.md` (§6) до сих пор объявляет `source` = `explicit|inferred|signal` каноническим provenance, TTL-свипы жёлтой и красной зон и `minor_lock`-джоб. Канон с 20.08 говорит другое. Спек не помечен устаревшим.
3. **RP против DEC-0023.** Репозиторная политика `ayla-memory-and-personalization.md` §4 описывает «3 источника данных с весами»: behavioral patterns ~50%, contextual signals ~20% — прямое противоречие DEC-0023 п.2. Тоже не помечена устаревшей. И обещает `MemoryAccessLog`, которого нет.
4. **Ложный докстринг в живом гейте:** `apps/consent/memory.py:17-19` против `apps/consent/models.py:75-77`.

---

## 5. Что доработать — по цене бездействия

Порядок — по тому, что дороже стоит не сделать, а не по объёму.

### 5.1. Решить, включается ли память на пилоте, и как — 1 строка кода, но решение не инженерное

**Цена бездействия: полная.** Пока `GLOBAL_BOT_ONBOARDING=false`, весь домен памяти — мёртвый груз: 4 миграции, 130 строк key-policy, ~930 строк тестов, канонический контракт и план миграции описывают систему, которая не выполняет ни одной строки. Любой следующий шаг плана (5, 6, 7, 8) будет написан вслепую и провалидирован только тестами.

Что сделать: либо снять флаг с freeze и включить онбординг на пилоте (тогда появляются согласия и появляются строки), либо явно записать, что персистентная память на пилоте не проверяется, и **остановить шаги 5–8** до того момента. Третьего не дано: делать шаги 5–8 поверх нуля строк — ровно та ошибка, которую замер 20.08 назвал главной.

### 5.2. Вынести `ClientProfile` в контур — или явно вывести из него письменным решением

**Цена бездействия: приватность и репутация, уже сегодня.** Выведенный профиль (сегмент, риск оттока, уровень лояльности) уходит в промпт LLM на каждом ходу — без зоны, согласия, TTL и аудита. Это работает **сейчас**, на живом пилоте, в отличие от `MemoryEntry`. При проверке по 152-ФЗ вопрос «на каком основании модель знает, что клиент в группе оттока» ответа не имеет.

Что сделать: одна страница — классификация, основание, retention, аудит; либо решение «в промпт не идёт» и снятие трёх строк из `intent_router.py:394-399`. Аналог уже отработан для `NutritionProfile` (MDC §11) — повторить механику.

### 5.3. Закрыть противоречие на трёх непокрытых поверхностях чтения

**Цена бездействия: пользователь видит бессмыслицу, регулятор получает неверную выгрузку.** «Покажи, что знаешь обо мне» отдаёт `vegan` и `keto` одновременно; выгрузка по 152-ФЗ — тоже. Промпт при этом видит одно значение. Это несогласованность между тем, что система показывает человеку, и тем, чем она пользуется.

Что сделать: провести `memory_commands.py:184` и `privacy.py:380` через `read_current_view` — тот же вызов, что уже используют два других пути. Малый диф, немедленный эффект. (Для экспорта по 152-ФЗ — решить отдельно: возможно, там как раз надо отдавать всё, но тогда с пометкой актуальности.)

### 5.4. Довести шаг 3.5 до конца: `status` при удалении и `updated_at` при переходах

**Цена бездействия: данные врут о самих себе, и это накапливается.** Каждое удаление после `0016` создаёт строку `status='active' AND soft_deleted_at IS NOT NULL`. Каждый `promote_zone` меняет состояние, не двигая `updated_at`. Как только шаг 5 начнёт фильтровать по `status`, удалённые записи вернутся в выдачу.

Что сделать: `memory_deleter` выставляет `status` в той же `update()`; `promote_zone` двигает `updated_at`; CHECK-констрейнт `status='deleted' ⇔ soft_deleted_at IS NOT NULL`. Плюс констрейнт `provenance IS NOT NULL WHEN source='explicit'` — сейчас инвариант держится на одном `if`.

### 5.5. Починить обратную функцию `0016`

**Цена бездействия: один откат стирает поля у всех строк.** `revert_step2_fields` — безфильтровый `update(status=None, effective_from=None, updated_at=None, expires_at=None)` по всей таблице. Сегодня безопасно (0 строк), завтра — нет. `0018` reverse имеет тот же характер и сам это документирует (`:17-23`).

Что сделать: ограничить reverse теми же условиями, что и forward, либо пометить миграции `irreversible` и убрать иллюзию обратимости из MP:85-86.

### 5.6. Аудит на чтение зелёной зоны

**Цена бездействия: разрыв в 152-ФЗ, который не закрывается задним числом.** Логи, которых не было, не появятся. Каждый день без аудита — день, за который нельзя отчитаться.

Что сделать: минимальная структурированная строка (subject, consumer, correlation_id, счётчик фактов, без значений) в двух точках `handler.py:809,820`. Это не требует resolver'а и не блокируется ничем.

### 5.7. Синхронизировать репозиторные документы или пометить устаревшими

**Цена бездействия: следующий исполнитель прочитает `docs/specs/memory-entry-schema.md` и напишет код по `source`, а не по `provenance`.** Это уже произошло однажды: `apps/consent/memory.py` описывает систему, которой нет в `apps/consent/models.py`.

Что сделать: шапка «superseded by Ayla Memory Domain Contract v1.0 (AYLA-DEC-0081)» в RS и RP; исправить докстринг `consent/memory.py:17-19`.

### 5.8. Привести MP в соответствие с фактом

**Цена бездействия: план перестаёт быть инструментом.** Шаг 1 помечен выполненным на дату, на два дня расходящуюся с историей; шаг 4 помечен выполненным при отсутствии кода в общей линии и при наличии отчёта, называющего тот же код черновиком под архив.

Что сделать: перепроверить bookkeeping-заметки v1.0.1–v1.0.3 по `git log` обоих репозиториев и переписать шаг 4 как **не выполненный**.

---

## 6. Что упирается в решение владельца, а не в работу

| # | Решение | Почему это не инженерный вопрос | Что заблокировано |
|---|---|---|---|
| 1 | **Включать ли онбординг (`GLOBAL_BOT_ONBOARDING`) на пилоте** | Это продуктовое обещание пользователю (152-ФЗ экран согласия) и снятие freeze-правила. Инженерно — одна переменная | Всё. Шаги 5–8 плана, любая валидация памяти на живых данных, memory-first продуктовая гипотеза (AYLA-DEC-0018 / CSR §10.3) |
| 2 | **Судьба `ClientProfile` как AI-контекста** | Вопрос приватности и продуктового обещания «Ayla не додумывает за вас», а не рефакторинга | Соответствие OR-MEM-1, DEC-0023 п.2 и CSR §10.1 на живом пилоте |
| 3 | **Тексты согласия `preference_memory`** — Legal/Product input, MP §3 фиксирует как внешний блокер | Формулировки нельзя придумать в коде | Шаги 7–8; переход с `personal_data`/`memory_green` на purpose-модель |
| 4 | **Age lookup #597** — Engineering + owner acceptance (MP §3) | Требует эндпоинта в Ayla и принятия риска | Шаг 9. Пока — жёлтая и красная зоны недоступны, и это по контракту норма |
| 5 | **`NutritionProfile` Privacy/Legal perimeter** (OD-MEM-2, MDC:377-386) | Классификация, шифрование, retention — юридическое решение | Снятие запрета «sensitive nutrition/health запрещено передавать как persistent AI context». **UNKNOWN:** не проверялось этим заходом, соблюдается ли запрет — в `beautygo_backend` есть `nutrition/services/cross_domain_engine.py` и слот `extra_hint`, ведущий в системный промпт |
| 6 | **Три security-коммита из брошенного клона `djangoproject`** (VK/Yandex login, secret-guards, env strictness) — открытый пункт из `REPORT_MEMORY_STAGE1_ACTIVATION §5` | Переносить или списать | Не память, но висит с 21.08 |
| 7 | **`DEV_SSH_KEY` и мёртвый auto-trigger `deploy-dev`** (там же, §6) | Operator/owner | Любая выкладка домена памяти идёт вручную через bundle+scp |

---

## 7. Где замер от 20.08 был неточен

Опровержения важнее подтверждений — вот всё, в чём предыдущий документ и постановка задачи разошлись с кодом.

1. **«Единственный живой путь записи — три регулярки» (AUDIT_MEMORY_DOMAIN §2.3).** Путь не живой. Он закрыт двумя гейтами выше: `can_store_green_memory` → `PERSONAL_DATA` → `GLOBAL_BOT_ONBOARDING=false`. На пилоте регулярки не выполняются ни разу. Ноль строк `MemoryEntry` — следствие этого, а не отсутствия веганов.
2. **«Четыре независимых пути чтения» (§4).** Их пять, и делятся они не по репозиториям, а по тому, проходят ли через key-policy и есть ли consent-гейт. Один из двух путей, инжектящих память в промпт, не гейтится согласием вообще.
3. **«Противоречия накапливаются — действующий баг» (§2.4)** — верно, но замер не отметил, что write path не менялся и не планировался к изменению на шаге 1: фикс сознательно read-side, и он покрывает 2 поверхности из 5.
4. **`ClientProfile` в §4 описан нейтрально** («coordinator … про `MemoryEntry` не знает вообще»). Это не пробел покрытия, а второй persistent-memory store, работающий в проде и запрещённый OR-MEM-1. Самая дорогая находка этого захода.
5. **Постановка: «что приехало 21-22.08 … плюс `live-shadow-stage1`».** Shadow Stage 1 к домену памяти не относится: `MemoryEntry` не читается и не пишется, миграций и моделей нет. Считать его частью памяти — переоценивать прогресс.
6. **Постановка: «Shadow Stage 1 side-effect-free».** Для БД — да, и это покрыто тестом. Но каждый сэмплированный ход делает реальный LLM-вызов, а отсутствие записи в БД держится только на `SURFACES=global`.
7. **Постановка: «домен построен основательно».** Форма — да. Но `tenant_id`, `category` и типизированный `value` отсутствуют не как переименования, а по существу; `content` — нешифруемо-невалидируемый `{key, value}`; `source_event_id` уникален на всегда-`NULL` колонке; TTL-свипов нет вовсе, `expires_at` не читает никто. «Основательно» относится к зонам, шифрованию, RLS и журналу красной зоны — то есть к той трети, которая защищает пустую таблицу.

---

## 8. Что проверялось и что нет

**VERIFIED кодом:** состав `MemoryEntry` и все enum'ы; миграции 0015–0018 включая обратные функции; `memory_writer` целиком; цепочка достижимости записи от вебхука до `.create()`; все продовые чтения `MemoryEntry`; `memory_key_policy`; consent-типы и точки выдачи; отсутствие `resolve_context`, `MemoryProposal`, `DecisionRecord`, `preference_memory`, `MemoryAccessLog`; shadow-путь и его конфиг; AST-линтеры и их место в CI; отсутствие Step 4 во всех remote-ref `beautygo_backend`.

**INFERRED:** причина нуля строк на пилоте (код + известное состояние согласий на 20.08); что `GLOBAL_BOT_ONBOARDING` на пилотном box стоит в default.

**UNKNOWN:** фактические значения env на пилотном box (проверялись только дефолты репозитория); соблюдается ли запрет OD-MEM-2 на передачу sensitive nutrition/health в модельный контекст (`nutrition/services/cross_domain_engine.py`, слот `extra_hint` → `render_system_prompt`); состояние рабочих деревьев других исполнителей (не проверялись по условию задачи).
