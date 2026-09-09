ЗАДАЧА: ЗАФИКСИРОВАТЬ SYSTEM & RECOVERY STATES ДЛЯ AYLA MASTER MVP И ЗАМОРОЗИТЬ ЭТОТ UX-СЛОЙ ДЛЯ IMPLEMENTATION

Контекст

Основные Master MVP surfaces уже спроектированы и зафиксированы:

- Сегодня / Operations Home;
- Расписание;
- Working Hours / Specific Day Exception / Time Off / Conflict Guard;
- Manual Booking;
- Appointment Detail;
- Ayla.

Appointment Detail P0 UX уже frozen for implementation.

Теперь нужно зафиксировать единый сквозной UX-язык системных и recovery-состояний, который применяется ко всем Master MVP surfaces.

Это НЕ новый product feature.
Это НЕ отдельная domain model.
Это НЕ новая state machine.
Это presentation/recovery contract поверх существующих canonical projections, commands, permissions, freshness, idempotency и authoritative readback semantics.

Главная цель:

engineering-agent не должен самостоятельно решать:

- что показывать при Loading;
- чем Empty отличается от Error;
- можно ли читать cached data Offline;
- можно ли писать при Stale;
- что делать при Unknown result;
- как показывать Permission;
- как локализовать Error;
- что делать при business Conflict.

После этой задачи System & Recovery UX считается frozen для Master MVP.

==================================================
0. СНАЧАЛА ПРОЧИТАТЬ АКТУАЛЬНЫЙ CANON
==================================================

Перед изменениями изучи актуальные версии минимум:

00 Foundation/Ayla Repository Responsibility Matrix.md

00 Foundation/Ayla Knowledge Area Taxonomy.md

05 Architecture/Ayla MVP Appointment Contract.md

05 Architecture/Ayla Domain Event Registry.md

06 Product/Ayla Salon Operations MVP Contract.md

07 UX/Ayla Master Operations Screen Contract.md

07 UX/Ayla Appointment Detail Screen Contract.md

07 UX/Ayla Master Appointment Flow MVP.md

07 UX/Ayla Master App Information Architecture.md

07 UX/Ayla Master Schedule UX Contract.md

Также найди актуальный UX/document, в который по Knowledge Area Taxonomy логично поместить общие System / Recovery presentation rules.

Не создавать новый документ автоматически.

Сначала применить Document Creation Rule:

- если эти правила принадлежат существующему Master UX contract / shared UX contract и могут быть добавлены туда без смешения ответственности — обновить существующий документ;
- если существующего canonical owner действительно нет и без отдельного contract возникает дублирование между Today / Schedule / Appointment Detail / Manual Booking / Ayla — тогда создать минимальный отдельный canonical UX contract в правильной canonical area.

Не создавать вспомогательные документы без необходимости.

==================================================
1. HANDOFF И MOCKUPS
==================================================

Handoff documents не использовать как Source of Truth.

Visual mockup является Design Evidence / Visual Reference.

Актуальный System & Recovery composite:

/mnt/data/a_clean_ui_design_system_ux_state_guide_poster_f.png

Если локальный путь недоступен агенту, текст owner decisions этого prompt являются достаточным нормативным основанием.

Visual Reference не переопределяет canon.

Приоритет:

Canonical Domain/Product Contract
→ Owner Decision
→ Canonical UX Contract
→ Design Evidence.

==================================================
2. ГЛАВНЫЙ UX-ПРИНЦИП
==================================================

Зафиксировать:

Если на экране уже есть usable previously loaded data, системная проблема НЕ должна автоматически уничтожать рабочий контекст.

Правило:

NO USABLE DATA
→ full-surface recovery state.

USABLE PREVIOUS DATA
→ preserve content
→ mark trust/freshness/problem
→ show recovery locally.

Это применяется ко всем Master MVP surfaces.

Не превращать каждую ошибку в blank screen.

==================================================
3. TRUST MODEL
==================================================

Зафиксировать единый trust model.

FRESH

- данные можно читать;
- разрешённые writes могут начинаться;
- каждый consequential write всё равно использует canonical server-side validation / version / concurrency rules.

STALE

