-- DRF-2513 · отметка «ДО» для моста уходящей памяти (DRF-2511)
--
-- ЧТО ЭТО. Конструкционный пол замера: население, связанность личности,
-- состояние согласия и живые зелёные факты с разрезом по происхождению.
-- Только чтение, только количества. Ни одного персонального значения:
-- ни текстов, ни имён, ни идентификаторов живых людей.
--
-- ЧЕГО ЗДЕСЬ НЕТ, И ПОЧЕМУ ЭТО НЕ ЛЕНЬ (см. §«Пределы» в отчёте):
--   * числителя «сколько доехало до подсказки» — он считается сборкой блока:
--     гейт согласия + REST к Ayla за заявленными предпочтениями + РАСШИФРОВКА
--     `content` + разрешение конфликтов по ключу + рендер ai-core. В SQL
--     ничего из этого нет;
--   * разреза «отсеяно правилом» — правило отбирает по КЛЮЧУ факта, а ключ
--     лежит внутри `content`, зашифрованного на диске (`encrypt(...)` в
--     `apps/identity/models.py`). Прочитать его SQL не может физически;
--   * разреза «сбой при сборке» — сбой существует только строкой журнала
--     (`channels.max.global.memory_block_failed`), в базу не пишется;
--   * короткой памяти — она в Redis, не в Postgres.
--
-- ПОЧЕМУ ЭТОТ ПОЛ ВСЁ РАВНО НУЖЕН. Мост (DRF-2511) пишет НОВЫЕ строки
-- `MemoryEntry` с `source='inferred'`. Значит разрез по `source` — прямая
-- отметка «до»: после моста доля `inferred` вырастет. И это единственная
-- часть замера, которую можно снять, не заходя в прикладной слой.
--
-- КАК ЗАПУСКАТЬ. Целиком, одним куском, под транзакцией только для чтения.
-- Запись невозможна физически, а не по договорённости:
--
--     BEGIN READ ONLY;
--     \i DRF-2513-before-sql.sql
--     ROLLBACK;
--
-- ОХВАТ. Окно активности задаётся один раз в `window_spec` и печатается в
-- каждой строке вывода: число без охвата процитировать нельзя.

WITH window_spec AS (
    -- Внешний признак отбора: активность за период. НЕ по содержанию памяти —
    -- иначе «после» померится на другом множестве, потому что мост меняет
    -- ровно содержание.
    SELECT
        now() AT TIME ZONE 'UTC'                      AS measured_at,
        (now() - interval '30 days') AT TIME ZONE 'UTC' AS window_start
),

-- Человек — это `ayla_user_id`, а не строка личности. У одного человека
-- бывает несколько строк (уже ловили шесть на одного), и считать по строкам
-- значило бы получить верное число про неверный предмет.
people AS (
    SELECT DISTINCT bu.ayla_user_id AS person
    FROM identity_botuser bu, window_spec w
    WHERE bu.deleted_at IS NULL
      AND bu.ayla_user_id IS NOT NULL
      AND bu.last_seen >= w.window_start
),

-- Несвязанные личности считаются СТРОКАМИ, и это не небрежность: человека
-- без `ayla_user_id` нельзя свести к одному, потому что сводить не по чему.
unlinked AS (
    SELECT count(*) AS identity_rows
    FROM identity_botuser bu, window_spec w
    WHERE bu.deleted_at IS NULL
      AND bu.ayla_user_id IS NULL
      AND bu.last_seen >= w.window_start
),

consent_open AS (
    SELECT DISTINCT bu.ayla_user_id AS person, cr.consent_type
    FROM identity_botuser bu
    JOIN consent_consentrecord cr ON cr.bot_user_id = bu.id
    JOIN window_spec w ON TRUE
    WHERE bu.deleted_at IS NULL
      AND bu.ayla_user_id IS NOT NULL
      AND bu.last_seen >= w.window_start
      AND cr.granted IS TRUE
      AND cr.withdrawn_at IS NULL
      AND cr.consent_type IN ('personal_data', 'memory_green')
),

-- Живые зелёные факты: тот же отбор, что у `read_green_entries`, плюс тот же
-- «замок удаления», что у `deletion_gate` — заявка на удаление закрывает
-- человека целиком.
live_green AS (
    SELECT me.user_id AS person, me.source, me.kind
    FROM identity_memoryentry me
    JOIN identity_userpersonalcontext upc ON upc.user_id = me.user_id
    JOIN people p ON p.person = me.user_id
    WHERE me.sensitivity_zone = 'green'
      AND me.soft_deleted_at IS NULL
      AND me.delete_requested_at IS NULL
      AND upc.deletion_requested_at IS NULL
)

SELECT
    w.measured_at,
    w.window_start,
    'КТО СНЯЛ (роль в базе) и КОГДА' AS metric,
    current_user || ' @ ' || to_char(w.measured_at, 'YYYY-MM-DD HH24:MI:SS') || ' UTC' AS value
