# Shared corpus (`global_kb`) — topic backlog для expert-валидатора

**Статус:** draft для эксперта-косметолога / врача-косметолога.
**Дата:** 2026-05-18.
**Контекст:** в `apps/kb` уже seed-нуты 4 Google Docs (~150 KbDocument rows, 204 ChromaDB chunks). Этот документ — план расширения корпуса до запуска (2 недели). Не самопроизвольное «давайте сделаем больше», а **gap analysis** конкретных bot-запросов которые сейчас слабо отрабатываются.

---

## Что уже есть в `global_kb`

| Doc | doc_type | Гранулярность | Rows |
|---|---|---|---|
| Universal services catalog | SERVICE | subsection | ~80 |
| Contraindication matrix v0.1 | CONTRAINDICATION | subsection | ~36 |
| Aftercare protocol v0.1 | HELP_ARTICLE | section | ~30 |
| Symptoms & consequences (Doc 4) | CONTRAINDICATION | section | ~36 |

**Итого:** 4 дока, ~150 KbDocument rows, 204 ChromaDB chunks. Покрытие: услуги (полно), противопоказания (хорошо), aftercare (средне), red flags / симптомы (средне).

---

## Gap analysis — bot-запросы которые СЛАБО отрабатываются

Восемь типичных архетипов запросов от клиента бота. Колонка «Coverage» — оценка по golden-query тестам Sub-6 и здравому смыслу.

| Архетип запроса | Coverage | Что покрывает | Что упущено |
|---|---|---|---|
| «Можно ли мне X при состоянии Y?» | ✅ Сильно | Contraindication matrix | — |
| «Что делать после X?» | ⚠️ Средне | Aftercare protocol | Долгосрочный уход (months), exercise-restrictions, sleep position |
| «Что произойдёт если X?» | ⚠️ Средне | Symptoms doc + matrix | Long-term effects, cumulative effects, age-specific differences |
| «Сколько стоит / длится X?» | ❌ Per-salon | Catalog только имена услуг | Цены — это **per-salon** (через Shiro-Py) |
| «Какой результат от X?» | ❌ Слабо | Catalog только имена | **Эффективность, expected outcomes, "до-после" реалистичные** |
| «Какие препараты при X?» | ❌ Слабо | Catalog ничего | **Product comparisons (Botox/Dysport/Xeomin, Juvederm/Restylane/Belotero)** |
| «Чем X отличается от Y?» | ❌ Слабо | Catalog ничего | **Procedure comparisons** (chemical peel vs laser resurfacing) |
| «Можно ли совмещать X и Y?» | ❌ Слабо | Matrix только односторонне | **Procedure combinations / sequencing** |
| «Как часто делать X?» | ❌ Слабо | — | **Maintenance frequency** |
| «Как готовиться к X?» | ❌ Слабо | — | **Pre-procedure preparation** |

---

## Decision framework — что включать, что нет

### ✅ Включать в shared corpus:

- **Универсальные медицинские факты** — true everywhere (механизм действия гиалуроновой кислоты — везде одинаков)
- **Регуляторные обязательства** — 152-ФЗ, 38-ФЗ «О рекламе», требования к ИВД / медтехнике
- **Generic протоколы** — стандартизированные техники (стандарт реакции на анафилаксию)
- **Известные противопоказания и red flags** — кросс-салонная безопасность
- **Эффективность с честными expectations** — «филлеры держатся 6-18 мес» а не «эффект навсегда»

### ❌ НЕ включать (это per-salon):

- Конкретные цены — `Shiro-Py/salon-knowledge`
- Имена мастеров — `Shiro-Py/salon-knowledge`
- Расписание салона — `Shiro-Py/salon-knowledge`
- Конкретные акции / скидки — `Shiro-Py/salon-knowledge`
- Внутренние политики возврата — `Shiro-Py/salon-knowledge`

### ⚠️ Включать ОСТОРОЖНО (с экспертом + дислеймером):

- Brand-specific сравнения (Botox vs Dysport) — фактически верно, но юридически = реклама. Нужен disclaimer
- «Какой препарат лучше для X» — opinion, не fact. Только если есть head-to-head исследование
- Off-label uses — могут быть законны клинически, но рекламировать публично нельзя
- Любые claims с «-эффективнее» — требуют peer-reviewed citation

---

## 🔴 P0 — критично, делаем первыми (на 2-недельный запуск)

### Topic 1 — Эффективность процедур + reality check

**Зачем:** клиенты завышают ожидания → постпроцедурная разочарованность → негативные отзывы. Бот должен честно говорить «X улучшит Y на 30-50%, не на 100%».

