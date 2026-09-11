# MEASUREMENT: сквозной Safety (п.9) и флаги/рубильники (п.32)

**Окно:** `ayla-06`, матрица готовности Controlled Pilot.
**Дата замера:** 09.09.2026. **Режим:** MEASURE-FIRST, read-only. Код не тронут.
**Предмет:** пункт 9 (Safety end-to-end) и пункт 32 (feature flags / kill switches).
**Норма:** `Ayla_Safety_Architecture_v1_FINAL_FREEZE_2026-09-09.md` (FINAL FREEZE, V1–V8 и M1–M14 не переоткрываются). Ниже — только замер рантайма и его расхождения с уже принятым.

---

## 1. Базы замера

| repo | ветка | фактический SHA на 09.09.2026 | чем снято |
|---|---|---|---|
| `ai-bot-platform` | `origin/dev` | `b1a119bdfb26765bc75ce35dfbf1dd82227183d0` | `git show` / `git grep` по ref |
| `djangoproject-catalog` | `origin/dev` | `95c917e684652476feef3ae9d790fb2c8d277378` | `git show` / `git grep` по ref |
| `ayla-ai-core` | `origin/main` | `d72a5de451f985d118d9449d2b17ce51bf0a6e25` | `git rev-parse` (в предмет не входит) |
| `ayla-knowledge` | `origin/main` | `207eeb638580e529aaff66e0d1412aa6b559c0a3` | `git rev-parse` |

Все четыре SHA **совпали** со сводом; замер по канону, не по рабочему дереву. Рабочий чекаут `ai-bot-platform` стоит на `feat/recommendation-boundary-client`, `ayla-ai-core` — на `fix/memory-origin-vocabulary`; ни один файл рабочего дерева в замер не попал.

---

## 2. Executive verdict — по одному ответу на строку

1. **Канонической цепочки `UserEvent → Evidence → Safety producer → SafetyResult → capability decisions → enforcement → Outbound` в рантайме не существует.** Существуют два независимых обрубка: regex-детектор на входе канала (ai-bot-platform) и enum-гейт на входе резолвера (djangoproject-catalog). Между ними нет ни одного звена.
2. **Producer'а SafetyResult нет ни в одном из двух рантаймов.** Слово `safety_state` в `ai-bot-platform/apps/**` встречается **ноль** раз; значение enum'а на проводе некому произвести.
3. **Из восьми состояний в рантайме реально исполняются три: `STOP`, `UNKNOWN`, `NOT_APPLICABLE` — и все три только внутри `djangoproject-catalog/recommendation/`.** `CAUTION` и `CLARIFY` объявлены в enum и **не упомянуты ни в одной ветке кода** — ведут себя как «пропустить». `NORMAL` недостижим на живой поверхности. `ERROR` и `POLICY_CONFLICT` не существуют нигде.
4. **`capability_decisions` (`ALLOWED | REQUIRES_RESOLUTION | BLOCKED`) не существуют ни в одном репозитории.** Потребители выводят допустимость из состояния — ровно то, что канон §M13 запрещает.
5. **Четыре из пяти критических поверхностей ПРОПУСКАЮТ бронь услуги с `requires_health_check=true` при отсутствии валидного SafetyResult.** Пятая (диалоговая бронь MAX-бота) закрывает — но только пока `BOOKING_VIA_AYLA_REST=true`.
6. **По правилу владельца это STOP PILOT.**
7. **Найдена ПЯТАЯ, неучтённая поверхность:** `djangoproject-catalog/ai/` — живой LLM-чат `POST /api/v1/ai/chat/` + бронь `POST /api/v1/ai/chat/action/` + **собственный ранжировщик со взвешенной суммой (rating 30 %)**. Ноль safety: ни inbound-проверки, ни outbound-стража, ни `requires_health_check`. Смонтирован безусловно, без единого флага.
8. **Гейт `NOT_APPLICABLE` на живой поверхности обесточен адаптером:** три из четырёх его входов — литеральные `False` в `users/recommendation_source.py:271,285,286`, четвёртый (`requires_health_check`) на живых данных тоже `False`.
9. **Да, флаг может превратить safety в небезопасный legacy-фолбэк.** `BOOKING_VIA_AYLA_REST=false` переводит единственный работающий health-гейт с ветки «неизвестно → закрыто» на ветку «строки нет → `return False`» (`apps/skills/booking/skill.py:1512-1513`).
10. **Рубильников `RECOMMENDATIONS`, `PLAN_LITE`, `PROACTIVE_HINTS` не существует ни в одном рантайме.** Ни под этими, ни под синонимичными именами.
11. **Отключить рекомендации переменной окружения нельзя вовсе.** Единственный рычаг — `RECOMMENDATION_CANDIDATE_SOURCE`, и он захардкожен литералом в `djangoProject/settings/base.py:81`, а не читается из env: выключение = деплой кода.
12. **Если рекомендации всё же выключить (сняв этот литерал) — прямой просмотр и прямая бронь остаются.** Резолвер вызывается ровно из двух мест, оба — полки Mini App; `appointments/` и каталожные листинги от него не зависят.
13. **Персистентности safety-состояния между ходами нет конструктивно.** Единственная межходовая память рядом — памятка скрининга с TTL 1800 с, и она гасит только повтор `SOFT`, никогда `RED_FLAG`.
14. **Красный флаг скрининга не получает состояния и не закрывает запись** — отдаёт текст и заканчивается.
15. **На глобальном пути решение вызвать скрининг принимает LLM** (tool `health_screening`), что прямо противоречит запрету №8 канона.
16. **Outbound-страж при исключении внутри себя пропускает ответ наружу** (`outbound.py:292-294`) — fail-OPEN там, где канон M14 требует `ERROR` + fail-closed.
17. **Положительное:** `SAFETY_PATTERNS` устроен так, что настройкой можно только добавить паттерны, но не убрать; `NUTRITION_*`-флаги скрининг не гейтят; HTTP-ручка резолвера при неподключённом источнике честно отдаёт 503, а не пустую выдачу.

---

## 3. Сводка по классам числом

CURRENT STATE (28 находок):

| класс | шт. | номера |
|---|---|---|
| `MISSING` | 9 | F1, F3, F4, F16, F17, F18, F19, F26, F27 |
| `CONTRADICTS_CANON` | 9 | F2, F5, F6, F9, F10, F11, F13, F14, F14bis |
| `PARTIAL` | 4 | F7, F8, F12, F25 |
| `DEAD_CODE` | 2 | F20, F21 |
| `STALE_SPEC` | 1 | F22 |
| `EXISTS` (положительное) | 3 | F23, F24, F28 |

PILOT IMPACT: `STOP` — 7 (F2, F5, F6, F7, F14bis, F18, F19); `DEGRADED` — 14; `POST_PILOT` — 4; положительных — 3.

TEST STATUS по предмету: `E2E_GREEN` — 0; `CROSS_BOUNDARY` — 0; `CONTRACT_ONLY` — 2; `UNIT_ONLY` — 9; `MISSING` — 11 (поимённо в §8).

---

## 4. Пункт 9 — проход по канонической цепочке, стрелка за стрелкой

Канон (FINAL FREEZE §1):

```
UserEvent → Evidence Extraction → Evidence Origin Validation → SafetySignals
→ versioned SafetyRules → RuleResults → SafetyResult
→ DecisionReadiness/Consumers → Response Generation → Outbound Validation → User
```

