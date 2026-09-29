# PROMPT АГЕНТУ — UX CANON RECONCILIATION: MAY SPECS ↔ AUGUST LINEAR ↔ RUNTIME ↔ OWNER RULINGS

Ты работаешь как **senior product architect / UX systems auditor** проекта Ayla.

Твоя задача — провести **read-only reconciliation двух слоёв UX-канона** и текущей реализации, чтобы окончательно установить:

- что является CURRENT;
- что является историческим provenance;
- что superseded;
- что реально осталось построить;
- какие расхождения являются настоящими дефектами;
- какие раньше были ложными выводами из-за сравнения только August Linear ↔ code.

Это **НЕ задача на редизайн** и **НЕ задача на исправление кода**.

НЕ:
- менять код;
- менять макеты;
- закрывать Linear-задачи;
- мержить PR;
- переписывать specs;
- удалять legacy;
- вводить новую архитектуру.

Сначала нужен один reconciliation-документ.

---

# 0. КЛЮЧЕВАЯ НАХОДКА, КОТОРУЮ НЕЛЬЗЯ ПОТЕРЯТЬ

У проекта обнаружены два независимых слоя UX-спецификаций.

## MAY layer — repository specs / handoffs

В репозитории есть:

- **19 screen specs** в `docs/screens/`;
- около **20 handoff-документов**;
- по этим материалам фактически строился текущий код.

Из известных файлов обязательно проверить:

```text
provider-calendar-schedule-flow.md
provider-booking-detail-flow.md
provider-messages-flow.md
master-solo-surface.md
```

Также отдельно установлено, что клиентская спека записи — большая и подробная
(порядка 1000+ строк), а для Food Scanner тоже существует отдельная спецификация
и follow-up документ.

## AUGUST layer — Linear redesign

В августе появились новые эпики и задачи:

```text
DRF-1234 — Salon MVP / Production UI
DRF-1180 — Master MVP / Production UI
DRF-1318 — Client MVP / Production UX
DRF-1174 — Conversation C01–C05
```

Они проектировали UX заново и **не были системно сверены с майскими specs**.

Следствие:

НЕЛЬЗЯ больше автоматически писать:

```text
code deviates from August mock
→ bug
```

Потому что code может точно реализовывать May spec.

---

# 1. НОВОЕ ПРАВИЛО АУДИТА

Для каждой capability нужна **четырёхсторонняя сверка**:

```text
MAY SPEC
   ↕
CURRENT CODE
   ↕
AUGUST LINEAR / MOCK
   ↕
OWNER RULING
```

Не ограничиваться двумя источниками.

---

# 2. ВЕРДИКТЫ

Использовать только такие статусы:

```text
KEEP_FROM_MAY
SUPERSEDED_BY_AUGUST
SUPERSEDED_BY_OWNER
CURRENT_RUNTIME_ONLY
IMPLEMENTATION_MISMATCH
REDESIGN_NOT_RECONCILED
THREE_WAY_DIVERGENCE
UNRESOLVED
NOT_IN_PILOT
```

Смысл:

## KEEP_FROM_MAY

May spec соответствует current runtime и не отменён более новым owner ruling.

## SUPERSEDED_BY_AUGUST

August явно заменил старую механику, и это подтверждено owner/current decision.

## SUPERSEDED_BY_OWNER

Есть более новый явный owner ruling.

## CURRENT_RUNTIME_ONLY

Поведение реально есть и используется, но не имеет ещё нормального CURRENT UX contract.

## IMPLEMENTATION_MISMATCH

May и August/Owner согласны между собой, а code реально не соответствует.

## REDESIGN_NOT_RECONCILED

May = code, August отличается, но August ещё не был официально объявлен superseding contract.

## THREE_WAY_DIVERGENCE

May ≠ August ≠ runtime.

## UNRESOLVED

Нельзя установить precedence.

## NOT_IN_PILOT

Capability может быть описана/построена, но не входит в Controlled Pilot.

---

# 3. ОСОБОЕ ПРАВИЛО

Не считать May автоматически legacy.

Не считать August автоматически CURRENT только потому, что он новее по дате.

Precedence определяется:

1. explicit owner ruling;
2. explicit CURRENT/canonical designation;
3. accepted implementation contract;
4. runtime provenance;
5. только потом дата документа.