**Структура:**
- H1: Эффективность бьюти-процедур — что реально ожидать
- H2: Per группа процедур (массажи, инъекции, лазеры, пилинги, аппаратные, эпиляция, ногтевой сервис, волосы)
- H3 per процедура: ожидаемый эффект, срок наступления, длительность результата, реалистичные before/after метрики

**KbDocType:** `HELP_ARTICLE`
**Granularity:** `subsection`
**Estimated rows:** 80-100

**Sources (по убыванию authority):**
- Производители: Allergan Botox insert, Galderma Dysport monograph, Lumenis IPL clinical studies
- AAD efficacy guidelines: https://www.aad.org/public/cosmetic
- Cochrane Reviews для процедур у которых есть meta-analyses (laser hair removal, cellulite treatments)
- Минздрав clinical recommendations если есть

**Эксперт-валидация:** обязательна. Любые числовые claims (% улучшения, duration weeks) нужны источники.

---

### Topic 2 — Подготовка к процедурам (pre-procedure preparation)

**Зачем:** клиент пришёл с алкоголем в крови / на разжижителях крови / без анализов → procedure rescheduled или ухудшился результат. Бот должен спросить ДО записи.

**Структура:**
- H1: Подготовка к процедурам
- H2: Per группа процедур
- H3: Что делать за 2 недели / 3 дня / 24 часа / в день процедуры
- Подразделы: medications to avoid, alcohol, sun exposure, makeup, food, exercise

**KbDocType:** `HELP_ARTICLE`
**Granularity:** `section` (per группа)
**Estimated rows:** 20-30

**Sources:**
- Allergan/Galderma patient prep instructions (включены в product inserts)
- AAD pre-procedure checklists
- Российская ассоциация эстетической медицины (если есть guidelines)

---

### Topic 3 — Combinations & sequencing (можно ли совмещать)

**Зачем:** клиент хочет лазер + ботокс в один визит. Безопасно? Или botox + microneedling через неделю? Это типичные вопросы.

**Структура:**
- H1: Совместимость процедур
- H2: Категории (laser × injectables, peels × resurfacing, mechanical × chemical)
- H3 per pair: можно одновременно / safe interval / нельзя совмещать совсем

**KbDocType:** `CONTRAINDICATION`
**Granularity:** `subsection`
**Estimated rows:** 30-40

**Sources:**
- Allergan combination therapy guidelines
- Journal of Cosmetic Dermatology meta-analyses on multi-modal protocols
- AAD position statements on same-day procedures

**Эксперт-валидация:** критична. «Можно ли совмещать» — это safety call, не маркетинг.

---

## 🟡 P1 — важно, делаем в неделю 2 если есть время

### Topic 4 — Maintenance frequency (как часто делать)

**Зачем:** «как часто можно ботокс» — самый частый запрос. Сейчас бот не знает.

**Структура:**
- H1: Частота и поддерживающие процедуры
- H2: Per процедура
- H3: Минимальный интервал, оптимальный, признаки что пора повторить

**KbDocType:** `HELP_ARTICLE`
**Granularity:** `subsection`
**Estimated rows:** 50-60

**Sources:** product inserts + AAD guidelines + клинические рекомендации Минздрава где есть.

---

### Topic 5 — Skin type matching (под мою кожу)

**Зачем:** «у меня жирная кожа, что подойдёт» — клиент ждёт персонализации.

**Структура:**
- H1: Подбор процедур по типу кожи
- H2: Per skin type (жирная, сухая, комбинированная, чувствительная, проблемная, возрастная, обезвоженная)
- H3 per type: рекомендованные процедуры, чего избегать, особенности ухода

**KbDocType:** `SERVICE`
**Granularity:** `subsection`
**Estimated rows:** 40-50

**Sources:**
- AAD skin type guidelines (Fitzpatrick scale)
- Dermalogica / Holy Land / Christina professional training materials
- Биология кожи textbook references

---

### Topic 6 — Brand-specific сравнения (с disclaimer)

**Зачем:** клиент спрашивает «у вас Ботокс или Диспорт». Бот не знает разницу — выглядит непрофессионально.

**Структура:**
- H1: Сравнение препаратов
- H2: Категории (нейротоксины, филлеры, биоревитализанты, мезо-коктейли)
- H3 per pair (Botox vs Dysport vs Xeomin; Juvederm vs Restylane vs Belotero; Princess vs Hyalrepair)
- Подразделы: химия / origin / onset / duration / зоны применения

**ВАЖНО:** дисклеймер сверху каждой секции — «информация о свойствах препаратов, не реклама конкретного бренда. Выбор препарата — за врачом-косметологом».

**KbDocType:** `HELP_ARTICLE`
**Granularity:** `subsection`
**Estimated rows:** 30-40