| # | стрелка | состояние | доказательство |
|---|---|---|---|
| 1 | `UserEvent → Evidence` | **РАЗОРВАНА** | Типизированного `SafetyEvidence` не существует. `pre_check(text)` принимает голую строку и не порождает объекта свидетельства: `ai-bot-platform/apps/orchestrator/safety/pre_check.py:186-191` |
| 2 | `Evidence → Origin Validation` | **РАЗОРВАНА** | Origins нет. Ассистентский текст от пользовательского на safety-пути не отличается ничем; единственная защита от «модель проверяет себя собой» — точечная, в одном инструменте: `apps/orchestrator/nutrition_global.py:292-312` |
| 3 | `Evidence → SafetySignals` | **ЧАСТИЧНО, в виде детектора** | 7 групп regex `pre_check.py:78-141` + классификатор скрининга `apps/skills/health_screening/classifier.py`. Канон §7 разрешает их как «detectors/inventory only» — но других сигналов нет |
| 4 | `Signals → versioned SafetyRules` | **ОТСУТСТВУЕТ** | `rule_id`, `rule_version`, `policy_version` — ноль совпадений по `apps/**` в обоих репозиториях. Машиночитаемого артефакта политики в `ayla-knowledge` для safety runtime не читает |
| 5 | `Rules → RuleResults` | **ОТСУТСТВУЕТ** | Индивидуальных RuleResult нет; regex сразу редуцируется в один вердикт `pre_check.py:245` |
| 6 | `RuleResults → SafetyResult` | **ОТСУТСТВУЕТ; имя занято** | `pre_check.py:67-73` — `SafetyResult` это `verdict + matched_patterns + reason`, вердикт из `{allow, clarify, block, handoff}`. Ни `applicability`, ни `evaluation_status`, ни `capability_decisions`, ни `policy_version` |
| 7 | `SafetyResult → capability decisions` | **РАЗОРВАНА** | `ALLOWED / REQUIRES_RESOLUTION / BLOCKED` — ноль совпадений в обоих рантаймах |
| 8 | `capability decisions → Recommendation enforcement` | **ПОДМЕНЕНА состоянием** | `djangoproject-catalog/recommendation/_stages.py:240,268` — исполнение выводится напрямую из enum состояния, а не из решения по capability |
| 9 | `capability decisions → Booking enforcement` | **РАЗОРВАНА** | Ни один путь создания брони ни в одном репозитории не читает safety. Единственная проверка — `_service_requires_health_check` в диалоговом скилле бота (`apps/skills/booking/skill.py:1436`), и она читает булев признак услуги, а не SafetyResult |
| 10 | `enforcement → Response Generation` | **ОТСУТСТВУЕТ** | `response_constraints` нет; при BLOCK/HANDOFF отдаётся константный текст `gate.py:80-95` |
| 11 | `Response → Outbound Validation` | **СУЩЕСТВУЕТ, отдельным словарём** | `guard_outbound` (`gate.py:179`) на 5 живых точках; словари `{allowed, categories}` (`outbound.py:263-268`) и `{allow, revise, block}` (`post_check.py:54-57`) — с осью состояний не связаны |
| 12 | `Outbound → User` | **СУЩЕСТВУЕТ, с fail-open** | `outbound.py:292-294`: любое исключение внутри проверки → `allowed=True`, ответ уходит |

### Восемь состояний — что существует и что происходит фактически

| состояние | ai-bot-platform | djangoproject-catalog | что фактически происходит |
|---|---|---|---|
| **NORMAL** | **нет.** Ближайшее — `SafetyVerdict.ALLOW` (`pre_check.py:61`) | **есть** в enum `recommendation/_types.py:79` | Единственная ветка, читающая NORMAL, — `_stages.py:330-331` (выдать `ELIG_SAFETY_CLEARED`). Прийти оно может только через HTTP-ручку `POST /internal/recommendation/resolve`, у которой **нет production-caller'а**. На живой поверхности не встречается никогда |
| **CLARIFY** | **есть** (`pre_check.py:62`), но `gate.py:145-146` **намеренно не короткозамыкает** — ход продолжается | **есть** в enum, **не упомянут ни в одной ветке** `apply_eligibility` | Пропускает. В каталоге отличается от NORMAL ровно одним: не выдаётся `ELIG_SAFETY_CLEARED` (`_stages.py:323-331`). Вопроса не порождает: «решает трек A» |
| **CAUTION** | **НЕ СУЩЕСТВУЕТ ВОВСЕ.** `git grep CAUTION origin/dev -- apps` → 0 | **есть** в enum `_types.py:81`, **не упомянут ни в одной ветке** | Поведение побитово совпадает с CLARIFY: пропускает, `ELIG_SAFETY_CLEARED` не выдаёт. Capability restrictions, которых канон M6 требует от каждого CAUTION-правила, не существуют — значит «ограниченное продолжение» неотличимо от обычного |
| **STOP** | **нет.** Есть `BLOCK` и `HANDOFF`, оба → константный ответ (`gate.py:128-143`) | **есть**, `_stages.py:240` | В каталоге закрывает **всю выдачу целиком**, а не affected capability — грубее канона M1, но в безопасную сторону. В боте BLOCK/HANDOFF гасят ход, но **не создают состояния** и на следующем ходу не помнятся |
| **UNKNOWN** | **нет** | **есть**, `_stages.py:240` — fail-closed вместе со STOP | Закрывает выдачу. Отдельный AST-сторож запрещает воскрешать его как NOT_APPLICABLE (`recommendation/tests/test_safety_not_applicable.py:172-219`). Самая сильная точка контура |
| **ERROR** | **НЕТ** | **НЕТ** | Исключение резолвера → 500 (нет `try` вокруг `resolve()`, `recommendation/views.py:78-92`) — фактически fail-closed. Исключение outbound-стража → **ответ уходит наружу** (`outbound.py:292-294`) — fail-OPEN. Технический отказ не отличим от «проверено и чисто» |
| **POLICY_CONFLICT** | **НЕТ** | **НЕТ** | Конфликтовать нечему: versioned-политики с `rule_id` не существует. Наблюдаемого инцидента, которого требует M10, тоже нет |
| **NOT_APPLICABLE** | **нет.** Единственные совпадения — `shadow_turn.py:68,158`, пометка шага tool invocation, к safety отношения не имеет | **есть**, `_types.py:84` | **Единственное состояние, которое реально приходит на живую поверхность:** `users/catalog_recommendations_api.py:476` объявляет его константой поверхности. Отменитель (`_is_safety_sensitive`) существует и покрыт сторожами — но обесточен адаптером (F5) |

**Вывод по восьми состояниям:** в рантайме исполняются три (`STOP`, `UNKNOWN`, `NOT_APPLICABLE`), и все три — в одном модуле одного репозитория. Два (`CLARIFY`, `CAUTION`) объявлены и мертвы. Три (`NORMAL` — практически, `ERROR`, `POLICY_CONFLICT`) недостижимы или не существуют.

### Пять критических поверхностей

Вопрос один и тот же: **услуга с `requires_health_check=true`, валидного SafetyResult нет — закрывается путь или пропускает?**
Оговорка, общая для всех пяти: валидного SafetyResult нет **никогда** (F1), поэтому вторая половина условия истинна всегда.

| # | поверхность | точка входа | ответ | доказательство |
|---|---|---|---|---|
| 1a | **Рекомендация — полки Mini App** | `djangoproject-catalog/users/catalog_recommendations_api.py:509-530` → `recommendation.api.resolve` | **ЗАКРЫВАЕТСЯ механизмом, но механизм не включается данными** | `_stages.py:241` + `_stages.py:363-367`: `requires_health_check=True` у любого кандидата отменяет `NOT_APPLICABLE` → `safety_blocks_all=True` → полка пуста. Но признак приезжает из `users/recommendation_source.py:275`, где он `False` для всех живых услуг (F5, F6) |
| 1b | **Рекомендация — выдача MAX-бота** | `ai-bot-platform/apps/marketplace/discovery.py` | **ПРОПУСКАЕТ** | Слова `safety` в модуле нет вовсе (единственное совпадение — `discovery.py:315`, комментарий про другое). Признака `requires_health_check` модуль не читает |
| 1c | **Рекомендация — каталожный AI-чат** | `djangoproject-catalog/ai/application/services/recommendation_engine.py` через `SpecialistContextBuilder` | **ПРОПУСКАЕТ** | Взвешенная сумма `WEIGHT_RATING=0.30` (`:57`, `:155-163`), `requires_health_check` не читается ни в одной строке `ai/**` |
| 2 | **Прямая бронь** | `djangoproject-catalog/appointments/views.py:177` → `CreateBookingService.execute` | **ПРОПУСКАЕТ** | `git grep requires_health_check origin/dev -- appointments` → **0 совпадений**. `_validate_pre_transaction` (`create_booking_service.py:118-169`) проверяет активность специалиста, услуги и окно времени; здоровье — нет |
| 3a | **Бронь из каталожного чата — MAX-бот** | `apps/skills/booking/skill.py:1037` (confirm) и `:1893` (pick_slot) | **ЗАКРЫВАЕТСЯ — условно** | При `BOOKING_VIA_AYLA_REST=true`: `:1477-1496`, неизвестное ребро → `return True` → handoff `booking_health_check_required`. При `false`: `:1502` и `:1512-1513` → `return False`. **Один флаг переворачивает направление отказа** |
| 3b | **Бронь из каталожного чата — каталожный AI-чат** | `djangoproject-catalog/ai/application/services/action_service.py:105-197` → `CreateBookingService` | **ПРОПУСКАЕТ** | Между разбором `data["service_id"]` (`:129`) и `self._booking_service.execute(dto)` (`:167`) нет ни одной проверки, кроме формата даты |
| 4 | **Бронь из Mini App** | `ai-bot-platform/apps/miniapp_api/views.py:1036` → `_create_booking_via_ayla` → `POST /internal/appointments/` | **ПРОПУСКАЕТ** | `_service_requires_health_check` в модуле не импортируется и не вызывается. Проверка на `:947-959` — про **grounding** (`ayla_service_id` синхронизирован), её комментарий ссылается на health-гейт, но код проверяет другое. Принимающая сторона `appointments/internal_api.py:199` — тот же `CreateBookingService` без проверки |
| 5 | **Бронь мастером** | `appointments/views.py:247-293` (walk-in) и `ai-bot-platform/apps/admin_api/views_booking_create.py` | **ПРОПУСКАЕТ** | Ноль совпадений `health` в обоих файлах |

