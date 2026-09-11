# Реестр замеров боевого пилота

**Ведёт любое окно, снявшее замер.** Главное окно отвечает за формат и за то, чтобы протухшее было помечено, а не за монополию на запись. Все числа сняты живыми запросами к работающим контейнерам — не выведены из кода и не пересказаны из отчётов.

> **Замер, не записанный в реестр, не существует.** Знание, живущее только в переписке, к утру становится противоречием — и выстреливает, когда по нему уже работают.

## Как читать этот файл

У каждого замера четыре обязательные части: **когда**, **чем снято**, **что получено**, **что это значит**. Пятая — **срок годности**.

**Число старше суток цитировать нельзя.** Перемерить дешевле, чем ошибиться: все команды приведены дословно и воспроизводятся одной строкой.

## Контур

| | хост | контейнер | что это |
|---|---|---|---|
| бот | `api-dev.gobeauty.site` | `ayla-bot-staging-web-1` | MAX-бот, Mini App API, зеркало каталога |
| Ayla | `dev.gobeauty.site` | `dev-web-1` | источник истины: салоны, мастера, услуги, цели, питание |
| Mini App | `miniapp-dev.gobeauty.site` | статика | собранный клиент |

Доступ: `ssh taximeter@176.119.159.141`.

**Пробуйте сами.** Раньше здесь было написано, что у окон доступ закрыт классификатором и замеры идут через главное окно. 09.09 два окна проверили и опровергли: **стена оказалась свойством отдельных сессий, а не проекта**. Не вышло у вас — просите числа у главного окна, но сначала попробуйте.

**Кто снял — тот и записывает.** Раздел подписывается окном и датой.

---

# 1. Каталог и разметка услуг

## 1.1 Статусы связи услуги с шаблоном

**Снято** 08.09.2026 16:40 MSK, контейнер `dev-web-1`.

```python
from services.models import SalonService
from django.db.models import Count
dict(SalonService.objects.values_list("mapping_status").annotate(n=Count("id")))
```

```
{'review_required': 206, 'unmapped': 59}
```

`verified` **отсутствует как ключ, то есть ноль**.

**Что значит.** Полка подбора не загорится ни при каком коде: подтверждённых связей нет. Это **штатное состояние**, а не поломка — клиент честно отвечает `NO_VERIFIED_CANDIDATES`.

## 1.2 Разбивка по салонам — форма важнее суммы

**Снято** 08.09.2026 16:55 MSK, `dev-web-1`.

```
fevralskiy-svet   review_required 43
formula-tela      unmapped        58     <- ноль review_required
mednyy-kovsh      review_required 17
mkt-afrodita      review_required  8
mkt-lumina        review_required  2
mkt-mediclinic    review_required 24
mkt-spatrium      review_required  1 / unmapped 1
olhovyy-dvor      review_required 62
pylca-i-lyon      review_required 21
sorok-okon        review_required 28
```

**Что значит.** Не градиент, а **разрыв**: у `formula-tela` ноль связей, которые шаблон хотя бы предположил; у остальных десяти — наоборот.

**Осторожно: вывод из этого разрыва напрашивается неверный.** «Каталог не узнаёт живой салон» — **неправда**, проверено по коду: 206 связей демо-салонов **не подобраны, а созданы из каталога** автором заполнения (`seed_demo_salons.py:402` берёт шаблон из собственной фикстуры). Поле `suggested_template` **не вычисляет никто** — только читают и выставляют руками.

**Сопоставления не происходило ни у кого.** Ноль у живого салона — это «не пробовали», а не «не совпало».

---

# 2. Мост идентификаторов подбора

## 2.1 Пул кандидатов Ayla

**Снято** 08.09.2026 16:38 MSK, `dev-web-1`.

```python
from users.recommendation_source import SpecialistCandidateSource
from recommendation.api import NeedOrigin, NeedSpec, Scope, ScopeMode
facts = SpecialistCandidateSource().fetch(
    scope=Scope(ScopeMode.MARKETPLACE), need=NeedSpec(origin=NeedOrigin.MEMORY))
```

```
recommendation.source pool=31
```

Совпадает с числом из `OPEN_DECISIONS` §75 («31 мастер, достижимы 3»).

## 2.2 Зеркало

**Снято** 08.09.2026, `ayla-bot-staging-web-1`.

```
MIRROR rows=34  with_key=31  sellable=31
```

## 2.3 Пересечение — дефект, найденный замером, а не тестами

**08.09, до правки:** оба множества по 31, **пересечение ПУСТОЕ**.

Причина, проверена на живой строке:

```
SpecialistProfile: pk_field=id
SAMPLE profile_id=00a66447-...   <- этот ключ клал резолвер
       user_id   =08e039ef-...   <- этот ключ хранит зеркало
```

Один человек под двумя UUID. Резолвер отдавал **ключ профиля**, зеркало хранит **ключ пользователя**.