---

# 4. CLIENT — ПРОВЕРИТЬ ПЕРВЫМ

По клиенту уже есть важный результат:

```text
C05 → screen → endpoint matrix:
12 closed
6 partial
3 not built
```

И главное:

```text
Recommendation
→ booking
```

проходится без тупиков.

Значит клиентский execution foundation — не greenfield.

Нужно перепроверить эту матрицу уже с учётом May specs.

Особенно:

- recommendation acceptance;
- service selection;
- provider selection;
- slot selection;
- confirmation;
- booking write;
- success;
- booking detail;
- records;
- cancel/reschedule;
- Mini App handoff;
- Food Diary;
- Food Scanner.

---

# 5. КЛИЕНТСКИЙ НАСТОЯЩИЙ P0 DEFECT

Проверить и подтвердить отдельно:

После booking success:

```text
Открыть запись
→ legacy booking detail route
```

А из бота:

```text
Мои записи
→ current booking detail route
```

Если факт подтверждается:

это настоящий Pilot consistency defect.

Не строить третью detail surface.

Нужно определить:

```text
ONE CANONICAL BOOKING DETAIL ROUTE
```

и все entry points должны вести туда.

Но в этом reconciliation-заходе **не чинить**, только классифицировать и
сформулировать minimum fix direction.

---

# 6. FOOD SCANNER

Раньше была гипотеза:

```text
макета нет
```

Она опровергнута.

Установлено:

- спека есть;
- она крупная;
- есть follow-up;
- поверхность спроектирована.

Поэтому настоящий вопрос:

```text
входит ли Food Scanner в Controlled Pilot?
```

а не:

```text
нужно ли его проектировать?
```

Проверить owner decisions.

Текущее рабочее направление:

```text
Food Diary = Pilot
Food Scanner = optional / не должен быть hidden dependency
```

Если явного owner ruling о включении Scanner в Pilot нет —
классифицировать как:

```text
NOT_IN_PILOT / OPTIONAL
```

а не как missing design.

---

# 7. SALON — НЕ ПЕРЕСМАТРИВАТЬ OWNER RULINGS МОЛЧА

Последние owner decisions по салону были приняты уже по current runtime.

Не открывать их заново только потому, что May spec говорит иначе.

Но нужно показать:

```text
MAY
AUGUST
RUNTIME
OWNER
```

по ключевым capabilities.

Особенно:

- Day / Today;
- schedule;
- team;
- services;
- chats;
- settings;
- booking create;
- cancel;
- reschedule;
- complete visit;
- client roster;
- unread messages / attention;
- rooms/cabinet;
- Ayla operational changes.

---

# 8. ТЕКУЩИЙ OWNER RULING ПО SALON IA

Для Controlled Pilot:

```text
День
Команда
Услуги
Чаты
Настройки
```

Это текущая рабочая IA.

Она не объявлена вечной target architecture.

Раздел можно убрать только после того, как его capability реально покрыта другой
Pilot surface.

Не строить пустую вкладку `Ayla`.

Не считать строку:

```text
Сегодня | Расписание | Ayla
```

автоматически CURRENT только потому, что она есть в августовском mock.

---

# 9. SALON — ДИАЛОГ И «ОБРАЩЕНИЕ»

Зафиксировать границу:

```text
Диалог — источник.
Обращение — отдельная доменная сущность, которой нет.
```

Допустимо вывести из реального conversation:

- есть непрочитанное;
- клиент написал;
- клиент ждёт ответа;
- время последнего сообщения;
- conversation требует внимания.

НЕДОПУСТИМО выдумывать:

```text
Обращение №...
тип: звонок
создал сотрудник
ответственный
статус
```

если такого domain object нет.

Общее правило:

```text
projection of existing fact = allowed
invented business entity for mock = forbidden
```

---

# 10. MASTER — ПРОВЕРИТЬ MAY СПЕКИ

Обязательно открыть и сверить:

```text
provider-calendar-schedule-flow.md
provider-booking-detail-flow.md
provider-messages-flow.md
master-solo-surface.md
```

Не для отмены owner decisions,
а чтобы установить, что именно August переписал поверх May.

---

# 11. CURRENT OWNER RULING — MASTER IA

Для мастера салона:

