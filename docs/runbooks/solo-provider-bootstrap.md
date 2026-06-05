# Solo Provider Bootstrap — Runbook

| Поле | Значение |
|---|---|
| **Дата** | 2026-05-29 r3 (canonicalized by Tau) |
| **Статус** | Canonical — consolidated for Tech Lead review; updated for Smart Landing / Provider Landing Enrichment |
| **Тип документа** | Runbook / операционная инструкция |
| **Канонический путь** | `docs/runbooks/solo-provider-bootstrap.md` |
| **Связанный UX** | `docs/screens/provider-onboarding/provider-landing-enrichment-flow.md` |
| **Критичность** | P0 для запуска solo-мастера |

---

## 0. Назначение

Этот документ описывает технический bootstrap solo-мастера после того, как пользователь прошёл один из стартовых путей:

```text
A. Smart Landing: Ayla нашла данные во внешних источниках
B. Upload Price: пользователь загрузил прайс/PDF/Excel/фото
C. Template Bootstrap: источников нет, Ayla создала профиль по шаблону
D. Manual Bootstrap: оператор заводит мастера вручную
```

Главное правило:

```text
provider-landing-enrichment-flow.md = пользовательский UX
solo-provider-bootstrap.md = техническое создание сущностей после review
```

---

## 1. Что приходит на вход bootstrap

После `ProviderLandingSession` на вход bootstrap приходит подтверждённый черновик:

```text
AcceptedProfileDraft
AcceptedServiceDraft[]
AcceptedScheduleDraft
AcceptedMediaDraft[]
AcceptedSourceProvenance[]
UserReviewDecision[]
```

Источник черновика может быть:

```text
external_enrichment
uploaded_price_extraction
template_bootstrap
manual_input
```

---

## 2. Что создаём для solo-мастера

Для solo-provider один человек совмещает роли:

```text
owner
admin
master
```

Создать:

```text
Tenant(type=solo_provider)
BotUser или связать существующего
TenantMembership roles=[owner, admin, master]
ProviderProfile / SpecialistProfile
Services
WorkingHours
Schedule presets / exceptions
Media drafts / confirmed media
ProviderNotification settings
Knowledge documents, если включён RAG
```

---

## 3. Smart Landing bootstrap path

Если данные пришли из `provider-landing-enrichment-flow.md`, bootstrap должен:

```text
1. Проверить, что пользователь подтвердил ключевые поля.
2. Создать tenant.
3. Создать или связать BotUser.
4. Назначить роли owner/admin/master.
5. Создать профиль мастера.
6. Создать услуги из accepted service drafts.
7. Создать расписание из accepted schedule draft или template preset.
8. Сохранить provenance для ключевых полей.
9. Сгенерировать go-live checklist.
10. Запустить smoke test: free slots → booking → provider sees booking.
```

---

## 4. Template Bootstrap path

Если источников нет и пользователь выбрал шаблон, bootstrap создаёт всё как draft/requires_review до публикации.

Пример:

```text
Template: nail_master_solo
Region: Пенза
Price level: medium
```

Создаём:

```text
профиль
услуги
типовые длительности
региональные ценовые подсказки
буфер после услуги
расписание по умолчанию
подготовительные заметки
```

Нельзя публиковать без подтверждения:

```text
цены
длительности
адрес
фото
описание
список услуг
```

---

## 5. Go-live checklist

Перед запуском записи клиентов должно быть true:

```text
[ ] tenant active
[ ] BotUser linked
[ ] roles owner/admin/master assigned
[ ] profile confirmed
[ ] at least one visible/bookable service
[ ] service has price
[ ] service has duration
[ ] service assigned to master
[ ] working hours confirmed
[ ] at least one free slot exists
[ ] provider can open Ayla Pro
[ ] customer can create test booking
[ ] provider sees booking
[ ] customer can request reschedule
[ ] Ayla can auto-reschedule eligible booking
[ ] provider receives rescheduled_by_ayla notification
```

---

## 6. Связь с UX-документами

| Документ | Что должен учитывать bootstrap |
|---|---|
| `provider-landing-enrichment-flow.md` | источник черновиков и review decisions |
| `provider-services-prices-flow.md` | услуги могут прийти из enrichment/template draft |
| `provider-calendar-schedule-flow.md` | расписание может прийти из источников или template preset |
| `provider-booking-detail-flow.md` | запись должна открываться после bootstrap |
| `provider-messages-flow.md` | provider notification должна работать после auto-reschedule |

---

## 7. Definition of Done

Bootstrap завершён, если:

```text
solo-provider создан
может открыть Ayla Pro
имеет услуги
имеет расписание
клиент может записаться
provider видит запись
Ayla может перенести eligible booking
provider получает notification
tenant isolation проверена
```

---

## Last verified

2026-05-29 — Canonicalized by Tau from Codex `solo-provider-bootstrap.updated.md`; placed at canonical `docs/runbooks/`. Canon verified: solo-provider = one User combining owner+admin+master per ADR-0008 / `solo-provider-universal-ui`; bootstrap creates bot-platform entities (Tenant/BotUser/TenantMembership) — W4-owned per ADR-0009 §5, NOT Alpha; template/manual/enrichment draft sources require review before publish; go-live checklist + end-to-end smoke (free slot → booking → provider sees → Ayla reschedule → notification) + tenant isolation gate. Pairs with UX doc `provider-onboarding/provider-landing-enrichment-flow.md`.
