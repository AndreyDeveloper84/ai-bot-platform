# Решения владельца — P0-контракт салона (23.08.2026)

**Второй аудит DRF-1297 принят. Четыре архитектурных вопроса В-1 — В-4 разрешены владельцем в формулировках ниже. Считать каноническими.**

Записано главным окном дословно по фрагментам сообщения владельца.

---

## Рамка работы

Репозиторий: `AndreyDeveloper84/beautygo_backend`, целевая ветка `dev`.

Работать через отдельную ветку и PR. **Не пушить напрямую в `dev`. Production не трогать. Linear-задачи не закрывать.**

Задача меняется: **не проводить ещё один аудит**, а аккуратно привести Linear и backend в соответствие с подтверждённым P0-контрактом.

### Главный принцип

- **НЕ** расширять Controlled Pilot.
- **НЕ** строить новую AI-specific бизнес-логику.
- **НЕ** создавать второй booking/scheduling engine.
- **НЕ** ослаблять permissions ради Ayla.

---

## В-1. AYLA READ / WRITE BOUNDARY — ACCEPT

**READ:** Ayla использует service-to-service credential **только для tenant-scoped read API**.

**WRITE:** Ayla **не хранит JWT сотрудника** и **не получает service credential, способный мутировать салон от имени человека**.

### Подтверждённый write path

```
Ayla понимает запрос
  -> разрешает контекст
  -> выполняет необходимые read/preflight проверки
  -> показывает конкретный proposal
  -> человек явно подтверждает
  -> canonical backend command вызывается с реальным actor
  -> отдельно проверяется authority этого actor
  -> authoritative result/readback
```

### Важные факты из аудита

1. Существующий механизм bearer + actor **уже позволяет такой путь**.
2. Actor должен определяться **реальной связанной клиентской учётной записью**.
3. Просто аккаунт с ролью `admin` **недостаточен**, если отсутствует требуемая tenant linkage.
4. Internal master list, который возвращает мастеров **всей платформы**, нельзя использовать как Ayla salon read API.
5. Любой новый read-access Ayla **обязан быть tenant-scoped**.

---

## В-2. PERMISSION MODEL — ACCEPT

**Не строить новый RBAC.**

Существующее разделение сохраняется: **«может видеть» ≠ «может выполнить действие»**.

В коде уже есть отдельный механизм определения authority вызывающего относительно конкретной записи. Он уже используется в части операций.

**Переиспользовать существующий механизм для:**

- create appointment;
- reschedule appointment;
- cancel appointment;

если второй аудит подтвердил именно эти gaps.

**Не делать отдельную новую permission architecture.**

**Privacy клиента — отдельный слой.** Право выполнить действие **НЕ означает** автоматическое право видеть полный телефон клиента.

---

## В-3. RESCHEDULE — SAME MASTER ONLY — ACCEPT

**Канонический P0-контракт:**

> RESCHEDULE = изменение даты и/или времени существующей записи **У ТОГО ЖЕ МАСТЕРА**.

Смена мастера через Reschedule **не поддерживается**.

**Причина подтверждена кодом:** цена находится на связи мастер ↔ услуга и обязательна. Следовательно, смена мастера потенциально меняет коммерческие условия и не является обычным переносом.

### ВАЖНО

**Не писать в Linear и кодовых комментариях, что длительность персональна мастеру.** Аудит подтвердил обратное: `duration` берётся **не** из персональной цены `SpecialistService`.

Дополнительного запрета `master_id` в reschedule **может не требоваться**, потому что `master_id` уже отсутствует в контракте. **Не добавлять искусственный guard**, если изменить мастера через endpoint технически невозможно.

Если пользователь хочет другого мастера: **создание новой записи + отдельная отмена старой**. Не создавать скрытую compound-operation cancel + create.

---

## В-4. AVAILABILITY CONFLICT GUARD — ACCEPT

**Общий safety invariant:** ни одно сокращение доступности не должно молча проходить поверх активной записи.

Но одинаковый rich Conflict Guard для всех операций **не требуется**.

### Каноническое разделение

| Операция | Требование |
|---|---|
| **A.** Частичная недоступность мастера | существующий полный Conflict Guard / impact preview / resolution path |
| **B.** Specific-date «не работаю» | достаточно **hard 409** при реально затрагиваемых активных записях |
| **C.** Tenant closure / закрытие салона | достаточно **hard 409** при реально затрагиваемых активных записях |
| **D.** Weekly recurring template PUT/PATCH | существующего guard **недостаточно** — отдельный backend gap |