```text
Сегодня | Расписание | Ayla
```

Profile / Settings — из header/menu.

Для solo practitioner:

```text
День | Записи | Клиенты | Услуги | Ещё
```

Solo — отдельная operational persona.

Не считать разные bottom bars нарушением design system.

Design system определяет внешний вид компонента,
а не одинаковый набор разделов для разных ролей.

---

# 12. CURRENT OWNER RULING — «ВИЗИТ ИДЁТ»

Допустим passive derived UI:

```text
Идёт сейчас
Началось 14:00
До конца ≈ 20 мин
```

при условии:

- отдельный domain state `VISIT_IN_PROGRESS` не создаётся;
- это вычисление current time относительно booking schedule;
- UI ничего не требует от мастера.

---

# 13. CURRENT OWNER RULING — COMPLETION AUTHORITY

Обычный Master не подтверждает факт состоявшегося визита.

Admin / Owner на Salon surface может закрыть визит.

Если один физический человек имеет роли Master + Admin/Owner:

```text
authority определяется role/surface context,
а не личностью пользователя.
```

То есть:

```text
Master surface
→ no complete action

Salon/Admin surface
→ explicit confirmation
→ canonical completion command
→ authoritative readback
```

Downstream:

```text
commission
review eligibility
lifecycle / RFM
post-visit flow
```

запускаются только от authoritative completed visit.

Не пересматривать это без нового owner decision.

---

# 14. COLOR / VISUAL CANON

Отдельно не смешивать UX reconciliation с palette decision.

Последний owner decision:

**цвета Mini App должны соответствовать текущим Ayla-макетам.**

То есть:

- purple primary;
- lavender secondary;
- white surfaces;
- neutral grey;
- green success;
- red destructive.

Dusty rose / terracotta больше не target visual direction.

Но reconciliation-документ должен только показать provenance:
May / runtime / August / owner.

Не делать CSS правки в этом окне.

---

# 15. КАК РАБОТАТЬ С MAY SPECS

Для каждого крупного May spec не пересказывать 1000 строк.

Нужно извлечь только:

```text
USER JOB
PRIMARY FLOW
NAVIGATION
KEY STATES
ERROR / RECOVERY
DATA CONTRACT
ROLE / AUTHORITY
NOTABLE DO-NOT
```

После этого сравнить с August и runtime.

---

# 16. ДЕЛАТЬ НЕ ПО ЭКРАНАМ, А ПО CAPABILITY

Не надо строить reconciliation вида:

```text
Screen 1
Screen 2
Screen 3
```

Лучше:

```text
Booking
Schedule
Visit lifecycle
Client communication
Service management
Team management
Goal flow
Food diary
Food scanner
Profile/privacy
```

Потому что один capability может иметь несколько экранов и несколько поколений specs.

---

# 17. ФОРМАТ ОСНОВНОЙ МАТРИЦЫ

Создать таблицу:

| Capability | May spec | August Linear/mock | Current runtime | Owner ruling | Final status | Action |
|---|---|---|---|---|---|---|

Пример:

```text
Booking detail

May:
provider-booking-detail-flow.md

August:
DRF-...

Runtime:
legacy + current routes

Owner:
one canonical route

Status:
IMPLEMENTATION_MISMATCH

Action:
repoint all entry points to canonical detail
```

---

# 18. ОТДЕЛЬНОЕ ПРАВИЛО ДЛЯ CODE DEFECT

Code defect ставить только если доказано:

```text
CURRENT contract
        ≠
runtime
```

Не достаточно:

```text
August mock ≠ runtime
```

---

# 19. LINEAR

Не закрывать и не переписывать задачи.

Но подготовить:

```text
PROPOSED LINEAR MUTATIONS
```

для каждой задачи:

- оставить;
- amend;
- mark provenance;
- mark superseded;
- split;
- create CURRENT reconciliation task.

Формат:

| Issue | Current problem | Proposed mutation | Why |
|---|---|---|---|

---

# 20. SOURCE OF TRUTH RULE

В конце reconciliation предложить явный порядок source-of-truth.

Целевой принцип:

```text
Explicit Owner Decision
        ↓
CURRENT Canon / CURRENT Linear contract
        ↓
Reconciled UX spec
        ↓
Runtime
        ↓
May provenance / superseded history
```