- данные можно читать;
- presentation должна явно сообщать, что данные могли измениться;
- consequential write требует authoritative revalidation;
- пользователь не обязан вручную нажимать Refresh перед каждым действием;
- действие само инициирует необходимую revalidation.

OFFLINE

- last-known/cached read допускается, если такой data context уже существует;
- cached data должна явно маркироваться как неактуальная/последняя известная;
- consequential writes в Master MVP не выполняются;
- offline command queue НЕ входит в P0.

UNKNOWN WRITE RESULT

- результат consequential command неизвестен;
- нельзя считать операцию success;
- нельзя считать её confirmed failure;
- нельзя blindly повторять write;
- требуется authoritative reconciliation/readback.

Важно:

Fresh / Stale / Offline являются data trust conditions.

Unknown относится прежде всего к operation result.

Не изображать их как один линейный lifecycle:

Fresh → Stale → Offline → Unknown.

==================================================
4. LOADING
==================================================

Разделить:

INITIAL LOADING

и

BACKGROUND REFRESH.

Initial loading:

- если usable data ещё нет — skeleton / loading structure;
- skeleton должен сохранять ожидаемую геометрию поверхности;
- не показывать fake content;
- consequential actions недоступны до получения необходимого authoritative context.

Background refresh:

- существующие данные остаются на экране;
- показывается compact refresh/progress indication;
- не заменять весь экран skeleton-ом;
- не создавать layout jump без необходимости.

Application shell / global navigation не должны исчезать при обычной загрузке content surface, если пользователь уже авторизован и shell известен.

==================================================
5. EMPTY
==================================================

Empty является нормальным business state.

Empty НЕ равно:

- Error;
- Offline;
- Permission denied;
- Stale;
- Loading.

Примеры:

Today:
«На сегодня записей нет».

Schedule:
рабочий день может не иметь Appointment, но available intervals остаются видимыми и clickable согласно Schedule Contract.

Если есть разрешённый obvious next action, Empty может показывать его.

Например:

+ Добавить запись

Но action показывается только если canonical permission позволяет его.

Не использовать драматический error-like copy или error iconography.

Если система показывает «Следующий рабочий день», это допустимо только если такой факт получен/выведен из authoritative schedule context, а не предположен локально.

==================================================
6. OFFLINE
==================================================

P0 правило:

OFFLINE = READ LAST-KNOWN WHERE SAFE, NO CONSEQUENTIAL WRITE.

Если данные были загружены ранее:

- оставить их;
- показать компактный Offline banner/status;
- явно сообщить:
  «Нет подключения»
  «Показаны последние данные»
  или эквивалентный смысл.

Appointment information может оставаться читаемой.

Schedule может оставаться читаемым.

Но cached availability не должна выглядеть как гарантированно fresh/actionable.

Например cached:

14:00 Свободно

должно визуально и семантически восприниматься как last-known availability, а не подтверждённый current slot.

Если пользователь пытается начать consequential write Offline:

не выполнять command.

Объяснить:

«Для изменения расписания нужно подключение к интернету.»

или эквивалент по контексту.

Не реализовывать:

- offline appointment creation;
- offline Time Off writes;
- offline Working Hours writes;
- offline No Show writes;
- offline command queue;
- optimistic offline state mutation.

Это Deferred/Future.

==================================================
7. STALE
==================================================

Stale не является Error.

Показывать спокойный freshness indicator:

«Расписание могло измениться»

или эквивалент.

Допускается:

[ Обновить ]

Но главное правило:

пользователь НЕ должен вручную обслуживать freshness model системы перед каждым action.

Пример:

stale Schedule
→ пользователь taps available interval
→ система автоматически выполняет authoritative revalidation
→ если interval всё ещё доступен — продолжает flow
→ если нет — обновляет контекст / показывает Conflict / fresh alternatives.

То есть:

STALE READ
→ allowed.

STALE WRITE INTENT
→ revalidate first.

Не доверять stale slot как гарантированной availability.

==================================================
8. ERROR
==================================================

Разделить два случая.

ERROR WITHOUT USABLE DATA

Если usable data нет:

- full-surface error;
- короткое объяснение;
- safe recovery action:
  Повторить / Обновить / Вернуться.

Например:

Не удалось загрузить расписание.

[ Повторить ]