### Почему weekly — отдельный gap

Для сокращения weekly template нужно определить записи, которые **после изменения оказались ВНЕ новой рамки**, а не просто записи, находящиеся внутри произвольного интервала.

Также учитывать, что **appointment может легально существовать вне working hours**.

Нельзя делать наивную проверку «есть запись вне новых часов → конфликт», не сравнив старую и новую effective availability.

Текущий weekly write path также требует отдельной проверки transaction/race guarantees.

**До реализации корректного weekly guard:** Ayla **не должна** предлагать изменение recurring weekly template как полностью поддержанную consequential write.

---

# ЧАСТЬ 1. Привести Linear в соответствие

**Не удалять старые freeze-комментарии. Историю решений сохранить.** Добавить amendment-комментарии в задачи, где новый подтверждённый контракт уточняет frozen UX.

## DRF-1236 — Today

Проверить frozen блок «Ayla · Изменения» и «Требует внимания». Backend operational event projection **сейчас не существует**.

Добавить amendment:

- концепт остаётся целевым;
- в Controlled Pilot **нельзя показывать его как работающий источник фактов**, пока нет backend event source;
- LLM **не имеет права** реконструировать «что изменилось» сравнением произвольных snapshot;
- либо блок скрывается из реально реализуемого P0, либо остаётся backend-blocked до отдельного event source.

**Не придумывать event source в рамках этой работы.**

## DRF-1237 — Schedule

Исправить утверждения, которые обещают полноценный authoritative «Найти свободное время», если такого backend contract сейчас нет.

Канонический P0:

- расписание дня читается;
- пользователь может выбрать предполагаемое время из фактического day context;
- окончательная безопасность обеспечивается **canonical create-time conflict check**;
- Ayla/UI **не должны утверждать** «это время гарантированно свободно», если отдельный authoritative availability/search contract отсутствует.

Также явно зафиксировать: **Reschedule не меняет мастера.**

## DRF-1238 — Manual Booking

Сохранить existing flow. Проверить только consistency: **create-time authoritative conflict остаётся последней защитой**. Никаких изменений бизнес-flow без необходимости.

## DRF-1239 — Appointment Detail

Добавить amendment: **«Перенести запись» = тот же мастер, другая дата/время.**

Удалить из канона любую формулировку или пример вида:

```
Было: Денис
Стало: Инна
```

Правильный пример:

```
Было:  Денис · 12:30–13:30
Стало: Денис · 16:30–17:30
```

Если требуется другой мастер: новая запись + отдельная отмена старой.

## DRF-1240 — Working Time

Уточнить Conflict Guard. **Не писать больше, что один и тот же rich preview обязателен для любого уменьшения availability.**

Зафиксировать матрицу:

```
partial unavailability   -> полный существующий guard
specific date «не работаю» -> hard 409 допустим
tenant closure           -> hard 409 допустим
weekly recurring shrink  -> backend-blocked до корректного comparison guard
```

**Не ослаблять общий invariant:** активная запись не может быть молча оставлена без доступного мастера из-за изменения графика.

## DRF-1241 — System States / Permissions

Frozen permission model **НЕ заменять** правилом «кто видит, тот действует». Наоборот, добавить amendment:

- read permission и action authority **остаются различными**;
- полноценный UI управления ролями в P0 не требуется;
- pilot owner/admin может иметь полный разрешённый набор действий;
- backend **всё равно обязан** проверять authority конкретного actor для consequential write.

## DRF-1249 — Ayla Salon

Добавить owner-ruling comment с В-1 — В-4. Явно перечислить.

**SUPPORTED DESIGN SURFACE:**

- read tenant day;
- read appointments;
- find existing appointment;
- read masters / schedule через tenant-scoped surface;
- create booking proposal;
- reschedule proposal у того же мастера;
- cancel proposal;
- partial-unavailability proposal там, где existing guard поддерживает команду;
- clarification;
- permission denial;
- create/reschedule conflict;
- stale;
- unknown/readback.

**НЕ ПОКАЗЫВАТЬ КАК ГОТОВУЮ P0-ФУНКЦИЮ:**

- «Что изменилось сегодня?»;
- полноценный «Требует внимания» без operational-event source;
- утверждение «мастер точно свободен в X» без authoritative availability/search contract;
- reschedule со сменой мастера;
- изменение recurring weekly template через Ayla, пока weekly shrink guard не реализован.