FROM window_spec w

UNION ALL
-- ОТМЕТКА «ДО» ДЛЯ МОСТА. `source_event_id` — ключ идемпотентности §3.1;
-- сегодняшний писатель его НИКОГДА не ставит, и это записано в коде
-- (`memory_writer.py`: «source_event_id / evidence_refs … are never fabricated
-- here»). Мост обязан его ставить — идемпотентность у него в требованиях.
-- Значит здесь ожидается НОЛЬ, и это единственная величина замера, которую
-- нельзя восстановить задним числом: после моста она станет счётчиком его
-- урожая. Если тут НЕ ноль — значит кто-то уже пишет событийный ключ, и
-- маркер надо выбирать заново, а не списывать разницу на мост.
SELECT w.measured_at, w.window_start,
       'ОТМЕТКА «ДО»: зелёных фактов с source_event_id (ожидается 0)',
       (
           SELECT count(*) FROM identity_memoryentry me
           WHERE me.sensitivity_zone = 'green'
             AND me.soft_deleted_at IS NULL
             AND me.source_event_id IS NOT NULL
       )::text
FROM window_spec w

UNION ALL
-- Спутник того же маркера: `evidence_refs` документирован как «ссылки на
-- наблюдения, из которых факт подтверждён» — тоже пусто сегодня.
SELECT w.measured_at, w.window_start,
       'ОТМЕТКА «ДО»: зелёных фактов с непустым evidence_refs (ожидается 0)',
       (
           SELECT count(*) FROM identity_memoryentry me
           WHERE me.sensitivity_zone = 'green'
             AND me.soft_deleted_at IS NULL
             AND me.evidence_refs IS NOT NULL
             AND me.evidence_refs::text NOT IN ('[]', 'null', '{}')
       )::text
FROM window_spec w

UNION ALL
-- Надгробия: сколько фактов СТЁРТО по просьбе человека. Рядом с отметкой «до»
-- это не любопытство — мост просматривает старые сообщения, и если он не
-- смотрит надгробие ключа, стёртое вернётся. Число «до» даёт масштаб риска.
SELECT w.measured_at, w.window_start,
       'надгробий: стёрто по просьбе человека, причина=' || COALESCE(me.deletion_reason, '<не указана>'),
       count(*)::text
FROM identity_memoryentry me, window_spec w
WHERE me.sensitivity_zone = 'green'
  AND me.soft_deleted_at IS NOT NULL
GROUP BY w.measured_at, w.window_start, me.deletion_reason

UNION ALL
SELECT
    w.measured_at,
    w.window_start,
    'люди, активные за окно (по человеку, не по строке личности)' AS metric,
    (SELECT count(*) FROM people)::text                          AS value
FROM window_spec w

UNION ALL
SELECT w.measured_at, w.window_start,
       'строки личности БЕЗ связи с человеком (свести не по чему)',
       (SELECT identity_rows FROM unlinked)::text
FROM window_spec w

UNION ALL
SELECT w.measured_at, w.window_start,
       'из них с открытым personal_data (основание зелёной памяти)',
       (SELECT count(DISTINCT person) FROM consent_open WHERE consent_type = 'personal_data')::text
FROM window_spec w

UNION ALL
SELECT w.measured_at, w.window_start,
       'из них с открытым memory_green (основание заявленных предпочтений)',
       (SELECT count(DISTINCT person) FROM consent_open WHERE consent_type = 'memory_green')::text
FROM window_spec w

UNION ALL
SELECT w.measured_at, w.window_start,
       'людей с хотя бы одним живым зелёным фактом',
       (SELECT count(DISTINCT person) FROM live_green)::text
FROM window_spec w

UNION ALL
SELECT w.measured_at, w.window_start,
       'живых зелёных фактов всего (ЗНАМЕНАТЕЛЬ локальной стороны)',
       (SELECT count(*) FROM live_green)::text
FROM window_spec w

UNION ALL
-- Разрез по происхождению — прямая отметка «до» для моста: он пишет `inferred`.
SELECT w.measured_at, w.window_start,
       'живых зелёных фактов, происхождение=' || lg.source,
       count(*)::text
FROM live_green lg, window_spec w
GROUP BY w.measured_at, w.window_start, lg.source

UNION ALL
SELECT w.measured_at, w.window_start,
       'живых зелёных фактов, род=' || lg.kind,
       count(*)::text
FROM live_green lg, window_spec w
GROUP BY w.measured_at, w.window_start, lg.kind

UNION ALL
-- Распределение «сколько фактов на человека»: среднее скрыло бы, что у
-- большинства ноль или один.
SELECT w.measured_at, w.window_start,
       'людей с ровно ' || per.n::text || ' живыми зелёными фактами',
       count(*)::text
FROM (
    SELECT person, count(*) AS n FROM live_green GROUP BY person
) per, window_spec w
GROUP BY w.measured_at, w.window_start, per.n
ORDER BY metric;
