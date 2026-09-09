# ОТЧЁТ: перехват механизации — #1189 и дыра в сите

**Дата:** 2026-08-16 · **Каталог:** `ai-bot-platform-mech` · **Ветка:** `chore/mechanization`

Работу вело главное окно сабагентом после того, как окно механизации перестало отвечать (последняя активность 09:16). Отчёт сабагента приведён целиком; раздел «Доделано главным окном» дописан мной.

Классы: **VERIFIED** — прогон или чтение первоисточника · **INFERRED** — вывод без замера · **UNKNOWN** — не проверялось.

---

## §1. Корень падения #1189 — подтверждён

**VERIFIED.** Диагноз главного окна верен целиком: арифметика, не каскад по транзакции. Воспроизведено на Postgres 16, `PYTHONHASHSEED=1`:

```
django.db.utils.DataError: integer out of range
params = (..., Int4(3766074139), ...)
```

`3 766 074 139 > 2 147 483 647`. Каждый тест падает на своём INSERT.

**Уточнение к брифу:** упавших тестов не пять, а **шесть**. Шестой — `test_happy_path_full_chain_dispatches_master_dm` — был под `--deselect`, поэтому в лог CI не попал. Уязвимых шесть, видимых было пять.

**Починка.** `hash(str(x)) & 0xFFFFFFFF` → хелпер `_stable_external_id` (SHA-256, первые 4 байта, `& 0x7FFFFFFF`).

Почему не минимальная маска `0x7FFFFFFF`, которую допускал бриф: она чинит переполнение, но оставляет значение зависимым от `PYTHONHASHSEED` — тест остаётся плавающим. А `CatalogMaster` и `CatalogService` несут `unique_together = (("tenant", "external_id"))` (`apps/catalog/models.py:346`, `:162`, **VERIFIED** чтением), то есть значение обязано быть стабильным. Детерминированный счётчик отвергнут: теряет свойство «одинаковый вход → одинаковый `external_id`». SHA-256 — уже принятая в репозитории линия, `apps/skills/faq/tools.py:_cache_key` формулирует то же правило про `hash()`.

**VERIFIED:** зелено на seed 0/1/7/42/123 — 5 прогонов × 14 тестов на Postgres.

Снят единственный `--deselect` под этот дефект, комментарий-группа `(c)` в `ci.yml` переписан на RESOLVED, счётчик `27 tests` → `26`. **Новых `--deselect` — ноль.**

---

## §2. Полный список `hash()` → целочисленная колонка

Скан `apps/`, `tests/`, `tools/`, `config/`, `scripts/`, `legacy_*`. Шесть мест, переполнить может **только два**.

| # | Место | Выражение | Приёмник | Диапазон | Вердикт |
|---|---|---|---|---|---|
| 1 | `apps/skills/payment_failed/tests/test_skill.py:208` | `hash(str(x)) & 0xFFFFFFFF` | `CatalogMaster.external_id` `IntegerField` | 0…4 294 967 295 | **ПЕРЕПОЛНЯЛО — починено** |
| 2 | `apps/skills/payment_failed/tests/test_skill.py:230` | `hash(str(x)) & 0xFFFFFFFF` | `CatalogService.external_id` `IntegerField` | 0…4 294 967 295 | **ПЕРЕПОЛНЯЛО — починено** |
| 3 | `apps/catalog/services/tests/test_linking.py:77` | `abs(hash(slug)) % 10_000_000` | `external_id` `IntegerField` | 0…9 999 999 | в диапазоне, не тронуто |
| 4 | `apps/kb/tests/test_webhooks.py:229` | `100 + hash(t) % 1000` | `knowledge_doc_id` в payload | 100…1 099 | в диапазоне, не тронуто |
| 5 | `apps/skills/faq/tests/test_skill.py:172` | `f"imp-{hash(t) & 0xFFFF:x}"` | `channel_user_id`, строка | — | не целое |
| 6 | `apps/eventbus/tests/test_reviews_consumer.py:333` | `f"…{hash(...) % 10**12:012d}…"` | `event_id`, строка | — | не целое |

Места 3–6 остаются процесс-недетерминированными, но выйти за `integer` арифметически не могут. Чинить их — уборка, а не сито; в объём не брались.

**Продакшн решает ту же задачу правильно:** `apps/identity/services/solo_onboarding.py:237` — `-((uuid.int % (2**31 - 1)) + 1)`, детерминировано и в диапазоне. **VERIFIED.**

---

## §3. DRF-1157 — что вскрыла qualname-гранулярность

Ключ расширен до `(contract_id, файл, qualname, корень)`. `_ImportCollector` ведёт стек `class`/`def`; `Violation` получил поле `key`, чтобы регрессия «baseline == реальность» читала данные, а не парсила текст. Правило MKT1 намеренно оставлено файловым — оно по конструкции даёт одно нарушение на файл, его записи несут синтетический `<file>`. **Контракт G9 не менялся.** +6 тестов, главный — `test_baselining_one_call_site_does_not_cover_its_neighbour`.

**Было 26 записей, они покрывали 36 реальных мест. Невидимых — ровно 10:**

| Контракт | Файл | Что было скрыто | Δ |
|---|---|---|---|
| G5.1 | `miniapp_api/views.py` | одна запись `transitions` покрывала **шесть** тел: `_booking_to_dict`, `booking_cancel_{request,confirm,undo}`, `booking_reschedule_{request,confirm}` | +5 |
| G9 | `miniapp_api/views.py` | `_collect_occupied`, `_get_booking_owned`, `customer_recent_activity` | +3 |
| G9 | `loyalty/subscribers.py` | `LoyaltySubscriber._revoke_visit` (под `_credit_visit`) | +1 |
| G2.1 | `skills/booking/provider.py` | `get_booking_provider` (под импортом в шапке) | +1 |