**Итог: 4 поверхности из 5 пропускают безусловно; 5-я — только пока держится один флаг.** По прямому правилу владельца — **STOP PILOT**.

Признание самого кода, дословно (`apps/skills/booking/skill.py:1469-1475`, докстринг `_service_requires_health_check`):

> «Note what this gate is and is not. No other booking entry point in this codebase consults it — `apps/booking/services/create.py`, `apps/admin_api/views_booking_create.py` and the miniapp all create bookings without reading the flag — and Ayla's `appointments` app does not enforce it server-side either. It is the conversational channel's routing policy ("hand this one to a human"), **not a system-wide safety interlock**.»

Проверено грепом отдельно, комментарию не поверив: `git grep -n requires_health_check origin/dev -- apps` в `ai-bot-platform` даёт совпадения только в `apps/catalog/**` и `apps/skills/booking/**`; в `djangoproject-catalog` — только в `services/**`, `recommendation/**`, `users/recommendation_source.py`. **Комментарий говорит правду.**

---

## 5. Ключевой шов: «отсутствие связи стало утверждением о безопасности»

Подтверждаю указанный факт на текущих SHA и называю **все** места.

### F6 — `services/models.py:574-583` — `CONTRADICTS_CANON` / `STOP`

```python
def resolved_requires_health_check(self) -> bool:
    """Escalate-only OR across template floor, salon, specialist (D1)."""
    salon = self.salon_service
    template = salon.template
    template_floor = (
        template.requires_health_check if template is not None else False
    )
    return bool(
        template_floor or salon.requires_health_check or self.requires_health_check
    )
```

`template is None` — это `UNMAPPED`, то есть «канонической связи нет». Оно превращается в `False`, то есть в «пол проверки здоровья не требует». Тип возврата `bool` третьего состояния не допускает. `mapping_status` не спрашивается ни разу.

### F7 — `services/serializers.py:222,252,261-262` — граница, на которой отсутствие становится утверждением — `PARTIAL` / `STOP`

```python
def get_resolved_requires_health_check(self, obj: SpecialistService) -> bool:
    return obj.resolved_requires_health_check()
```

Наружу через `SpecialistServiceInternalSerializer` уезжает `false`. На принимающей стороне (`ai-bot-platform/apps/catalog/models.py:800-806`, `help_text` миграции `0012`) `NULL` означает «никогда не синхронизировали → скрининг нужен», а `false` — «решено: не нужен». **Продюсер уничтожает третье состояние до того, как потребитель успевает его увидеть.** Потребитель написан правильно (`http_client.py:776-796`, `_parse_optional_bool` — честный tri-state, отсутствие ключа → `None` → гейт закрыт), и именно поэтому дефект невидим: защита стоит от «поля нет», а приходит «поле есть и врёт».

### F5 — `users/recommendation_source.py:271,285,286,388` — гейт обесточен адаптером — `CONTRADICTS_CANON` / `STOP`

`_is_safety_sensitive` (`_stages.py:363-367`) читает ровно три поля:

```python
return any(
    facts.requires_health_check or facts.prior_completed_visit
    or facts.prior_completed_same_category
    for facts in candidates
)
```

В единственном production-адаптере два из трёх — литеральные `False`:

```
users/recommendation_source.py:271:            safety_blocked=False,
users/recommendation_source.py:285:            prior_completed_visit=False,
users/recommendation_source.py:286:            prior_completed_same_category=False,
```

Третье (`requires_health_check`, `:275`) собирается из `_MappingFacts`, чьё начальное значение тоже `False` (`:388`) и поднимается в `True` только внутри цикла по связям (`:206-207`) — то есть у специалиста без активных `SpecialistService`-связей остаётся `False`.

Следствие: **override владельца «активная персонализация отменяет `NOT_APPLICABLE`» в рантайме не может сработать никогда** — персонализация выражена двумя полями, оба захардкожены в `False`. Полка 1 «твои места» строится по `NeedOrigin.MEMORY` (`catalog_recommendations_api.py:513`), то есть персонализирована **по построению**, а фактам об этом сказать нечем.

Вместе с F6 это означает: на живой поверхности `safety_blocks_all` не станет `True` **ни при каких данных**, кроме прямого объявления `STOP`/`UNKNOWN` вызывающим — которого никто не объявляет.

### F8 — `recommendation/_types.py:361,365` — умолчания dataclass — `PARTIAL` / `DEGRADED`

```
safety_blocked: bool = False
requires_health_check: bool = False
```

Любой будущий `CandidateSource`, забывший заполнить поля, получит «безопасно» молча. Тот же класс, что снятая константа, вернувшаяся вычисленной: умолчание входа воскрешает выброшенное число.

### Все места, читающие отсутствие как `False` — полный список

| # | место | что именно | класс |
|---|---|---|---|
| 1 | `djangoproject-catalog/services/models.py:578-580` | `template is None → False` | `CONTRADICTS_CANON` |
| 2 | `djangoproject-catalog/services/serializers.py:261-262` | tri-state схлопывается в `bool` на границе | `CONTRADICTS_CANON` |
| 3 | `djangoproject-catalog/users/recommendation_source.py:388` | `_MappingFacts.requires_health_check = False` — «связей не нашли» = «проверка не нужна» | `CONTRADICTS_CANON` |
| 4 | `djangoproject-catalog/users/recommendation_source.py:271` | `safety_blocked=False` литералом | `CONTRADICTS_CANON` |
| 5 | `djangoproject-catalog/users/recommendation_source.py:285-286` | `prior_completed_* = False` литералом → override персонализации мёртв | `CONTRADICTS_CANON` |
| 6 | `djangoproject-catalog/recommendation/_types.py:361,365` | умолчания dataclass | `PARTIAL` |
| 7 | `djangoproject-catalog/services/models.py:388` и `:534` | `models.BooleanField(default=False)` на `SalonService` и `SpecialistService` — «оператор не отвечал» = «оператор ответил нет» | `PARTIAL` |
| 8 | `ai-bot-platform/apps/catalog/services/http_client.py:114` и `:827` | `requires_health_check: bool = False` в DTO и `bool(row.get("requires_health_check", False))` — отсутствие ключа в ответе Ayla → `False`. Питает **legacy-ветку** health-гейта (`skill.py:1514`) | `PARTIAL` |
| 9 | `ai-bot-platform/apps/skills/booking/skill.py:1512-1513` | `if row is None: return False` — услуги нет в зеркале → бронировать можно | `CONTRADICTS_CANON` |
| 10 | `ai-bot-platform/apps/skills/booking/skill.py:1502` | `except ImportError: return False` — каталога нет → бронировать можно | `PARTIAL` |

Единственные места во всём контуре, где отсутствие **не** становится `False`, — `http_client.py:776-796` (`_parse_optional_bool`) и `skill.py:1496` (`unknown_edge_closed → return True`). Оба на стороне потребителя, оба защищают от «поля нет», ни один — от «поле есть и содержит `false`, потому что связи нет».

**Про 58 из 58.** Число снято главным окном 08–09.09 по боевой базе. В режиме read-only и без доступа к контуру я его **не перепроверял** — это факт БД, а не кода. Что подтверждено на текущих SHA: **механизм, который делает это число опасным**, существует целиком и работает ровно так, как описано (F6 + F7 + F5). Команда для снятия числа — в §10.3.

---

## 6. Остальные находки по п.9

**F1. Producer канонического SafetyResult отсутствует в обоих рантаймах — `MISSING` / `STOP`.**
`git grep -n "safety_state\|SafetyState" origin/dev -- apps` в `ai-bot-platform` → **пусто**. Enum на проводе обязателен (`recommendation/_serializers.py:104`, `ChoiceField` без `default`), но со стороны бота его некому заполнить. Единственный производитель значения — `catalog_recommendations_api.py:476`, где оно **константа поверхности**, а не результат оценки.

**F2. Имя `SafetyResult` занято regex-вердиктом — `CONTRADICTS_CANON` / `STOP`.**
`ai-bot-platform/apps/orchestrator/safety/pre_check.py:67-73`. Канон §M13 требует от `SafetyResult` девяти полей (`applicability`, `evaluation_status`, `aggregate_state`, `rule_results`, `capability_decisions`, `unresolved_requirements`, `escalation`, `response_constraints`, `policy_version`). Есть три: `verdict`, `matched_patterns`, `reason`. Опасность имени — в миграции: следующий исполнитель, увидев `SafetyResult`, решит, что producer есть.

**F3. `rule_id` / `rule_version` / `policy_version` / `evidence_ref` — `MISSING` / `STOP`.** Ноль совпадений в `apps/**` обоих репозиториев. §V8 («runtime не читает Markdown как policy») исполнен вырожденно: runtime не читает политику вовсе.