---

# ЧАСТЬ 2. Минимальные backend изменения

После Linear amendments перейти к коду. **Перед каждым изменением ещё раз проверить фактическое состояние `dev`. Не писать код для gap, который уже закрыт.**

## 1. Tenant-scoped read access для Ayla

Ayla должна читать **только данные конкретного tenant**. Не использовать internal endpoint, который возвращает мастеров всей платформы.

Переиспользовать существующие tenant-scoped serializers/services/routes, если возможно.

Если permission class расширяется для service credential:

- `GET`/`HEAD`/`OPTIONS` можно разрешить;
- `POST`/`PUT`/`PATCH`/`DELETE` **не должны открыться автоматически**;
- tenant должен вычисляться из разрешённого контекста;
- **нельзя принимать произвольный `tenant_id`** и отдавать чужой салон без дополнительной проверки.

Тесты: service credential tenant A → видит tenant A; → **не получает** tenant B; → **не получает** write authority через этот read path.

## 2. Action authority

Переиспользовать **уже существующий** модуль authority. Подключить его к тем consequential operations, где аудит подтвердил отсутствие проверки: **create, reschedule, cancel**.

Не копировать новую permission логику в каждый endpoint, если можно вызвать существующий policy/service.

Тесты обязательно:

- разрешённый actor → success;
- actor без нужной linkage/authority → 403;
- роль `admin` **без требуемой tenant linkage** → не должна магически обходить authority;
- доступ к чтению сам по себе → **не даёт** write authority.

## 3. Specific-date hard 409

Если specific-date «не работаю» сейчас может пройти поверх активных appointment — добавить минимальный корректный guard.

**Проверять только реально затрагиваемый диапазон этой даты.** Не использовать неправильную семантику weekly-frame comparison.

Ответ должен быть **стабильным domain conflict**, который frontend может отобразить как «Нельзя сохранить изменение». **Не возвращать случайный 500 / IntegrityError.**

Тесты: нет записей → change succeeds; есть активная пересекающаяся запись → 409; отменённая/неактивная запись → поведение согласно существующему active-status contract.

## 4. Tenant closure hard 409

Аналогично. **Не реализовывать автоматическую отмену записей. Не переносить клиентов автоматически. Не добавлять resolution workflow.**

## 5. Общий helper для простого overlap guard

Аудит обнаружил один и тот же embedded query, скопированный несколько раз. Если это подтверждается текущим `dev` — вынести **только безопасно идентичную часть** проверки в небольшой helper/service.

**Не пытаться этим helper решить weekly template shrink.** Simple interval overlap и weekly-frame comparison — разные задачи. **Не создавать «универсальный ConflictEngine»**, если для этого нет необходимости.

## 6. Weekly template

**НЕ делать поспешный фикс.** Сначала оформить подтверждённый backend gap в отчёте / Linear amendment:

для корректного уменьшения recurring template нужны **old effective frame vs new effective frame** + развёртка по ограниченному P0 horizon + определение активных appointment, которые **именно из-за изменения** потеряли покрытие + transaction/race protection.

Если отдельной задачи на реализацию weekly guard ещё нет — **не реализовывать большой новый механизм внутри этого PR.** Он не должен случайно разрастить текущую работу.

**Главное: до закрытия gap Ayla weekly writes считаются unsupported.**

---

# ЧАСТЬ 3. Customer privacy

Ещё раз проверить customer resolution.

Если frozen UX требует различать одноимённых клиентов, а API не отдаёт privacy-safe признак — **не отдавать Ayla полный телефон «для удобства»**.

Минимальный правильный контракт: **masked phone** или другой утверждённый privacy-safe discriminator.

Если gap существует — зафиксировать его отдельно. **Не расширять PII surface без необходимости.**

---

# ЧАСТЬ 4. Operational events

**Код event system сейчас НЕ писать.**

Подтвердить в финальном отчёте: **Operational events: NOT SUPPORTED.**

Следовательно, Ayla не должна отвечать как факт «с 8 утра произошло 3 изменения», если нет authoritative event projection. **Не строить это на LLM memory или snapshot diff.**

---

# ЧАСТЬ 5. Тесты и верификация

После минимальных code changes прогнать: существующие booking tests; salon appointment operation tests; schedule/time-off tests; permission/authority tests; новые tenant isolation tests; create/reschedule/cancel negative permission tests; conflict tests для specific date / closure.

**Проверить regression:**

