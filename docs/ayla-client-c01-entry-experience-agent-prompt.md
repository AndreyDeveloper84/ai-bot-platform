# PROMPT ДЛЯ АГЕНТА --- AYLA CLIENT P0: ПЕРВЫЙ ПОЛЬЗОВАТЕЛЬСКИЙ ПУТЬ

## Контекст

Продолжаем разбор P0 MVP Ayla. Работаем по пользовательскому пути, а не
по общему списку экранов.

Порядок поверхностей: 1. Клиент. 2. Мастер. 3. Салон.

Сейчас работаем только с клиентом и только с первым экраном / первым
опытом.

## Зафиксированное продуктовое направление

Исходим из owner direction:

> **Цель пользователя --- это продукт. Каталог и booking не являются
> центром Ayla.**

Существующий booking-контур не считать ошибочной работой. Это
fulfillment layer --- транзакционный слой исполнения решения Ayla.

Сейчас фактически построена логика:

``` text
НАЙТИ → ВЫБРАТЬ → ЗАПИСАТЬСЯ → ОБСЛУЖИТЬ ЗАПИСЬ
```

Целевая логика:

``` text
ПОНЯТЬ ЧЕЛОВЕКА
→ ПОНЯТЬ ЕГО ЦЕЛЬ
→ ОПРЕДЕЛИТЬ, ЧТО ЕМУ НУЖНО
→ ПРЕДЛОЖИТЬ СЛЕДУЮЩИЙ ШАГ
→ при необходимости УСЛУГА
→ НАЙТИ
→ ВЫБРАТЬ
→ ЗАПИСАТЬСЯ
→ ПОЛУЧИТЬ РЕЗУЛЬТАТ
→ ОБНОВИТЬ КОНТЕКСТ
→ ПРЕДЛОЖИТЬ СЛЕДУЮЩИЙ ШАГ
```

Каталог, booking, кабинет мастера и салонную админку не удалять и не
объявлять построенными «мимо» без анализа.

## Что проверяет Controlled Pilot

Для пилота на «Формуле тела» рабочая гипотеза:

> **Может ли Ayla, зная цель и минимально необходимый контекст человека,
> привести его к более осмысленному следующему действию, чем обычный
> каталог услуг, объяснить это действие и при необходимости довести его
> до реальной записи?**

Минимальный проверяемый цикл:

``` text
Первый запуск
→ цель / проблема
→ уточнения Ayla
→ минимальный Personal Context
→ Intent / Goal
→ Recommendation
→ Explanation
→ Procedure
→ Specialist
→ Availability
→ Booking
→ Visit / Result
→ Feedback
→ Updated Personal Context
```

Проверь этот контур против актуального канона и фактической реализации.
Не считай эту схему новым каноном автоматически.

## Раздели три слоя

### Product P0

``` text
Transformation Goal
→ Personal Context
→ Recommendation / Plan
→ Action
→ Result
→ Progress / Updated Context
```

### Controlled Pilot

``` text
Goal / Intent
→ Minimal Context
→ Recommendation
→ Procedure
→ Specialist
→ Booking
→ Feedback
```

### Booking Foundation

``` text
Catalog
→ Provider
→ Availability
→ Appointment
→ Notifications
→ Master / Salon Operations
```

Проверь, подтверждается ли такое разделение актуальными документами и
кодом.

------------------------------------------------------------------------

# SCREEN C01 --- FIRST CLIENT EXPERIENCE

Не переходи к экрану №2 до owner ruling по C01.

По предварительным данным текущий `HelloScreen` реализует:

``` text
Hello → Auth → Catalog
```

Проверь это по реальному коду.

Установи: - где находится `HelloScreen`; - какие navigation actions он
вызывает; - поведение anonymous/authenticated user; - destination после
auth; - действительно ли Catalog является default destination; - API; -
analytics events; - loading/error states.

Не доверяй пересказу --- проверяй реализацию.

## Вариант A --- Catalog First

``` text
Hello → Auth → Catalog
```

Модель пользователя:

> «Я уже знаю, какую услугу хочу».

Определи: - какую гипотезу проверяет; - что Ayla добавляет поверх
marketplace; - сколько существующего кода reuse; - что теряется
относительно product vision; - можно ли оставить как secondary fast
path.

## Вариант B --- Goal First

``` text
Welcome
→ Auth / Identity
→ Goal Creation
→ Context Collection
→ Ayla
```

Модель:

> «Я знаю, чего хочу добиться, но не обязательно знаю нужную услугу».

Проверь: - соответствие P0 canon; - необходимые domain contracts; -
backend readiness; - mobile readiness; - новые screens/states; - reuse
существующей реализации.

## Вариант C --- Conversation First

Исследуй особенно внимательно:

``` text
Welcome
→ Ayla Conversation
→ natural-language goal
→ Intent extraction
→ Transformation Goal
→ Context clarification
→ Recommendation / Plan
```

Пример:

``` text
Ayla:
«Привет. Расскажи, что ты хочешь изменить?»

User:
«Через три месяца еду в отпуск.
Хочу похудеть, убрать живот и вообще лучше выглядеть».
```