**F4. `capability_decisions` — `MISSING` / `STOP`.** `ALLOWED`/`REQUIRES_RESOLUTION`/`BLOCKED` — ноль совпадений. Потребитель выводит допустимость из состояния (`_stages.py:240,268`), что §M13 запрещает прямо.

**F9. Приоритет состояний в коде — прежний и другой — `CONTRADICTS_CANON` / `DEGRADED`.**
`pre_check.py:254-259`: `_VERDICT_PRIORITY = [ALLOW, CLARIFY, BLOCK, HANDOFF]`, старший выигрывает. Канон: `STOP > CLARIFY > CAUTION > NORMAL`. Это не переименование: `HANDOFF` по §V7 вообще не состояние, а escalation, и держать его старшим в оси состояний — запрещённый ярлык №3.

**F10. Персистентности состояния нет конструктивно — `CONTRADICTS_CANON` / `STOP`.**
Все четыре проверки (`pre_check`, `post_check`, `evaluate_outbound`, `validate_voice`) stateless и per-message. Единственная межходовая память рядом — `apps/skills/health_screening/memo.py:62`, `STATE_TTL_SECONDS = 1800`, и она по построению гасит только повтор `SOFT` (`skill.py:120-124`; `RED_FLAG` в памятку не пишется вовсе, комментарий `:121-123`). Инвариант V4 («SafetyState не сбрасывается следующей репликой») неисполним: сбрасывать нечего.

**F11. Красный флаг скрининга не получает состояния и не закрывает запись — `CONTRADICTS_CANON` / `STOP`.**
`apps/skills/health_screening/skill.py:110-118`: `RED_FLAG` → `SkillResult(reply_text=RED_FLAG_REPLY, meta={"reply_kind": "health_red_flag"})`. Ни записи в `skill_state`, ни влияния на `_service_requires_health_check`, ни на резолвер. Человек, сказавший «онемела рука», через реплику записывается на массаж.

**F12. `health_screening` и safety-движок кодом не связаны — `PARTIAL` / `DEGRADED`.** Ни один не импортирует другого (проверено `git grep` по обоим пакетам). Связь только по порядку регистрации в диспетчере.

**F13. На глобальном пути вызов скрининга выбирает LLM — `CONTRADICTS_CANON` / `STOP`.**
`apps/orchestrator/nutrition_global.py:88-107` — `HEALTH_SCREENING_TOOL_SPEC`, модель-callable tool; `concierge.py:685` включает его в `CONCIERGE_TOOL_SPECS`. Докстринг модуля признаёт это дословно (`nutrition_global.py:43-45`): «The accepted risk (owner decision, brief §8) is that the model may not call the tool where `matches()` would have fired». Канон, запрет №8: «Let LLM choose SafetyState/severity» — запрещено. Смягчение существует и оно правильное: если модель инструмент всё-таки вызвала, классификатор судит по словам **человека**, а не по пересказу модели (`:292-312`), и это закрывает половину дефекта — но не ту, где модель не позвала вовсе.

**F14. Outbound-страж fail-OPEN при собственном отказе — `CONTRADICTS_CANON` / `DEGRADED`.**
`apps/orchestrator/safety/outbound.py:288-294`:

```python
except Exception:  # noqa: BLE001 — a broken regex must not eat the turn
    logger.exception("safety.outbound.check_failed")
    return OutboundVerdict(allowed=True, text=body)
```

Канон M14: «Technical runtime failure = `ERROR`, не medical STOP. Safety-sensitive capability fail-closed». Здесь технический отказ проверки медицинских утверждений превращается в разрешение их выпустить. Закреплено тестом `test_outbound.py:177` (`test_a_broken_check_never_eats_the_answer`) — то есть это осознанное поведение, а не недосмотр, и меняться оно должно решением, а не правкой.

**F14bis. Пятая поверхность, которую никто не считал — `djangoproject-catalog/ai/` — `CONTRADICTS_CANON` / `STOP`.**

Смонтирована безусловно: `djangoProject/urls.py:150` → `ai/urls.py:21,22`. Аутентификация — `IsAuthenticated, IsClientApp` (`ai/views.py:77,169`), **флага нет**.

Что там есть:
- `AIChatView` — LLM-диалог с клиентом. `git grep -i safety origin/dev -- ai` → **ни одного совпадения**. Ни `pre_check`, ни `guard_outbound`, ни `post_check`.
- `AIChatActionView` → `ActionService._handle_confirm_booking` (`ai/application/services/action_service.py:105-197`) — **создаёт бронь**. Между разбором `service_id` и `execute(dto)` проверок нет.
- `RecommendationEngine` (`ai/application/services/recommendation_engine.py:57-61,155-163`) — **вторая авторитетная система рекомендаций** со взвешенной суммой: `rating 0.30`, `distance 0.25`, `availability 0.20`, `service_match 0.15`, `history 0.10`. Ровно тот дефект, от которого защищается резолвер (`_stages.py:1-8`: «единая взвешенная сумма запрещена буквой канона §9… соответствие нужде весит 0.15, а рейтинг 0.30 — вдвое больше»). Докстринг называет себя «**the** ranker» (`:14`).

Почему это не поймал структурный сторож: `apps/channels/tests/test_handler_safety_parity.py:399-425` сканирует **только `ai-bot-platform/apps`** и **только модули, вызывающие `orchestrate_turn(`**. Каталожный чат — другой репозиторий и другой «мозг». Сторож правильный и по дереву, а не по тексту; он просто не видит второго дома.

**F15. Три несовместимых вердиктных словаря — `CONTRADICTS_CANON` / `DEGRADED`.**
`pre_check.py:60-64` `{allow, clarify, block, handoff}`; `post_check.py:54-57` `{allow, revise, block}`; `outbound.py:263-268` бинарный `{allowed, categories}`. Ни один не связан с осью состояний.

**F16. `ERROR` не существует — `MISSING` / `DEGRADED`.** Нигде. Технический отказ либо роняет запрос (резолвер: `views.py:78-92` без `try`), либо пропускает (F14).

**F17. `POLICY_CONFLICT` не существует — `MISSING` / `POST_PILOT`.** Ни enum'а, ни метрики, ни инцидента. Не блокирует пилот только потому, что политики, способной конфликтовать, тоже нет.

**F20. HTTP-ручка резолвера мертва — `DEAD_CODE` / `DEGRADED`.**
`ai-bot-platform/apps/integrations/ayla/recommendation_resolver_client.py:86` `resolve_recommendation` — production-caller'а нет (проверено грепом по всему `apps`, кроме тестов и собственных докстрингов). Значит единственный путь, по которому в резолвер могло бы приехать реально вычисленное `NORMAL` или `CAUTION`, не используется. Живой путь — внутрипроцессный `_resolve_layer` с константой.

**F21. `apps/orchestrator/pipeline.py` (полный pipeline с safety-ветвлением) в проде не используется** — заявлено докстрингом `gate.py:3-8` и подтверждено тем, что живые обработчики зовут `evaluate_inbound`, а не `pipeline.turn`. `DEAD_CODE` / `POST_PILOT`.

**F22. `STALE_SPEC`: `recommendation/_stages.py:92` утверждает «из схемы запроса убрано поле безопасности (§72)», фактически поле обязательно** (`_serializers.py:104`). Комментарий, обещающий отсутствие, при существующем поле — тот же класс, что комментарий, обещающий код.

**F26. Ни одной из десяти метрик наблюдаемости канона §11 не существует** (`safety_evaluations_total`, `safety_state_total{state}`, `safety_rule_activated_total`, …). Есть три события бота: `channels.max.safety.pre_check_triggered`, `safety.outbound_blocked` (`gate.py:212-224`), `safety_triggered`. `MISSING` / `DEGRADED`.

**F27. Аудитного пути, реконструирующего `evidence → rule → result → decision`, не существует.** Хранится вердикт как данные (`Message.action_type="safety_pre_check"`, replay-поле `safety_decision`), происхождение — только `matched_patterns` (сырые regex-строки). `MISSING` / `POST_PILOT`.

---

## 7. Пункт 32 — полная перепись флагов обоих рантаймов

Правило свода соблюдено: **умолчание — не живое значение**. Ниже — умолчания из кода; живые значения снимает заказчик командами из §10.

### 7.1 Ответы на прямые вопросы владельца

**Существуют ли рубильники `RECOMMENDATIONS`, `PLAN_LITE`, `NUTRITION_PROACTIVE`, `PROACTIVE_HINTS`?**