**Ключевой отрицательный результат: ни одного нового файла.** Файловый ключ прятал неизвестные *места вызова внутри уже известных файлов*, а не неизвестные модули. **Дыра уже, чем читалась из брифа и из архитектурного ревью.**

Бриф просил остановиться при десятке — вскрылось ровно десять, но все внутри уже забаселиненных файлов, и **ни одно не чинилось**: перенесены в baseline с построчной аннотацией (кто гейчен флагом, кто нет, кто не триажирован). Альтернатива из §2 брифа (запрет `BookingRequest` в `apps/miniapp_api/` целиком) не выбрана: qualname вышел, он дешевле и не требует нового аксессора. Шва у Mini App по-прежнему нет — это отдельная задача.

### Два опровержения брифа, оба с доказательством

**(1) Импортов `BookingRequest` в `views.py` не два, а четыре** — строки 392 `_collect_occupied`, 1136 `bookings_list`, 1198 `_get_booking_owned`, 2201 `customer_recent_activity`. «391 и 1194» из брифа — это `def` и докстрока рядом; и 1198 это `_get_booking_owned`, а гейченный флагом путь на 1136.

**(2) `_collect_occupied` — УЖЕ НЕ живой дефект слотов.** `git log -L 588,601:apps/miniapp_api/views.py`:

```
0860183 Sat Aug 15 13:30:22 2026  fix(miniapp): read customer slots from Ayla,
                                  the system of record (DRF-1062)
+    if getattr(settings, "BOOKING_VIA_AYLA_REST", False):
+        ayla_slots, error = _slots_from_ayla(...)
+        return JsonResponse({"slots": ayla_slots})
```

**VERIFIED.** DRF-1062 (влита в #1186 15.08) поставила гейт в вызывающей функции `slots`: при включённом флаге до `_collect_occupied` управление не доходит. Guard этого не видит — гейт кадром выше. Запись в baseline остаётся честной, но читать её как «вот он, дефект слотов» уже нельзя: закрыт 15.08.

---

## §4. Сверка headRefOid — совпадает

```
gh pr view 1189 --json headRefOid  → 2f2097525fadd0a021a2393a2d0f82af85ee577c
git rev-parse origin/chore/mechanization → 2f2097525fadd0a021a2393a2d0f82af85ee577c
```

---

## §5. Статус CI на момент сдачи сабагентом

* **Прогон на ветке (`push`, run 31948974090) — полностью зелёный**, 26 минут, включая `pytest apps/` целиком на Postgres, mypy, ruff, оба AST-линтера. **VERIFIED.**
* **Прогон PR (`pull_request`, run 31948976623) — красный, ровно две строки:**

```
[G9] STALE BASELINE — apps/master_api/services/dashboard.py …
[G9] STALE BASELINE — apps/master_api/services/schedule.py  …
```

`origin/dev` ушёл вперёд; DRF-1085 (`869285c`, `205f2dd`) перевела эти две поверхности на зеркало `RemoteBookingProxy`, импорт `BookingRequest` из файлов ушёл. **Это храповик baseline, работающий как задуман:** долг погашен → строку обязали удалить. **VERIFIED** чтением `git show origin/dev:…`.

---

## §6. Границы, которые сабагент соблюл

PR #230 и `beautygo_backend-mech` — не тронуты, ни одной команды. Ничего не смержено. Найденные нарушения границ не чинились — только названы. Контракт G9 не менялся. Тесты гонялись на Postgres 16, не на SQLite. Временный контейнер и worktree убраны, рабочее дерево чистое.

Попытка `git merge origin/dev` была отклонена — сабагент прочитал запрет на merge широко. Формально бриф запрещал merge PR, а не подтягивание `dev` в свою ветку, но осторожное чтение здесь предпочтительнее.

---

## §7. Доделано главным окном

Влит `origin/dev` (22 коммита) в `chore/mechanization`, сняты две записи `STALE BASELINE`.

Записи удалены, а не сохранены — в этом и смысл храповика. **Устаревшая строка baseline это ложь о форме кода:** она утверждает пересечение границы там, где его нет, и следующий читатель обязан выяснять это руками.

`python tools/lint/import_boundaries.py apps/` → **exit 0**.

Локальный `pytest` прогнать не удалось — среда сломана (`ModuleNotFoundError: pkg_resources` в `pylama`). Не чинил: по правилу проекта авторитет всё равно CI на Postgres, а не локальный прогон. Это же правило родилось из находки «SQLite врёт».

Коммиты: `57f613a`, `2f20975` (сабагент), `d8a242c` (главное окно).

---

## §8. Что это дало проекту

Три находки, которые переживут задачу:

1. **`hash()` нельзя класть в `IntegerField`** — ни как маску, ни как остаток без запаса. Значение процесс-зависимо, а SQLite ширину не проверяет, поэтому дефект виден только на Postgres и только иногда.
2. **Файловый baseline активно скрывает смешанные файлы**, а смешанные файлы и есть места, где живут нарушения границ. Чем грязнее файл, тем полнее его укрывает baseline.
3. **Линтер импортов не видит гейт кадром выше.** `_collect_occupied` выглядит нарушением и им числится, хотя вызов до него не доходит. Это ограничение инструмента, а не дефект кода, и его надо помнить при чтении baseline.