- обычный пользовательский booking flow не сломан;
- master flow не сломан;
- existing ProApp/internal consumers не потеряли нужный доступ;
- service credential не получил write privilege;
- reschedule по-прежнему не умеет менять мастера;
- partial time-off rich Conflict Guard работает как раньше.

---

# ЧАСТЬ 6. PR

Один PR допустим **только если** изменения остаются небольшими и логически связанными. Если tenant-read permissions и schedule conflict guards оказываются двумя независимыми большими изменениями — **разделить PR**.

В описании PR обязательно:

- **WHY** — что было небезопасно/неполно;
- **CONTRACT** — какое P0 поведение теперь гарантировано;
- **NON-GOALS** — что намеренно не реализовано: operational events; weekly shrink guard; reschedule master change; new RBAC; AI-specific booking logic;
- **TESTS** — что конкретно проверено.

---

# ЧАСТЬ 7. Финальный отчёт

1. **Linear amendments** — для каждой DRF-задачи, что именно добавлено.
2. **Backend commits / PR** — SHA, PR, файлы.
3. Что реально изменено в code contract.
4. **Что осталось gap:** operational events; weekly recurring shrink guard; authoritative free-time search, если всё ещё отсутствует; customer masked discriminator, если всё ещё отсутствует.
5. **Обновлённая матрица DRF-1249** — SUPPORTED / PARTIAL / BLOCKED для: salon today read; appointment search; create; same-master reschedule; cancel; schedule reads; partial unavailability; specific-date non-working; tenant closure; weekly template write; free-time query; operational changes; permission denial; conflict; stale/unknown.
6. **Итоговый вердикт:** `DRF-1249 READY TO DESIGN` / `READY TO DESIGN WITH EXPLICIT BLOCKED STATES` / `STILL BLOCKED`.

## Критерий успеха

> После этого прохода дизайнер должен иметь возможность открыть DRF-1249 и нарисовать только те Ayla-сценарии, которые соответствуют реальному backend contract, без выдуманных возможностей и без будущей переделки базовой архитектуры.

---

# ДОПОЛНЕНИЕ 24.08.2026 — какая салонная админка пилотная

**Записано главным окном дословно. Считать каноническим.**

Повод: обнаружено, что салонных админок **две**, и владелец пользуется не той, что нарисована.

## Решение владельца

> **На макетах пилотная.**

То есть пилотная поверхность — та, что зафиксирована в DRF-1236…1241: навигация **`Сегодня · Расписание · Ayla`**, бэкенд Ayla **`/api/v1/tenants/me/`**.

## Что это значит в цифрах

Проверено главным окном по коду 24.08:

```
СУЩЕСТВУЕТ В КОДЕ (не пилотная)          НА МАКЕТАХ (пилотная)
День · Команда · Услуги · Чаты ·          Сегодня · Расписание · Ayla
  Настройки
  AdminTabBar.tsx:130-152                 DRF-1236/1237/1241, нижняя панель

11 экранов                                0 реализованных экранов
  apps/miniapp/src/screens/admin/

/api/v1/admin/ внутри бота                /api/v1/tenants/me/ в Ayla
  apps/admin_api/urls.py                    13 маршрутов, PR #240/#241

вызовов из бота к tenants/me: 0
```

## Три следствия, которые решение порождает

**Первое. Четыре вкладки, которыми салон пользуется сегодня, не покрыты ничем.**

«Команда», «Услуги», «Чаты», «Настройки» существуют в коде и работают — владелец открывал их 24.08 в 08:59. Макета у них **нет ни одного**, задачи — **ни одной**. Макеты покрывают только «Сегодня», «Расписание» и «Ayla».

Значит выбор макетов пилотными **не сужает работу, а расширяет её**: либо набор макетов вырастает на четыре экрана, либо пилот теряет способности, которые у него уже есть.

**Второе. Пути к данным нет.** Бот не зовёт `tenants/me` **ни разу**. API открыто, звать его некому. Пилотная админка без этого клиента — экраны без данных.

**Третье. `PR #240` и `#241` не влиты.** Вся матрица готовности «8 SUPPORTED / 1 PARTIAL / 6 BLOCKED» написана как состояние **после** мержа. До мержа она описывает несуществующее.

## Что НЕ следует из решения

**Существующие 11 экранов не выключаются сегодня.** Они работают, ими пользуются. Замена идёт по вкладкам: вкладка гаснет тогда, когда её способность накрыта пилотной поверхностью, а не раньше.