Но не объявлять May superseded массово.

Каждый capability получает статус отдельно.

---

# 21. DELIVERABLE

Создать:

```text
docs/UX_CANON_RECONCILIATION.md
```

Структура:

# 0. Executive verdict

Ответить:

- сколько May specs реально влияют на current runtime;
- сколько August redesigns не были reconciled;
- сколько настоящих implementation defects;
- сколько ложных дефектов было снято;
- сколько owner rulings уже имеют precedence.

# 1. Canon layers

MAY
AUGUST
RUNTIME
OWNER

# 2. Reconciliation rules

# 3. Client

## 3.1 Booking / C05
## 3.2 Booking detail
## 3.3 Records
## 3.4 Food Diary
## 3.5 Food Scanner
## 3.6 Profile / privacy

# 4. Salon

## 4.1 IA
## 4.2 Day
## 4.3 Schedule
## 4.4 Team
## 4.5 Services
## 4.6 Chats
## 4.7 Settings
## 4.8 Conversation vs Inquiry
## 4.9 Visit completion

# 5. Master

## 5.1 Salon master IA
## 5.2 Solo IA
## 5.3 Schedule
## 5.4 Booking detail
## 5.5 Messages
## 5.6 Current visit
## 5.7 Completion authority

# 6. Three-way divergence matrix

# 7. True implementation defects

Только доказанные.

# 8. Redesigns not reconciled

# 9. May specs still authoritative

# 10. Superseded May specs

Только по доказательству.

# 11. Owner rulings with precedence

# 12. Proposed Linear mutations

# 13. Final CURRENT map

---

# 22. ОТДЕЛЬНО — CLIENT BOOKING DETAIL

Обязательно проверить:

```text
booking success → Открыть запись
bot → Мои записи
```

Если ведут на разные detail routes:

пометить как настоящий P0/Pilot consistency defect.

Не создавать новый экран.

Предложение:

```text
ONE canonical booking detail route
```

---

# 23. ОТДЕЛЬНО — FOOD SCANNER

Если May spec подтверждена:

не писать `missing design`.

Проверить только scope:

```text
Pilot?
Optional?
Post-pilot?
```

Если owner ruling отсутствует:

вывести как отдельный owner scope question,
но не blocker автоматически.

---

# 24. EVIDENCE STANDARD

Каждый вывод:

```text
MAY:
path + section

AUGUST:
Linear issue / mock / comment

CODE:
file:line

OWNER:
OD / CURRENT comment / explicit ruling
```

Если один слой не найден:

```text
NO SOURCE
```

Если вывод предполагаемый:

```text
INFERRED
```

Если проверен:

```text
VERIFIED
```

---

# 25. НЕ ДЕЛАТЬ

НЕ:

- переписывать May specs;
- объявлять их legacy пакетом;
- массово канонизировать August;
- менять код;
- открывать PR;
- мержить;
- deploy;
- закрывать Linear;
- рисовать новые экраны;
- расширять scope Food Scanner;
- создавать Inquiry;
- менять authority;
- менять navigation owner rulings.

---

# 26. ФИНАЛЬНЫЙ ОТВЕТ ГЛАВНОМУ ОКНУ

После отчёта вернуть коротко:

```text
UX CANON RECONCILIATION

May specs reviewed: X
August contracts reviewed: X
Capabilities reconciled: X

KEEP_FROM_MAY: X
SUPERSEDED_BY_AUGUST: X
SUPERSEDED_BY_OWNER: X
REDESIGN_NOT_RECONCILED: X
IMPLEMENTATION_MISMATCH: X
THREE_WAY_DIVERGENCE: X
UNRESOLVED: X

True Pilot defects:
1.
2.
3.

Previously false defects removed:
1.
2.
3.

Owner decisions still needed:
1.
2.

Highest-priority reconciliation action:
...
```

---

# 27. ГЛАВНАЯ ЦЕЛЬ

После этого документа мы должны перестать задавать вопрос:

> «Какой макет правильный?»

и начать отвечать на более точный:

> «Какой contract CURRENT для конкретной capability и почему?»

Если после твоей работы следующее окно снова сможет открыть старый May spec и
объявить уже принятое owner решение ошибкой — reconciliation не выполнен.
