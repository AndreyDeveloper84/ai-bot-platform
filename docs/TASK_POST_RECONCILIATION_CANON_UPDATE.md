# PROMPT АГЕНТУ — POST-RECONCILIATION CANON UPDATE + CONTROLLED PILOT RULINGS

Ты работаешь как **senior product architect / canon maintainer** проекта Ayla.

Исходный reconciliation уже выполнен:

`ai-bot-platform/docs/UX_CANON_RECONCILIATION.md`

Его ключевой вывод сохраняется:

`SUPERSEDED_BY_AUGUST = 0`

Августовский слой сам по себе не доказал отмену ни одной майской механики. Теперь появились новые явные owner rulings, которые надо внести в CURRENT canon.

---

# 0. ЦЕЛЬ ЭТОГО ЗАХОДА

Нужно:

1. обновить `UX_CANON_RECONCILIATION.md`;
2. снять уже закрытые owner questions;
3. поставить surgical CURRENT OVERRIDE только там, где May Canonical text реально противоречит новому owner ruling;
4. не ставить override там, где foundation подтверждён;
5. обновить Controlled Pilot scope;
6. сформировать финальный список Pilot implementation work;
7. не переписывать May specs целиком;
8. не объявлять весь May layer legacy.

Это прежде всего **canon update**, а не новый UX-аудит.

---

# 1. OWNER DECISION — FOOD DIARY + FOOD SCANNER

## OD-FOOD-PILOT — 25.08.2026

**Весь существующий контур Food Diary + Food Scanner входит в Controlled Pilot.**

Это supersede предыдущий вывод:

`Food Scanner → NOT_IN_PILOT / OPTIONAL`

и любые старые scope-пометки `postpilot`, если capability уже описана в существующих food specs.

В Pilot входят все уже описанные food capabilities, включая, если они реально присутствуют в specs/runtime:

- Food Diary;
- Food Scanner;
- вход в Scanner из бота / Mini App;
- камера;
- галерея;
- загрузка фото;
- распознавание блюда;
- loading;
- high confidence;
- low confidence;
- clarification;
- not recognized;
- API failure;
- upload failure;
- offline;
- manual fallback;
- ручной ввод;
- meal type;
- дата / время;
- backdate;
- portion adjustment;
- сохранение;
- открытие записи;
- редактирование;
- удаление;
- дневные итоги;
- recovery states.

Важно:

`existing food capability → IN PILOT`

`new speculative capability → NOT automatically approved`

Ничего нового не придумывать только из-за формулировки «всё, что касается».

---

# 2. ОБНОВИТЬ FOOD STATUS В RECONCILIATION

Больше нельзя оставлять:

`Food Scanner → NOT_IN_PILOT / OPTIONAL`

Новая классификация:

`Food Scanner → SUPERSEDED_BY_OWNER / CONTROLLED_PILOT`

`Food Diary → CONTROLLED_PILOT`

Если статусная модель документа допускает только один verdict, использовать:

`SUPERSEDED_BY_OWNER`

и рядом:

`Pilot scope: INCLUDED`

Пересчитать summary честно.

Минимально ожидаемо:

`NOT_IN_PILOT: 1 → 0`

`SUPERSEDED_BY_OWNER: +1`

если методика подсчёта это подтверждает.

---

# 3. FOOD SCANNER БОЛЬШЕ НЕ OWNER QUESTION

Удалить из списка открытых решений вопрос:

`Food Scanner в Controlled Pilot?`

Он закрыт.

Не оставлять его как:

- OPEN;
- OPTIONAL;
- POST-PILOT.

---

# 4. `open_food_scan` БОЛЬШЕ НЕ SCOPE DRIFT

Раньше runtime-entry считался hidden scope drift, потому что owner ruling отсутствовал.

Теперь:

`open_food_scan in Pilot entry surface → EXPECTED / ALLOWED`

Не убирать кнопку из Pilot.

Если уже подготовлена задача «убрать open_food_scan» — пометить её как отменённую новым owner decision, но **не закрывать Linear самостоятельно**.

---

# 5. FOOD DIARY НЕ ЗАВИСИТ ОТ SCANNER

Оба входят в Pilot, но архитектурная граница сохраняется:

`Food Diary ≠ wrapper around Scanner`

Diary должен оставаться usable через:

- Scanner;
- manual entry;
- другие уже предусмотренные entry paths.

Scanner не делать обязательной hidden dependency.

---

# 6. FOOD DATA НЕ СТАНОВИТСЯ OUTCOME EVIDENCE АВТОМАТИЧЕСКИ

Сохраняется:

`Food Diary → everyday signal / Plan Adherence / nutrition context`

Но:

