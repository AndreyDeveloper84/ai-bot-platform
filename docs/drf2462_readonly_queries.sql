-- DRF-2462 · этап 3 и этап 5 промпта владельца · ТОЛЬКО ЧТЕНИЕ
--
-- Запросы повторяют классификацию команды `manage.py audit_false_completions`
-- (apps/booking/management/commands/audit_false_completions.py, PR #2073) на
-- языке базы, чтобы их можно было выполнить read-only транзакцией без ORM.
--
-- Что в выводе: количества, разрезы и ИДЕНТИФИКАТОРЫ записей. Ни имён, ни
-- телефонов, ни текстов — ни один столбец с персональными данными не выбирается.
--
-- Правило классификации — то же, что в коде. ИСПРАВЛЕНО 25.09: первая редакция
-- этого файла считала `mirror_confirmed` ЗАКОННЫМ, а команда кладёт его в
-- `unprovable`. Расхождение было на 7 строках из 10 — ровно там, где решается,
-- трогать строку или нет. Прав оказался код: сверка с каноном показала, что из
-- тех семи `completed` только три (DRF-2519).
--   зеркало ищется по ключу (tenant_id, bot_user_id, start_at = visit_at);
--   legitimate  = completed_by НЕ 'system' и не пусто  ИЛИ  mirror_status = 'completed';
--   false       = mirror_status IN ('cancelled','no_show','pending_payment','tentative');
--   unprovable  = зеркала нет · зеркал больше одного · bot_user пуст ·
--                 mirror_status = 'confirmed' · незнакомое состояние зеркала.
--
-- ГЛАВНОЕ: зеркало — копия без `updated_at`, и она стухает. Числа отсюда —
-- НИЖНЯЯ граница множества ложных штампов (по замеру 25.09: 3 против 5 по
-- канону). Окончательная разбивка — по канону: `docs/drf2462_canon_crosscheck.sql`
-- и затем команда с `--canon-file`.
--
-- ЗАПУСКАТЬ ЦЕЛИКОМ В ОДНОЙ ТРАНЗАКЦИИ:
--   BEGIN READ ONLY;  \i этот_файл  ROLLBACK;

-- ---------------------------------------------------------------------------
-- 0. Охват. Печатается ПЕРВЫМ: ноль ниже честен только на непустом охвате.
-- ---------------------------------------------------------------------------
SELECT 'scope' AS q,
       count(*) AS rows_with_completed_at,
       count(*) FILTER (WHERE completed_by = 'system') AS closed_by_clock,
       count(*) FILTER (WHERE completed_by <> 'system' AND completed_by <> '') AS closed_by_human,
       min(visit_at) AS earliest_visit,
       max(visit_at) AS latest_visit,
       now() AS measured_at
FROM booking_bookingrequest
WHERE completed_at IS NOT NULL;

-- ---------------------------------------------------------------------------
-- 1. Разбивка по состоянию канона (этап 3 отчёта владельцу).
-- ---------------------------------------------------------------------------
WITH stamped AS (
    SELECT br.id, br.tenant_id, br.bot_user_id, br.visit_at,
           br.status AS local_status, br.completed_at, br.completed_by
    FROM booking_bookingrequest br
    WHERE br.completed_at IS NOT NULL
),
matched AS (
    SELECT s.*,
           (SELECT count(*) FROM booking_remotebookingproxy p
             WHERE p.tenant_id = s.tenant_id
               AND p.bot_user_id = s.bot_user_id
               AND p.start_at = s.visit_at) AS mirror_count,
           (SELECT min(p.status) FROM booking_remotebookingproxy p
             WHERE p.tenant_id = s.tenant_id
               AND p.bot_user_id = s.bot_user_id
               AND p.start_at = s.visit_at) AS mirror_status
    FROM stamped s
),
classified AS (
    SELECT m.*,
           CASE
             WHEN m.completed_by <> 'system' AND m.completed_by <> '' THEN 'legitimate:human_closer'
             WHEN m.bot_user_id IS NULL THEN 'unprovable:no_key'
             WHEN m.mirror_count = 0 THEN 'unprovable:no_mirror'
             WHEN m.mirror_count > 1 THEN 'unprovable:ambiguous'
             WHEN m.mirror_status = 'completed' THEN 'legitimate:mirror_completed'
             WHEN m.mirror_status = 'confirmed' THEN 'unprovable:mirror_confirmed'
             WHEN m.mirror_status IN ('cancelled', 'no_show', 'pending_payment', 'tentative')
                  THEN 'false_by_canon:mirror_' || m.mirror_status
             ELSE 'unprovable:mirror_' || coalesce(m.mirror_status, 'null')
           END AS verdict
    FROM matched m
)
SELECT 'buckets' AS q, verdict, count(*) AS n
FROM classified
GROUP BY verdict
ORDER BY verdict;

-- ---------------------------------------------------------------------------
-- 2. Трасса по каждой строке со штампом (этап 3 промпта: идентификаторы можно,
--    персональные данные нельзя). appointment_id зеркала — каноническая запись.
-- ---------------------------------------------------------------------------
WITH stamped AS (
    SELECT br.id, br.tenant_id, br.bot_user_id, br.visit_at,
           br.status AS local_status, br.completed_at, br.completed_by
    FROM booking_bookingrequest br
    WHERE br.completed_at IS NOT NULL
)
SELECT 'trace' AS q,
       s.id AS booking_request_id,
       (SELECT min(p.appointment_id::text) FROM booking_remotebookingproxy p
         WHERE p.tenant_id = s.tenant_id
           AND p.bot_user_id = s.bot_user_id
           AND p.start_at = s.visit_at) AS canonical_appointment_id,
       s.local_status,
       (SELECT min(p.status) FROM booking_remotebookingproxy p
         WHERE p.tenant_id = s.tenant_id
           AND p.bot_user_id = s.bot_user_id
           AND p.start_at = s.visit_at) AS canonical_status,
       (SELECT count(*) FROM booking_remotebookingproxy p
         WHERE p.tenant_id = s.tenant_id
           AND p.bot_user_id = s.bot_user_id
           AND p.start_at = s.visit_at) AS mirror_matches,
       s.completed_at,
       s.completed_by,
       s.visit_at,
       s.tenant_id
FROM stamped s
ORDER BY s.visit_at;

-- ---------------------------------------------------------------------------
-- 3. Очередь по строкам со штампом (этап 5). Событие связывается с бронью по
--    data->>'booking_id' — так его кладёт продюсер (apps/bookings/tasks.py).
-- ---------------------------------------------------------------------------
SELECT 'queue_by_row' AS q,
       e.data ->> 'booking_id' AS booking_request_id,
       e.event_name,
       count(*) AS events,
       count(*) FILTER (WHERE e.is_dispatched) AS dispatched,
       count(*) FILTER (WHERE NOT e.is_dispatched) AS pending,
       count(*) FILTER (WHERE e.dead_lettered_at IS NOT NULL) AS dead_lettered,
       min(e.occurred_at) AS oldest,
       max(e.occurred_at) AS newest
FROM eventbus_domainevent e
WHERE e.data ->> 'booking_id' IN (
        SELECT br.id::text FROM booking_bookingrequest br WHERE br.completed_at IS NOT NULL
      )
GROUP BY 2, 3
ORDER BY 2, 3;

-- ---------------------------------------------------------------------------
-- 4. Весь ящик по именам (этап 5, вопросы 1–3): что создано, что отправлено,
--    что ждёт. Знаменатель для любого нуля выше.
-- ---------------------------------------------------------------------------
SELECT 'outbox_by_name' AS q,
       event_name,
       count(*) AS total,
       count(*) FILTER (WHERE is_dispatched) AS dispatched,
       count(*) FILTER (WHERE NOT is_dispatched) AS pending,
       count(*) FILTER (WHERE dead_lettered_at IS NOT NULL) AS dead_lettered,
       min(occurred_at) AS oldest,
       max(occurred_at) AS newest
FROM eventbus_domainevent
GROUP BY event_name
ORDER BY pending DESC, event_name;

-- ---------------------------------------------------------------------------
-- 5. Контроль обратимости (этап 5, вопрос 4): отправлено ли ХОТЬ ОДНО
--    booking.completed вообще. Ноль здесь — единственное, что позволяет
--    сказать «последствия ещё удерживаются очередью».
-- ---------------------------------------------------------------------------
SELECT 'completed_dispatched_ever' AS q,
       count(*) FILTER (WHERE is_dispatched) AS dispatched_ever,
       count(*) AS total_completed_events,
       min(occurred_at) FILTER (WHERE is_dispatched) AS first_dispatched_at
FROM eventbus_domainevent
WHERE event_name = 'booking.completed';
