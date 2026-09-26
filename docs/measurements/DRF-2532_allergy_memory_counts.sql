-- DRF-2532 — что уже лежит в постоянной памяти: только количества.
--
-- Дерево: ai-bot-platform origin/dev 0a0dd57f. Прогоняет главное окно;
-- автор замера на стенд не ходит. Все запросы ТОЛЬКО ЧИТАЮТ: одна
-- транзакция READ ONLY, в конце ROLLBACK. Ни имён, ни текстов, ни id —
-- только зоны, kind, признаки согласия/удаления и числа.
--
-- ПРИЗНАК ОТБОРА — kind, а НЕ текст. Почему так и в чём предел:
--
-- * `content` зашифрован Fernet'ом (EncryptedJSONField, ADR-0006). В базе
--   нет ни «аллергии», ни «орехов» — подстрокой ловить нечего. Текстовый
--   признак возможен только расшифровкой в приложении, а shell/ORM на
--   стенде запрещены. Значит, текст этим SQL не меряется ВООБЩЕ.
-- * Поэтому признак — классификация, которую сделал писатель: kind из
--   KIND_CHOICES (apps/identity/models.py:861). Кандидаты в медицинское:
--   'contraindication' и 'symptom'. Остальные ('preference', 'lifestyle',
--   'other', …) — вне признака.
-- * ПРЕДЕЛ, который обязан стоять рядом с каждым числом: «мне нельзя
--   орехи, задыхаюсь», записанное писателем как kind='preference' или
--   'other', в число НЕ попадёт. Число по kind — нижняя граница
--   медицинских фактов, а не их полный счёт. Насколько она низкая,
--   скажет только замер писателей (кто какой kind ставит), не SQL.
--
-- ПРЕДЕЛ ПО ЗОНЕ — КРАСНАЯ ЗОНА ЭТИМ SQL НЕ МЕРЯЕТСЯ, ПО ПОСТРОЕНИЮ:
--
-- * migration 0008: RLS-политика memory_entry_non_red_visible (RESTRICTIVE,
--   FORCE) прячет red-строки без GUC ayla.red_zone_access_context. Обход
--   даёт НОЛЬ строк, а не ошибку. Роль ayla_ops к таблице не допущена
--   вовсе — только представление memory_entry_safe, где red исключены.
-- * Поэтому все запросы ниже идут через memory_entry_safe и честно
--   называются «зелёные и жёлтые». Ноль красных здесь ничего не значит.
-- * Счёт красных возможен только под GUC (RedZoneReader / break-glass по
--   runbook с журналом). Это обход защитного слоя, и решение о нём — не
--   автора замера. Блока с GUC здесь НЕТ намеренно.
--
-- Колонки представления: без status, provenance, purpose_tags (они
-- добавлены позже 0008 и во view не попали) — поэтому «живая» строка
-- здесь = soft_deleted_at IS NULL AND delete_requested_at IS NULL.

BEGIN TRANSACTION READ ONLY;

-- 0. Кто смотрит и что видит. Без этой строки остальные числа не читаются.
SELECT
    current_user                                                   AS role,
    has_table_privilege('identity_memoryentry', 'SELECT')          AS base_table_select,
    has_table_privilege('memory_entry_safe', 'SELECT')             AS safe_view_select,
    coalesce(current_setting('ayla.red_zone_access_context', true), '') <> ''
                                                                   AS red_guc_set;

-- 1. Вся видимая память: зона × kind × живая/удалённая.
SELECT
    sensitivity_zone,
    kind,
    (soft_deleted_at IS NULL AND delete_requested_at IS NULL)      AS live,
    count(*)                                                       AS rows
FROM memory_entry_safe
GROUP BY 1, 2, 3
ORDER BY 1, 2, 3;

-- 2. Признак отбора: kind IN ('contraindication','symptom') — по зонам,
--    с согласием и без, живые. Главный вопрос листа: лежит ли медицинское
--    в ЗЕЛЁНОЙ зоне (там согласие не требуется и констрейнт молчит).
SELECT
    kind,
    sensitivity_zone,
    (consent_at IS NOT NULL)                                       AS has_consent,
    source,
    count(*)                                                       AS live_rows,
    count(DISTINCT user_id)                                        AS people
FROM memory_entry_safe
WHERE kind IN ('contraindication', 'symptom')
  AND soft_deleted_at IS NULL
  AND delete_requested_at IS NULL
GROUP BY 1, 2, 3, 4
ORDER BY 1, 2, 3, 4;

-- 3. Проверка констрейнта на живых данных (обязана дать 0): жёлтая строка
--    без согласия и без надгробия. Не 0 — значит, CHECK на стенде снят
--    или NOT VALID; это важнее всех остальных чисел.
SELECT count(*) AS yellow_without_consent_or_tombstone
FROM memory_entry_safe
WHERE sensitivity_zone = 'yellow'
  AND consent_at IS NULL
  AND soft_deleted_at IS NULL;

-- 4. Состояние самого констрейнта в каталоге базы (validated или нет).
SELECT conname, convalidated, pg_get_constraintdef(oid) AS definition
FROM pg_constraint
WHERE conrelid = 'identity_memoryentry'::regclass
  AND conname = 'memory_entry_yellow_red_requires_consent';

ROLLBACK;