ERROR WITH USABLE PREVIOUS DATA

Если предыдущие usable data существуют:

- сохранить данные;
- показать local/banner error:
  «Не удалось обновить»
  «Показаны последние данные»
  или эквивалент;
- recovery action:
  Повторить.

Не путать Error и Offline.

Offline:
connectivity known absent.

Error:
request/system operation failed по иной или неизвестной причине.

==================================================
9. PERMISSION
==================================================

Разделить:

OBJECT / SCREEN LEVEL PERMISSION

и

ACTION LEVEL PERMISSION.

OBJECT LEVEL

Если пользователь больше не имеет права видеть Appointment/object/surface:

показать safe state:

«Запись недоступна»

«У вас больше нет доступа к этой записи.»

Primary action:

«Вернуться к сегодня»

или другой безопасный parent context.

Не раскрывать скрытые данные объекта.

ACTION LEVEL

Если пользователь может читать object, но не имеет права на конкретный action:

- сам screen остаётся доступен;
- запрещённый action обычно не показывается;
- не показывать disabled button просто ради информирования, если это не нужно для объяснения уже начатого flow.

Пример:

can_view = true
can_mark_no_show = false

→ Appointment Detail показывается.
→ «Клиент не пришёл» action отсутствует.

Если permission изменилась между presentation и submit и сервер отклонил command:

показать локальное объяснение результата.

==================================================
10. PENDING / UNKNOWN
==================================================

Это обязательный P0 recovery state для consequential operations.

Пример:

CreateAppointment request отправлен.
Transport interrupted.
Неизвестно, committed write или нет.

UI должен сообщать примерно:

«Проверяем результат…»

«Пока не удалось подтвердить результат.»

«Не повторяйте действие, пока мы проверяем состояние.»

Не показывать:

«Ошибка. Создать ещё раз.»

если операция могла быть committed.

Не выполнять blind retry.

Главное правило:

UNKNOWN BELONGS TO THE OPERATION,
NOT AUTOMATICALLY TO THE WHOLE APPLICATION.

Например:

CreateAppointment = Unknown

не означает:

Schedule = broken.

Глобальная навигация и другие независимые reads могут оставаться доступны.

==================================================
11. AUTOMATIC RECONCILIATION
==================================================

При Unknown reconciliation должна по возможности стартовать автоматически.

Flow:

UNKNOWN
↓
automatic authoritative readback / reconciliation
↓
known result
→ update presentation

если быстро не resolved:
→ оставить explicit Unknown
→ предоставить fallback action:
  «Проверить снова».

Таким образом:

«Проверить снова»

является fallback/recovery affordance,
а не обязательным механизмом разрешения каждого Unknown.

Использовать существующие canonical:

- idempotency;
- version;
- correlation;
- readback;
- freshness;
- concurrency

rules.

Не создавать новую operation state machine в UX contract.

==================================================
12. CONFLICT
==================================================

Conflict является normal business concurrency state, а не generic Error.

Пример Manual Booking:

выбранное время 14:00 стало недоступно до final commit.

Показывать:

«Это время уже занято»

или эквивалент.

Сохранять максимально возможный draft context.

Пример:

Customer ✓
Service ✓
Date ✓
Time ✕

Не уничтожать весь booking draft.

Показывать fresh alternatives, если authoritative availability позволяет:

14:30
15:30
17:00

Primary action:

- выбрать новую альтернативу;
- или открыть time picker.

Не выполнять silent time shift.

==================================================
13. SMALLEST FAILURE SURFACE PRINCIPLE
==================================================

Добавить сквозной owner-approved UX принцип:

SYSTEM FAILURE SHOULD AFFECT THE SMALLEST SURFACE THAT ACTUALLY BECAME UNTRUSTWORTHY.

Примеры:

Customer Context failed
→ Appointment Summary может продолжать работать.
→ Service Context может продолжать работать.
→ Customer Context section показывает local error.

CreateAppointment result unknown
→ Booking operation показывает Unknown.
→ Schedule shell/navigation могут продолжать работать.

Ayla availability read failed
→ Ayla не должна уверенно утверждать current availability.
→ это не обязательно ломает весь Ayla conversation.