`Food Diary ≠ automatic Desired Outcome evidence`

Нельзя автоматически делать:

`food entry → ProgressObservation`

Нельзя автоматически выводить:

`sodium → edema`

или иные health inference без отдельного approved evidence/policy contract.

---

# 7. OWNER DECISION ПО ПАЛИТРЕ — PURPLE РЕШЕНИЕ ОТМЕНЕНО

В reconciliation сейчас есть устаревшее:

`palette → UNRESOLVED`

`owner → purple`

Это нужно исправить.

CURRENT foundation:

- `docs/design/policies/ayla-identity-and-brand.md`
- `docs/design/system/design-tokens.md`

CURRENT Mini App palette:

`SAGE`

Ключевые anchors:

`#7ba478` = sage-400 = decorative brand accent only

`#5a8557` = sage-500 = text-safe / UI-boundary-safe primary

`#4a6e47` = sage-600 = hover / pressed / stronger focus

Purple остаётся только для:

`MAX / Telegram bot-channel avatar / channel identity`

Purple не использовать как Mini App primary.

Terracotta runtime:

`#c47b6c → IMPLEMENTATION DRIFT FROM VISUAL CANON`

---

# 8. НЕ СТАВИТЬ OVERRIDE НА BRAND FOUNDATION

Ранее потенциальной «миной» считались:

- `design-tokens.md`;
- `ayla-identity-and-brand.md §4.4`.

После отмены purple ruling это больше не мины.

Наоборот, эти документы остаются CURRENT foundation.

НЕ:

- ставить override;
- объявлять sage superseded;
- переписывать palette в foundation.

---

# 9. ДВА OVERRIDE-БАННЕРА, КОТОРЫЕ НУЖНЫ

## 9.1 `provider-calendar-schedule-flow.md` §3

Старый Canonical May IA:

`Главная · Календарь · Клиенты · Команда · Профиль`

CURRENT Salon Controlled Pilot IA:

`День · Команда · Услуги · Чаты · Настройки`

Поставить CURRENT OVERRIDE только перед §3 / navigation contract.

Остальную spec не объявлять superseded.

Рекомендуемый текст:

> [!IMPORTANT]
> **CURRENT OVERRIDE — 25.08.2026**
>
> Навигационный контракт этого раздела superseded явным owner ruling.
>
> Для Salon Controlled Pilot CURRENT IA:
>
> `День · Команда · Услуги · Чаты · Настройки`
>
> Раздел нельзя убирать из навигации, пока его capability реально не покрыта другой Pilot surface.
>
> Override относится только к IA / §3. Остальная спецификация сохраняется как implementation provenance и действует там, где не отменена отдельно.

## 9.2 `provider-booking-detail-flow.md` §11

Старый Canonical role matrix:

`Team-master → Mark completed: yes`

CURRENT:

`ordinary Master → NO completion authority`

`Admin / Owner surface → MAY complete visit`

Для multi-role пользователя:

`authority определяется active role/surface context`

Поставить CURRENT OVERRIDE перед authority matrix / §11.

Не отменять downstream lifecycle chain после authoritative completion.

Рекомендуемый текст:

> [!IMPORTANT]
> **CURRENT OVERRIDE — 25.08.2026**
>
> Матрица authority этого раздела superseded в части `Team-master → Mark completed`.
>
> CURRENT:
>
> - ordinary Master НЕ подтверждает обычный состоявшийся визит;
> - на Master surface action `Состоялся` не показывается;
> - Admin / Owner может выполнить completion на Salon/Admin surface;
> - для multi-role пользователя authority определяется active role/surface context.
>
> Lifecycle chain после authoritative `completed` остаётся действующей.

---

# 10. RECOMMENDATION + WHY — OWNER RULING

Reconciliation нашёл главный клиентский mismatch:

`✨ Ayla подобрала`

показывается без displayable WHY.

May требует reasoning как NON-NEGOTIABLE.

August C04 = `Recommendation + WHY`.

CURRENT owner decision:

**Нет displayable WHY → нет блока «Ayla подобрала».**

Если backend отдаёт допустимые reasons:

`Recommendation = WHAT + WHY + WHAT NEXT`

WHY:

- 2–3 коротких displayable reasons;
- только из реальных разрешённых user facts / explicit context;
- без internal reason codes;
- без confidence numbers;
- без chain-of-thought;
- без fake copy.

Если WHY нет — брендированную рекомендацию Ayla не показывать.

Можно показать обычный каталог / execution surface, но нельзя подписывать его как персональную подборку Ayla.

Фронтенду запрещено генерировать WHY самостоятельно или использовать generic заглушки вроде:

- «подходит тебе»;
- «выбрано по твоей цели»;
- «Ayla рекомендует»;

