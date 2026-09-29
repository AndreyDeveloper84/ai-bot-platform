# ДРАФТ — Registry amendment: scope `wellness_observation` (В-1, persistent body observations)

**Подготовлено окном goals 25.08.2026.** Дочерняя задача эпика DRF-1331
(ca8736b0). Требует **Privacy/Legal review**; окно одобрения не выдаёт.
По образцу `docs/CONSENT_EXCEPTION_DRAFT.md`: запись не вносится в Registry
до одобрения, ни одна строка канона этим документом не изменена.

**Основания:** В-1 (`docs/OD_CARE_CONTRACT_RULINGS.md`), GOALS-R1/R5/R6
(`docs/OD_GOALS_RULINGS.md`), release gate владельца:
`schema ready → code deployed fail-closed → Privacy/Legal + Registry →
consent integration verified → writes enabled`.

---

## 1. Каноническое требование

`Consent Scope Registry.md` §4: `health_related_signal` — чувствительность
special/high, persistent storage **«Вне MVP; запрещено»**. §5.7
`preference_memory` — единственный persistent-write scope MVP — прямо
запрещает эту категорию. §3: wildcard-scopes запрещены.

Проверено окном 25.08 по §5.1–§5.7: **persistent write наблюдений тела не
покрывает ни один существующий scope.** Обхода нет: основание нельзя выбрать,
его нужно зарегистрировать (SPEC_CARE_CONTRACT О-5).

## 2. Что предлагается зарегистрировать

Новый узкий scope **`wellness_observation`** — persistent хранение
**явно сообщённых пользователем** наблюдений о теле (user-stated), для
сопровождения заявленного им же Desired Outcome.

Соседний scope `goal_memory` (GOALS-R6) покрывает **намерение**; этот —
**наблюдения**. Два scope, два гейта (`goal_intention_gate` / Gate O):
намерение и факт о теле — разные классы данных (GOALS-R5 §1–2), и сводить
их под один scope значит повторить схлопывание, которое владелец развёл.

## 3. Проект записи scope (формат §5 Registry)

```yaml
scope_id: wellness_observation
scope_version: "1.0"
purpose: >
  Хранить явно сообщённые пользователем наблюдения о фактическом состоянии
  тела (точки временного ряда) для сопровождения его Desired Outcomes.

allowed_data:
  - body_observation_user_stated   # WEIGHT (numeric, kg);
                                   # SELF_ASSESSMENT (ordinal по versioned
                                   # instrument, первый: NOTICEABILITY_0_3_V1)

permissions:
  - consumer: beautygo_backend      # wellness bounded context (В-2)
    operations: [read, write, delete]
    data_categories: [body_observation_user_stated]
    condition: через Gate O с типизированной ConsentAttestation
               (authority + provenance), purpose=processing
  - consumer: ayla-ai-core
    operations: [model_transfer]    # без read/write/delete — как §5.7
    data_categories: [body_observation_user_stated]

persistent_context: >
  Это scope persistent write наблюдений тела. Raw wellness records не
  становятся памятью (AMD-020:130): запись в MemoryEntry запрещена.

prohibited:
  - origin != user_stated            # measured/device, external, inferred,
                                     # derived — до отдельного owner approval
                                     # на пару (тип × origin × instrument)
  - symptom                          # до решения по DRF-1295
  - health/behavioral inference из ряда (включая любую связь натрий↔отёчность,
    GOALS-R5 §7: система её не создаёт)
  - использование для рекламы
  - read значений проактивным слоем  # повод читается, значение — нет
                                     # (SPEC §1.3, «кто читает»)

authorization_basis: explicit_consent   # обязателен для любой write (по §5.7)
consent_requirement: >
  Отдельное явное согласие. Отработанная в коде форма основания для данных
  этого класса: PERSONAL_DATA И HEALTH (прецедент
  ai-bot-platform/apps/orchestrator/nutrition_context.py:187, 152-ФЗ ст. 10).
  ConsentType.HEALTH объявлен (apps/consent/models.py:70) — технический
  reuse candidate, НЕ правовое основание; текст согласия и document_version
  требуют юридической проверки.
validity: До отзыва или MAJOR-изменения scope (§7).
revocation_effect: >
  Отзыв немедленно прекращает processing: наблюдения не пишутся и не
  используются для Progress / Recommendation до нового разрешения.
  Отзыв НЕ блокирует inspect / export / correction / deletion уже
  сохранённого — гейт защищает processing, не права субъекта
  (amendment C, GOALS). Удаление аннулирует производный прогресс
  автоматически (прогресс — производная, не сущность).
audit_events:  # именование — по Ayla Domain Event Registry (§9.1)
  - consent_record_checked
  - wellness_observation_written      # payload: код типа и instrument,
                                      # НЕ значение (прецедент reason code,
                                      # AUDIT_CARE_RUNTIME:802-808)
  - wellness_observation_read
  - wellness_observation_deleted
scope_owner: Wellness Domain (AMD-020, Raw Wellness History) + Privacy Owner
mvp_status: blocked
blocking_reason: Privacy/Legal approval текста согласия и document_version
```

## 4. Компенсирующие меры, уже построенные в коде (факт, не аргумент)

Каркас `wellness` смигрирован **fail-closed** (коммит `b4d0ac72`, ветка
`feat/wellness-goals-framework`):

- write-path отказывает всегда, даже с валидной attestation — trusted
  source не подключён; bypass/feature-flag отсутствуют как класс (GO);
- `origin` имеет единственное значение `user_stated` — выводимое наблюдение
  невозможно записать физически;
- CI-проверка `test_fail_closed.py` краснеет при любом ослаблении;
- значения не попадают в аудит/логи — только коды.

## 5. Что требуется от Privacy/Legal

**Первое.** Достаточно ли формы PERSONAL_DATA + HEALTH (152-ФЗ ст. 9 + ст. 10)
для user-stated наблюдений тела, или нужен отдельный тип согласия/текст.

**Второе.** Текст согласия и `document_version` для `wellness_observation`.

**Третье.** Retention для ряда наблюдений и для superseded-строк (исправление
оставляет историю — «удалить стирает, исправить оставляет»; приемлемо ли).

**Четвёртое.** Подтверждение, что revocation effect разделён верно:
processing прекращается, права субъекта не блокируются.

## 6. Что окно НЕ утверждает

- Что отклонение/новый scope безвреден — это оценка Privacy/Legal.
- Что компенсирующих мер достаточно — они перечислены как факт.
- Что scope будет одобрен до пилота — иначе writes остаются выключенными,
  и это рабочее состояние, а не сбой.

## Приложение — источники

| Утверждение | Файл | Место |
|---|---|---|
| В-1 дословно | `docs/OD_CARE_CONTRACT_RULINGS.md` | §2 |
| GOALS-R1/R5/R6, release gate, запрет bypass | `docs/OD_GOALS_RULINGS.md` | целиком |
| health_related_signal запрещено; preference_memory не подходит | Consent Scope Registry | §4, §5.7 |
| Формат записи scope | там же | §5.7 |
| Raw Wellness History: владелец, «не память» | AMD-020 Pilot Scope Registry | :118-130 |
| PERSONAL_DATA + HEALTH, 152-ФЗ ст. 10 | `ai-bot-platform/apps/orchestrator/nutrition_context.py` | :187 |
| Образец подачи | `docs/CONSENT_EXCEPTION_DRAFT.md` | целиком |