Timeline refresh failed
→ сохраняем last-known timeline, если он есть.

Не поднимать локальный section failure до full-screen error без необходимости.

==================================================
14. AYLA
==================================================

Ayla использует те же trust/recovery rules.

Если authoritative schedule недоступен:

Ayla не должна отвечать как будто знает актуальную availability.

Допустимая semantics:

«Не могу сейчас подтвердить актуальное расписание.»

Если разрешено показать last-known context:

«По последним данным...»

но он должен быть явно labelled stale/cached.

Conversational write:

«Запиши Марию в 14:00»

не превращается в executable proposal, если required authoritative availability/preflight недоступен.

AI uncertainty никогда не превращается в operational certainty.

==================================================
15. NAVIGATION RECONCILIATION
==================================================

В System & Recovery Design Evidence визуально присутствует bottom navigation:

Сегодня / Расписание / Создать / Ayla / Ещё

НЕ канонизировать эту navigation из mockup.

Актуальное owner decision Master MVP:

Сегодня | Расписание | Ayla

Manual Booking:

contextual action / Add action,
а не обязательный global navigation destination.

«Ещё» также не считается P0 global destination только потому, что присутствует в visual iteration.

В Design Evidence явно отметить:

bottom navigation shown in the composite is non-canonical / superseded visual chrome where it differs from:

Сегодня | Расписание | Ayla.

Не менять принятую navigation model.

==================================================
16. SEVERITY / VISUAL PRIORITY
==================================================

Зафиксировать семантический приоритет, не pixel-perfect colors.

Empty
→ neutral.

Stale
→ mild warning / freshness condition.

Offline
→ connectivity warning.

Error
→ explicit failure.

Conflict
→ contextual business resolution state.

Unknown
→ informational uncertainty requiring reconciliation.

Permission
→ access/security boundary.

Не использовать одинаково тревожный visual treatment для всех states.

System chrome не должен постоянно визуально доминировать над operational content.

Time-sensitive master work remains primary.

==================================================
17. GLOBAL STATE MATRIX
==================================================

Включить компактную matrix примерно такого смысла:

State | Existing data | Read | Consequential write

Loading
- no usable data
- wait
- no

Empty
- authoritative empty
- yes
- yes where permitted

Offline
- cached/last-known may exist
- yes, explicitly marked
- no

Stale
- yes
- yes
- only after authoritative revalidation

Error + cached data
- yes
- yes with warning
- only when required data can be revalidated

Error without data
- no
- no
- no

Permission
- only authorized
- authorized subset
- only authorized subset

Pending/Unknown
- existing reads remain where valid
- yes where independent
- do not blindly repeat unknown write

Conflict
- fresh conflict context
- yes
- choose/submit allowed alternative

Не считать эту таблицу новой domain state machine.

Это UX behavior matrix.

==================================================
18. P0 NON-GOALS
==================================================

Не включать в P0:

- offline-first architecture;
- background offline command queue;
- optimistic domain writes;
- complex sync conflict resolution;
- user-facing technical diagnostics;
- retry storm;
- hidden auto-resubmission of unknown writes;
- generic global error page for every failure;
- duplicate booking retry from Unknown;
- new domain statuses for loading/stale/offline/unknown/conflict.

==================================================
19. DESIGN EVIDENCE
==================================================

Добавить/зафиксировать System & Recovery composite как Design Evidence.

Состояния:

1. Loading
2. Empty
3. Offline
4. Stale
5. Error without data
6. Error with previous data
7. Permission object-level
8. Permission action-level
9. Pending / Unknown
10. Conflict

Design Evidence подтверждает behavioral language, но не domain semantics.

Обязательная annotation:

The visual bottom navigation in this iteration is not canonical where it differs
from the approved Master MVP navigation Today | Schedule | Ayla.

Также Design Evidence должен трактоваться с owner refinements:

- offline availability is not fresh/actionable;
- stale writes auto-revalidate;
- unknown auto-reconciles;
- failure is localized to smallest affected surface;
- contextual actions do not become global navigation destinations.

==================================================
20. RECONCILIATION
==================================================

Проверить минимум:

Master Operations Screen Contract
Master Schedule UX Contract
Appointment Detail Screen Contract
Master Appointment Flow MVP
Master App Information Architecture
Ayla-related current UX sections/contracts
Appointment Contract
Repository Responsibility Matrix

Не переписывать документы массово.

Если existing document уже содержит совместимые:

Loading
Offline
Stale
Unknown
Conflict
Permission

rules — не дублировать бесконтрольно.

Новый/shared system-state section должен стать single UX reference там, где это возможно, а surface-specific contracts могут ссылаться/оставлять только специфические особенности.

Если создание shared contract по taxonomy неоправданно, оставить rules в существующем правильном owner document и сделать минимальный reconciliation.

==================================================
21. P0 UX FREEZE
==================================================

После успешной интеграции зафиксировать:

MASTER MVP SYSTEM & RECOVERY UX — FROZEN FOR IMPLEMENTATION.

Freeze включает:

- Loading;
- Empty;
- Offline;
- Stale;
- Error;
- Permission;
- Pending / Unknown;
- Conflict;
- trust model;
- automatic revalidation;
- automatic reconciliation;
- smallest failure surface;
- no offline writes;
- approved navigation boundary.

Дальнейшие изменения только при:

1. demonstrated implementation blocker;
2. canonical domain contradiction;
3. explicit owner decision.

Не считать обычные визуальные polish-задачи основанием для reopening UX semantics.

==================================================
22. VALIDATION
==================================================

После изменений выполнить:

git diff --check

targeted validator

knowledge validator

релевантные unit tests.

Проверить:

- новые domain statuses не созданы;
- новые domain events не созданы;
- новые commands не созданы;
- offline writes не появились;
- Unknown не считается failed;
- Stale не считается fresh;
- Empty не считается Error;
- Error не всегда уничтожает previous content;
- Permission применяется на минимально необходимом уровне;
- bottom navigation не изменена на основании mockup;
- System state локализуется к smallest affected surface;
- P0 freeze зафиксирован.

Baseline errors не исправлять в рамках этой задачи.

==================================================
23. SUMMARY
==================================================

Создать/обновить REPLY summary согласно repository conventions.

Раздел:

FINAL MASTER MVP SYSTEM & RECOVERY UX

Обязательно перечислить:

- preserve previous usable data where possible;
- Loading initial vs background refresh;
- Empty is normal;
- Offline cached read / no consequential write;
- Stale read allowed / automatic revalidation before write;
- Error with and without prior data;
- object-level vs action-level Permission;
- Unknown does not equal failure;
- automatic reconciliation;
- Conflict preserves valid draft context;
- Smallest Failure Surface Principle;
- Ayla follows same trust rules;
- composite Design Evidence considered;
- non-canonical Create / More bottom tabs explicitly rejected;
- approved navigation remains Today | Schedule | Ayla;
- System & Recovery UX frozen for implementation.

==================================================
24. DEFINITION OF DONE
==================================================

Работа завершена, если developer может без дополнительных owner questions ответить:

1. Что показывать при initial Loading?
2. Что делать при background refresh?
3. Чем Empty отличается от Error?
4. Что мастер может делать Offline?
5. Можно ли доверять cached availability?
6. Что делать с Stale data?
7. Нужно ли мастеру вручную Refresh перед action?
8. Когда применяется automatic revalidation?
9. Чем Error without data отличается от Error with previous data?
10. Что делать при object-level Permission denial?
11. Что делать при action-level Permission denial?
12. Что показывать при Unknown write result?
13. Можно ли повторить unknown write?
14. Когда применяется automatic reconciliation?
15. Что сохраняется при Conflict?
16. Почему Conflict не является generic Error?
17. Почему local failure не должен ломать весь screen?
18. Как Ayla ведёт себя при stale/offline/unknown context?
19. Какая global navigation является canonical?
20. Какие offline/sync capabilities намеренно не входят в P0?

Ключевой P0 принцип:

READ WHEN SAFE.
WRITE ONLY WHEN AUTHORITATIVE VALIDATION IS POSSIBLE.
UNKNOWN → RECONCILE BEFORE RETRY.
FAIL LOCALLY, NOT GLOBALLY.

После успешной validation считать:

AYLA MASTER MVP SYSTEM & RECOVERY UX — FROZEN FOR IMPLEMENTATION.