если source этого не доказывает.

---

# 11. ОБНОВИТЬ §3.1.3 RECONCILIATION

В Recommendation WHY больше нельзя оставлять:

`OWNER: NO SOURCE`

Добавить owner ruling:

`NO displayable WHY → recommendation block hidden`

Статус остаётся:

`IMPLEMENTATION_MISMATCH`

пока runtime не соответствует.

---

# 12. `Ayla · Изменения` — УТОЧНИТЬ BACKEND BLOCK

Сохраняется:

`Ayla · Изменения → backend-blocked`

Но причина должна быть точной.

Не писать:

`событий нет`

Писать:

`booking lifecycle events существуют, но часть публикуется как best-effort telemetry и может быть потеряна`

`authoritative complete operational source для create/cancel/reschedule отсутствует`

Развести:

`apps.events → best-effort telemetry → not source of truth`

`apps.eventbus.DomainEvent → reliable outbox → покрывает недостаточный lifecycle set`

Не строить operational history на lossy telemetry.

Для разблокировки нужен authoritative lifecycle source + reliable outbox + projection/read API или эквивалентно полный источник.

---

# 13. CLIENT BOOKING DETAIL — P0 DEFECT

Сохраняется доказанный дефект:

`CustomerBookingSuccessScreen → /my-visits/:id`

CURRENT canonical:

`/customer/records/:id`

Не строить третий detail screen.

Минимальное направление:

`success CTA → canonical booking detail`

Но более широкий legacy migration cleanup вынести отдельно.

---

# 14. SALON TAB BAR — P0 DEFECT

Сохраняется:

`AdminTabBar = 5 tabs`

CSS:

`repeat(4, 1fr)`

Это настоящий implementation defect независимо от IA-споров.

Исправлять отдельной минимальной задачей.

---

# 15. SALON SCHEDULE / MASTER BOOKING DETAIL

Reconciliation показал:

`Salon Schedule → May canonical exists, runtime not built`

`Master Booking Detail → May canonical exists, runtime not built`

Не рисовать заново.

Следующий шаг:

`read May contract → validate domain/API → estimate → implement`

---

# 16. SOURCE-OF-TRUTH ORDER — ОБНОВИТЬ

Новый порядок:

1. Explicit Owner Ruling
2. Strategic / Foundation Policy
3. Canonical capability spec
4. Fully signed accepted implementation spec
5. Sanctioned runtime deviation / orchestrator GO
6. Reconciled CURRENT Linear contract
7. Unreconciled August redesign / ordinary Linear task
8. Draft spec without sign-off
9. Runtime behavior without contract

Дата документа сама по себе не даёт precedence.

---

# 17. AUGUST LAYER

Сохраняется:

`SUPERSEDED_BY_AUGUST = 0`

Если capability совпадает с August после нового owner ruling, классифицировать её как:

`SUPERSEDED_BY_OWNER`

если именно owner сделал её CURRENT.

---

# 18. ОБНОВИТЬ FINAL CURRENT MAP

Минимум привести к такому состоянию:

## CLIENT

Booking F1–F5  
→ signed May booking contract

Recommendation + WHY  
→ May NON-NEGOTIABLE + C04 + owner ruling  
→ no WHY = no `Ayla подобрала`

Booking detail  
→ `/customer/records/:id` canonical

Food Diary  
→ CONTROLLED PILOT

Food Scanner  
→ CONTROLLED PILOT  
→ весь существующий food-spec contour included

Profile  
→ signed May + newer privacy rulings

## SALON

IA  
→ `День · Команда · Услуги · Чаты · Настройки`

Day  
→ runtime + owner ruling

Schedule  
→ canonical May, NOT BUILT

Team / Services / Chats  
→ May provenance + runtime

Settings  
→ CURRENT_RUNTIME_ONLY

Visit completion authority  
→ owner ruling

Ayla · Изменения  
→ backend-blocked: authoritative operational projection absent

## MASTER

Salon master IA  
→ owner ruling

Solo IA  
→ May + owner ruling

Schedule  
→ May LOCKED + runtime

Booking detail  
→ canonical May, NOT BUILT

Messages  
→ canonical May + runtime

Current visit  
→ passive derived UI allowed

Completion authority  
→ no completion action for ordinary Master

## VISUAL

Mini App  
→ sage foundation

sage-400 `#7ba478` decorative

sage-500 `#5a8557` interactive/text-safe

purple  
→ bot/channel avatar only

terracotta  
→ runtime drift

---

# 19. ОБНОВИТЬ EXECUTIVE VERDICT

После изменений пересчитать:

- status counts;
- unresolved count;
- owner questions;
- Pilot scope.

