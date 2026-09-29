-- DRF-2462 · сверка зеркала с КАНОНОМ · ТОЛЬКО ЧТЕНИЕ · база КАТАЛОГА
--
-- Зачем: классификация ложных штампов (`audit_false_completions`) читает
-- **зеркало** канона (`booking_remotebookingproxy` в базе бота). Зеркало — копия,
-- и у нас есть ИЗМЕРЕННОЕ расхождение копии с источником: DRF-2437 — визит
-- `f3deff6f…` (бот) / `186a7d50…` (каталог): бот и зеркало говорят `confirmed`,
-- каталожная `appointments_appointment` — `awaiting_payment`.
--
-- Следствие: строка, признанная законной по зеркалу, может быть ложной по канону.
-- Поэтому перед любым `--apply` состояние каждой строки-кандидата надо прочитать
-- у источника, а не у копии.
--
-- Где запускать: база КАТАЛОГА (`djangoProject`), read-only транзакцией:
--   BEGIN READ ONLY;  \i этот_файл  ROLLBACK;
--
-- В выводе только идентификаторы, статусы и времена — ни имён, ни телефонов.

-- Подставить идентификаторы канонических визитов из трассы бота
-- (`canonical_appointment_id`, запрос 2 в docs/drf2462_readonly_queries.sql).
WITH wanted(appointment_id) AS (
    VALUES
        ('4d67c3cd-4b4f-4401-a271-7bc51a64712b'::uuid),
        ('2bceb3f6-07d9-44b6-8de0-592688a45433'::uuid),
        ('f6b58394-35e8-4db1-ae28-7b39a64329ad'::uuid),
        ('6f5267fc-3408-4891-a888-7b37f155b9a7'::uuid),
        ('e516bfcf-a39b-45e1-b24a-b0f636bc0ea6'::uuid),
        ('f8e7d83a-9cf0-453d-8c5c-425f5321e119'::uuid),
        ('4b382a0f-59d4-40e9-870f-271a9a12423a'::uuid),
        ('37730943-82d4-43ae-944b-795adac4a8a1'::uuid),
        ('681614d2-a578-4466-9e6c-915bded5ad05'::uuid),
        ('186a7d50-94d9-4215-95ae-d1df57ced966'::uuid)
)
SELECT 'canon_status' AS q,
       w.appointment_id,
       a.status AS canonical_status,
       a.start_at,
       a.updated_at,
       (a.id IS NULL) AS missing_in_canon
FROM wanted w
LEFT JOIN appointments_appointment a ON a.id = w.appointment_id
ORDER BY a.start_at NULLS LAST;

-- Сводка: сколько по каждому каноническому состоянию, и сколько визитов канон
-- не знает вовсе (это тоже ответ: зеркало может держать строку, которой нет).
WITH wanted(appointment_id) AS (
    VALUES
        ('4d67c3cd-4b4f-4401-a271-7bc51a64712b'::uuid),
        ('2bceb3f6-07d9-44b6-8de0-592688a45433'::uuid),
        ('f6b58394-35e8-4db1-ae28-7b39a64329ad'::uuid),
        ('6f5267fc-3408-4891-a888-7b37f155b9a7'::uuid),
        ('e516bfcf-a39b-45e1-b24a-b0f636bc0ea6'::uuid),
        ('f8e7d83a-9cf0-453d-8c5c-425f5321e119'::uuid),
        ('4b382a0f-59d4-40e9-870f-271a9a12423a'::uuid),
        ('37730943-82d4-43ae-944b-795adac4a8a1'::uuid),
        ('681614d2-a578-4466-9e6c-915bded5ad05'::uuid),
        ('186a7d50-94d9-4215-95ae-d1df57ced966'::uuid)
)
SELECT 'canon_summary' AS q,
       coalesce(a.status, '(нет в каноне)') AS canonical_status,
       count(*) AS n
FROM wanted w
LEFT JOIN appointments_appointment a ON a.id = w.appointment_id
GROUP BY 2
ORDER BY 2;
