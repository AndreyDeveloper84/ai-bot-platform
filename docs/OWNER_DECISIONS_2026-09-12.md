# Ayla --- Owner Decisions Package

## Сводный пакет решений владельца

**Дата фиксации:** 12.09.2026\
**Статус:** OWNER RULINGS CLOSED\
**Назначение:** единый handoff для оркестратора, агентов и команды
разработки.\
**Принцип:** решения ниже фиксируют owner direction. Если реализация
обнаруживает реальное противоречие с каноном, безопасностью или
фактическим runtime-контрактом, затрагиваемая ветка останавливается и
выносится на отдельное решение; молча переинтерпретировать rulings
нельзя.

------------------------------------------------------------------------

## 1. DRF-979 / #353 --- GitHub credential на pilot

### Проблема

На pilot `GH_DEPLOY_TOKEN` записывается в plaintext в `.git/config`
через `git remote set-url`. Тот же секрет присутствует в host `.env`,
попадал в Docker build через `ARG` и потенциально существует на
twin-host `ruvds-l2wyz`. `.git/config` доступен группе.

PR/#353 убирает `set-url` и добавляет guard: если credential обнаружен в
`origin`, deploy должен отказаться продолжать работу. Отдельно
существует draft исправления Dockerfile без `ARG`.

### Решение владельца

**GO: repository-scoped read-only GitHub Deploy Key.**

PAT не должен оставаться постоянным deploy credential.

### Порядок миграции

1.  Создать read-only Deploy Key именно для репозитория
    `beautygo_backend`.
2.  Установить private key в `~taximeter/.ssh/` на pilot с корректными
    permissions.
3.  При наличии нескольких SSH-ключей использовать явный SSH host
    alias/config.
4.  Перевести `origin` на SSH URL без credential.
5.  Выполнить реальный `git fetch origin` от пользователя `taximeter`.
6.  Только после успешного fetch считать, что «ключ стоит».
7.  Merge #353 и Dockerfile fix.
8.  Выполнить первый green deploy через новый SSH path.
9.  После подтверждённого green deploy отозвать PAT в GitHub.
10. Удалить repo secret и `GH_DEPLOY_TOKEN` из `.env`.
11. Проверить twin-host `ruvds-l2wyz`.
12. Убрать `ARG` из image build path, пересобрать образы и очистить
    релевантные caches/layers.

### Acceptance criteria

Фраза **«ключ стоит»** допустима только если одновременно выполнено:

-   Deploy Key добавлен как read-only;
-   private key имеет корректные permissions;
-   `origin` использует SSH и не содержит credential;
-   `git fetch origin` успешно выполняется от `taximeter`.

**Важно:** PAT нельзя отзывать до подтверждения рабочего SSH deploy
path.

------------------------------------------------------------------------

## 2. DRF-1698 --- consent для персональных норм питания

### Проблема

Персональный расчёт норм использует шесть параметров:

-   вес;
-   рост;
-   возраст;
-   пол для расчёта;
-   уровень активности;
-   цель.

Food Diary должен работать и без этих данных. Текущий draft consent
слишком легко смешивает согласие на персональные нормы с использованием
дневника питания вообще.

### Решение владельца

**APPROVE WITH TEXT CHANGES.**

Consent относится **только к персональному расчёту норм**, а не к Food
Diary в целом.

Рекомендуемый purpose: `NUTRITION_PERSONAL_NORMS`. Не использовать общий
`HEALTH_CONSENT=true` как замену специфического purpose.

### CONSENT_ASK

> Хотите, чтобы Ayla рассчитывала ваши персональные нормы?
>
> Для расчёта понадобятся шесть параметров: вес, рост, возраст, пол для
> расчёта, уровень активности и ваша цель.
>
> Я сохраню эти данные в вашем профиле и буду использовать их только для
> персонального расчёта и пересчёта норм, когда данные изменятся.
>
> Это необязательно. Без персонального расчёта дневник продолжит
> работать: вы сможете записывать еду и воду и видеть итоги за день.
>
> Согласие можно отозвать в любой момент. После отзыва Ayla перестанет
> использовать эти параметры для персонального расчёта.