Должны исчезнуть:

- `Food Scanner owner question`;
- `Food Scanner NOT_IN_PILOT`;
- `Palette UNRESOLVED`.

Palette считать закрытым:

`CURRENT = sage`

---

# 20. МЕРА УСПЕХА RECONCILIATION

Раньше оставалось три «мины».

Теперь:

1. `provider-calendar-schedule-flow.md §3` → поставить override.
2. `provider-booking-detail-flow.md §11` → поставить override.
3. `design-tokens.md + ayla-identity-and-brand.md` → НЕ мина; это CURRENT foundation.

После двух surgical overrides и обновления reconciliation этап можно считать завершённым.

---

# 21. ЧТО МОЖНО МУТИРОВАТЬ

Разрешено:

- обновить `docs/UX_CANON_RECONCILIATION.md`;
- добавить два CURRENT OVERRIDE banner;
- обновить reconciliation/report docs;
- обновить canonical owner-rulings markdown, если в проекте есть принятое место для owner decisions;
- исправить ссылки и статусные таблицы документации.

Не разрешено без отдельного GO:

- менять runtime code;
- merge;
- deploy;
- закрывать Linear;
- массово переписывать May specs;
- удалять legacy routes;
- строить Salon Schedule;
- строить Master Booking Detail;
- строить operational event projection;
- подключать Prompt Registry runtime;
- менять food business logic.

---

# 22. LINEAR — ТОЛЬКО PROPOSED MUTATIONS

Подготовить, не выполнять автоматически:

| Scope | Proposed action |
|---|---|
| Food Scanner | amend: INCLUDED IN CONTROLLED PILOT |
| Food Diary | explicitly mark Pilot |
| Recommendation WHY | P0: no branded recommendation without WHY |
| Booking success | P0: route to canonical booking detail |
| Client legacy migration | separate cleanup task |
| Salon tabbar | P0: 5 tabs / 4 columns |
| Salon Schedule | implementation task from May canonical |
| Master Booking Detail | implementation task from May canonical |
| Ayla · Изменения | authoritative event source + projection |
| Visual palette | runtime tokens → sage foundation |

Не закрывать существующие задачи.

---

# 23. FOOD PILOT READINESS — СЛЕДУЮЩИЙ E2E

Так как Food Diary + Scanner теперь в Pilot, следующий аудит должен проверять readiness:

`entry → scanner → camera/gallery → upload → recognition → high/low confidence → clarify → manual fallback → portion → meal type → date/time/backdate → save → diary → reopen → edit → delete`

Плюс:

- offline;
- API down;
- upload failed;
- not recognized;
- retry;
- cancel;
- re-entry;
- idempotency.

Для каждого проверить:

- UI;
- endpoint;
- persistence;
- error state;
- recovery;
- analytics/audit if applicable.

В текущем canon-update не выполнять этот E2E, если окно ограничено документацией. Сформировать как следующий Pilot readiness task.

---

# 24. VISUAL PILOT READINESS

Следующий implementation track:

`runtime terracotta → sage canonical tokens`

Использовать `design-tokens.md`.

Не переводить Mini App в purple.

Не делать механический HEX replace.

Проверить semantic aliases, accessibility и blast radius.

---

# 25. ФИНАЛЬНЫЙ ОТЧЁТ

Вернуть:

```text
POST-RECONCILIATION CANON UPDATE

Updated:
- UX_CANON_RECONCILIATION.md
- provider-calendar-schedule-flow.md §3 override
- provider-booking-detail-flow.md §11 override
- [другие docs]

Owner questions closed:
- Food Scanner Pilot scope
- Mini App palette

Current food scope:
Food Diary   = PILOT
Food Scanner = PILOT

Current palette:
sage = Mini App
purple = bot/channel only
terracotta = runtime drift

Recommendation:
no displayable WHY → no branded recommendation block

Remaining true P0 defects:
1.
2.
3.

Remaining unbuilt capabilities:
1.
2.

Remaining backend-blocked:
1.

Remaining owner decisions:
NONE / только реально оставшиеся.
```

---

# 26. ГЛАВНЫЙ ПРИНЦИП

После этого обновления нельзя снова задавать:

`Food Scanner входит в Pilot?`

Ответ:

**ДА. Весь существующий Food Diary + Food Scanner contour входит в Controlled Pilot.**

Нельзя снова задавать:

`Mini App purple или sage?`

Ответ:

**SAGE. Purple — только channel identity.**

Нельзя снова оставлять:

`Ayla подобрала`

без displayable WHY.

Ответ:

**Нет WHY → нет branded recommendation.**

Если эти три вопроса снова появляются как OPEN после обновления — canon update выполнен неправильно.