**Sources:** ТОЛЬКО product monographs + peer-reviewed head-to-head studies. Никаких блогов / маркетинговых брошюр.

**Эксперт-валидация:** обязательна + legal review (38-ФЗ «О рекламе»).

---

### Topic 7 — Drug interactions (с медикаментами)

**Зачем:** клиент на разжижителях крови (варфарин, аспирин), на аккутане, на гормонах → процедуры опасны. Сейчас матрица противопоказаний это упоминает кратко, нужна detail.

**Структура:**
- H1: Лекарственные взаимодействия с косметологическими процедурами
- H2: Категории препаратов (антикоагулянты, ретиноиды, иммуносупрессоры, гормоны, антибиотики, антидепрессанты, …)
- H3 per category: какие процедуры под запретом / с осторожностью / safe interval после отмены

**KbDocType:** `CONTRAINDICATION`
**Granularity:** `subsection`
**Estimated rows:** 40-50

**Sources:**
- FDA / EMA drug-procedure interaction databases
- Минздрав клинические рекомендации
- PubMed для специфических процедур (laser + isotretinoin — целая литература)

**Эксперт-валидация:** обязательна. Любое drug-related — высокий риск.

---

## 🟢 P2 — backlog после первого салона

### Topic 8 — Возрастные различия (age-specific protocols)
Anti-age 30+ vs 50+ vs 65+. Что начинать, чего избегать. Source: AAD age guidelines.

### Topic 9 — Сезонность (seasonal recommendations)
Пилинги осенью/зимой, лазер не летом, увлажнение зимой. Source: AAD seasonal skin care.

### Topic 10 — Постпроцедурные осложнения и red flags (deep)
Текущий symptoms doc — обзорный. Здесь детально per category: когда отёк норма, когда срочно к врачу. Sources: ASDS complication guidelines.

### Topic 11 — Ингредиенты и аллергены (active ingredients deep-dive)
Hyaluronic acid mechanisms, retinoids, vitamin C, peptides — что они делают, где applied. Sources: peer-reviewed dermatology.

### Topic 12 — Беременность и кормление (pregnancy/lactation matrix)
Уже частично в matrix v0.1, но узкий. Раскрыть: что безопасно по триместрам, что safe lactation, что absolute no. Source: WHO, ACOG.

### Topic 13 — Подростковая косметология (под 18)
Особый протокол, особые legal требования (согласие родителей), особые риски. Source: AAD adolescent skincare position.

### Topic 14 — Мужская косметология (men-specific)
Бритьё × лазер, mens depilation, мужской уход за кожей. Sources: AAD men's grooming guidelines.

### Topic 15 — Этно-специфика (Asian / African skin)
Other Fitzpatrick types react differently to laser, peels. Особенно актуально для смешанных регионов России. Source: dermatology by skin-type literature.

### Topic 16 — Восстановление после серьёзных процедур (medical-adjacent)
Mastectomy reconstruction, post-cancer-treatment beauty care, scar revision. Source: medical oncology dermatology.

### Topic 17 — Подбор домашнего ухода (home care recommendations)
Что использовать ДО / ВО ВРЕМЯ / ПОСЛЕ курса процедур. SPF, ретиноиды, кислоты. Source: AAD home care.

### Topic 18 — Когнитивные искажения и nocebo
Клиент ожидает осложнения — получает. Образовательный раздел про placebo/nocebo, как настраиваться. Source: psychology of beauty literature.

### Topic 19 — Стоматологическая косметология (если в скоупе)
Виниры, отбеливание, импланты — другая дисциплина, но если боты обслуживают full beauty — нужно. Sources: ADA, российские стоматологические guidelines.

### Topic 20 — Trichology (трихология, волосы)
Mesotherapy для волос, PRP, протезирование. Sources: International Society of Hair Restoration Surgery.

---

## Source authority hierarchy (для эксперта)

По убыванию веса. Любое claim в тексте должно ссылаться хотя бы на один источник этого уровня.

| Уровень | Источник | Когда использовать |
|---|---|---|
| 1 | Manufacturer official protocol / product insert | Для product-specific facts (Botox, Juvederm, IPL devices) |
| 2 | Peer-reviewed journal (PubMed, Cochrane) | Для efficacy claims, head-to-head comparisons |
| 3 | Professional society guidelines (AAD, EADV, ASDS) | Для standard-of-care, best practices |
| 4 | Regulatory (FDA, EMA, Минздрав, Roszdravnadzor) | Для regulatory facts, approved indications |
| 5 | WHO position statements | Для public health framing (pregnancy, age, ethics) |