Кнопки:

-   `Рассчитать мои нормы`
-   `Не сейчас`

### CONSENT_DECLINED

> Хорошо, персональный расчёт не включаем.
>
> Дневник продолжит работать как обычно: записывайте еду и воду, а Ayla
> покажет итог за день.
>
> Если позже захотите персональные нормы, их можно включить в разделе
> «Питание».

Если такого UI-раздела ещё нет:

> Если передумаете, напишите: «Рассчитать мои нормы».

### Отзыв согласия

Нужна детерминированная canonical action:

`Отключить персональный расчёт`

Free text может инициировать отзыв, но не должен быть единственным
механизмом. Перед удалением:

-   `Отключить и удалить`
-   `Оставить как есть`

После подтверждения:

-   параметры перестают использоваться;
-   параметры удаляются согласно контракту;
-   derived personal norms инвалидируются/становятся недоступными;
-   история Food Diary сохраняется.

`CONSENT_RECORDED_BUT_UNREADABLE` должен fail-close только персональный
расчёт. Сам дневник продолжает работать.

------------------------------------------------------------------------

## 3. OD-PILOT-9 --- Recommendation Shelf в первом Controlled Pilot

### Исходная ситуация

Для полноценного recommendation shelf требуется существенный
дополнительный critical path: canonical recommendation authority,
candidate-level safety, contract fixes и human verification mapping. На
момент решения VERIFIED mappings отсутствуют.

При этом прямой путь
`service → provider/catalog → availability → booking` может работать без
recommendation shelf.

### Решение владельца

**Первый Controlled Pilot запускается без recommendation shelf.**

Это **не удаляет Recommendation System / Product Brain из продукта**.
Recommendation System продолжает развиваться, но shelf не является
release gate первой pilot wave.

### Pilot path

``` text
direct service
→ catalog/provider
→ location/distance
→ availability
→ booking
```

Если нет достаточного VERIFIED evidence для рекомендации:

> Пока у меня недостаточно подтверждённых данных, чтобы уверенно
> посоветовать конкретный вариант. Могу показать доступные услуги или
> помочь уточнить, что тебе сейчас нужно.

Actions:

-   `Посмотреть услуги`
-   `Уточнить запрос`

### Stage 2 gate для Recommendation Shelf

Shelf можно включать после выполнения всех условий:

-   human review необходимых mappings;
-   `VERIFIED > 0`;
-   candidate-level Safety (DRF-1627);
-   canonical RecommendationResolver является единственным
    Recommendation Authority;
-   отсутствует legacy semantic recommendation fallback;
-   grounded WHY;
-   протестированы честные empty states;
-   работает `recommendation_id` attribution.

Включение --- через feature flag / pilot cohort.

### Distance contract

Canonical internal/API unit:

``` text
distance_meters: integer
```

Например:

``` text
distance_meters: 1834
```

UI:

-   `< 1000` → метры;
-   `>= 1000` → километры, например `1.8 км`.

Catalog surfaces называются:

-   `Рядом с вами`
-   `Доступные услуги`

Термин `Ayla рекомендует` резервируется только для canonical
Recommendation.

------------------------------------------------------------------------

## 4. §2 Identity model --- Person vs ClientProfile

### Проблема

Имя `ClientProfile` уже занято RFM/analytics snapshot. Новому unified
human identity profile требуется отдельное каноническое имя.

### Решение владельца

**Canonical target entity = `Person`.**

Существующий `ClientProfile` сохраняет своё RFM/analytics значение.

### Правила

Не создавать таблицу `Person` немедленно только ради переименования.

До identity migration SHADOW/status может оставаться на `BotUser`.

Целевая модель:

``` text
BotUser        → person_id
TenantCustomer → person_id
Master         → person_id
Provider       → person_id
```