| требуемый рубильник | найдено | класс |
|---|---|---|
| `RECOMMENDATIONS` | **НЕТ.** Ни в одном репозитории нет флага, выключающего рекомендации. Ближайший рычаг — `RECOMMENDATION_CANDIDATE_SOURCE`, но это **литерал** в `djangoProject/settings/base.py:81`, не `os.environ.get` | `MISSING` / `STOP` |
| `PLAN_LITE` | **НЕТ.** `git grep -i "plan_lite\|plan-lite\|planlite"` по обоим репозиториям → 0. Ближайшее по смыслу — `wellness/services.py:70-103`, и там не флаг, а **захардкоженный fail-closed** (`GateDecision(allowed=False, reason_code="scope_not_approved")`) | `MISSING` / `DEGRADED` |
| `NUTRITION_PROACTIVE` | **ЕСТЬ**, полноценно: `NUTRITION_PROACTIVE_ENABLED` + `NUTRITION_PROACTIVE_DRY_RUN` | `EXISTS` |
| `PROACTIVE_HINTS` | **НЕТ как системного рубильника.** `proactive_hints` — это **согласие конкретного человека** (`apps/consent/customer.py:254`, ручка `POST /api/v1/customer/me/consents/proactive-hints/`), а не рычаг оператора. Выключить фичу для всех одной переменной нельзя | `MISSING` / `DEGRADED` |
| «прочие необязательные AI-поверхности» | частично: `CONCIERGE_MEMORY_ENABLED`, `CONCIERGE_NUTRITION_CONTEXT_ENABLED`, `AI_DRAFTS_AUTO_TRIGGER_ENABLED`, `ORCHESTRATOR_SHADOW_ENABLED`, `INTENT_RESOLUTION_FROM_TOOL_CHOICE_ENABLED`. **Не покрыты рубильником:** каталожный AI-чат `/api/v1/ai/**` (флага нет вовсе), выдача MAX-бота `apps/marketplace/discovery.py`, tools консьержа `show_masters` / `start_booking` / `ask_clarification` | `PARTIAL` / `DEGRADED` |

**Может ли какой-нибудь флаг превратить «safety выключен» в «небезопасный legacy-фолбэк»? — ДА, один. F19, класс `MISSING`+`CONTRADICTS_CANON` / `STOP`.**

`BOOKING_VIA_AYLA_REST` (`ai-bot-platform/config/settings/base.py:771`, умолчание **`false`**) читается в `apps/skills/booking/skill.py:1477` и разводит health-гейт на две ветки с **противоположным направлением отказа**:

```
ON  (:1477-1496)   ребро не найдено / признак не синхронизирован → return True  (fail-CLOSED, handoff)
OFF (:1504-1513)   строки нет в зеркале                          → return False (fail-OPEN, бронируем)
```

Докстринг этого не скрывает (`:1443-1444`): «If we can't find the service row (e.g. catalog isn't synced yet) we DEFAULT to `False` — better UX to attempt the booking than to dead-end every flow».

Дополнительно: на legacy-ветке ключ ищется как `external_id=int(service_id)` (`:1507`), тогда как на пилоте идентификаторы услуг — UUID. То есть переключение флага на пилотных данных даёт не «мягкую деградацию», а неопределённое поведение на первой же брони.

Умолчание флага — `false`, то есть **небезопасная ветка является умолчанием кода**. Живое значение на контуре не замерено (см. §9).

Проверено, что **не** является таким флагом (важно для полноты ответа):
- `SAFETY_PATTERNS` — `pre_check.py:169-183`: `merged.setdefault(verdict, []).extend(patterns)`. Настройка умеет только **добавить** паттерн; списка удаления нет, невалидное значение молча пропускается (`:180-181`). Ослабить детектор настройкой нельзя. `EXISTS` (F23, положительное).
- `NUTRITION_*` — скрининг ими **не** гейтится: `CONCIERGE_TOOL_SPECS` (`concierge.py:679-687`) включает `NUTRITION_TOOL_SPECS` безусловно, а `execute_nutrition_tool` для `health_screening` (`nutrition_global.py:292-330`) флаги не читает. Выключение всего питания скрининг не выключает. `EXISTS` (F24, положительное).
- `RECOMMENDATION_CANDIDATE_SOURCE` — при снятии даёт **503 `SERVICE_UNAVAILABLE`** с явным различением «недоступность ≠ пустая выдача» (`recommendation/_source_binding.py:40-45`, `recommendation/views.py:65-76`). Фолбэка на legacy нет. `EXISTS` (F28, положительное).
- Прокси Mini App при отказе резолвера отдаёт 502/503 и **не** падает на legacy-ранжировщик (`apps/miniapp_api/views.py:2584-2589`).