**09.09, после правки** (PR #303 + #1492; `recommendation_source.py:258` теперь `specialist.user_id`): пул выдаёт те же 31 значения, что лежат в зеркале.

```
было:  0 из 31
стало: 31 из 31
```

**Что значит.** Двадцать пять зелёных тестов этого не видели: все они живут с одной стороны границы и сверяют Ayla с Ayla. **Тест, где обе стороны данных построил один автор, проверяет согласованность фикстуры, а не системы.**

---

# 3. Мастера и расписание

## 3.1 Флаг источника расписания

**Снято** 09.09.2026, `ayla-bot-staging-web-1`.

```
BOOKING_VIA_AYLA_REST = True
```

**Что значит.** Часы клиенту продаёт **Ayla**, локальная таблица `apps.scheduling` — зеркало. Подтверждать её значило бы **подтвердить не то расписание**.

## 3.2 Покрытие рабочими часами

**Снято** 09.09.2026, `ayla-bot-staging-web-1`.

```
продаваемых мастеров 31, строки WorkingHours есть у 4
```

**Что значит.** У двадцати семи подтверждать нечего. Вопрос «можно ли подтвердить пустое расписание» — **не краевой случай, а основной**.

**Число про зеркало, а не про гейт.** Цену правила называет замер 3.4 ниже — он снят и даёт другое число.

## 3.4 Рабочие дни в Ayla — источник, а не зеркало

**Снято** 09.09.2026, контейнер `dev-web-1`, модель `appointments.SpecialistWorkingHours`.

```python
from appointments.models import SpecialistWorkingHours as W
from users.recommendation_source import SpecialistCandidateSource
from recommendation.api import NeedOrigin, NeedSpec, Scope, ScopeMode
from users.models import SpecialistProfile
facts = SpecialistCandidateSource().fetch(
    scope=Scope(ScopeMode.MARKETPLACE), need=NeedSpec(origin=NeedOrigin.MEMORY))
profs = SpecialistProfile.objects.filter(
    user_id__in=[f.ref.id for f in facts]).values_list("id", flat=True)
W.objects.filter(specialist_id__in=profs, is_working_day=True).values_list(
    "specialist_id", flat=True).distinct().count()
```

```
пул подбора                     31
профилей найдено                31
имеют строки расписания          9
имеют хотя бы один рабочий день  9
дней у этих девяти: 7,7,7,7,6,6,6,6,6
```

**Что значит.** **Девять из тридцати одного.** В зеркале (3.2) было четыре — источник опережает зеркало более чем вдвое, и это отдельный факт.

Распределение важнее числа: **у всех девяти неделя полная или почти полная**, промежуточных нет. Расписание **есть целиком или отсутствует целиком**.

**Цена включения гейта подтверждения расписания (§83):** подтвердить можно максимум **девятерых**, **двадцать два уйдут из продажи** — не потому, что расписание не подтвердили, а потому что подтверждать нечего.

## 3.5 Событие `master.schedule.updated` не приходило ни разу

**Снято** 09.09.2026, контейнер `ayla-bot-staging-web-1`.

```python
from apps.eventbus.models import DomainEvent, IngestDedupe, IngestDLQ
from collections import Counter
Counter(IngestDedupe.objects.values_list("event_name", flat=True))
```

Полный перечень **всего**, что дошло до бота за историю:

```
входящие (IngestDedupe):
  booking.created         34
  booking.confirmed       29
  booking.cancelled       19
  appointment.rescheduled  2    последнее 2026-08-08

исходящие (DomainEvent):
  booking.created  16 | booking.attribution.assigned 16
  booking.completed 8 | customer.consent.changed 13

недоставленные (IngestDLQ): 8, все handler_exception, про расписание ни одного
```

**Что значит.** `master.schedule.updated` **отсутствует полностью**. `appointment.rescheduled` — другой предмет: перенос конкретной записи, а не изменение рабочих часов мастера.

**Следствие для §83:** под включённым `BOOKING_VIA_AYLA_REST` источник часов — Ayla, а событие о его изменении до нас не доезжает. **Хука сброса подтверждения не существует**, и правило «любое изменение часов отменяет подтверждение» сегодня неисполнимо: подтверждение станет вечным.

Единственный возможный механизм — периодический обход. Он даёт сброс **с задержкой в один цикл**, а не мгновенный, и выдавать его за исполнение правила нельзя.

**Поправка к цене обхода (окно расписания, 09.09):** проверять надо **только тех, у кого подтверждение есть** — у неподтверждённого сбрасывать нечего. Сегодня это максимум **девять** вызовов за проход, а не 31; после кампании — ровно число подтверждённых.

**Обратное направление обход не покрывает.** Он видит, что у подтверждённого часы изменились, но **не видит, что у неподтверждённого они появились**. Мастер, у которого расписания не было и появилось, не станет подтверждаемым сам собой и никого об этом не уведомит. Сегодня таких потенциально двадцать два.

## 3.3 Режим тенантной изоляции

**Снято** 08.09.2026, `ayla-bot-staging-web-1`.

```
STRICT_TENANT_SCOPE = 'strict'
```

Проверено косвенно: `BotUser.objects` без контекста **бросил** `CrossTenantError`.

**Что значит.** `TenantScopedManager` при `strict` бросает, при `audit` **молча возвращает `.none()`**. На пилоте — первое. Значит админские страницы без контекста тенанта **падают**, а не деградируют.

---

# 4. Цели

## 4.1 Курируемые цели и связи

**Снято** 09.09.2026, `dev-web-1`.

```
GoalOption: 7 - body_shape, event, new_look, recharge, relax, self_care, skin_care
GoalOptionCategory: 20
  relax 3 | body_shape 2 | self_care 4 | event 3 | new_look 4 | skin_care 2 | recharge 2
```

## 4.2 Цели клиентов

```
ClientGoal: всего 33, активных 3, с ключом 31, со свободным текстом 2
активные: relax x2, recharge x1
```

## 4.3 Ограничение «одна активная цель» — это СХЕМА, а не реализация

**Снято** 09.09.2026, `dev-web-1`.

```
active_rows=3  clients_with_active=3  max_per_client=1
distribution=[1, 1, 1]
constraints: CheckConstraint  'clientgoal_key_or_text_present'
             UniqueConstraint 'clientgoal_one_active_per_client'
```

**Что значит.** Канон разрешает несколько активных целей, а **база физически не даст записать вторую**. Это не «реализовано частично» — это жёсткое ограничение, и снятие потребует миграции и решения по существующим строкам.

Зафиксировано владельцем 09.09 как **schema-level contradiction**; продуктовое решение не переоткрывается.

## 4.4 Анкета целей

```
GoalAnketaRun: 5, завершённых 5
GoalAnketaAnswer: 15
шаги: area 5 | feeling 5 | goal 5
свободным текстом ответили: 0
```

**Что значит.** Три шага, все ответили чипами. **Два шага из трёх ни на что не влияют** — список целей от ответов не сужается.

---

# 5. Питание

## 5.1 Профили и ориентиры

**Снято** 09.09.2026, `dev-web-1`.

```
NutritionProfile: total=6  kcal_gt0=6  water_gt0=6  with_target_but_no_weight=2
```

**Что значит.** **Вода — ориентир у всех шести. Калории — ни у одного** как показываемое значение: сводка не читает `daily_kcal`.

**Двое из шести носят ориентир от чужого тела:** `nutrition_profile_service.py:34-37` подставляет `female / 40 / 165 / 70` («медиана пензенской аудитории») за любое пропущенное поле.

**Восьмёрка вернулась вычисленной.** У этих двоих ориентир по воде — ровно восемь стаканов: `70 x 30 = 2100 мл`, `round(2100/250) = 8`. Придуманное число, вычищенное из клиента, вернулось через подстановку медианного тела — и **теперь неотличимо от честного расчёта**.

## 5.2 Флаги питания

**Снято** 09.09.2026, `ayla-bot-staging-web-1`.

```
NUTRITION_ENABLED            True     <- анкета открыта живым людям
NUTRITION_COACH_ENABLED      True
NUTRITION_COACH_DRY_RUN      True
FOOD_PHOTO_SCAN_ENABLED      False
NUTRITION_PROACTIVE_ENABLED  False    <- чужая норма никому не пишет сама
WELLNESS_PROACTIVE_ENABLED   False
```

**Что значит.** Анкета доступна **сегодня**, стоп-сценариев нет ни одного: возраст принимается с 14 лет, согласия не спрашивают, про беременность, кормление, РПП и заболевания не спрашивают вовсе.

Проактивные поверхности **выключены** — это снимает худшую форму дефекта: чужую норму не рассылают непрошено, её видит только открывший дневник.

---

# 6. Часовой пояс

## 6.1 До правки

**Снято** 08.09.2026, `ayla-bot-staging-web-1`.

```
TZ_DIST={'Europe/Moscow': 26}   TOTAL=26
```

**Что значит.** 26 из 26 на нетронутом умолчании. Умолчание колонки — **настоящий часовой пояс**, поэтому «никто не выбирал» неотличимо от «человек живёт в Москве».

## 6.2 После миграции DRF-1606

**Снято** 09.09.2026, тот же контейнер, после выкладки.

```
TZ={'': 26}
миграция identity: 0023_timezone_unset_is_empty применена
```

**Что значит.** Окно закрылось вовремя: пока никто не выбирал осознанно, backfill был однозначен. После первого осознанного «Москва» снять неоднозначность было бы нечем **никогда**.

---

# 7. Раздача статики

**Снято** 09.09.2026.

**До:**

```
GET https://api-dev.gobeauty.site/static/admin/css/base.css -> 404 nginx/1.18.0
внутри контейнера: ls /app/staticfiles -> No such file or directory
```

**Диагноз.** nginx настроен верно (`location /static/` с alias на каталог хоста; `docker-compose.staging.yml:69` монтирует `./:/app`). **`collectstatic` не выполнялся ни разу** — шага не было ни в выкладке, ни в запуске; `whitenoise` в проекте отсутствует.

**После** `manage.py collectstatic --noinput`:

```
155 static files copied to '/app/staticfiles'
base.css        -> 200 text/css 22120
ayla-admin.css  -> 200 text/css  6923
```

**Что значит.** Админка бота была без стилей **с первого дня**, и ни один прогон этого не показывал: тесты проверяют разметку, а не то, доехал ли CSS до браузера. Постоянная починка — шаг в выкладке, PR #1494, слит.

---

# 8. Mini App — что реально отдаётся

**Снято** 09.09.2026, публичные запросы к `miniapp-dev.gobeauty.site`.

```
CSS: --c-accent: #4452ff     определён, используется 98 раз
     --c-divider             используется 117 раз
     всего ссылок на токены  815
     #838c9f (новая граница) ОТСУТСТВУЕТ - PR #1498 не слит
JS:  UNRENDERABLE_CANDIDATES 1 | NO_VERIFIED_CANDIDATES 2
     food_scanner_consent 1 | «Хранение данных» 3
```

**Что значит.** Фиолетовая палитра **живёт на пилоте** с 04.09; сборка **свежая** — в ней вчерашние правки. Границы и текстовые токены статусов появятся только после слияния PR #1498.

---

# 9. Салоны

## 9.1 «Тестовый салон» заведён не там

**Снято** 09.09.2026, оба контейнера.

```
Ayla:  салонов с «Тестов...» - 0
бот:   slug=testovuy-salin  id=7ec5292d-...  город Пенза
       мастеров 0   last_catalog_sync_ok_at=None
```

**Что значит.** Таблица салонов в боте — **зеркало**. Синхронизация спрашивает Ayla буквально по `str(tenant.id)` (`services/sync.py:182`), а при создании через админку бота идентификатор берётся **случайный** — Ayla про него не знает и не ответит никогда.

**Салоны и мастера заводятся в админке Ayla**, в карточке салона.

---

# 10. Выкладка

## 10.1 Прогоны, на которые можно ссылаться

Настоящий `ci` = workflow **274201556**, `replay` = **274201559**, `deploy-dev` = **274201557**. Пары `*-docs-skip` (**279530142** / **279530140**) носят **те же имена заданий** — это механизм для документных PR, не подделка. **Цитировать id прогона, не id воркфлоу.**

## 10.2 Случай 09.09: код сел в `dev` и не доехал на пилот

```
f512aa1  мост       ci         -> CANCELLED  (run 34308679711)
92ebc6c  документы  deploy-dev -> SKIPPED    (run 34312569873)
```

Прогон на коде шёл, когда следом слили документный PR; вытеснение по concurrency отменило его. Новая голова — документная, у настоящего `ci` стоит `paths-ignore: docs/**`, значит он на ней **не запустится никогда**, а `deploy-dev` подписан на него. **Цепочка обрывается насовсем.**

Починено ручным запуском (`gh workflow run deploy-dev.yml --ref dev`, прогон **34312675966**, success) и проверено **сквозным замером в контейнерах**, а не галочкой.

**Правило отсюда:** не сливать ничего, пока идёт прогон предыдущего слияния.

---

# 11. Пилотный контур: какой tenant, чем доказано

**Снято** 09.09.2026 16:30–16:40 UTC (19:30–19:40 MSK), окно матрицы готовности, оба контейнера.

**Пилотный tenant — `formula-tela` («Формула тела»), `b32a057a-56c7-4bf0-ae50-e11e76ab44be`.**
Это не вывод из числа услуг, а три независимых живых признака:

```
бот:  EVENT_INGEST_ALLOWED_TENANTS  = frozenset({'b32a057a-56c7-4bf0-ae50-e11e76ab44be'})
бот:  BOOKING_NO_PREPAYMENT_TENANTS = frozenset({'b32a057a-56c7-4bf0-ae50-e11e76ab44be'})
Ayla: b32a057a-56c7-4bf0-ae50-e11e76ab44be  formula-tela  Формула тела
```

## 11.1 Каталог по салонам — что реально продаётся

**Снято** 09.09.2026 16:35 UTC, `dev-web-1`.

```
tenant              salon_services  active  specialist_services  active  специалистов
ayla-marketplace           0           0            0              0        0
fevralskiy-svet           43          43           48             48        4
formula-tela              58          58          232             95        4
mednyy-kovsh              17          17           34             34        3
mkt-afrodita               8           8            8              8        1
mkt-lumina                 2           2            2              2        1
mkt-mediclinic            24          24           48             48        2
mkt-spatrium               2           2            2              2        1
olhovyy-dvor              62          62           65             65        7
pylca-i-lyon              21          21           38             38        4
sorok-okon                28          28           47             47        4
                        ---         ---          ---            ---       ---
итого                    265         265          524            387       31
```

**Что значит.** Записываемых единиц (`SpecialistService`) на контуре **387 активных**, а не 265: одна услуга салона продаётся несколькими мастерами. У пилотного салона из 232 строк активны **95** — то есть 137 связей «мастер — услуга» заведены и выключены.

## 11.2 Разметка — перемерена, и провенанса нет ни у одной строки

**Снято** 09.09.2026 16:30 UTC, `dev-web-1`. Заменяет замер §1.1 от 08.09 (истёк срок годности).

```
{'review_required': 206, 'unmapped': 59}      verified отсутствует = 0
всего SalonService 265
с шаблоном 206 / без шаблона 59
```

**Новое, чего не было в §1.1.** Проверены все пять колонок происхождения:

```
mapping_confirmed_at   не NULL:  0
mapping_source_ref     не пусто: 0
mapping_confirmed_by   не NULL:  0
mapping_confirmed_rule не пусто: 0
```

**Что значит.** «`verified` = 0» — ещё мягкая формулировка. **Ни одна строка каталога не несёт вообще никакого следа подтверждения.** Схемный `CheckConstraint` тут ни при чём: он запрещает `verified` без провенанса, но пустые колонки у `review_required` не запрещает — и они пусты у всех 265.

**Следствие, которое стоит назвать.** `206 review_required` в точности равно `206 с шаблоном`, `59 unmapped` — `59 без шаблона`. Правило миграции `0017` («шаблон не пуст ⇒ `review_required`») на сегодня **не разошлось с данными ни на одной строке**: строк, созданных после миграции с шаблоном и оставшихся `unmapped`, пока нет. Дефект «статус зависит от даты вставки» реален в коде и **ещё не проявился в данных** — это разные утверждения, и путать их нельзя.

## 11.3 Гейт здоровья: 96 записываемых единиц из 387 решены отсутствием шаблона

**Снято** 09.09.2026 16:35 UTC, `dev-web-1`, перебором с вызовом самого доменного метода.

```
активных записываемых единиц (SpecialistService.is_active=True): 387
  resolved_requires_health_check() == True :   1
  resolved_requires_health_check() == False: 386
     из них БЕЗ шаблона (пол принудительно False):  96
     из них с шаблоном, у которого флаг True:        1
```

**Что значит.** `SpecialistService.resolved_requires_health_check()` (`djangoproject-catalog:services/models.py`, `template_floor = template.requires_health_check if template is not None else False`) превращает **отсутствие связи** в **утверждение «скрининг не нужен»** у 96 из 387 активных записываемых единиц.

95 из этих 96 — **пилотный салон**: у `formula-tela` шаблона нет ни у одной из 58 услуг, значит **все 95 его активных записываемых единиц объявлены безопасными по умолчанию**. Один оставшийся — `mkt-spatrium`.

Это уточняет формулировку «58 из 58» из передачи: в единицах, которыми человек реально пользуется, счёт **95 из 95**.

## 11.4 Расписание на стороне Ayla — белое пятно закрыто

**Снято** 09.09.2026 16:40 UTC, `dev-web-1`. Закрывает первый пункт списка «что НЕ замерено».

```
мастеров с >=1 активной SpecialistService: 31
строк SpecialistWorkingHours у них:        63
из них имеют >=1 строку:                    9
имеют >=1 is_working_day=True:              9
имеют >=1 рабочий день СО временем начала и конца: 9
не имеют расписания вовсе:                 22

по салонам (продаваемых / с рабочим днём):
  fevralskiy-svet  4 / 0      mkt-lumina      1 / 1
  formula-tela     4 / 4      mkt-mediclinic  2 / 2
  mednyy-kovsh     3 / 0      mkt-spatrium    1 / 1
  mkt-afrodita     1 / 1      olhovyy-dvor    7 / 0
  pylca-i-lyon     4 / 0      sorok-okon      4 / 0

SpecialistTimeOff: 0     TenantClosure: 0
```

**Что значит.** Цена включения гейта расписания — **9 из 31, а не 4 из 31**. (Разбор происхождения числа 4 — §24: зеркало заполнено один раз 22.07 кодом, который с тех пор удалён.) Число 4 из §3.2 — про **зеркало бота**, и зеркало отстаёт: часы продаёт Ayla (`BOOKING_VIA_AYLA_REST=True`), и в Ayla их вдвое больше. **Мерить надо на стороне Ayla**, как и сказано в §3.1.

Пилотный салон покрыт полностью: **4 из 4**. Пустое расписание — свойство маркетплейсных салонов, а не пилота.

## 11.5 Живые значения флагов — два расходятся с ожиданием

**Снято** 09.09.2026 16:33 UTC, оба контейнера, чтением `django.conf.settings`.

```
Ayla (dev-web-1)
  GOAL_RESOLUTION_ENABLED   = True     <- умолчание в коде false
  GOAL_ANKETA_ENABLED       = True
  BOOKING_MIN_AHEAD_MINUTES = 60   BOOKING_MAX_AHEAD_DAYS = 60   BOOKING_SLOT_GRID_MINUTES = 30
  BOOKING_AUTO_COMPLETE_ENABLED = True (через 3 ч, батч 200)
  CROSS_DOMAIN_ENABLED = False     EXTERNAL_BUSY_ENABLED = False

бот (ayla-bot-staging-web-1)
  DISCOVERY_CLARIFY_MIN_TIER = 4
  BOOKING_VIA_AYLA_REST = True   STRICT_TENANT_SCOPE = 'strict'   STRICT_TENANT_REFUSE = False
  CONCIERGE_MEMORY_ENABLED = True   CONCIERGE_NUTRITION_CONTEXT_ENABLED = True
  INTENT_RESOLUTION_FROM_TOOL_CHOICE_ENABLED = True
  ORCHESTRATOR_SHADOW_ENABLED = False (sample_rate 0.0)
  SKILL_CONFIDENCE_FLOOR_LIVE_ENABLED = False
  POST_VISIT_FOLLOWUP_ENABLED = False (DRY_RUN True)
  AI_DRAFTS_AUTO_TRIGGER_ENABLED = False   REPLAY_LIVE_CAPTURE_ENABLED = False
  NUTRITION_ENABLED True | COACH True | COACH_DRY_RUN True | PHOTO_SCAN False
  NUTRITION_PROACTIVE_ENABLED False (DRY_RUN True) | WELLNESS_PROACTIVE_ENABLED False
```

**Ни одного флага с именем про рекомендации ни в одном контуре** — единственное совпадение `RECOMMENDATION_CANDIDATE_SOURCE` — это путь к фабрике источника кандидатов, не рубильник.

**Два вывода, меняющие прежние классификации.**

1. **`GOAL_RESOLUTION_ENABLED = True` на контуре.** Замеры G2 (трек A, узел 8) знали только умолчание `false` и честно помечали живое значение `UNKNOWN`. Оно снято: фильтр выдачи по сохранённой цели **включён**. Комментарий рядом с флагом (`settings/base.py:502-505`) обосновывает безопасность выкладки тем, что «на 2026-08-29 `ClientGoal` = 0, поэтому выкладка ничего не меняет ни для кого». Сегодня `ClientGoal` = **36**, активных 3 — обоснование истекло вместе с числом.

2. **`DISCOVERY_CLARIFY_MIN_TIER = 4`, и умолчание тоже 4.** Замер DecisionReadiness §3 утверждает: «гейт `DISCOVERY_CLARIFY_MIN_TIER` default 0 → **фактически отключён**». Это **неверно на обеих базах**: `config/settings/base.py:793` на `origin/dev` и `:775` на измеренном `fd6f4e87` одинаково задают `int(os.environ.get("DISCOVERY_CLARIFY_MIN_TIER", "4"))`. Ноль — это фолбэк `getattr(settings, ..., 0)` **внутри функции**, недостижимый, пока настройка объявлена в `base.py`. Различающий вопрос каталога **работает**, порог 4 (ниже 2 он отключился бы — `discovery.py:2574-2576`).

**Правило отсюда:** умолчание, прочитанное у `getattr`, — не умолчание системы. Настоящее умолчание живёт в `settings`, живое значение — только в контейнере.

---

# 12. Событийный слой и наблюдаемость

**Снято** 09.09.2026 16:45–16:55 UTC, оба контейнера.

## 12.1 Аналитика Ayla — два имени событий на весь контур

```
AnalyticsEvent всего: 37
   goal_selected             36
   external_identity_bound    1
```

**Что значит.** Из шестнадцати событий, которых владелец требует до пилота (§15 промпта матрицы), в аналитике Ayla нет **ни одного**, кроме косвенно связанного `goal_selected`. Ни брони, ни рекомендации, ни вопроса, ни безопасности.

## 12.2 Телеметрия бота — 21 930 строк, но не про решения

```
events.Event всего: 21930   (поля: tenant, event_type, event_name, payload,
                             properties, distinct_id, dialog_id, trace_id, created_at)

catalog_synced_completed        12802     booking_confirmed         29
worker.subscriber_audit          2765     booking_created           25
conversations.message.stored      979     booking_cancelled         19
identity.bot_user.resolved        748     consent_granted           18
client_profile_recomputed         620     consent_withdrawn         13
worker.handler_started            586     handoff_initiated         12
channels.max.global.received      583     ingress.webhook_duplicate  9
ingress.webhook_received          580     marketplace.visits.listed  6
skill_dispatched                   77     channels.max.outbound.sent 5
marketplace.handoff.service_unresolved 40  master.invite_dispatched  3
marketplace.handoff.entered        37
```

**Что значит.** Инфраструктурная наблюдаемость у бота **есть и богатая**: `trace_id` и `dialog_id` — колонки таблицы; вход, воркер, диспетчеризация скилла, идентичность, согласия и три состояния брони пишутся. **Чего нет — событий о решениях:** вопрос задан / решён / повторён, рекомендация показана / принята, нет подтверждённых кандидатов, результат безопасности, протухший тап, заблокированное утверждение на выходе. Техническую дорогу события расследовать можно, смысловую — нечем.

## 12.3 `booking.completed` на пилоте попадает в мёртвую очередь — сегодня

```
IngestDLQ: 8 строк, все reason=handler_exception, replayed_at=None у всех
   booking.completed 6 | booking.created 1 | booking.confirmed 1
   от 2026-07-22 05:53 UTC до 2026-09-09 12:42 UTC   <- последняя сегодня

EVENT_INGEST_ALLOWED_EVENTS = {booking.confirmed, booking.cancelled,
                               booking.created, appointment.rescheduled}
EVENT_INGEST_TENANT_VERIFY_FAIL_OPEN = False
```

Дословная ошибка из `HandlerFailureTracker`:

```
TenantAuthorizationError: tenant_authorization_denied reason=event_not_allowed
event_name=booking.completed tenant_id=b32a057a-56c7-4bf0-ae50-e11e76ab44be.
TenantUserRelationship is not available and the T-02 pilot allowlist did not
admit this envelope, so the helper fails CLOSED.
```

**Что значит.** `booking.completed` **не входит в пилотный аллоулист**, а Ayla его шлёт — по пилотному салону. Гейт отрабатывает **правильно** (fail-closed по решению Round-2 AS8: `TenantUserRelationship` ещё не поставлен, Sprint 1 #246), но следствие: бот **не узнаёт о завершённом визите через шину**. Восемь писем лежат мёртвыми с 22 июля, ни одно не переигрывалось.

Смягчающее, но **не проверенное**: в расписании бота есть задача `bookings.detect_completed_bookings` — завершение может определяться опросом, а не событием. Работает ли она и покрывает ли пилотный салон — не замерено.

## 12.4 Исходящая шина бота не отправлена ни разу

```
DomainEvent: всего 53, отправлено 0, попыток отправки 0, мёртвых 0
   booking.created 16 | booking.attribution.assigned 16 |
   customer.consent.changed 13 | booking.completed 8
   период: 2026-08-12 05:47 UTC .. 2026-09-08 11:00 UTC
CELERY_BEAT_SCHEDULE: 25 задач, диспетчера исходящего ящика среди них нет
```

**Что значит.** Событие **`booking.attribution.assigned` существует и пишется** — атрибуция «рекомендация → бронь» в контуре заведена, шестнадцать строк. И **ни одна из 53 записей ни разу не пыталась уехать**: `dispatch_attempts = 0` у всех, в расписании из 25 задач диспетчера нет. Это не сбой доставки — это отсутствие доставщика.

## 12.5 Доверие к заголовку прокси не подтверждено

Предупреждение при старте бота, дословно:

```
eventbus.ingest.proxy_trust_risky depth=1 edge_ack=False
X-Forwarded-For chains are spoofable unless the edge proxy canonicalises the header.
Set EVENT_INGEST_EDGE_CONFIGURED_ACK=True after verifying nginx/Cloudflare strips inbound XFF.
```

`EVENT_INGEST_EDGE_CONFIGURED_ACK` в настройках **отсутствует**. Цепочка `X-Forwarded-For` подделываема, пока край не проверен; система сама говорит об этом на каждом старте.

## 12.6 Команды воспроизведения §11–§12

```bash
H=taximeter@176.119.159.141

# пилотный tenant и салоны
ssh $H 'docker exec dev-web-1 python manage.py shell -c "
from tenants.models import Tenant
[print(t.id, t.slug) for t in Tenant.objects.order_by(\"slug\")]"'

# разметка и провенанс
ssh $H 'docker exec dev-web-1 python manage.py shell -c "
from services.models import SalonService as S
from django.db.models import Count
print(dict(S.objects.values_list(\"mapping_status\").annotate(n=Count(\"id\"))))
print(S.objects.filter(mapping_confirmed_at__isnull=False).count(),
      S.objects.exclude(mapping_source_ref=\"\").count(),
      S.objects.filter(mapping_confirmed_by__isnull=False).count(),
      S.objects.exclude(mapping_confirmed_rule=\"\").count())"'

# расписание на стороне Ayla
ssh $H 'docker exec dev-web-1 python manage.py shell -c "
from appointments.models import SpecialistWorkingHours as W
from services.models import SpecialistService as SS
ids=set(SS.objects.filter(is_active=True).values_list(\"specialist_id\",flat=True))
print(len(ids), len(set(W.objects.filter(specialist_id__in=ids, is_working_day=True)
      .values_list(\"specialist_id\",flat=True))))"'

# событийный слой бота
ssh $H 'docker exec ayla-bot-staging-web-1 python manage.py shell -c "
from django.apps import apps
from django.db.models import Count
E=apps.get_model(\"events\",\"Event\"); print(E.objects.count())
D=apps.get_model(\"eventbus\",\"DomainEvent\")
print(D.objects.count(), D.objects.filter(is_dispatched=True).count())
Q=apps.get_model(\"eventbus\",\"IngestDLQ\")
print(dict(Q.objects.values_list(\"event_name\").annotate(n=Count(\"id\"))))"'
```

---


# 13. Конверт ответа — дефект доказан НА ГРАНИЦЕ, живьём

**Снято** 09.09.2026 17:05 UTC, `ayla-bot-staging-web-1`, **собственным клиентом бота** к живой Ayla. Не код, не фикстура — сетевой ответ.

Замер G2 трек A §9.1 доказал дефект кодом обеих сторон и честно пометил проявление как `UNKNOWN_NOT_MEASURED`: «боевой ответ ручки не снимался». Снят.

**Чем снято.** Из контейнера бота вызван его же клиент границы, с идентификатором в том виде, в каком его строит рантайм (`external_user_id_for(bot_user)` → `bot:<channel>:<channel_user_id>`):

```python
from apps.integrations.ayla import goals_client
from apps.integrations.ayla.user_proxy import external_user_id_for
doc = goals_client.fetch_decision_context(external_user_id=external_user_id_for(bot_user))
```

**Что получено** (14 привязанных `BotUser`, все 14 вызовов — HTTP 200):

```
top-level keys : ['data']          <- 'known' на корне ОТСУТСТВУЕТ у всех 14
doc.get('known')            -> None
doc['data'] keys            -> ['intents', 'known', 'missing', 'next', 'suggestions', 'version']
doc['data']['known']        -> {'goal': {'goal_key': 'skin_care', 'goal_text': None,
                                 'selected_at': '2026-09-08T18:54:03.707200+00:00',
                                 'source_channel': 'miniapp'}}
doc['data']['version']      -> 2
```

**Цели у живых людей есть** — три разных человека, три активные цели:

```
bot:max:831…    goal_key=skin_care   выбрана 2026-09-08 18:54 UTC
bot:max:260237491   goal_key=relax       выбрана 2026-09-04 13:55 UTC
bot:max:229…   goal_key=relax       выбрана 2026-09-08 11:01 UTC
bot:max:663…    goal=None            (цели нет — честный None)
```

**Что видят читатели бота — исполнен их собственный код на этом самом документе:**

```
nutrition_coach._goal_from_document(doc) -> None      <- цель есть, коуч её не видит
doc.get("known")                          -> None      <- строка miniapp_api/views.py:2746
                                                          и adminconsole/clients.py:307
```

**Что значит.** Дефект перестал быть выводом из чтения кода. У трёх людей на пилоте есть выбранная цель; четыре потребителя в боте получают `None` — не потому, что цели нет, а потому, что читают на уровень выше, чем лежит документ. Дашборд «Моя цель» отдаёт пустой список, карточка клиента говорит «нет», коуч питания цель в промпт не кладёт, сверка после `ReadTimeout` докладывает успешную запись как неудачу.

**Побочно подтверждено расхождение фикстуры.** Живой документ несёт `"version": 2`; фикстура бота (`apps/miniapp_api/tests/test_wellness_today.py:77-84`) объявляет `"version": 1` и кладёт `known` на корень. Тест зелен, потому что проверяет согласованность с самим собой: обе стороны данных построил один автор.

**Замечание к идентичности** (в матрицу, пункт 1): четырнадцать строк `BotUser` дают **семь** различных `ayla_user_id` и **семь** различных `bot:max:<id>` — один человек имеет несколько строк `BotUser` (по одной на тенанта) и одну личность в Ayla. Разрешение идёт через `resolve_external_user`, формат `<source>:<id>[:<id>]` обязателен: **голый UUID отвергается с 403**. Это не дефект, а контракт — но он значит, что «403 на внутренней ручке» может означать «неверный формат заголовка», а не «нет прав».

---


# 14. Гейт здоровья на пилоте: останавливать нечего, и это не хорошая новость

**Снято** 09.09.2026 17:20 UTC, оба контейнера. Запросы Р-3, Р-4, Р-8 замера `MEASUREMENT_PILOT_BOOKING.md`.

## 14.1 Единственная услуга с гейтом во всём контуре — не на пилоте

```
GATED: sorok-okon | Медицинский педикюр | template: Медицинский педикюр
       tmpl_flag: True | salon_flag: True | spec_flag: True

formula-tela (пилотный салон) gated: 0
```

Из 387 активных записываемых единиц гейт здоровья поднят **у одной**, и она принадлежит демо-салону `sorok-okon`. У пилотного салона таких услуг **ноль**.

**Что значит, и здесь легко ошибиться.** Соблазн прочитать это как «на пилоте безопасно — останавливать нечего». Так читать нельзя. Ноль получен не тем, что услуги пилота безопасны, а тем, что **у всех 58 услуг пилотного салона нет шаблона**, а `resolved_requires_health_check()` читает отсутствие шаблона как `False` (§11.3). Это тот же дефект, только с другой стороны: сначала отсутствие связи объявляется утверждением о безопасности, потом отсутствие поднятых гейтов объявляется доказательством, что гейт не нужен.

**Следствие для классификации.** Дефект «ни одна точка создания брони не читает флаг» (0 из 8 — замер брони, §4) на пилоте сегодня **не может проявиться данными**: флага, который следовало бы прочитать, нет ни у одной пилотной услуги. Но это не смягчает дефект, а лишает пилот возможности им управлять: поднять гейт на пилотной услуге можно только руками через `SalonService.requires_health_check`, и **ни одна из восьми точек создания его всё равно не прочитает**. Экспозиция ноль **по данным**, а не по контролю.

## 14.2 Fail-closed бота срабатывает на нуле строк (Р-4)

```
зеркало бота, MasterService: total=387  null_flag=0  gated=1  false=386
Ayla,      SpecialistService: total=387  gated=1        false=386
```

**Паритет полный** — зеркало не отстаёт ни на строку, и это хорошо. Но:

**Защита `NULL → скрининг нужен` в боте (`skills/booking/skill.py:1392`) не срабатывает никогда.** Строк с `NULL` — **ноль**. Каталог вычисляет `False` из отсутствующего шаблона **до** пересечения границы и отдаёт боту явное «не нужно». Бот получает уверенное отрицание там, где источник не знал ответа, и его собственный fail-closed нечему ловить.

Это точный ответ на гипотезу «fail-closed сработает на них»: он срабатывает на нуле рёбер из 387.

## 14.3 Незакрытые pending-строки (Р-8)

```
PendingBookingAction: total=13  consumed=11  expired_unconsumed=2
```

Накопления нет: две протухшие непогашенные строки при TTL 10 минут — рабочий фон, а не утечка.

## 14.4 Что снять НЕЛЬЗЯ без решения владельца

Замер брони просит три живые проверки — **Р-5** (двойной `POST` создания брони без ключа идемпотентности), **Р-6** (бронь на услугу с поднятым гейтом мимо чата) и **Р-7** (перенос из Mini App). Все три — **запись на боевом контуре**: создают настоящие записи у настоящих мастеров живого салона, поднимают напоминания и занимают реальные слоты.

**Не выполнены.** Режим замера это запрещает, и цена ошибки несимметрична: снять число можно завтра, отменить чужую бронь в чужом расписании — уже событие для салона. Нужны либо решение владельца, либо тестовый тенант, где такие записи никого не касаются. Вопрос вынесен как есть, без обхода.

---


# 15. Что реально доезжает до бота по шине — и что не доезжало ни разу

**Снято** 09.09.2026 17:30 UTC, `ayla-bot-staging-web-1`, таблица `eventbus.IngestDedupe`
(в неё попадает то, что **принято**, в отличие от `IngestDLQ` — того, что отвергнуто).

```
booking.created          34   последнее 2026-09-08 11:01 UTC
booking.confirmed        29   последнее 2026-09-08 11:01 UTC
booking.cancelled        19   последнее 2026-09-07 17:17 UTC
appointment.rescheduled   2   последнее 2026-08-08 09:59 UTC
```

**Четыре имени. Ровно те четыре, что стоят в аллоулисте** (`EVENT_INGEST_ALLOWED_EVENTS`, §12.3). Больше не доехало ничего.

## 15.1 `master.schedule.updated` не доезжал ни разу — белое пятно закрыто

Второй пункт списка «что НЕ замерено» звучал так: «доезжает ли живьём событие `master.schedule.updated` — под включённым флагом это **единственный** хук сброса подтверждения; без него правило „изменение часов отменяет подтверждение“ не исполняется вовсе».

**Ответ: ни разу.** Ноль строк в `IngestDedupe`, ноль в `IngestDLQ`. При этом обработчик **зарегистрирован** — бот пишет об этом при каждом старте:

```
eventbus.ingest.handler_registered name=master.schedule.updated version=1
```

И **в аллоулист он не входит**: там четыре имени, `master.schedule.updated` среди них нет. То есть даже если Ayla его пошлёт, он не будет принят — уйдёт в мёртвую очередь тем же путём, что и `booking.completed` (§12.3).

**Что значит.** Зарегистрированный обработчик — не доказательство доставки. Правило «изменение часов отменяет подтверждение» сегодня не исполняется **дважды**: событие не приходит, и, придя, не было бы допущено. Предмет живого окна расписания (PR #1502) — передаю числом, не трогаю.

## 15.2 Сводка по шине, одной таблицей

| событие | в аллоулисте | принято | в мёртвой очереди | последнее |
|---|---|---:|---:|---|
| `booking.created` | да | 34 | 1 | 08.09 11:01 |
| `booking.confirmed` | да | 29 | 1 | 08.09 11:01 |
| `booking.cancelled` | да | 19 | 0 | 07.09 17:17 |
| `appointment.rescheduled` | да | 2 | 0 | 08.08 09:59 |
| `booking.completed` | **нет** | **0** | **6** | мёртвое 09.09 12:42 |
| `master.schedule.updated` | **нет** | **0** | 0 | не приходило |

---


# 16. Наблюдаемость: сырьё собирается, сводка не считается ни разу

**Снято** 09.09.2026 17:40 UTC, `ayla-bot-staging-web-1`. Проверка находок замера
`MEASUREMENT_PILOT_ANALYTICS_OBSERVABILITY.md` живыми числами.

```
aggregate_ai_metrics_daily в CELERY_BEAT_SCHEDULE : False
задачи observability в расписании                 : ['apps.observability.tasks.compute_shadow_delta']

observability.AIRequestMetric      266 строк      <- сырьё собирается
observability.AIDailyMetricSummary   0 строк      <- сводка не посчитана ни разу
replay.ReplayTrace                   0 строк      <- ни одного захваченного пути

events.Event  event_type='booking_completed'  : 0 за всё время
events.Event  event_type содержит 'recommend' : 0 за всё время
```

**Что значит.** Замер наблюдаемости назвал самым опасным то, что задача
`apps.observability.tasks.aggregate_ai_metrics_daily` не стоит в расписании и не имеет
вызывающих. **Подтверждено живьём и усилено данными:** сырьё есть — 266 строк
`AIRequestMetric`; сводки нет — **ноль** строк `AIDailyMetricSummary`. На этой сводке висят
дашборд качества, единственный пороговый алерт и единственная связь «ход → бронь». Все три
мертвы не потенциально, а фактически, и зелёный `test_ai_aggregation.py` этого не показывает,
потому что зовёт задачу напрямую.

**`ReplayTrace` — ноль строк.** Возможность «расследовать один путь по `trace_id` в админке»
существует в коде и **на этом контуре не захватила ничего**. Частичный зачёт по
наблюдаемости, выданный по коду, живыми данными не подтверждается.

**`booking_completed` — имя есть, строк ноль.** Событие числится в каноническом реестре имён
бота, и **ни одна строка за всё время не написана**. Сходится с §12.3 и §15: событие
`booking.completed` по шине не принимается (нет в аллоулисте), а задача
`bookings.detect_completed_bookings`, стоящая в расписании, аналитического события тоже не
даёт. Владельцу требуется `booking_completed` — сегодня его нет ни одним путём.

**`recommendation*` — ноль строк за всё время.** Ни `recommendation_shown`, ни любого другого.
Сходится с §11.2: рекомендовать некого, потому что `verified = 0`.

## 16.1 Сколько на пилоте вообще происходит

```
события за последние 24 часа:
   catalog_synced_completed      960     <- синхронизация каталога
   worker.subscriber_audit        56
   client_profile_recomputed      26
   conversations.message.stored    3
   ingress.webhook_received        2
   channels.max.global.received    2
   identity.bot_user.resolved      2
```

**Что значит.** За сутки на контуре — **два входящих обращения человека и три сохранённых
сообщения**. 96 % событий суток (960 из ~1055) — собственная синхронизация каталога.
Это надо знать до объявления порогов раскатки: сегодняшний контур не даёт статистики,
на которой можно калибровать что бы то ни было, и первые 5-10 человек будут **первой
нагрузкой**, а не приростом к существующей.

---

# 17. Доступность и недоступность: дефекты реальны, население дыр — ноль

**Снято** 09.09.2026 17:50 UTC, оба контейнера. Проверка находок
`MEASUREMENT_PILOT_AVAILABILITY.md` живыми числами.

## 17.1 Два определения «продаваемого мастера» совпали

Замер доступности показал, что **читающий и пишущий пути Ayla спрашивают разные столбцы**:
чтение — `status + is_available + user.is_active`, запись — `is_booking_enabled + status`.
Значит и «продаваемых» можно считать двумя способами. Посчитаны оба:

```
READ-PATH  (status=active, is_available=True, user.is_active=True) : 31
CATALOG-LINK (есть хотя бы одна активная SpecialistService)        : 31
пересечение                                                         : 31
в read-path, но без активных услуг                                  :  0
с активными услугами, но не в read-path                             :  0
```

**Множества тождественны.** Значит число §11.4 (**9 из 31** с рабочим днём) устойчиво к выбору
определения — и это стоило проверить, потому что расхождение столбцов делало вопрос
неочевидным.

## 17.2 Обе дыры расхождения сегодня пусты

```
ДЫРА P1: is_booking_enabled=False при status=active и is_available=True : 0
ДЫРА обратная: is_available=False при is_booking_enabled=True           : 0
всего SpecialistProfile: 31, все со статусом active
```

**Что значит.** Дефект «мастер на паузе продаёт слоты и отказывает после подтверждения»
реален в коде (столбцы действительно спрашиваются разные) и **на сегодняшних данных
непроявим: ни одной строки ни в ту, ни в другую сторону**. Это третий случай за день той же
формы — дефект есть, экспозиция ноль **по данным, а не по контролю** (ср. §14.1 гейт здоровья,
§14.2 fail-closed на нуле строк).

**Вывод, который отсюда следует делать, и вывод, который делать нельзя.** Следует: на первых
5-10 человек эти дыры не выстрелят, пока никто не поставит мастера на паузу. Нельзя: считать
их закрытыми — первое же нажатие «приостановить приём записи» в админке создаёт строку в
дыре P1, и тогда мастер начнёт продавать часы, которые не продаёт.

## 17.3 Зеркало мастеров: три строки без ключа

```
CatalogMaster: 34 строки, is_active=True 31, с ayla_user_id 31
```

Три строки зеркала не несут ключа Ayla и неактивны — остаток, не влияющий на выдачу.
Сходится с §2.2 («MIRROR rows=34 with_key=31 sellable=31»).

## 17.4 Что осталось не снятым по этому предмету

Два targeted proof из замера доступности требуют **записи или изменения данных** на боевом
контуре: слоты для мастера с выключенной записью (нужно выключить запись живому мастеру) и
регистр `error.code` на занятом слоте (нужно занять слот). **Не выполнены** — по той же
причине, что Р-5 - Р-7 в §14.4.

Отдельно отмечу находку, которую можно проверить **только** записью, и потому она остаётся
утверждением о коде: `ai-bot-platform:apps/miniapp_api/views.py:997` различает причину отказа
регистрозависимым `"slot" in exc.code`, а Ayla отдаёт `SLOT_NOT_AVAILABLE` заглавными —
то есть ветка «время заняли, выберите другое» не срабатывает никогда, и все отказы
«мир изменился» приезжают в Mini App одинаковым `bad_request`. Строкой выше в той же
функции код лоуэркейзится явно.

---

# 18. Периметр внутренних ручек: снаружи закрыт, изнутри — один ключ на всё

**Снято** 09.09.2026 18:00 UTC. Проверка находок `MEASUREMENT_PILOT_IDENTITY_AUTH.md`.

## 18.1 Что проверено живьём

```
GET https://dev.gobeauty.site/api/v1/internal/users/<uuid>/personal-data/export/
   без заголовка Authorization      -> HTTP 403
   с заведомо неверным Bearer       -> HTTP 403
```

**Снаружи периметр закрыт.** Ручка экспорта персданных не отвечает ни анонимно, ни на
неверный токен. Это важно зафиксировать, потому что без этой проверки находка замера
идентичности читалась бы как «персданные лежат в открытом доступе». Это не так.

## 18.2 Что при этом верно и проверено по исходнику, а не живым вызовом

`djangoproject-catalog@95c917e6:users/personal_data_api.py`:

```
75: class InternalPersonalDataExportView(APIView)
79:     permission_classes = [IsInternalBearer]
99:     def get(self, request: Request, user_id: UUID) -> Response

136: class InternalPersonalDataDeleteView(APIView)
140:     permission_classes = [IsInternalBearer]
162:     def delete(self, request: Request, user_id: UUID) -> Response
```

`IsInternalBearer` по собственному докстрингу (`users/permissions.py`) — «service-to-service
calls **without user resolution**», `request.user` остаётся анонимным, `X-External-User-ID`
не требуется. Субъект берётся **из URL**. Проверки владения объектом нет: `has_object_permission`
не реализован ни в одном permission-классе каталога.

**Значит:** предъявитель общего `AYLA_INTERNAL_API_TOKEN` может выгрузить персданные **любого**
человека по его UUID и **стереть** их — то же самое одной строкой, но глаголом `DELETE`.
Ролей у токена нет, ротации нет.

## 18.3 Чего я НЕ делал и почему

**Не выполнял аутентифицированный кросс-пользовательский экспорт**, хотя команда и токен были
доступны: успешный ответ выгрузил бы телефон, почту и ФИО живого человека в мою сессию.
Доказывать отсутствие проверки владения ценой выгрузки чужих персданных не нужно — отсутствие
проверки доказано исходником, и доказано сильнее, чем одним ответом.

**Тем более не выполнял `DELETE`** — это стирание данных живого человека.

Чего эта осторожность стоит: строчку «проверено живым вызовом» напротив F-14 поставить нельзя,
и в матрице это будет отмечено как доказано **кодом**, а не наблюдением.

## 18.4 Расхождение по `STRICT_TENANT_SCOPE` — разрешено замером

Замер идентичности справедливо возразил общему своду: умолчание в коде — `audit`
(`config/settings/base.py:256`), `production.py` его не переопределяет, `strict` жёстко стоит
только в `staging.py:27`. Вопрос был оставлен открытым.

**Он закрыт живым числом** (§11.5): на боте пилота `STRICT_TENANT_SCOPE = 'strict'`, и это
подтверждено поведением — `Model.objects` вне контекста тенанта **бросает** `CrossTenantError`
(наблюдалось при переписи таблиц, §12.2). То есть контур пилота поднят на `staging.py`.

**Правило отсюда:** умолчание в коде и значение на контуре — разные утверждения, и второе
снимается только в контейнере. Свод был прав про пилот, замер — про код; спор снят числом,
а не аргументом.

---

# 19. Пятая поверхность: AI-чат каталога существует, смонтирован и им пользовались

**Снято** 09.09.2026 18:10 UTC, `dev-web-1`. Проверка находки №6 замера
`MEASUREMENT_PILOT_SAFETY_E2E_AND_FLAGS.md` («найдена пятая, неучтённая поверхность»).

## 19.1 Маршруты существуют

Прочитан живой `URLconf` каталога, а не файл на диске:

```
api/v1/ai/chat/                                 name=chat
api/v1/ai/chat/<uuid:conversation_id>/action/   name=chat-action
api/v1/ai/conversations/                        name=conversations-list
api/v1/ai/conversations/<uuid:conversation_id>/ name=conversations-detail
```

## 19.2 Ими пользовались

```
ai.Conversation: 2
ai.Message:      4
```

**Что значит.** Это не мёртвый код и не заготовка: поверхность смонтирована в корневом
`URLconf` каталога, несёт **собственный путь исполнения действия** (`chat/<uuid>/action/` —
тот самый, которым подтверждается бронь), и по ней уже проходили живые диалоги. Объём
крошечный, но «ноль строк» и «четыре строки» — разные утверждения: первое позволяло бы
списать поверхность в `DEAD_CODE`, второе — нет.

При этом по замеру безопасности у неё **ноль проверок здоровья, ноль флагов и собственный
ранжировщик** с весом рейтинга 30 %. То есть к двум известным авторитетам ранжирования
(резолвер каталога и поиск MAX-бота) добавляется третий, и он же — ещё один путь к брони.

## 19.3 Внешняя проба маршрутов ничего не доказывает — калибровка

Попытка определить существование маршрутов снаружи дала одинаковый ответ на всё:

```
/api/v1/ai/chat/                      403
/api/v1/zzz-definitely-not-a-route/   403      <- заведомо несуществующий
/healthz                              403
```

**Край отвечает `403` на что угодно**, включая заведомо отсутствующие пути. Поэтому
`403` **нельзя** читать ни как «ручка защищена», ни как «ручка существует». Единственный
надёжный способ — `URLconf` изнутри контейнера, как сделано в §19.1.

**Правило отсюда:** прежде чем толковать код ответа периметра, снимите тот же код на
заведомо несуществующем пути. Без калибровки «403» — это буква, а не факт.

---


# 20. Выкладка, перенос и аллоулист — три уточнения

**Снято** 09.09.2026 18:20–18:40 UTC.

## 20.1 Выкладка идёт не на проверенный SHA — но сегодня это стоило только документов

```
ci         34324588828  head_sha db601b0b829612a3f66c8ab5cd9044a8d4f329ab  success 07:35 UTC
deploy-dev 34327958994  head_sha 83ed56a94eb0a96d9599c632f0e6ba2d48c6c5b7  success 08:13 UTC (workflow_run)
чекаут на хосте пилота ~/ai-bot-platform-dev :   83ed56a9
origin/dev на момент замера                  :   b1a119bd
```

`deploy-dev` делает `git pull --ff-only origin dev` + `checkout ref: dev` — то есть ставит то,
на что указывает `dev` **в момент выкладки**, а не тот коммит, который прошёл гейт.

**Проверено содержимым, а не родством** (правило: ancestry не доказывает состав):

```
git diff --name-only db601b0b 83ed56a9 | grep -v '^docs/'   -> пусто
git diff --name-only 83ed56a9 origin/dev | grep -v '^docs/' -> пусто
```

Шесть файлов дельты, все в `docs/`, +1793 строки. **На пилоте исполняется ровно тот код, который
проверил CI.** Механизм негоден и однажды выложит непроверенный код; сегодняшняя выкладка —
не тот случай, и говорить «на пилоте стоит непроверенный код» было бы неправдой.

## 20.2 Перенос не читает гейт здоровья — снято чтением, без записи

Вопрос «проверяет ли путь переноса `requires_health_check`» закрыт грепом по всему пути записи
каталога, без единой живой брони:

```
git grep -n "requires_health_check" origin/dev -- appointments/ booking/ admin_api/ ai/ users/ ':!*/tests/*'
```

Пять попаданий, **все пять — в путь чтения рекомендаций** (`users/recommendation_source.py`,
`users/catalog_recommendations_api.py`). В `appointments/`, `booking/`, `admin_api/`, `ai/` —
**ноль**. Ни создание, ни отмена, ни перенос флага не касаются.

**Приём, которым это снято, стоит записать:** вопрос «пропустит ли поверхность» отвечается
переписью читателей, а не попыткой пройти. Ноль читателей — это ответ; живая бронь подтвердила
бы уже доказанное и создала бы запись у настоящего мастера.

## 20.3 Аллоулист событий — переменная окружения, а не константа

Проверено на `origin/dev`:

```
config/settings/base.py:2176
    EVENT_INGEST_ALLOWED_EVENTS = _parse_ingest_event_allowlist(
        os.environ.get("EVENT_INGEST_ALLOWED_EVENTS", "")
    )
except _IngestAllowlistConfigurationError -> ImproperlyConfigured (отказ загрузки)
```

Пустое значение = deny all, мусор = процесс не стартует. **Значит `booking.completed` и
`master.schedule.updated` добавляются правкой окружения пилота и рестартом — без PR и без
выкладки.**

И это не наша правка конфигурации, а **пополнение прежнего решения владельца**:
`docs/runbooks/eventbus-subscriber-activation.md:160-166` — **OD-T02-5**, «контролируемый пилот
принимает четыре события, 4 из 18 имён контракта», владелец расширенную поверхность принял явно.

**Прецедент точный до совпадения патологии.** Четвёртое имя, `booking.confirmed`, было добавлено
именно потому, что без него события «rejected, retried, and dead-lettered». Сегодняшняя мёртвая
очередь `booking.completed` (§12.3) — ровно то же самое на пятом имени, и рунбук это заранее
описал.

---


# 21. Закрытие белых пятен матрицы

**Снято** 09.09.2026 19:00–19:30 UTC. Закрывает пять из четырнадцати `UNKNOWN_NOT_MEASURED`
матрицы. Два закрытия меняют формулировки, данные раньше.

## 21.1 Настройки `web` и `worker` совпадают — дрейфа нет

Замер безопасности предупреждал: `web` и `worker` собирают настройки отдельно, значения могут
разойтись. Проверены оба контейнера по девятнадцати флагам:

```
ayla-bot-staging-web-1  и  ayla-bot-staging-celery-worker-1  — значения ИДЕНТИЧНЫ по всем
```

Дрейфа нет. Гипотеза снята замером, а не рассуждением.

## 21.2 Аллоулист по-прежнему четыре имени — в обоих процессах

```
web    EVENT_INGEST_ALLOWED_EVENTS = {booking.confirmed, booking.cancelled,
                                      booking.created, appointment.rescheduled}
worker EVENT_INGEST_ALLOWED_EVENTS = то же самое
```

Файл окружения пилота, по сообщению главного окна, уже несёт шесть имён, но **процессы не
пересозданы**, и живое значение — прежние четыре. Поэтому §12.3 остаётся **действующим**, а не
историческим: `booking.completed` и `master.schedule.updated` продолжают уходить в мёртвую
очередь. Перемерено самостоятельно, а не записано со слов.

## 21.3 Бот УЗНАЁТ о завершённом визите — сломана доставка, а не обнаружение

**Это уточняет §12.3, и уточнение существенное.**

`bookings.detect_completed_bookings` (расписание, каждые 30 минут) — **производитель**, а не
потребитель: он сам сканирует подтверждённые брони, чьё время прошло, штампует `completed_at`
через CAS и испускает `booking.completed`. То есть ингест от Ayla ему не нужен.

Живая проверка:

```
BookingRequest: всего 9 (подтверждённых 8, отменённая 1)
completed_at заполнен : 8 из 8 подтверждённых
completed_by          : пусто у всех восьми
последний completed_at: 2026-08-28 13:00 UTC
DomainEvent booking.completed: 8   <- ровно те же восемь
celery-beat: Up 9 hours
```

**Что значит.** Задача работает: все восемь подтверждённых броней зашлёпаны, и на каждую
испущено событие. Пустой `completed_by` у всех восьми — не дефект, а датировка: решение
владельца от 30.08 ввело `completed_by='system'`, а последнее завершение случилось **28.08**,
до него. С тех пор ни одна бронь не завершалась — проверять новое поведение не на чем.

**Но `emit()` по докстрингу — «persist one domain event to the outbox»**, и только. Подписчики
работают от разбора исходящего ящика, а он не разбирается никогда (§12.4: 53 события, 0 попыток,
диспетчера в расписании нет). Значит `LoyaltySubscriber`, который комментарий в расписании
называет «разблокированным этим производителем», по-прежнему не срабатывал ни разу.

**Поправка к моей же формулировке.** Сказать «бот не узнаёт о завершённом визите» было бы
неточно: на уровне своей БД узнаёт и штампует. Не работает **доставка события подписчикам** — и
именно поэтому аналитическое `booking_completed` имеет ноль строк (§16). Мёртвая очередь ингеста
(§12.3) — отдельная потеря: она лишает бота **авторитетного** завершения от Ayla, оставляя
собственное, вычисленное по часам, о котором сам код говорит: «часы, досчитавшие до конца, ничего
не знают о том, пришёл ли человек».

## 21.4 На выложенном коммите не было НИ ОДНОГО прогона проверки

Перечислены все прогоны на SHA, который стоит на пилоте:

```
83ed56a9 — все прогоны:
   34345109199  miniapp-drift        schedule       success  11:21 UTC
   34337081886  planning-rules-sync  schedule       success  09:51 UTC
   34327958994  deploy-dev           workflow_run   success  08:13 UTC

настоящий ci (274201556) на 83ed56a9 : не запускался
ci-docs-skip (279530142) на 83ed56a9 : не запускался
```

**Что значит.** На коммите, который исполняется на пилоте, **нет собственного вердикта
проверки** — ни настоящего `ci`, ни его документного двойника. Единственное, что его защищает, —
совпадение содержимого с коммитом `db601b0b`, который проверку прошёл (§20.1: дельта только
`docs/`).

Это точнее прежней формулировки «выкладка идёт не на проверенный SHA»: дело не в том, что взяли
не тот коммит из проверенных, а в том, что **выложенный коммит не проверялся вовсе**.

## 21.5 Конфигурация пилота не воспроизводится из репозитория

```
на хосте  : docker-compose.yml, docker-compose.staging.yml, docker-compose.staging.local.yml
в git     : docker-compose.yml, docker-compose.staging.yml
```

`docker-compose.staging.local.yml` (1707 байт) существует только на машине, и первой строкой сам
это объявляет: «ТОЛЬКО НА ЭТОЙ МАШИНЕ, не в git». Он несёт три вещи, без которых пилот не
поднимется прежним:

1. **Прокси на сборку** (`http_proxy`/`https_proxy` на внутренний адрес) — «канал этой машины
   режет загрузку объектов с GitHub, а сборка тянет приватный `ayla-ai-core`». Без него образ на
   этой машине не собрать.
2. **Пины собственных доменов** на `176.119.159.141` через `extra_hosts` для пяти сервисов —
   поставлены после переезда 03.09, когда TTL записей был сутки и бот брал из кэша адрес **старой**
   машины, упираясь в 502.
3. **Отключение chromadb** пустыми `CHROMA_HTTP_HOST` и `CHROMA_AUTH_TOKEN` — попутно закрывает
   ещё одно белое пятно: векторная база на пилоте **не работает**, и это сделано намеренно.

**Что значит для отката (B-5).** Дело не только в том, что образы не тегируются. **Машину нельзя
пересобрать из репозитория:** восстановление на другом железе или после потери диска потеряет
прокси сборки, пины доменов и выключатель chroma. Это усиливает блокер, а не добавляет к нему.

## 21.6 Откат статики Mini App имеет под собой настоящий артефакт

```
~/ai-bot-platform-dev/apps/miniapp/dist       есть
~/ai-bot-platform-dev/apps/miniapp/dist.prev  есть
```

`mv dist.prev dist` — не фигура речи: предыдущая сборка лежит рядом. Глубина отката — **одно
поколение**; позапрошлой сборки не существует.

---


# 22. Ещё четыре белых пятна закрыты

**Снято** 09.09.2026 19:40–19:55 UTC.

## 22.1 Переезд состоялся 03.09, и пины доменов теперь — латентная опасность

```
uptime машины 176.119.159.141 : 6 суток  (то есть с ~03.09)
dev.gobeauty.site             -> 176.119.159.141
api-dev.gobeauty.site         -> 176.119.159.141
miniapp-dev.gobeauty.site     -> 176.119.159.141
```

Переезд по `MIGRATION_RUNBOOK.md` **состоялся**, публичный DNS с тех пор сошёлся: все три домена
указывают на текущую машину.

**И отсюда следует то, чего в рунбуке нет.** Пины `extra_hosts` в
`docker-compose.staging.local.yml` (§21.5) ставились, когда DNS ещё отдавал **старый** адрес, и
тогда они спасали. Сегодня DNS согласен с ними — то есть они **избыточны**. Но при следующей
смене адреса машины они станут **активно неверными** и будут молча перебивать правильный DNS
изнутри контейнеров: пять сервисов пойдут на мёртвый адрес, а `nslookup` с хоста будет показывать
правильный. Это ровно та форма, которая уже стоила 502-х: не «нет записи», а «есть чужая запись,
которую никто не перечитывает».

## 22.2 Ask-eligibility policy — детерминированная, и она уже работает

Белое пятно «внутренности внешней policy в Ayla» закрыто чтением
`djangoproject-catalog@95c917e6:users/personalization_engine.py:106-142`.

Четыре правила, в порядке от дешёвого к дорогому, **без единого обращения к модели**:

```
1. onboarding не завершён            -> не спрашивать ("first_interaction")
2. данные уже есть ИЛИ стёрты нарочно -> не спрашивать ("already_have_data")
   (data_sources[field] in {explicit, inferred, ERASED})
3. спрашивали меньше 24 ч назад      -> не спрашивать ("cooldown_24h")
4. пропустил дважды и <30 дней       -> не спрашивать ("double_skip_pause")
иначе                                -> спрашивать
```

`mark_asked` штампует `last_asked_at[field]` идемпотентно.

**Что значит.** Это **работающий детерминированный гейт «спрашивать или нет»** с явными кодами
причин отказа и уважением к стиранию — то есть ровно та форма, которой требует канон от
DecisionReadiness. Он существует, он живой, и он **не связан** с readiness-контуром, потому что
readiness-контура нет (§матрица п.7-8). Отдельно стоит отметить правило 2: намеренно стёртое
поле приравнено к заполненному — «забудь» не превращается в «спроси заново».

## 22.3 `ayla-ai-core` в предметах матрицы не участвует — проверено, а не предположено

```
origin/main d72a5de4, поиск по src/:
   safety                -> 1 файл   requires_health_check -> 0
   mapping_status        -> 0        recommendation_id     -> 0
   goal                  -> 0
```

Единственное совпадение на `safety` — комментарий про **потокобезопасность**
(`src/ayla_ai_core/composer.py:127`, «preserves replay-determinism + thread safety»), к домену
безопасности отношения не имеющий.

Прежние замеры утверждали это же; теперь это **проверено на канонической ветке**, а не унаследовано.
Классификация меняется с `UNKNOWN_NOT_MEASURED` на `ABSENT (измерено)`.

## 22.4 `frontAyla` поверхностью гейтов не является

```
frontAyla/apps: client, pro
поиск requires_health_check | mapping_status | recommendation по apps/ и packages/ -> 0 файлов
```

Ноль совпадений в исходниках. Прежний беглый поиск давал попадания только в
`node_modules/@react-native/debugger-frontend/**` — отладочные бандлы Chrome DevTools, к проекту
отношения не имеющие. Классический ложный след: **греп без исключения `node_modules` находит
чужой код и выдаёт его за свой.**

---


# 24. Часы в зеркале: писателя нет, а строки есть. Разбор

**Снято** 09.09.2026 20:10–20:35 UTC, оба контейнера. Предмет — вопрос главного окна: почему у
пяти мастеров в зеркале нет строк `scheduling_workinghours`, при том что у источника часы есть.
Гипотеза «писателя часов в зеркало нет вовсе» проверена первой, как и просили.

## 24.1 Писателя в рабочем коде нет — подтверждено

Полный перебор по `ai-bot-platform@b1a119bd`, `apps/`, вне тестов и миграций:

```
WorkingHours.objects/.all_tenants .create/.bulk_create/.update_or_create/.get_or_create
   -> вне тестов ОДНО попадание:
      apps/catalog/management/commands/seed_dev_formula_tela.py:230
```

Всё остальное — чтение (`filter`, `.first()`), протокол типов `WorkingHoursLike`, докстринги.
Плюс модель зарегистрирована в админке (`apps/scheduling/admin.py:34`) — то есть править руками
можно, но это человек, а не путь синхронизации.

**Ни синхронизации мастеров, ни отдельной синхронизации часов в зеркало не существует.**

## 24.2 Но вывод «значит, четвёрка посеяна» — НЕВЕРЕН, и я его чуть не сделал

Напрашивалось: писателя нет, единственный писатель — сид `formula-tela`, значит четыре пилотных
мастера с часами — посев, и зеркало часов не отражает ничего.

**Сверка формы это опровергает.** Константа сида:

```
seed_dev_formula_tela.py:110-118  WORKING_HOURS
   пн-пт 10:00-20:00, обед 14:00-15:00
   сб    11:00-18:00, без обеда
   вс    ВЫХОДНОЙ
```

Живое зеркало:

```
все 4 мастера, все 7 дней: is_working=True, 10:00-19:00, обеда НЕТ
28 строк, created_by=None, created_at 2026-07-22 16:17:57.99 .. 16:17:58.17  (0,2 секунды)
```

Ни время окончания, ни обед, ни воскресенье не совпадают. **Сид этих строк не создавал.**

Урок в чистом виде: «единственный писатель — сид» плюс «строки есть» не даёт «строки от сида».
Совпадение по одному следу (кто теоретически мог написать) не доказывает происхождения — нужна
сверка **содержимым**, и она развернула вывод на противоположный.

## 24.3 Настоящая причина: приглашение мастера когда-то изготавливало расписание, и это убрали

`apps/admin_api/views_invite.py:90-92`, дословно:

> «Two things used to stand under it and are gone: `WorkingHours` seeding (removed by
> **DRF-1062**) and the post-commit MAX DM…»

История коммитов подтверждает обе стороны:

```
c08398f5  feat(admin): master invite flow (MM2) — … + WorkingHours seed + MAX DM dispatch
99f8da61  refactor(admin): DRF-1062 stop the invite from manufacturing a schedule (#1198)
```

Название коммита-удаления и есть диагноз: **приглашение изготавливало расписание**, которого
никто не заводил, и это убрали намеренно.

**Отсюда всё сходится.** Четыре мастера пилотного салона были приглашены **до** удаления —
их 28 строк родились одним пакетом за 0,2 секунды 22.07. Пятеро маркетплейсных заведены иначе
или позже, и писателя к тому моменту уже не было. Ноль у пятерых — **не сломанный путь
синхронизации, а отсутствие пути вообще**, плюс удалённый одноразовый.

## 24.4 Направление копирования установить нельзя — у источника нет отметок времени

```
appointments.SpecialistWorkingHours, поля:
   id, specialist, day_of_week, is_working_day, start_time, end_time, break_start, break_end
```

**Ни `created_at`, ни `updated_at`.** Значит вопрос «эти часы в Ayla появились раньше или позже
зеркальных» **неотвечаем на текущей схеме**, и утверждать, что зеркало скопировало Ayla (или
наоборот), нечем.

Само по себе это отдельная находка: **у источника истины о расписании нет провенанса во
времени.** «Когда эти часы появились и кто их поставил» не спросишь ни у одной строки.

## 24.5 Форма часов подозрительна с обеих сторон

```
источник, formula-tela (4 мастера) : 7 строк, рабочих 7, 10:00-19:00
источник, маркетплейс (5 мастеров) : 7 строк, рабочих 6
зеркало,  formula-tela (4 мастера) : 7 строк, рабочих 7, 10:00-19:00, без обеда
```

Пилотные мастера работают **семь дней в неделю с 10 до 19 без обеда** — у всех четверых
одинаково, до минуты. Маркетплейсные — шесть дней. Живой салон так не работает; так выглядит
**изготовленное умолчание**, и одинаковое с обеих сторон границы.

Поэтому совпадение зеркала с источником **не доказывает, что синхронизация работала**: у
совпадения есть вторая, столь же простая причина — обе стороны получили одно и то же
изготовленное значение. Различить их нечем (§24.4).

## 24.6 «Строк нет» против «строки есть, все выходные» — по просьбе главного окна

Разделено по всем 31 продаваемым мастерам Ayla:

```
COUNT(*) = 0, строк нет вовсе        : 22
строки есть, все дни выходные        :  0
есть хотя бы один рабочий день       :  9
```

**Ни одного случая «завёл расписание и закрыл всю неделю».** Все 22 — «не заходил никогда».
Для кампании подтверждения это один адресат, а не два.

Девятка с рабочими днями: `formula-tela` ×4 (по 7 рабочих), `mkt-mediclinic` ×2,
`mkt-spatrium`, `mkt-lumina`, `mkt-afrodita` (по 6 рабочих).

## 24.7 Что из этого следует для пилота

1. **Часы в зеркале заморожены с 22.07.** Писателя нет; любое изменение часов в Ayla после этой
   даты в зеркало не попадёт. Сегодняшнее согласие двух сторон держится на том, что источник не
   менялся, а не на механизме.
2. Это **смыкается с §15.1**: `master.schedule.updated` не доезжает и не допущен аллоулистом.
   Двух путей обновления часов нет — ни событийного, ни синхронизацией.
3. Под флагом `BOOKING_VIA_AYLA_REST=True` клиенту часы продаёт Ayla, поэтому на **продажу**
   заморозка зеркала сегодня не влияет. Она влияет на всё, что читает зеркало напрямую:
   мастерский кабинет и локальный резолвер слотов при выключенном флаге.
4. **Число «4 из 31» из §3.2 надо читать иначе, чем читалось.** Это не «зеркало отстаёт» — это
   «зеркало заполнили один раз семь недель назад удалённым с тех пор кодом».

---


# 25. Динамика дыр `is_booking_enabled`: её нет, и это доказуемо

**Снято** 09.09.2026 20:50 UTC, `dev-web-1`. Последнее из пяти оставшихся белых пятен матрицы,
которое можно снять отсюда.

## 25.1 Назад история не читается — но есть граница, и она отвечает

Аудита у профиля мастера нет: `simple_history`, `HistoricalRecords`, `auditlog`,
`django_reversion` — ноль совпадений по `users/`. Значит **точная история переключений
невосстановима**, и честный ответ на «менялся ли флаг раньше» из таблицы не добывается.

Но у `SpecialistProfile` есть `created_at` и `updated_at` (оба в `readonly_fields` админки), а
`updated_at` — `auto_now`. Любая правка профиля его сдвинула бы. Это граница сверху, и она
оказалась плотной:

```
профилей: 31
is_booking_enabled: {True: 31}
is_available:       {True: 31}
status:             {active: 31}

профилей, у которых updated_at позже created_at более чем на 5 с:  0 из 31
```

**Ни один профиль не правился после создания. Ни разу.** Значит обе дыры пусты не «сегодня», а
**за всю жизнь данных**: `is_booking_enabled=False` не выставлялся никогда, и `is_available=False`
тоже.

## 25.2 Волны создания объясняют и часы

```
21.07.2026 09:21 — 4 профиля   (formula-tela, пилотный салон)
23.08.2026 09:21 — 5 профилей  (mkt-spatrium, mkt-lumina, mkt-mediclinic x2, mkt-afrodita)
01.09.2026 03:53 — 22 профиля  (остальной маркетплейс, одним пакетом)
```

Сопоставление с §24 и §11.4 закрывает вопрос о часах окончательно:

| волна | профилей | часы в Ayla | часы в зеркале |
|---|---:|---|---|
| 21.07 | 4 | 7 строк, рабочих **7**, 10:00-19:00 | **есть** (те же 7/7, 10:00-19:00) |
| 23.08 | 5 | 7 строк, рабочих **6** | нет |
| 01.09 | 22 | **строк нет вовсе** | нет |

**Часы приходят вместе с волной создания, а не синхронизацией.** Пятеро из волны 23.08 имеют
часы у источника и не имеют в зеркале ровно потому, что к 23.08 писателя часов в зеркало уже не
существовало (удалён DRF-1062). Двадцать два из волны 01.09 не имеют часов **нигде** — их никто
не заводил ни на одной стороне.

И две разные формы у источника — 7 рабочих дней у волны 21.07 против 6 у волны 23.08 —
подтверждают, что это **умолчания разных загрузок**, а не расписания, которые заводили люди.

## 25.3 Кто вообще может выключить запись

```
присваивания is_booking_enabled = False в проде: ОДНО
   services/management/commands/seed_demo_salons.py:387  (сид демо-салонов)
остальные совпадения — фильтры выборки (recommendation_engine, catalog_recommendations_api)
админка: поле НЕ в readonly_fields -> правится руками
```

То есть выключить запись мастеру сегодня можно **только руками через админку**; продуктовой
поверхности «поставить мастера на паузу» не существует. Это снижает вероятность попасть в дыру
P1 случайно — и не снимает саму дыру: одно нажатие в админке её создаёт, а расхождение столбцов
чтения и записи (§замер доступности, п.8) останется.

## 25.4 Итог по пункту

Классификация меняется с `UNKNOWN_NOT_MEASURED` на **измеренную**:
дыры пусты за всю историю данных; динамики нет, потому что **ни одного изменения профиля не
происходило вообще**; поверхности, которая создала бы дыру в обычной работе, не существует —
только ручная админка. Дефект остаётся дефектом кода (§матрица, B-6), но вероятность его
проявления на пилоте определяется теперь не догадкой, а числом: **0 правок профиля за 50 суток**.

---


# 26. Выкладка на 09.09 вечер: пилот переехал, и вердикт на нём есть

**Снято** 09.09.2026 19:05 UTC. Перемер §20.1 и §21.4 по правилу свежести — базы уехали.

## 26.1 Что стоит на пилоте сейчас

```
origin/dev                     463f9b3d  (#1504, спасение 245 документов — только docs)
чекаут на хосте пилота         09352a0d  (#1505, фикс фильтра ci-docs-skip)
git diff 09352a0d origin/dev, не-docs файлы:  пусто
```

Было утром `83ed56a9`, стало `09352a0d`. Выкладка **34390568868**, 18:41 UTC, success.

## 26.2 На выложенном коммите вердикт ЕСТЬ — в отличие от утреннего

```
все прогоны на 09352a0d:
   34386215890  ci        event=push  success  17:58 UTC
   34386215728  replay    event=push  success  17:58 UTC
   34390568868  deploy-dev            success  18:41 UTC
```

**Это меняет §21.4 по факту, но не по существу.** Утром на выложенном `83ed56a9` не было
ни одного прогона проверки; сейчас на выложенном `09352a0d` есть зелёные `ci` и `replay`.

**Почему разница — и это точнее прежней формулировки дефекта.** `#1505` менял
**не-docs** файлы (два workflow), поэтому настоящий `ci` на нём запустился: `paths-ignore:
docs/**` его не исключил. Следом слили `#1504` — документный, `ci` на нём не запускается, и
`deploy-dev` не сработал. Пилот остался на последнем коммите с кодом, и этот коммит проверен.

Значит механизм ломается **не всегда, а ровно в одном стечении**: документный коммит,
приземлившийся **между** прогоном `ci` кодового коммита и его выкладкой. Тогда `deploy-dev`
берёт голову `dev`, а голова — документная и вердикта не несёт. Утром 09.09 было именно это.

**Дефект остаётся** (выкладка идёт на голову ветки, а не на SHA прогона), но его частота —
не «каждый раз», а «когда документы обгоняют выкладку». Это снижает срочность B-5.2 и не
отменяет его: обгон уже случался сегодня и повторится, а цена — выложить непроверенное.

## 26.3 Ловушка двойников подтвердилась наблюдением, и её починили

`#1505` (`09352a0d`) чинит фильтр `ci-docs-skip`: имена файлов читаются через `-z`, потому что
кириллица приезжала C-экранированной (`"docs/\320\236…"`), ведущая кавычка ломала якорь
`^docs/`, и документный PR объявлялся смешанным. Комментарий в самом фиксе описывает следствие
дословно:

> «This workflow then self-cancels while ci.yml stays silent on its paths-ignore, leaving the
> required check with no authority at all and the PR blocked forever (observed on PR #1504).»

**Что это добавляет к §10.1 и к матрице §22.** Механизм двойников не просто хрупок — он уже дал
состояние «обязательная проверка без авторитета вообще», наблюдавшееся на живом PR. Это второй
за сутки случай, когда цепочка `ci → deploy` рвётся по причине, не имеющей отношения к коду:
первый — вытеснение по concurrency, второй — кириллица в имени файла.

## 26.4 Что из этого следует для матрицы

* §22 матрицы: SHA-примеры (`db601b0b` / `83ed56a9`) остаются верными как **зафиксированный
  случай**, но перестали описывать текущее состояние. Помечено датой.
* Оценка **B-5.2** (выкладка по SHA прогона, 2 SP) не меняется: дефект тот же, наблюдений теперь
  два, частота уточнена.
* Утверждение «на пилоте исполняется ровно тот код, который проверил CI» **остаётся верным** и
  сейчас — проверено содержимым, дельта до головы только документная.

---


# 27. Цена правки гейта здоровья — снята ДО слияния, а не после

**Снято** 09.09.2026 21:30 UTC, оба контейнера. Предмет: что произойдёт на пилоте,
когда сольются PR-A (`beautygo_backend`) и PR-B (`ai-bot-platform`), делающие
«отсутствие связи» отличимым от «скрининг не нужен».

Правка правильная и блокер снимает. Но у неё есть следствие, которое дешевле узнать
сейчас, чем на живых людях.

## 27.1 Что закроется

```
активных записываемых рёбер пилотного салона formula-tela : 95
из них без шаблона (после правки → NULL «не знаю»)        : 95   (100 %)
```

Гейт брони в диалоговом канале (`apps/skills/booking/skill.py:1037`,
`_service_requires_health_check`) при `BOOKING_VIA_AYLA_REST=True` читает
`NULL` как «скрининг нужен» — и это не вопрос человеку, а **безусловная передача
оператору**: `_handoff(reason="booking_health_check_required")` с готовым текстом.

Значит после слияния **любая попытка подтвердить бронь в чате MAX по услуге
пилотного салона уедет человеку.** Не часть, не «услуги с противопоказаниями» —
все 95 из 95, пока каталог не размечен.

## 27.2 По какой поверхности пилот бронирует на самом деле

```
бот, BookingRequest : 9 строк, booking_source = {'ai_direct': 9}, все formula-tela
Ayla, Appointment   : 18 строк, все formula-tela
```

**Все девять бронирований бота прошли `ai_direct`** — то есть ровно тем путём, у
которого стоит гейт. Другой поверхности в этих девяти нет.

Смягчающее: путь Mini App флага не читает вовсе (в `apps/miniapp_api/` ноль
упоминаний `requires_health_check`), поэтому бронь из Mini App правкой не
затрагивается. Восемнадцать записей в Ayla против девяти у бота говорят, что
второй путь используется — но **чем именно, установить нечем**.

## 27.3 Провенанс брони: у записи нельзя спросить, как её сделали

```
appointments.Appointment, поля со словом source: []
```

**Ни одного.** У модели записи в Ayla нет ни `source`, ни `booking_source`, ни
`created_via`. Разделить 18 записей по поверхностям происхождения невозможно —
ни для этой оценки, ни для метрики «Recommendation → Booking», ни для разбора
инцидента.

Это отдельная находка того же класса, что §24.4 (у расписания нет отметок
времени): **у данных, на которых стоит пилот, не спросишь, откуда они взялись.**

## 27.4 Что это значит для порядка работ

Правка снимает блокер B-1 и **одновременно** переводит пилотный салон в режим
«каждая бронь через человека» на диалоговом пути. Оба утверждения верны, и второе
не является доводом против первого: сегодня те же 95 рёбер бронируются без единого
вопроса о здоровье, потому что каскад ответил за салон. Выбор не между «безопасно»
и «удобно», а между **«молча решили за салон»** и **«спросили человека, пока салон
не ответил»**.

Развилки, которые из этого следуют, — владельцу, не исполнителю:

1. слить A+B и принять передачу оператору как штатный режим до разметки;
2. слить A+B вместе с разметкой хотя бы пилотных услуг (тогда `NULL` станет
   осмысленным `True`/`False`, и закроются только те, что вправду требуют);
3. слить A+B и временно поднять `SalonService.requires_health_check=False` явным
   решением салона — тогда это будет **ответ салона**, а не умолчание каскада.

Третий вариант стоит назвать отдельно: он выглядит как «вернуть как было», но
таковым не является. Разница ровно та, ради которой правка и делается: сегодня
`False` сочинён кодом, а там будет `False`, сказанный человеком.

## 27.5 Перемер после слияния — команда готова

Пересдаёт основание решения DRF-1545 (`OPEN_DECISIONS §36`: «all 387 pilot edges
carried a synced verdict»). Утверждение было верным и остаётся верным — но 96 из
тех вердиктов изготовлены, и после правки распределение обязано измениться.

**Выполнять: после слияния A и B и после ближайшего прогона синхронизации каталога**
(`catalog_sync_every_15min`), не раньше — иначе замер покажет доправочное зеркало.

```bash
H=taximeter@176.119.159.141

# 1. Источник: как разложились 387 рёбер после правки
ssh $H 'docker exec dev-web-1 python manage.py shell -c "
from services.models import SpecialistService as SS
rows=list(SS.objects.select_related(\"salon_service\",\"salon_service__template\").filter(is_active=True))
from collections import Counter
c=Counter(r.resolved_requires_health_check() for r in rows)
print(\"источник, активных рёбер:\", len(rows), dict(c))
p=[r for r in rows if r.tenant and r.tenant.slug==\"formula-tela\"]
print(\"пилотный салон:\", len(p), dict(Counter(r.resolved_requires_health_check() for r in p)))
"'

# 2. Зеркало: доехало ли «не знаю» через границу
ssh $H 'docker exec ayla-bot-staging-web-1 python manage.py shell -c "
from apps.catalog.models import MasterService as M
from django.db.models import Count
qs=M.all_tenants.all()
print(\"зеркало:\", qs.count(),
      \"NULL:\", qs.filter(resolved_requires_health_check__isnull=True).count(),
      \"True:\", qs.filter(resolved_requires_health_check=True).count(),
      \"False:\", qs.filter(resolved_requires_health_check=False).count())
"'

# 3. Сработал ли гейт живьём — по журналу, а не по догадке
ssh $H 'docker logs --since 24h ayla-bot-staging-web-1 2>&1 | grep -c "booking.confirm.health_check_required"'
```

**Что считать успехом.** Успех — это НЕ «ноль NULL». Успех — совпадение трёх чисел:
сколько рёбер без шаблона у источника, столько же `NULL` в зеркале, и гейт закрылся
ровно на них. Расхождение источника и зеркала означало бы, что PR-B не доехал, а
ноль `NULL` при рёбрах без шаблона — что не доехал PR-A.

---


# Что НЕ замерено — честный список

* ~~сколько из 31 мастера имеют в Ayla хотя бы один `is_working_day=True`~~ — **закрыто 09.09, см. §11.4: девять из 31, а не четыре**;
* ~~доезжает ли живьём событие `master.schedule.updated`~~ — **закрыто 09.09, см. §15.1: не доезжало ни разу и в аллоулист не входит**;
* **защита ветки `dev` в каталоге** — включена, но первым PR ещё не проверена;
* **что видит оператор на `/admin/booking/bookingrequest/`** — падает страница или молча пуст фильтр; замер поручен исполнителю DRF-1608.

Пустое место здесь честнее выдуманного факта.

---

## §23. Кого можно подтверждать по расписанию — оба источника, снято главным окном 09.09.2026

**Зачем.** Кампания подтверждения расписания (PR #1502, §83) не начинается без списка:
подтверждать можно только того, у кого у источника есть хотя бы один рабочий день.

### Источник — Ayla (`dev-web-1`), по нему и запускать кампанию

| | |
|---|---|
| всего мастеров (`SpecialistProfile`) | **31** |
| есть хотя бы один рабочий день — **можно подтверждать** | **9** |
| нет ни одного — подтверждать нечего | **22** |

Все девять — `status=active`, `booking_source=ayla_local`.

Из девяти **четыре — пилотный салон «Формула тела»** (Архипкин, Сазонова, Паламарчук,
Тихонова, по 7 дней каждый). Оставшиеся пять — салонные позиции вида «Салон · услуга»:
SPAtrium · массаж, Афродита · лазерная эпиляция, Люмина · массаж, Медиклиник ·
косметология, Медиклиник · лазерная эпиляция, по 6 дней.

Двадцать два без часов — «Февральский свет», «Ольховый двор», «Сорок окон»,
«Медный ковш», «Пыльца и лён» целиком.

### Зеркало бота (`ayla-bot-staging-web-1`) — для сверки, к продаже отношения не имеет

| | |
|---|---|
| активных мастеров | **31** (34 строки, 3 архивных) |
| есть рабочие дни | **4** — только «Формула тела» |
| нет | **27** |

### Расхождение 9 против 4 — фактическое, причина не снята

Пять «салон · услуга» в зеркале **есть**, `is_active=True`, связаны по `ayla_user_id`,
но строк в `scheduling_workinghours` у них ровно **ноль** — не выходные, а отсутствие
записей. Часы источника до зеркала не доехали. Это ровно те пять, что не относятся
к пилотному салону.

**Причину не снимали.** Кандидат для следующего замера, не вывод.

### Модель и поле — дословно, чтобы следующий не искал

| | модель | поле |
|---|---|---|
| Ayla | `appointments.models.SpecialistWorkingHours` (`/app/appointments/models.py:478`) | **`is_working_day`** |
| зеркало | `apps.scheduling.models.WorkingHours`, таблица `scheduling_workinghours` | **`is_working`** |

Салон в Ayla: `SpecialistProfile.tenant` → `tenants.Tenant.name`.
У `CatalogMaster` поля часов нет — **и это не значит, что часов нет**: они в отдельной
таблице. Главное окно на этом ошиблось и было поправлено окном расписания.

**Строка ≠ рабочий день.** При `is_working=False` строка означает выходной, времена
обязаны быть null (CHECK-constraint). Считать только истинные.

### Положительная стража нуля

Ноль получен не пустым запросом. В Ayla `SpecialistWorkingHours` — **63 строки**,
из них **58** истинных, распределены ровно по **9** специалистам. В зеркале — **28**
строк, все истинные, ровно по **4** мастерам. Таблицы найдены, непусты; 22 и 27
нулей — реальное отсутствие записей.

### Тенантный контекст

В Ayla enforcement на этих моделях нет — обычный менеджер. В зеркале
`CatalogMaster.objects` и `WorkingHours.objects` — `TenantScopedManager`, вне контекста
бросают `CrossTenantError`, а `.all_tenants()` отсутствует. Мерено обходом
`for t in Tenant.objects.all(): with tenant_scope(t):` по двенадцати тенантам с суммированием.

### Воспроизведение

Ayla:

```
ssh taximeter@176.119.159.141 "docker exec -i dev-web-1 python manage.py shell 2>/dev/null" <<'EOF'
from django.db.models import Count, Q
from users.models import SpecialistProfile as SP
qs = SP.objects.select_related("tenant").annotate(
    wd=Count("working_hours", filter=Q(working_hours__is_working_day=True))
).order_by("-wd", "display_name")
for p in qs:
    print("%s\t%s\t%s" % (p.display_name, (p.tenant.name if p.tenant else "-"), p.wd))
EOF
```

Зеркало:

```
ssh taximeter@176.119.159.141 "docker exec -i ayla-bot-staging-web-1 python manage.py shell 2>/dev/null" <<'EOF'
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant
from apps.scheduling.models import WorkingHours as WH
from apps.catalog.models import CatalogMaster as CM
for t in Tenant.objects.all().order_by("name"):
    with tenant_scope(t):
        for mid in set(WH.objects.filter(is_working=True).values_list("master_id", flat=True)):
            m = CM.objects.filter(id=mid).first()
            print(getattr(m, "name", mid), t.name,
                  WH.objects.filter(master_id=mid, is_working=True).count())
EOF
```

**Срок годности — сутки.** Часы заводятся людьми; число меняется без единой правки кода.