`Person` --- identity-level entity, не tenant-owned entity.

В `Person` нельзя складывать tenant-specific:

-   booking state;
-   RFM;
-   catalog settings;
-   provider configuration;
-   salon-specific commercial state.

Не требуется делать rename churn только для освобождения имени
`ClientProfile`.

------------------------------------------------------------------------

## 5. Synthetic fixtures среди salon clients

### Исходная ситуация

В pilot dataset обнаружено 14 client records:

-   7 реальных;
-   7 синтетических fixtures.

Среди известных fixture identifiers присутствуют значения вида `888888`,
`999995–999999`, `drf954-test-001…`.

Если выполнить §2 migration как есть, fixtures пересекут domain
migration boundary и начнут участвовать в SHADOW/status, identity
reconciliation и pilot metrics как будто это реальные клиенты.

### Решение владельца

**CLEAN BEFORE MIGRATION.**

Семь подтверждённых fixture-клиентов удаляются до §2 migration.

### Ограничение

Удаление выполняется **только по явному allowlist конкретных известных
записей**.

Запрещено использовать эвристики вида:

``` text
phone starts with 99999 → delete
contains "test" → delete
```

### Безопасная процедура

1.  Сформировать exact allowlist 7 fixture records.
2.  Выполнить dry-run.
3.  В dry-run показать:
    -   точные IDs/identifiers;
    -   причину классификации как fixture;
    -   связанные объекты;
    -   cascade/reset impact;
    -   `REAL RECORDS TOUCHED = 0`.
4.  Проверить, нет ли у fixtures реальных bookings/events, которые
    должны быть сохранены.
5.  Проверить bidirectional sync/reset.
6.  Проверить, что import/sync не создаст fixtures повторно.
7.  Выполнить `--apply` только по explicit allowlist.
8.  Повторно измерить dataset.

### Acceptance criteria

После cleanup:

``` text
clients total = 7
known fixture identifiers = 0
real clients = unchanged
```

Fixtures для тестов должны жить в test factories, fixtures, staging/test
tenant или isolated seed dataset, но не в pilot production data.

Удаление фиксируется как **operational fixture cleanup**, а не как
пользовательское удаление реальных данных.

------------------------------------------------------------------------

## 6. S2 --- solo-master identity linking без identity-token в bot

### Проблема

Solo registration создаёт master/tenant contour, но Ayla должна
доказуемо связать созданного мастера с тем же человеком, который
взаимодействует через MAX.

Ранее bot мог получить слишком широкий identity credential. Это нарушает
границы ответственности.

### Решение владельца

**Identity-token боту не выдаём. Вариант с широким identity credential
--- NO-GO.**

``` text
MAX bot
= channel + UX

Identity service
= кто этот человек

Catalog / Master domain
= кем он является как мастер
```

`MAX bot ≠ Identity authority`.

### Phase 0 --- Controlled Pilot

Используется **operator-assisted identity linking** как временный
контролируемый provisioning step.

``` text
solo registration
→ own tenant/master workspace
→ IDENTITY_LINK_PENDING
→ controlled operator verification/link
→ IDENTITY_LINKED
→ publication readiness
```

`IDENTITY_LINK_PENDING` не считается полным успехом.

Пользователь может настраивать workspace, если tenant authorization это
безопасно разрешает:

-   профиль;
-   услуги;
-   цены;
-   расписание;
-   locations.

Но публикация требует:

``` text
identity_link = LINKED
```

### Phase 1

Целевая модель --- автоматический proof-of-possession, предпочтительно
OTP через контролируемый specialist path после security review:

``` text
solo registration
→ OTP/proof
→ IDENTITY_LINKED
→ READY
```

### Изменение Phase 0 DoD

Требование `no operator intervention` снимается **только для
identity-linking в Phase 0**.

Остальной normal onboarding flow остаётся self-service.

### Provisioning token

`AYLA_TENANT_PROVISIONING_TOKEN` не получает автоматически полномочия
identity authority.