**НЕ используем как primary source:**
- Wikipedia (можно как secondary fact-check, но primary — нет)
- Блоги, форумы, инфлюенсеров
- Маркетинговые брошюры производителей (≠ protocol)
- ChatGPT / AI-generated content без human review
- Российские «эстетические» журналы без peer review

---

## Workflow создания нового дока

### Шаг 1 — Андрей создаёт Google Doc по структуре

```
# Название темы (H1)
Краткое intro 2-3 предложения.

## Раздел 1 (H2)
### Подраздел 1.1 (H3)
Содержимое.

Sources: [Allergan Botox insert 2024 §3.2]; [AAD efficacy guidelines]

### Подраздел 1.2 (H3)
...
```

### Шаг 2 — Расшарить на «anyone with the link can view»
(Чтобы наш `GoogleDocsClient` мог читать без auth.)

### Шаг 3 — Отправить URL эксперту-валидатору

Эксперт читает, оставляет inline comments. Контракт:
- Любое числовое claim требует source citation
- Любое сравнение брендов / препаратов требует disclaimer
- Любое contraindication требует чёткую severity (A/R/T/L — absolute / relative / temporary / local) ровно как в Doc 2

### Шаг 4 — Андрей вносит правки эксперта

### Шаг 5 — Запустить seed cmd

```bash
python manage.py seed_kb_from_gdocs \
    --doc-id <new-doc-id> \
    --doc-type <chosen> \
    --granularity <chosen> \
    --tenant-slug global_kb
```

Идемпотентно. K6 Celery sweep подберёт за минуты.

### Шаг 6 — Опционально: golden query test

Если новый док покрывает новый archetype запросов — добавить smoke test в `apps/kb/services/tests/test_global_fallback_smoke.py` с одним golden query на этой теме.

---

## Anti-patterns — чего избегать в shared corpus

- ❌ **Имена врачей / мастеров / салонов** — per-salon, не общее
- ❌ **Цены** — per-salon
- ❌ **«Лучший препарат для X»** — opinion, не fact (если нет peer-reviewed head-to-head)
- ❌ **«Я гарантирую результат»** — illegal (38-ФЗ; никаких guarantees в medical context)
- ❌ **Off-label use claims** — если препарат одобрен для X, нельзя рекламировать для Y, даже если используется так клинически
- ❌ **PII** — даже case studies должны быть anonymized
- ❌ **Раздел «отзывы клиентов»** — не shared knowledge, плюс PII риск
- ❌ **Маркетинговый язык** — «революционный», «уникальный», «единственный в России» — все эти эпитеты в medical context = реклама / mis-leading

---

## Что от эксперта-валидатора нужно

Когда я отдаю topic backlog → эксперт:

1. Выбирает 3-5 топиков для первой итерации (рекомендую P0: Topics 1, 2, 3)
2. Помогает с источниками — у него профессиональные базы (UpToDate, Cochrane, dermatology textbooks)
3. Пишет первый draft (~2-4 часа на док)
4. После моего code-review структуры (правильные H2/H3 для granularity) — финализирует
5. Прогоняет seed cmd мы вместе → смотрим что вышло в ChromaDB → golden query smoke

Total expert time на P0: ~15-20 часов в течение 2 недель.

---

## Что от Андрея нужно

1. Контакт эксперта — кому отправить этот документ
2. Если эксперт ещё не зафиксирован — найти / договориться за следующие 3 дня (это блокер для P0)
3. Решение по Topic 6 (brand сравнения) — нужен legal review до публикации, либо вообще выкидываем из shared в этой итерации

---

## Анти-список — что НЕ ходит в эту итерацию

- Создание изначального катало услуг — уже есть (Doc 1 — 1500 услуг)
- Polish контента в существующих 4 docs — отдельная задача, не сейчас
- UI для редактирования shared corpus — не в скоупе, правки через Google Docs + seed cmd
- Multi-language — пока только ru-RU; en-US подождёт

---

## Timeline под 2-недельный запуск

| День | Кто | Что |
|---|---|---|
| Сегодня (1) | Я | Бэклог готов (этот документ) |
| 1-2 | Андрей | Передать эксперту |
| 2-3 | Эксперт | Выбрать 3 топика из P0, начать первый |
| 3-7 | Эксперт | Написать первые 3 Google Docs |
| 7 | Я | Прогон seed cmd на готовые доки |
| 7-10 | Я + эксперт | Golden query smoke + fixup |
| 10-14 | Эксперт | Второй заход — Topic 4 и 5 если есть время |
| 14 | Launch | Бот отвечает на расширенный список запросов |

P0 (3 топика) — реалистичная цель на 2 недели. P1 — bonus если эксперт быстр.

---

**Готов передать. Жду контакт эксперта-валидатора, либо знака что мы его ещё ищем.**
