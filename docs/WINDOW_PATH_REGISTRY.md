# Реестр путей по окнам

**Ведёт главное окно. Заведён 07.09.2026** после двух ошибок раздачи подряд за одни сутки: сперва занятый пакет был отдан второму окну (#289), затем целый репозиторий отдан окну, которому принадлежит лишь одна из четырёх живущих в нём поверхностей.

Вывод, ради которого файл существует: **раздавать по памяти нельзя.** Раздача идёт по этому файлу, и только по нему.

## Правило

**До первого коммита окно письменно объявляет главному окну, какие файлы и какой пакет забирает.** Пересечение разбирает главное окно, а не два окна между собой в момент конфликта. Задним числом к уже начатой работе правило не применяется.

Объявление собирается **из файлов своих PR и проверяется `git cat-file -e` против `origin/dev`** — не по памяти. Несуществующего пути в реестре быть не должно.

## Второе правило: реестр путей НЕ защищает от общего чекаута

Пути можно поделить между окнами. **Рабочее дерево у главного репозитория одно**, и оно в любой момент стоит на чьей-то ветке. 07.09 окно клиентской поверхности открыло файл в общем чекауте `ai-bot-platform` и обнаружило, что редактирует **чужое рабочее дерево**: чекаут стоял на `feat/recommendation-boundary-client` (PR #1453, окно границы рекомендаций), а правившийся файл был там `untracked`. Правку заметили, сверили с `origin/dev` и восстановили байт-в-байт.

**Правило: каждое окно работает только в своём worktree. Общий чекаут не трогает никто** — ни чтобы «быстро посмотреть и поправить», ни чтобы «всё равно файл не отслеживается». Читать из него можно; писать — нет.

Отдельно: **имя ветки не называет поверхность.** `feat/recommendation-boundary-client` — это клиентская **сторона границы резолвера** (вызывающий код в `apps/integrations/ayla/`), а не клиентская **поверхность**. Совпадение слова «client» в двух разных смыслах едва не привело к тому, что окно сочло чужую ветку вторжением в свою territорию. Проверяй по файлам, а не по имени ветки.

## Ошибка, которую этот файл исправляет

`ai-bot-platform` держит **не одну поверхность, а четыре**: клиентскую, мастерскую, салонную админку и общий транспорт. Граница «по репозиториям» неверна в самой своей форме — она была отменена в тот же час, в который объявлена.

Граница проходит **по поверхностям и путям**.

---

## Клиентская поверхность — `ayla-95`

```
apps/miniapp/**                      SPA клиента целиком
apps/miniapp_api/**                  её ручки (кроме общих полос, см. ниже)
apps/skills/welcome/**               первый контакт клиента
apps/skills/privacy_consent/**       согласия клиента
apps/skills/health_screening/**      скрининг боли (DRF-1542)
apps/consent/customer.py             отзыв согласия клиента (Q-CLIENT-03)
apps/identity/services/privacy.py    удаление персональных данных
apps/skills/booking/**               диалог записи клиента  [АРБИТРАЖ]
```

## Мастерская поверхность — `ayla-96`

```
apps/catalog/master_state.py             три предиката, SaleBlock, RoleState
apps/catalog/admin.py                    подписи причин отказа
apps/catalog/migrations/                 граф миграций каталога
apps/booking/services/master_gate.py     единственный источник отказа продажи
apps/admin_api/services/staff_roster.py  ростер владелицы
apps/identity/services/role_resolver.py  кем считается человек
apps/identity/services/solo_onboarding.py  соло-мастер, сегодня мёртвый код
apps/master_api/**                       кабинет мастера целиком
apps/admin_api/services/master_deactivation.py   [АРБИТРАЖ]
apps/admin_api/services/availability.py          [АРБИТРАЖ]
apps/booking/master_notify.py                    [АРБИТРАЖ, занят до слияния]
```

Инварианты держат и падают первыми при чужой правке: `apps/catalog/tests/test_master_landing.py`, `apps/booking/tests/test_master_sale_gate_drf1548.py`, `apps/marketplace/tests/test_master_surfaces_drf1544.py`, `apps/marketplace/tests/test_discovery_rating_sanity.py`, `apps/master_api/tests/`.

Граница окна: **можно ли продать мастера, пускать ли её в кабинет, и каким словом объясняется отказ.** Дальше по потоку — запись, диалог, меню, питание — не его.

## Питание и диетолог — `ayla-53`

```
apps/nutrition_coach/**
apps/nutrition_proactive/**
apps/skills/food_scanner/**
apps/orchestrator/nutrition_context.py
apps/orchestrator/coach_observation.py
apps/orchestrator/nutrition_wellness.py
config/settings/base.py — блок флагов NUTRITION_*
```

Назначено сверх объявленного 07.09 (ремонт придуманных норм, обе половины, §65):

```
djangoproject: nutrition/services/water_entry_service.py
djangoproject: nutrition/services/nutrition_summary_service.py
djangoproject: nutrition/serializers.py
djangoproject: nutrition/tests/**
djangoproject: nutrition/services/nutrition_profile_service.py — ВОЗМОЖНО, только чтение
ai-bot-platform: apps/integrations/ayla/nutrition_client.py + тесты
ai-bot-platform: apps/miniapp_api/views.py — ТРИ КЛЮЧА одной функции
                 (calories_target, pfc, water_glasses_target), вход через главное окно
```

Разрешение по `views.py` расширено с одного водного гейта до трёх ключей: тот же дефект стоит тридцатью строками выше у калорий и БЖУ, и чинить треть экрана, зная о двух других третях, значит оставить дефект намеренно.

Окно питания **не трогает** в `djangoproject`: `recommendation/**`, `goals/**`, миграции. Новых полей не заводит — «нормы нет» выразимо без миграции.

## Граница рекомендаций — окно `HANDOFF_RECOMMENDER_BOUNDARY`

`beautygo_backend` (он же `djangoproject-catalog`):

```
recommendation/**                     приложение целиком
users/recommendation_source.py        + его тест
goals/wiring.py                       +1 функция
djangoProject/settings/base.py, djangoProject/urls.py
users/catalog_recommendations_api.py  + тест (PR #290)
recommendation/management/commands/recommendation_reachability.py
ai/tools.py, ai/tools_handlers.py     (PR #291)
ai/application/services/recommendation_engine.py
ai/application/services/specialist_context_builder.py
```

`ai-bot-platform` (PR #1453):

```
apps/integrations/ayla/recommendation_resolver_client.py  + тест
tests/contracts/test_recommendation_boundary_guard.py
докстринги в apps/integrations/ayla/recommendations_client.py
             и apps/miniapp_api/views.py
```

Объявлено окном как не своё и не планируемое: `apps/marketplace/**`, `apps/orchestrator/**` (это T12), `search/**` (DRF-1575), `apps/miniapp/src/**` (это T7 — клиентское окно).

## Салонная админка — исполнители главного окна

```
apps/admin_api/**  (кроме путей, отданных мастерской поверхности)
apps/adminconsole/**
```

---

## Ничьи пути — вход только через главное окно

Эти файлы правят все, и отдать их одному окну значит остановить остальные. Режим один: **пишешь главному окну, оно ставит в очередь, а не в конфликт.**

```
apps/skills/menu/marketplace.py     сейчас три исполнителя подряд:
                                    подписи и эмодзи; слияние «Мои записи» +
                                    «История визитов»; снос подменю «Ещё»
apps/channels/max/**                ТРАНСПОРТ, а не поверхность
apps/orchestrator/concierge.py      общий
apps/miniapp_api/views.py           клиентские ручки + ветки отказа мастера
                                    + водный гейт (ayla-53)
apps/booking/services/create.py     запись клиента + вызов гейта мастера
apps/booking/services/transitions.py
apps/orchestrator/discovery.py      зарезервировано под T12
apps/marketplace/discovery.py       зарезервировано под T12
```

**Файл может принадлежать одному окну, а строки в нём — другому.** `apps/miniapp/src/screens/CustomerBookingConfirmScreen.tsx` — клиентский экран (`ayla-95`), но **словарь отказов мастера на нём — мастерской поверхности**. Предложение `ayla-96` принято: файл числится за клиентским окном, строки отказа — за мастерским, вход через главное окно. Так же устроены `AdminPeopleScreen.tsx` и `apps/miniapp/src/lib/admin-api.ts`: экраны владелицы, содержимое — состояния мастера.

---

## Арбитраж 07.09.2026

**`apps/skills/booking/**` → клиентскому окну.** Это диалог, в котором записывается клиент. Доля мастерской поверхности здесь — не сам диалог, а **гейт**, и он уже отдельным файлом (`master_gate.py`) у `ayla-96`.

**`apps/booking/master_notify.py` → мастерской поверхности,** но **занят до слияния**: сейчас его держит исполнитель главного окна, снимающий салонные ступени уведомления. Забирать после слияния того PR, не раньше.

**`master_deactivation.py` и `availability.py` → мастерской поверхности.** По последней правке они клиентского окна, по предмету — мастерские. Предмет весит больше следа: следующая правка тут будет про мастера, а не про клиента.

**Ремонт нормы воды → окну питания, обе половины.** Корень в `djangoproject` (подстановка умолчания вместо «нормы нет», вывод нормы из веса), экранный гейт — в клиентских путях. Разрезать по репозиториям значило бы починить половину; предмет один, и материал уже в руках у окна питания.

**#289 не режется.** Условие «T2 и T4 сдают отдельно» писало главное окно и метило им в другое — чтобы не брали весь эпик 61 SP одним заходом и T6–T13 не поехали на непроверенном основании. #289 останавливается на границе и до потребителей не доходит: это фундамент, а не эпик целиком. Резать готовые 4556 строк ради буквы собственной фразы — цена реальная, польза словесная.

---

## Правило источника, без которого раздача бессмысленна

Разбор экрана целей показал: **на один экран существовало два документа**, оба добросовестно исполнены, и в код попал неутверждённый. Это не ошибка исполнителя — это **отсутствие правила, какой документ главный**.

**Утверждённым считается макет в Linear.** Внутренние документы в `docs/screens/**` — рабочие материалы; при расхождении с Linear они уступают, а расхождение выносится главному окну, а не разрешается исполнителем на месте.

Пока правило не записано в самих документах, любой следующий исполнитель выберет так же неудачно и так же добросовестно.

## Гигиена реестра решений

`docs/OPEN_DECISIONS.md` пишут несколько окон одновременно, и **§61 оказался занят дважды** (Plan Engine и Q-CLIENT-03). Разведено 07.09: вопрос Q-CLIENT-03 стал **§64**. Перед тем как взять номер, проверяй `grep -n "^## §" docs/OPEN_DECISIONS.md | tail`.