Его scope должен оставаться минимальным и явно определённым. Нельзя
просто переименовать широкий identity-token в provisioning-token и
сохранить прежние полномочия.

### Operator audit

Operator flow должен использовать controlled action, а не ручное
редактирование БД.

Минимальный audit package:

``` text
solo_registration_id
channel = MAX
channel_user_id
tenant_id
master_id
known/verified phone if available
request timestamp
```

При успешной связи фиксируются:

``` text
identity_link.status = LINKED
provenance = OPERATOR_VERIFIED
operator_id
timestamp
```

Для отказа нужен controlled state, например `IDENTITY_LINK_REJECTED`, с
внутренней controlled reason taxonomy и безопасным пользовательским
recovery message.

------------------------------------------------------------------------

## 7. `ayla-90` --- временный выход из auto-mode

### Проблема

Классификация старых задач уже выполнена, evidence собран, script
идемпотентный и часть записей уже применена. Но auto-mode не разрешает
окну выполнить необходимые Linear writes.

### Решение владельца

**Временно вывести `ayla-90` из auto-mode для применения заранее
проверенного reconciliation/update package.**

Это не постоянное расширение полномочий.

``` text
auto-mode ON
→ temporary write window
→ apply pre-reviewed idempotent package
→ exact report
→ auto-mode ON
```

### Перед write

Окно должно показать dry-run:

``` text
TO APPLY

CANCELLED_BY_PIVOT: ...
ABSORBED: ...
DUPLICATE: ...
POST_PILOT: ...

Already applied: 5
Remaining: N
No-op if rerun: yes
```

### После write

Нужен exact report:

``` text
APPLIED
- DRF-...

UNCHANGED
- DRF-...

FAILED
- DRF-...

AUTO MODE RESTORED: yes
```

Запрещено обходить ограничение через другого агента. Тот же исполнитель
применяет свой pre-reviewed package в явно разрешённом write window.

------------------------------------------------------------------------

## 8. DRF 3.18 в catalog

### Проблема

Upgrade DRF меняет форму `details` для list validation в
`PATCH personal-context`. Это означает потенциальный breaking change
публичного API contract, а не просто dependency bump.