Проверь, допускает ли такую модель существующий Intent / Conversation /
Goal / Memory canon.

Conversation First не даёт LLM права произвольно менять authoritative
state.

Проверь границу:

``` text
Natural language
→ Conversation / AI
→ structured intent candidate
→ validation / policy / consent
→ canonical domain command
→ Transformation Goal / Personal Context
```

Определи: - где заканчивается AI conversation; - где начинается
authoritative domain operation; - требуется ли user confirmation; -
transient vs persistent data; - consent boundary.

## Goal domain ≠ Goal UI

Не считай автоматически, что наличие canonical `Transformation Goal`
требует отдельной формы Goal Creation.

Различай:

``` text
DOMAIN:
Transformation Goal
```

и

``` text
UI:
Goal Creation Screen / Conversation / Progressive onboarding / Hybrid
```

Проверь, фиксирует ли канон конкретный UX или только обязательное
доменное состояние.

## Hybrid architecture

Проверь вариант:

``` text
              Welcome
                 ↓
                Ayla
                 ↓
        «Чем могу помочь?»
           ↙           ↘
«Не знаю, что          «Знаю, что
 мне нужно»             мне нужно»
     ↓                       ↓
Goal / Intent            Fast Search
     ↓                       ↓
Recommendation            Catalog
     └──────────┬────────────┘
                ↓
           Fulfillment
```

Catalog может остаться fast path, не являясь primary entry point.

## Существующие экраны

Не делай полный аудит всех 52 экранов. Для C01 достаточно
классифицировать релевантные клиентские экраны:

-   `REUSE AS IS`
-   `REUSE WITH NEW ENTRY POINT`
-   `REUSE AS FULFILLMENT`
-   `REWORK`
-   `OUTSIDE CLIENT P0`
-   `UNKNOWN`

Не объявляй все существующие экраны ненужными.

## Сравнение A / B / C

Заполни:

  Criterion                            A   B   C
  ------------------------------------ --- --- ---
  Product Vision                               
  P0 canon                                     
  Проверяет уникальную гипотезу Ayla           
  Reuse booking layer                          
  Backend readiness                            
  Mobile readiness                             
  Новые domain contracts                       
  AI runtime dependency                        
  Consent complexity                           
  Implementation effort                        
  Pilot risk                                   
  Controlled Pilot suitability                 

Не используй выдуманные проценты. Только качественная оценка с
доказательствами.

## Ограничение

На этом этапе production code не менять.

Разрешено: - читать код; - читать canon/ADR/PRD/spec; - строить схемы; -
выявлять gaps; - классифицировать reuse; - готовить owner decision.

Без отдельного GO запрещено: - переписывать `HelloScreen`; - менять
navigation; - удалять Catalog; - создавать новые backend contracts; -
менять Transformation Goal schema; - переписывать P0 documents; -
удалять screens.

# Формат результата

Верни отчёт строго так:

## 1. Current Reality

Фактический cold-start flow с файлами и кодом.

## 2. Canonical Requirement

Что требует актуальный канон. Раздели domain requirement и UI
requirement.

## 3. Gap

Сравни `CURRENT vs CANON vs PRODUCT DIRECTION`.

## 4. Option A --- Catalog First

Плюсы, минусы, reuse, риски, что проверяет.

## 5. Option B --- Goal First

То же.

## 6. Option C --- Conversation First

То же + AI → authoritative domain boundary.

## 7. Hybrid Option

Можно ли сохранить Catalog как secondary fast path.

## 8. Existing Screen Reuse

Классификация релевантных существующих экранов.

## 9. Minimal Pilot Architecture

Минимальный Controlled Pilot для «Формулы тела», а не весь будущий Ayla.

## 10. Missing Contracts

Только реально отсутствующие contracts.

## 11. Owner Decisions

Таблица:

  OD   Decision   Options   Agent recommendation   Consequence
  ---- ---------- --------- ---------------------- -------------

Обязательное первое решение:

`OD-C01 — Какой primary entry experience должен иметь новый клиент Ayla?`

Варианты: - A --- Catalog First - B --- Goal First - C --- Conversation
First - D --- Hybrid, с явным указанием primary path

## 12. Recommendation

Дай одну конкретную рекомендацию. Не отвечай «зависит».

Укажи: - рекомендуемый вариант; - почему; - какую гипотезу он
проверяет; - какой существующий код сохраняется; - какой минимальный
новый слой нужен.

## 13. STOP

После рекомендации остановись.

**Не переходи к следующему экрану. Жди owner ruling по `OD-C01`.**

# Главный принцип

Мы выбираем не красивый onboarding, а определяем:

> **Что такое Ayla для клиента с первой минуты использования?**

Если первое действие --- выбрать услугу, Ayla начинается как
marketplace.

Если первое действие --- рассказать, чего пользователь хочет добиться,
Ayla начинается как персональный AI-проводник.

Сначала установи canonical truth, затем current implementation, затем
gap. Не проектируй желаемую Ayla вместо существующей.