**Если выключить `RECOMMENDATIONS` — останется ли прямой просмотр и прямая бронь? — ДА, останется. Но выключить нечем.**
`resolve()` вызывается ровно из двух мест: `recommendation/views.py:78` (HTTP-ручка, production-caller'а нет — F20) и `users/catalog_recommendations_api.py:229` через `_resolve_layer` (полки 1–3 Mini App). Ни `appointments`, ни каталожные листинги, ни `users/home_api.py` от него не зависят. Снятие `RECOMMENDATION_CANDIDATE_SOURCE` погасит полки Mini App (503) и не затронет ни просмотр, ни бронь. **Но это правка `settings/base.py` и деплой, а не переменная окружения** — рубильника в требуемом владельцем смысле («контролируемый путь отключения») не существует.

**Отдельно, чего выключение рекомендаций НЕ погасит:** выдачу MAX-бота (`apps/marketplace/discovery.py` — собственный порядок, резолвер не зовёт) и каталожный AI-чат (`ai/recommendation_engine.py` — собственный ранжировщик). Два из трёх рекомендательных путей переживут выключение третьего.

### 7.2 Перепись — `ai-bot-platform`, `config/settings/base.py`

| флаг | строка | умолчание | кто читает | что при выключении | опасный фолбэк |
|---|---|---|---|---|---|
| `BOOKING_VIA_AYLA_REST` | 771 | `false` | `skills/booking/skill.py:1477`, `admin_api/services/availability.py:127`, `eventbus/consumers/booking.py:673`, `eventbus/signals.py:150` | **health-гейт переходит на fail-OPEN ветку (F19)**; бронь идёт через YClients | **ДА** |
| `NUTRITION_ENABLED` | 883 | `false` | `skills/food_scanner/skill.py:433`, `skills/menu/marketplace.py:898`, `adminconsole/health.py:107` | пункт меню исчезает, скилл отвечает «feature off» | нет |
| `FOOD_PHOTO_SCAN_ENABLED` | 884 | `false` | `skills/food_scanner/skill.py:444` | фото не уходит в распознавание | нет |
| `NUTRITION_PROACTIVE_ENABLED` | 1514 | `false` | `nutrition_proactive/tasks.py:93` | обе beat-задачи no-op | нет |
| `NUTRITION_PROACTIVE_DRY_RUN` | 1518 | `true` | `nutrition_proactive/tasks.py` | при `true` только лог, отправки нет | — |
| `NUTRITION_COACH_ENABLED` | 1556 | `false` | `nutrition_coach/flags.py:17` | все коуч-поверхности молчат | нет |
| `NUTRITION_COACH_DRY_RUN` | 1560 | `true` | `nutrition_coach` | недельный push только логируется | — |
| `WELLNESS_PROACTIVE_ENABLED` | 1573 | `false` | `wellness_proactive/tasks.py:129` | задача выходит, не трогая БД. DRY_RUN-двойника **нет намеренно** | нет |
| `POST_VISIT_FOLLOWUP_ENABLED` | 1594 | `false` | `bookings/followups.py:177` | пост-визитные follow-up не шлются | нет |
| `POST_VISIT_FOLLOWUP_DRY_RUN` | 1598 | `true` | `bookings/followups.py` | лог вместо отправки | — |
| `CONCIERGE_MEMORY_ENABLED` | 1878 | **`true`** | `orchestrator/memory_block.py:107` | консьерж теряет блок памяти | нет |
| `FOOD_SCANNER_MEMORY_ENABLED` | 1894 | **`true`** | `orchestrator/memory/food.py:229` | память сканера не читается | нет |
| `CONCIERGE_NUTRITION_CONTEXT_ENABLED` | 1927 | `false` | `orchestrator/nutrition_context.py:154` | консьерж не видит картину питания | нет |
| `CERTIFICATE_PAYMENT_ENABLED` | 920 | `false` | `skills/booking/tools.py:417,3194`, `prompts.py:152` | сертификаты не предлагаются | нет |
| `AI_DRAFTS_AUTO_TRIGGER_ENABLED` | 668 | `false` | `master_api/tasks.py:226` | черновики ответов мастеру не генерируются | нет |
| `ORCHESTRATOR_SHADOW_ENABLED` | 2331 | `false` | `orchestrator/shadow_turn.py:81` | теневой прогон выключен (side-effect-free) | нет |
| `ORCHESTRATOR_SHADOW_SAMPLE_RATE` | 2344 | `0.0` | там же | — | — |
| `INTENT_RESOLUTION_FROM_TOOL_CHOICE_ENABLED` | 2359 | `false` | `orchestrator/intent_resolution.py:259` | намерение не выводится из tool-choice | нет |
| **`INTENT_RESOLUTION_LIVE_ENABLED`** | **не объявлен** | `getattr(..., True)` — `intent_resolution.py:250` | там же | докстринг `:34` обещает «Rollback without redeploy: `INTENT_RESOLUTION_LIVE_ENABLED=0`» — **обещание ложное**: переменная окружения нигде не читается, флаг существует только как `override_settings` в тесте `test_intent_resolution.py:401` | **F25, `PARTIAL`: рубильник заявлен и не подключён** |
| `SKILL_CONFIDENCE_FLOOR_LIVE_ENABLED` | 414 | `false` | `channels/max/handler.py:700` | порог уверенности скилла не применяется | нет |
| `LIVE_PATH_AI_METRIC_ENABLED` | 426 | `false` | `channels/max/handler.py:501` | метрика не пишется | нет |
| `REPLAY_LIVE_CAPTURE_ENABLED` | 444 | `false` | `channels/max/handler.py:576` | трейсы не снимаются | нет |
| `PII_TOKENIZER_ENABLED` | 483 | **`1` (on)** | `llm/pii_protected_provider.py:196,290` | **PII уходит в модель нетокенизированным** | приватностный рубильник в опасную сторону |
| `LLM_QUOTA_FALLBACK_ENABLED` | 1672 | **`1` (on)** | `llm/router.py:428` | нет одношагового фолбэка провайдера | — |
| `LLM_WARMUP_ENABLED` | 1736 | **`1`** | `llm/warmup.py:213` | без прогрева | нет |
| `LLM_HEALTH_PROBE_ENABLED` | 1781 | **`1`** | `llm/health.py:544` | зонд здоровья модели выключен | нет |
| `PEL_REAPER_ENABLED` | 313 | `false` | `workers/reaper.py:480` | reaper не чистит | нет |
| `GLOBAL_BOT_ONBOARDING` | 1870 | `false` | `channels/max/global_onboarding.py` | онбординг глобального бота выключен | нет |
| `PILOT_CONVERSATIONAL_UX` | 1941 | **`true`** | UX-ветвление каналов | возврат к прежнему UX | — |
| `STRICT_TENANT_SCOPE` | 256 | `"audit"` (на пилоте `"strict"`) | тенантные менеджеры | `Model.objects` вне контекста перестаёт бросать | изоляционный рубильник в опасную сторону |
| `STRICT_TENANT_REFUSE` | 283 | `false` | там же | — | — |
| `EVENT_INGEST_TENANT_VERIFY_FAIL_OPEN` | 2146 | см. код | `eventbus` | **имя говорит само за себя** — содержимое ветки не читал, вынесено в §9 | требует отдельного замера |
| `DISCOVERY_CLARIFY_MIN_TIER` | 793 | `4` (int) | discovery/clarify | не рубильник, порог | — |
| `SAFETY_PATTERNS` | не объявлен | `getattr(..., None)` — `pre_check.py:177` | `pre_check` | только расширяет, ослабить нельзя (F23) | — |

**Снятая владельцем шестёрка питания** (`NUTRITION_ENABLED=True`, `NUTRITION_COACH_ENABLED=True`, `NUTRITION_COACH_DRY_RUN=True`, `FOOD_PHOTO_SCAN_ENABLED=False`, `NUTRITION_PROACTIVE_ENABLED=False`, `WELLNESS_PROACTIVE_ENABLED=False`) с кодом **не противоречит**: все шесть объявлены, все шесть читаются перечисленными выше потребителями, умолчания кода — как указано. Разграничение: **эти шесть замерены живьём, остальные — нет.**

### 7.3 Перепись — `djangoproject-catalog`, `djangoProject/settings/base.py`

| флаг | строка | умолчание | кто читает | что при выключении | опасный фолбэк |
|---|---|---|---|---|---|
| `GOAL_RESOLUTION_ENABLED` | 506 | `false` | **ровно одно место** — `goals/wiring.py:68` | фильтрация пассивной выдачи по цели не применяется; резолвер целей, API и decision-context работают всегда | нет |
| `GOAL_ANKETA_ENABLED` | 523 | **`true`** | `goals`, Mini App | OFF возвращает прежний документ DRF-1190; рубильник отката, не гейт раскатки | нет |
| `BOOKING_AUTO_COMPLETE_ENABLED` | 463 | `false` | `appointments` beat | визиты не автозавершаются | нет |
| `BOOKING_AUTO_COMPLETE_NOT_BEFORE` | 475 | `""` | там же | при пустом задача отказывается работать (fail-closed) | — |
| `SMS_ENABLED` | 529 | `false` | `users` OTP | SMS не шлются | нет |
| `CROSS_DOMAIN_ENABLED` | 855 | `"0"` | `nutrition/services/cross_domain_engine.py` | кросс-доменные правила не применяются; в самом движке fail-closed (`:104`) | нет |
| `EXTERNAL_BUSY_ENABLED` | 862 | `false` | `appointments/infrastructure/availability` | внешняя занятость не учитывается | нет |
| `MULTI_TENANT_STRICT` | 328 | `false` | middleware тенантов | ослабление изоляции | опасная сторона |
| `CELERY_TASK_ALWAYS_EAGER` | ~921 | env | Celery | синхронное выполнение | — |
| `AI_ANON_MESSAGE_CAP` | 585 | `5` | `ai` | лимит анонимных сообщений AI-чата | — |
| `AI_MAX_TOKENS_PER_USER_PER_DAY` | 582 | env | `ai` | бюджет токенов | — |
| `AI_SPECIALIST_MIN_RATING` | 590 | env | `ai/recommendation_engine.py:283` | порог рейтинга в **legacy-ранжировщике** | — |
| `AI_REC_CACHE_TTL` | 598 | `300` | `ai/recommendation_engine.py` | TTL кеша рекомендаций legacy-движка | — |
| `RECOMMENDATION_CANDIDATE_SOURCE` | 81 | **литерал, не env** | `recommendation/_source_binding.py:40` | 503 `SERVICE_UNAVAILABLE` (F28) | нет, честный отказ |
| `FOOD_SCANNER_PRIMARY` / `_FALLBACK` | 735-736 | `openai` / `yandex` | `nutrition` | выбор провайдера | штатный |
| `CAPTURE_SAFETY_BUFFER_MINUTES` | 628 | `60` | `payments` | к safety отношения не имеет (буфер платежей) | — |

**Флага, выключающего `/api/v1/ai/**` (LLM-чат + бронь + legacy-ранжировщик), НЕ СУЩЕСТВУЕТ.** F18, `MISSING` / `STOP`. Это единственная AI-поверхность контура, у которой нет ни рубильника, ни safety.

`DISCOVERY_CLARIFY_MIN_TIER` принадлежит `ai-bot-platform` (`base.py:793`, умолчание `4`); в каталоге его нет. Живое значение не замерено.

---

## 8. Реальность тестов

### Что покрыто и каким уровнем

| предмет | тесты | уровень |
|---|---|---|
| regex-детектор inbound | `apps/orchestrator/safety/tests/test_pre_check.py` — 24 теста: суицид RU/EN, острая медицина, насилие, лекарства, диагноз, юр. совет, приоритет вердиктов, elevation по risk_level, brand_voice, бюджет 100 вызовов < 50 мс | `UNIT_ONLY` |
| канальный гейт | `test_gate.py` — 11 тестов, включая гиперболу «умираю как хочу» и явный `test_clarify_proceeds_not_short_circuited` | `UNIT_ONLY` |
| outbound-стражи | `test_outbound.py` (21), `test_post_check.py` (20), `test_outbound_guard_budget.py` | `UNIT_ONLY` |
| паритет каналов | `apps/channels/tests/test_handler_safety_parity.py:399-425` и `:458+` — **структурные AST-сторожа по дереву**: каждый модуль, зовущий `orchestrate_turn`, обязан звать `evaluate_inbound` и `guard_outbound`. С защитой от вырожденного прохода (`assert len(brain_callers) >= 2`) | `CONTRACT_ONLY` |
| `NOT_APPLICABLE` / `UNKNOWN` в резолвере | `recommendation/tests/test_safety_not_applicable.py` — 6 поведенческих + AST-сторож на форму `x or NOT_APPLICABLE` и `default=NOT_APPLICABLE` + **сторож сторожа** (`:206-219`) | `CONTRACT_ONLY` |
| health-гейт брони бота | `apps/skills/booking/tests/test_skill.py:1063,1993,2312-2380` — включая `None → закрыто` и legacy-ветку | `UNIT_ONLY` |
| скрининг | `health_screening/tests/test_classifier.py`, `test_skill.py`, `test_screening_memo_1542.py` | `UNIT_ONLY` |

### Каких тестов НЕТ — поимённо

Счётчик покрытия состояний в резолвере, снятый грепом (`git grep -c "SafetyState.<S>" origin/dev -- recommendation/tests users/tests`):

```
NORMAL: 1 (только conftest.py)   CLARIFY: 0   CAUTION: 0
STOP: 2                          UNKNOWN: 2   NOT_APPLICABLE: 9
```

1. **`recommendation/tests/test_safety_states.py::test_caution_does_not_grant_elig_safety_cleared`** — нет файла, нет теста. `CAUTION` не упомянут ни в одном тесте каталога. Изменение его поведения на противоположное **не покраснит ничего**.
2. **`recommendation/tests/test_safety_states.py::test_clarify_does_not_admit_silently`** — то же для `CLARIFY`: 0 упоминаний.
3. **`recommendation/tests/test_safety_states.py::test_caution_produces_capability_restrictions`** — канон M6 требует, чтобы каждое CAUTION-правило возвращало явные restrictions. Ни поля, ни теста.
4. **`users/tests/test_recommendation_source_safety_facts.py::test_prior_completed_visit_is_not_hardcoded_false`** — сторожа на то, что три входа `_is_safety_sensitive` не литералы, не существует. Именно поэтому F5 прожил незамеченным: гейт покрыт шестью тестами, а его питание — нулём.
5. **`services/tests/test_resolved_health_check.py::test_unmapped_template_is_not_reported_as_no_screening`** — теста, краснеющего на `template=None → False`, нет.
6. **`services/tests/test_serializers_health_check.py::test_boundary_preserves_unknown`** — теста на то, что граница не схлопывает третье состояние, нет.
7. **`appointments/tests/test_booking_health_gate.py::test_direct_booking_refuses_service_requiring_health_check`** — **самый дорогой отсутствующий тест.** В `appointments` слово `requires_health_check` не встречается ни разу, включая тесты. Прямая бронь никогда не проверялась на этот счёт.
8. **`ai/tests/test_action_service_safety.py::test_confirm_booking_refuses_health_check_service`** — то же для каталожного AI-чата.
9. **`ai/tests/test_chat_safety_gate.py::test_inbound_crisis_phrase_does_not_reach_the_llm`** — каталожный чат вообще не имеет safety-тестов.
10. **`apps/miniapp_api/tests/test_create_booking_health_gate.py::test_miniapp_booking_consults_the_health_gate`** — теста, краснеющего на то, что Mini App бронирует мимо гейта, нет.
11. **Кросс-граничного теста «признак `requires_health_check`, проставленный в каталоге, доезжает до отказа в брони» не существует ни в одном репозитории.** Обе стороны покрыты своими фикстурами, стык — ничем. Тот самый класс, про который свод говорит: тест, где обе стороны данных построил один автор, проверяет согласованность фикстуры, а не системы.

### Golden Suite канона §10 — 40 обязательных случаев

Из сорока в рантайме воспроизводимы **шесть** (№10 UNKNOWN, №11 NOT_APPLICABLE, №38 NOT_APPLICABLE не выдаёт `ELIG_SAFETY_CLEARED`, №39 NORMAL выдаёт, №40 sensitive-кандидат отменяет N/A, частично №9 STOP). Остальные тридцать четыре описывают сущности (`CAUTION`, `RuleResult`, `capability_decisions`, `EVIDENCE_CONFLICT`, `POLICY_CONFLICT`, `question_id`, reask-reasons, `REVISE`-регенерация, unknown contract version), которых в коде нет. **Релизный гейт канона — «Golden Safety Suite != FULL PASS → NO safety-sensitive Controlled Pilot» — не проходится: набор нельзя даже написать.**

### Прогон тестов

Не запускал: режим read-only. Все утверждения выше — о **наличии и содержании** тестов, снятые `git show` / `git grep` по ref, а не о результате прогона. Ни одно число «N passed» в этом отчёте не приводится, потому что ни одного прогона не было.

---

## 9. Что НЕ замерено — честный список

1. **Живые значения флагов на контуре.** Замерены владельцем только шесть питания. Остальные — умолчания кода. Команды в §10.
2. **`BOOKING_VIA_AYLA_REST` на боевом контуре** — от него зависит, закрывается ли пятая поверхность. **Самое важное неснятое число отчёта.**
3. **58 из 58 услуг с `resolved_requires_health_check=false`** — унаследовано от главного окна (08–09.09), в этом замере не перепроверялось: факт БД, не кода.
4. **Живость `POST /api/v1/ai/chat/` и `/action/` на `api-dev.gobeauty.site`** — смонтированы в коде безусловно; отвечают ли на контуре, не проверял.
5. **Распределение живого трафика** между `apps/marketplace/discovery.py` и полками Mini App не снималось.
6. **`ayla-ai-core`** — предметом не был; по прежним замерам safety там отсутствует полностью, самостоятельно не перепроверял.
7. **`ayla-knowledge`** — наличие/отсутствие машиночитаемого артефакта safety-политики не инвентаризовал; вывод «политики нет» сделан со стороны рантайма (никто её не читает), а не со стороны хранилища.
8. **Результат прогона тестов** — прогонов не было.
9. **`EVENT_INGEST_TENANT_VERIFY_FAIL_OPEN`** — имя обещает fail-open, содержимое ветки не читал.
10. **Дрейф env-файла и живого процесса.** `config/env_file_drift.py` существует именно потому, что `.env.staging` и контейнер расходились молча (`DJANGO_ALLOWED_HOSTS`, `CHROMA_HTTP_HOST`, `ORCHESTRATOR_SHADOW_ENABLED`). Поэтому команды в §10 читают **`settings`, а не env-файл**: чтение файла не является замером контура.

---

## 10. Прошу снять на контуре — точные команды, по одной на строку

Общая оговорка: **читать `settings`, а не `.env`** — из-за дрейфа, описанного в `config/env_file_drift.py`.

### 10.1 Флаги `ai-bot-platform` (контейнер `web`)

```
docker compose exec -T web python manage.py shell -c "from django.conf import settings; print('BOOKING_VIA_AYLA_REST=', settings.BOOKING_VIA_AYLA_REST)"
docker compose exec -T web python manage.py shell -c "from django.conf import settings; print('DISCOVERY_CLARIFY_MIN_TIER=', settings.DISCOVERY_CLARIFY_MIN_TIER)"
docker compose exec -T web python manage.py shell -c "from django.conf import settings; print('PII_TOKENIZER_ENABLED=', settings.PII_TOKENIZER_ENABLED)"
docker compose exec -T web python manage.py shell -c "from django.conf import settings; print('STRICT_TENANT_SCOPE=', settings.STRICT_TENANT_SCOPE)"
docker compose exec -T web python manage.py shell -c "from django.conf import settings; print('STRICT_TENANT_REFUSE=', settings.STRICT_TENANT_REFUSE)"
docker compose exec -T web python manage.py shell -c "from django.conf import settings; print('EVENT_INGEST_TENANT_VERIFY_FAIL_OPEN=', settings.EVENT_INGEST_TENANT_VERIFY_FAIL_OPEN)"
docker compose exec -T web python manage.py shell -c "from django.conf import settings; print('CONCIERGE_MEMORY_ENABLED=', settings.CONCIERGE_MEMORY_ENABLED)"
docker compose exec -T web python manage.py shell -c "from django.conf import settings; print('CONCIERGE_NUTRITION_CONTEXT_ENABLED=', settings.CONCIERGE_NUTRITION_CONTEXT_ENABLED)"
docker compose exec -T web python manage.py shell -c "from django.conf import settings; print('FOOD_SCANNER_MEMORY_ENABLED=', settings.FOOD_SCANNER_MEMORY_ENABLED)"
docker compose exec -T web python manage.py shell -c "from django.conf import settings; print('CERTIFICATE_PAYMENT_ENABLED=', settings.CERTIFICATE_PAYMENT_ENABLED)"
docker compose exec -T web python manage.py shell -c "from django.conf import settings; print('AI_DRAFTS_AUTO_TRIGGER_ENABLED=', settings.AI_DRAFTS_AUTO_TRIGGER_ENABLED)"
docker compose exec -T web python manage.py shell -c "from django.conf import settings; print('SKILL_CONFIDENCE_FLOOR_LIVE_ENABLED=', settings.SKILL_CONFIDENCE_FLOOR_LIVE_ENABLED)"
docker compose exec -T web python manage.py shell -c "from django.conf import settings; print('LIVE_PATH_AI_METRIC_ENABLED=', settings.LIVE_PATH_AI_METRIC_ENABLED)"
docker compose exec -T web python manage.py shell -c "from django.conf import settings; print('REPLAY_LIVE_CAPTURE_ENABLED=', settings.REPLAY_LIVE_CAPTURE_ENABLED)"
docker compose exec -T web python manage.py shell -c "from django.conf import settings; print('ORCHESTRATOR_SHADOW_ENABLED=', settings.ORCHESTRATOR_SHADOW_ENABLED, 'rate=', settings.ORCHESTRATOR_SHADOW_SAMPLE_RATE)"
docker compose exec -T web python manage.py shell -c "from django.conf import settings; print('INTENT_RESOLUTION_FROM_TOOL_CHOICE_ENABLED=', settings.INTENT_RESOLUTION_FROM_TOOL_CHOICE_ENABLED)"
docker compose exec -T web python manage.py shell -c "from django.conf import settings; print('INTENT_RESOLUTION_LIVE_ENABLED=', getattr(settings, 'INTENT_RESOLUTION_LIVE_ENABLED', 'NOT_SET_default_True'))"
docker compose exec -T web python manage.py shell -c "from django.conf import settings; print('SAFETY_PATTERNS=', getattr(settings, 'SAFETY_PATTERNS', 'NOT_SET'))"
docker compose exec -T web python manage.py shell -c "from django.conf import settings; print('POST_VISIT_FOLLOWUP_ENABLED=', settings.POST_VISIT_FOLLOWUP_ENABLED, 'DRY_RUN=', settings.POST_VISIT_FOLLOWUP_DRY_RUN)"
docker compose exec -T web python manage.py shell -c "from django.conf import settings; print('PILOT_CONVERSATIONAL_UX=', settings.PILOT_CONVERSATIONAL_UX, 'GLOBAL_BOT_ONBOARDING=', settings.GLOBAL_BOT_ONBOARDING)"
docker compose exec -T web python manage.py shell -c "from django.conf import settings; print('PEL_REAPER_ENABLED=', settings.PEL_REAPER_ENABLED)"
docker compose exec -T web python manage.py shell -c "from django.conf import settings; print('LLM_QUOTA_FALLBACK_ENABLED=', settings.LLM_QUOTA_FALLBACK_ENABLED, 'WARMUP=', settings.LLM_WARMUP_ENABLED, 'HEALTH_PROBE=', settings.LLM_HEALTH_PROBE_ENABLED)"
```

Те же 22 строки повторить с `exec -T worker` — воркер собирает settings отдельно, и именно там расходились значения в DRF-1391.

### 10.2 Флаги `djangoproject-catalog` (контейнер `web`)

```
docker compose exec -T web python manage.py shell -c "from django.conf import settings; print('GOAL_RESOLUTION_ENABLED=', settings.GOAL_RESOLUTION_ENABLED)"
docker compose exec -T web python manage.py shell -c "from django.conf import settings; print('GOAL_ANKETA_ENABLED=', settings.GOAL_ANKETA_ENABLED)"
docker compose exec -T web python manage.py shell -c "from django.conf import settings; print('RECOMMENDATION_CANDIDATE_SOURCE=', settings.RECOMMENDATION_CANDIDATE_SOURCE)"
docker compose exec -T web python manage.py shell -c "from django.conf import settings; print('BOOKING_AUTO_COMPLETE_ENABLED=', settings.BOOKING_AUTO_COMPLETE_ENABLED, 'NOT_BEFORE=', settings.BOOKING_AUTO_COMPLETE_NOT_BEFORE)"
docker compose exec -T web python manage.py shell -c "from django.conf import settings; print('CROSS_DOMAIN_ENABLED=', settings.CROSS_DOMAIN_ENABLED)"
docker compose exec -T web python manage.py shell -c "from django.conf import settings; print('EXTERNAL_BUSY_ENABLED=', settings.EXTERNAL_BUSY_ENABLED)"
docker compose exec -T web python manage.py shell -c "from django.conf import settings; print('MULTI_TENANT_STRICT=', settings.MULTI_TENANT_STRICT, 'DEFAULT_SLUG=', settings.MULTI_TENANT_DEFAULT_SLUG)"
docker compose exec -T web python manage.py shell -c "from django.conf import settings; print('SMS_ENABLED=', settings.SMS_ENABLED)"
docker compose exec -T web python manage.py shell -c "from django.conf import settings; print('AI_SPECIALIST_MIN_RATING=', settings.AI_SPECIALIST_MIN_RATING, 'AI_REC_CACHE_TTL=', settings.AI_REC_CACHE_TTL, 'AI_ANON_MESSAGE_CAP=', settings.AI_ANON_MESSAGE_CAP)"
```

### 10.3 Факты, а не флаги — то, что решает вопрос «STOP или нет»

```
docker compose exec -T web python manage.py shell -c "from services.models import SpecialistService; rows=list(SpecialistService.objects.filter(is_active=True).select_related('salon_service','salon_service__template')); print('active_edges=',len(rows),'resolved_true=',sum(1 for r in rows if r.resolved_requires_health_check()),'template_is_none=',sum(1 for r in rows if r.salon_service.template_id is None))"
docker compose exec -T web python manage.py shell -c "from services.models import SalonService; from django.db.models import Count; print(list(SalonService.objects.filter(is_active=True).values('mapping_status').annotate(n=Count('id')).order_by()))"
docker compose exec -T web python manage.py shell -c "from services.models import ServiceTemplate; print('templates_total=',ServiceTemplate.objects.count(),'with_health_check=',ServiceTemplate.objects.filter(requires_health_check=True).count())"
curl -s -o /dev/null -w "%{http_code}\n" -X POST https://api-dev.gobeauty.site/api/v1/ai/chat/
curl -s -o /dev/null -w "%{http_code}\n" -X POST https://api-dev.gobeauty.site/api/v1/ai/chat/action/
```

Первая строка отвечает на «58 из 58» и на «включается ли `_is_safety_sensitive` хоть на одном ребре».
Последние две отвечают, живёт ли пятая, неучтённая поверхность (401/403 = живёт и требует авторизации; 404 = не смонтирована на контуре).

---

## 11. Точные команды воспроизведения этого замера

```
git -C ai-bot-platform rev-parse origin/dev
git -C djangoproject-catalog rev-parse origin/dev
git -C ai-bot-platform grep -n "CAUTION" origin/dev -- apps
git -C ai-bot-platform grep -n "safety_state\|SafetyState" origin/dev -- apps
git -C ai-bot-platform grep -n "POLICY_CONFLICT\|rule_id\|policy_version\|evidence_ref" origin/dev -- apps
git -C ai-bot-platform grep -n "requires_health_check" origin/dev -- apps
git -C ai-bot-platform show origin/dev:apps/orchestrator/safety/pre_check.py
git -C ai-bot-platform show origin/dev:apps/orchestrator/safety/gate.py
git -C ai-bot-platform show origin/dev:apps/skills/booking/skill.py | sed -n "1436,1515p"
git -C ai-bot-platform show origin/dev:apps/orchestrator/safety/outbound.py | sed -n "283,300p"
git -C ai-bot-platform show origin/dev:apps/miniapp_api/views.py | sed -n "900,1000p"
git -C ai-bot-platform grep -n "guard_outbound(\|evaluate_inbound(" origin/dev -- apps
git -C ai-bot-platform grep -nE "safety|health_check" origin/dev -- apps/marketplace
git -C ai-bot-platform grep -n "resolve_recommendation" origin/dev -- apps
git -C ai-bot-platform show origin/dev:config/settings/base.py | grep -nE "^[A-Z][A-Z0-9_]+ *= *\(?os\.environ"
git -C djangoproject-catalog show origin/dev:recommendation/_types.py | sed -n "61,85p"
git -C djangoproject-catalog show origin/dev:recommendation/_stages.py | sed -n "213,345p"
git -C djangoproject-catalog show origin/dev:services/models.py | sed -n "574,584p"
git -C djangoproject-catalog show origin/dev:users/recommendation_source.py | grep -n "safety_blocked\|requires_health_check\|prior_completed"
git -C djangoproject-catalog show origin/dev:users/catalog_recommendations_api.py | sed -n "467,530p"
git -C djangoproject-catalog grep -n "requires_health_check" origin/dev -- appointments
git -C djangoproject-catalog grep -ni "safety" origin/dev -- ai
git -C djangoproject-catalog show origin/dev:ai/application/services/action_service.py | sed -n "105,200p"
git -C djangoproject-catalog show origin/dev:ai/application/services/recommendation_engine.py | sed -n "1,70p"
git -C djangoproject-catalog grep -c "SafetyState.CAUTION" origin/dev -- recommendation/tests
git -C djangoproject-catalog grep -c "SafetyState.CLARIFY" origin/dev -- recommendation/tests
git -C djangoproject-catalog show origin/dev:djangoProject/settings/base.py | grep -nE "^[A-Z][A-Z0-9_]+ *= *"
```

---

## 12. Убрано за собой

**Ничего не создавал.** Ни worktree, ни веток, ни контейнеров, ни временных файлов вне сессионного `scratchpad` (там осталась одна сохранённая выдача `cat` большого файла; она сессионная и уходит вместе с сессией). Ни один файл ни в одном репозитории не изменён, ничего не закоммичено, Linear не тронут. Единственный созданный артефакт — этот отчёт.