Точная old/new response shape должна браться из зафиксированного
evidence (#284), а не предполагаться.

### Решение владельца

**HOLD: DRF 3.18 не обновлять до отдельной compatibility migration.**

Framework upgrade не должен молча менять public error contract перед
Controlled Pilot.

### Gate для разблокировки

1.  Зафиксировать golden contract текущего `PATCH personal-context` для
    проблемного list-case.
2.  Показать exact delta:

``` text
BEFORE:
...

AFTER DRF 3.18:
...
```

3.  Выполнить consumer census всех читателей `details`:
    -   ai-bot-platform;
    -   Mini App/frontend;
    -   backend-to-backend consumers;
    -   tests.
4.  Выбрать explicit compatibility strategy.

### Предпочтительный путь

Если adapter небольшой и однозначный, перед pilot предпочтительно
сохранить canonical API shape:

``` text
DRF 3.18 internal ValidationError
→ Ayla API error normalizer
→ stable canonical error envelope
```

Долгосрочный принцип:

> Public Ayla API error contract не должен случайно зависеть от
> внутренней сериализации ValidationError конкретной версии DRF.

Не следует:

-   обновлять DRF вместе с функциональным PR;
-   просто переписывать snapshots ради зелёного CI;
-   считать response shape внутренней деталью, если consumer его читает.

------------------------------------------------------------------------

## 9. Ruff 0.16 --- tool upgrade vs lint-policy expansion

### Проблема

В одном изменении предлагается смешать:

``` text
A. Ruff 0.15 → 0.16
B. расширение lint-policy до 413 правил + Markdown formatting
```

Это разные изменения с разным риском.

### Решение владельца

**Ruff 0.16 --- GO как изолированное tooling update.**

**Полное включение 413 новых правил одним пакетом --- HOLD.**

**Repository-wide Markdown formatting --- HOLD.**

### Правило обновления

Первый PR:

``` text
Ruff 0.16
+
effective 0.15 lint policy
+
0 intentional semantic changes
+
CI green
```

Если новая версия требует минимального compatibility delta конфигурации,
он допустим, но без массовой очистки repository.

### Новые правила

Перед включением сделать census и классифицировать правила минимум как:

``` text
SAFE
REVIEW
SEMANTIC_RISK
NOT_APPLICABLE
POST_PILOT
```

Для групп показать:

-   количество правил;
-   число текущих violations;
-   autofixability;
-   возможность semantic change;
-   затронутые pilot-critical files;
-   рекомендацию.

Безопасные группы включать небольшими отдельными пакетами.

### Markdown

Не выполнять repository-wide автоматическое форматирование `.md`.

Для Ayla Markdown содержит не только developer docs, но и canon,
handoff, ADR/decision и policy/specification документы.

Frozen/canonical Markdown нельзя переписывать только ради formatting
compliance.

### Version pinning

Не допускается рассинхронизация:

``` text
local → 0.15
CI → 0.16
pre-commit → latest
```

Версия Ruff должна быть controlled/pinned одинаково во всех применимых
execution surfaces.

### Acceptance criteria первого PR

``` text
Ruff 0.16 pinned
existing effective lint semantics preserved
no mass source reformat
Markdown corpus untouched
CI green
tests unchanged
no application behavior change
```

------------------------------------------------------------------------

# Итоговый реестр решений

  --------------------------------------------------------------------------
  №                 Тема              Owner ruling         Статус
  ----------------- ----------------- -------------------- -----------------
  1                 Pilot Git         Read-only repository CLOSED
                    credential        Deploy Key; PAT      
                                      удалить после green  
                                      SSH deploy           

  2                 Nutrition         Specific consent +   CLOSED
                    personal norms    deterministic        
                    consent           revoke; diary        
                                      независим            

  3                 Recommendation    Не входит в первую   CLOSED
                    Shelf             Controlled Pilot     
                                      wave; Recommendation 
                                      System остаётся      

  4                 Unified human     Canonical entity     CLOSED
                    identity          `Person`;            
                                      существующий         
                                      `ClientProfile`      
                                      остаётся             
                                      analytics/RFM        

  5                 Synthetic clients Удалить 7 explicit   CLOSED
                                      fixtures до §2       
                                      migration            

  6                 Solo-master       Phase 0              CLOSED
                    identity          operator-assisted;   
                                      Phase 1 OTP;         
                                      identity-token боту  
                                      запрещён             

  7                 `ayla-90`         Временное write      CLOSED
                                      window для           
                                      pre-reviewed         
                                      idempotent package   

  8                 DRF 3.18          HOLD до              CLOSED
                                      compatibility        
                                      migration и consumer 
                                      census               

  9                 Ruff 0.16         Tool upgrade GO;     CLOSED
                                      413-rule expansion и 
                                      mass Markdown        
                                      formatting отдельно  
  --------------------------------------------------------------------------

------------------------------------------------------------------------

# Общий implementation principle

Эти девять решений следуют одному правилу:

> **Controlled Pilot не должен получать ложную простоту за счёт
> ослабления security, подмены evidence, загрязнения production data,
> скрытых breaking contracts или массового технического churn.**

Поэтому:

-   security boundary важнее удобного deploy/provisioning shortcut;
-   consent имеет конкретный purpose;
-   Recommendation показывается только при достаточном evidence;
-   identity и tenant-domain не смешиваются;
-   test fixtures не становятся production truth;
-   временная ручная операция должна быть явной и аудируемой;
-   dependency upgrade не имеет права молча менять API;
-   tooling upgrade не смешивается с массовой сменой engineering policy.

**Следующий организационный шаг:** синхронизировать эти rulings с
DRF-1349 и соответствующими implementation/blocker issues, сохраняя
DRF-1349 как living owner-decision registry.
