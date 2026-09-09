# БРИФ окну-исполнителю: DRF-1004 — booking skill читает мёртвый каталог услуг

**Дата:** 2026-08-11
**Автор:** главное окно (Chief Architect / Release Owner)
**Репозиторий:** `C:\Users\user\PycharmProjects\ai-bot-platform`
**База:** `origin/dev` @ `0d94aaca23f78cdd3a3939ab15df5f9756f7de9d` (= merge PR #1163; он же задеплоен на пилоте и он же rollback-точка)
**Параметры запуска окна:** сложность **средняя**, контекст **средний**, модель **Opus 5**
**Рабочий каталог:** `C:\Users\user\PycharmProjects\ai-bot-platform`. Файлы шины — в `C:\Users\user\PycharmProjects\Ayla\docs\` (абсолютные пути).

**Первый шаг:** `git fetch origin`, убедиться что `origin/dev` = `0d94aac…`, создать от него ветку `fix/drf1004-canonical-service-catalog`.

---

## §0. Протокол

Работаем по `docs/WINDOW_PROTOCOL.md`:

- Твой отчёт — `docs/REPORT_DRF-1004.md` (создай). Ответы главного окна — `docs/REPLY_DRF-1004.md`, новыми секциями сверху; **поставь монитор и проверь, что он реально доставляет уведомления** (у двух прошлых окон монитор молча не работал — используй cron-проверку md5 каждые 1–2 мин).
- **Мутации Linear — только главное окно.** Кандидаты пиши секцией в REPORT.
- **Деплой — только после секции «GO НА ФАЗУ C» в REPLY** (GO даёт владелец). Read-only ssh-диагностика разрешена.
- Классы доказательности в отчёте обязательны: VERIFIED / INFERRED / CLAIMED / UNKNOWN.
- Не заявляй готовность, пока не увидел зелёный CI своими глазами (`gh pr checks`). Merge делает главное окно, не ты.

---

## §1. Цель

Воронка записи пилота доходит до выбора времени и там умирает: тап по слоту даёт «Контекст записи устарел. Начните выбор услуги заново.» Нужно, чтобы booking skill видел реальный каталог услуг тенанта и путь «услуга → мастер → дата → время → подтверждение» завершался созданием брони.

---

## §2. Симптом (VERIFIED, логи пилота 2026-08-11)

```
19:15:19.836  skills.dispatch.result name=booking action_type=booking tools=['show_slots']   ← выбор даты ОК
19:15:22.926  booking.pick_slot.unknown_service service=a4f31641-8d1c-4dce-bd57-aae85b4e4ef8
19:15:22.927  skills.dispatch.result name=booking action_type=booking tools=[]
```
Пользователь получает `_STALE_CONTEXT_TEXT`. Воспроизводится стабильно, на любой услуге и любом слоте.

---

## §3. Первопричина (VERIFIED — замерено главным окном в рантайме и в БД backend)

| Что | Якорь | Факт |
|---|---|---|
| Бот берёт каталог отсюда | `apps/integrations/ayla/booking_client.py:635-638` | `get_services()` → `GET internal/services/` (или `internal/specialists/<id>/services/`) |
| Этот эндпоинт на backend | `services/internal_api.py:24` `InternalServiceViewSet(ServicePublicViewSet)` | queryset — **легаси-модель `Service`** с `filter(is_active=True)` (`services/views.py:167-177`) |
| Состояние легаси-модели | БД backend, read-only замер | `Service.objects.count() = 0` — **пусто глобально**, не только у тенанта |
| Где живёт настоящий каталог | БД backend | `SalonService` у тенанта `b32a057a-…` → **58 строк, все `is_active=True`**; целевая `a4f31641-…` = «УЗ-кавитация — 1 зона». `SpecialistService` → 95 активных связок (результат DRF-974) |
| Как это ломает воронку | `apps/skills/booking/skill.py:380`, `:1255` | `allowed_service_ids = {_id_key(s.id) for s in services}` → пустое множество → `if service_id not in allowed_service_ids` валит ЛЮБОЙ `pick_slot` |
| Канонические эндпоинты живы | Замер запросом из контейнера бота | `GET internal/catalog/salon-services/?tenant=<tid>&is_active=true&page_size=200` → `count=58`, целевая услуга присутствует. `GET internal/catalog/specialist-services/?tenant=<tid>&is_active=true` → `count=95` |
| Их viewset'ы | `services/internal_api.py:41-60` | `InternalSalonServiceViewSet` (`filterset_fields = ['tenant','template','is_active']`), `InternalSpecialistServiceViewSet` |

**Почему всплыло только сегодня:** каталог проверяется ровно в одном месте — на шаге `pick_slot`. До фиксов DRF-988/997/998 воронка туда не доходила. Мастера (`internal/specialists/` → 4) и слоты (`internal/specialists/<id>/slots/`) берутся из других эндпоинтов и работают.

**Форма данных канонического ответа** (образец строки `salon-services`):
```json
{"id": "4d1e6e2c-…", "tenant": "b32a057a-…", "template": null,
 "category": "6909bc83-…", "name": "Антицеллюлитный массаж",
 "duration_minutes": 40, "base_price": "2800.00", "requires_…": …}
```
Обрати внимание: цена в поле **`base_price`** (строка), а текущий маппер `_service_from_wire` (`booking_client.py:357-369`) читает **`price`** → без правки все цены станут `0.0`.

---

## §4. Что требуется сделать

Одна ветка, один PR в `dev`. Осмысленно разбить на 2 коммита (клиент → тесты) — на твоё усмотрение.

1. **Перевести `get_services()` на канонический каталог.** `apps/integrations/ayla/booking_client.py:635-638` → `catalog/salon-services/` с фильтрами `tenant=<id текущего тенанта>` и `is_active=true`. Идентификатор тенанта бери из активного `tenant_scope` — тем же способом, что уже используется для ключа кэша (`_tenant_id_for_cache`, добавлен в DRF-997). Если тенанта в скоупе нет — это ошибка вызова, а не повод молча вернуть пустой список: возбуди понятное исключение.
2. **Починить маппинг полей.** `_service_from_wire` (`:357-369`): цена — `base_price` (с сохранением совместимости со старым `price`), длительность — `duration_minutes` (уже так), название — `name`, категория — `category`. Проверь, что `AylaService.price_min/price_max` заполняются осмысленно.
3. **Пагинация — обязательна.** 58 услуг не помещаются в дефолтную страницу DRF. `_as_rows` (`:348-354`) разбирает конверт `{"count","results"}`, но **не ходит по `next`**. Реализуй обход страниц (или явный `page_size`, но тогда с проверкой, что `count == len(rows)`, иначе — дочитывание). Молча потерять часть каталога недопустимо: это ровно тот класс дефекта, который мы сейчас чиним.
4. **Разобраться с веткой `specialist_id`.** Сейчас `get_services(specialist_id=...)` бьёт в `internal/specialists/<id>/services/` — он тоже пуст (проверено, `count=0`). Канонический аналог — `catalog/specialist-services/?tenant=<tid>&specialist=<id>&is_active=true`, где строка связки содержит `salon_service` (UUID услуги), `specialist`, `tenant`. Переведи и эту ветку либо, если она нигде не используется на пути Ayla, — задокументируй это в REPORT и оставь honest-fail вместо тихого пустого списка.
5. **Проверь остальных потребителей каталога.** `apps/skills/booking/provider.py:117-124` (адаптер) и `apps/skills/booking/skill.py:360` (префетч + `build_service_lookup`). Сопоставление услуг по названию (discovery/матчинг) не должно сломаться от смены источника — прогони соответствующие тесты.
6. **Кэш.** Если добавляешь кэширование каталога — ключ обязан включать тенанта, как в DRF-997. Без тенанта в ключе PR не приму.

**Критерии приёмки:**
- Тест, доказывающий, что `get_services()` возвращает непустой каталог из канонического эндпоинта (мок ответа с конвертом `count/results` и второй страницей `next`) — **красный до фикса, зелёный после**.
- Тест на пагинацию: `count=58` при `page_size` меньше 58 → возвращается 58 объектов.
- Тест на маппинг `base_price` → ненулевая цена.
- Тест уровня скилла: `pick_slot` с валидной услугой больше не даёт `unknown_service`.

---

## §5. Фаза C — деплой (после GO владельца)

Рецепт прежний (см. `docs/REPORT_DRF-989-997-998.md`, фаза D): bundle `0d94aac..origin/dev` → scp → checkout → пересборка → `up -d` проекта `ayla-bot-staging`. **Rollback-точка `0d94aac`**, откат предразрешён. Health: `/healthz/` на `127.0.0.1:8014` → 200, логи worker'а 5 минут. Известный шум (НЕ поломка): `worker.subscriber_audit`, `pii_protected_provider.no_active_scope`, `events.emit.non_canonical`.

Затем smoke read-only в контейнере: `get_services()` в скоупе тенанта `b32a057a-56c7-4bf0-ae50-e11e76ab44be` возвращает 58 услуг и среди них `a4f31641-8d1c-4dce-bd57-aae85b4e4ef8`; цена этой услуги не нулевая. **Мутаций данных пилота не делать** — записи не создавать, live-приёмку не имитировать.

---

## §6. Границы

- **Backend не трогать.** Дефект чиним на стороне бота: канонические эндпоинты уже отдают корректные данные. Если по ходу окажется, что без правки backend цель недостижима — это **эскалация** в REPORT, не самостоятельное решение.
- Не трогать фиксы DRF-988/989/997/998 — они проверены на рантайме.
- Не чинить `pii_protected_provider.no_active_scope`, `worker.subscriber_audit`, флаки-тест `test_distinct_ips_each_get_one_audit` (это DRF-999).
- Не создавать данные у тенанта `formula-tela` — пилот стартует с чистой историей броней.
- Секреты в отчёт/код/логи не попадают.

---

## §7. Готовность

1. PR в `dev`, **зелёный CI** (цитата `gh pr checks` в REPORT). Помни про известный флакер `test_distinct_ips_each_get_one_audit` — если красный только он, это не твой регресс, укажи ссылку на прогон.
2. Тесты «красный до / зелёный после» по пунктам §4.
3. REPORT: SHA, эвиденс, границы, кандидаты в Linear, известные ограничения.
4. Done ставит главное окно **после** live-приёмки владельца (merge и деплой ≠ Done).
