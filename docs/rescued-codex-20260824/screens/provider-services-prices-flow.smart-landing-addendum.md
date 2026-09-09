# Provider Services & Prices — Smart Landing Addendum

| Поле | Значение |
|---|---|
| **Дата** | 2026-05-28 r1 |
| **Статус** | Addendum for Smart Landing / Template Bootstrap |
| **Связанный основной документ** | `docs/screens/provider/provider-services-prices-flow.md` |
| **Источник UX** | `docs/screens/provider-onboarding/provider-landing-enrichment-flow.md` |

---

## 1. Что меняется

Услуги и цены могут быть созданы не только вручную, но и из:

```text
external enrichment draft
uploaded price extraction
template bootstrap
manual input
```

---

## 2. Service draft sources

Каждая созданная услуга должна знать источник:

```text
yclients
website
vk
yandex_maps
2gis
uploaded_price
template
manual
```

Для каждого важного поля хранить provenance:

```text
name
category
price
price_max
duration_minutes
buffer_after_minutes
description
```

---

## 3. Template-generated services

Если услуга пришла из шаблона, она создаётся как draft/requires_review.

Нельзя автоматически публиковать:

```text
шаблонные цены
шаблонные длительности
шаблонное описание
лишние услуги из шаблона
```

UX label:

```text
Создано по шаблону — проверьте перед публикацией.
```

---

## 4. Region price benchmarks

Региональные цены — это подсказки, не финальные цены.

Правильно:

```text
По похожим мастерам в вашем регионе цена обычно в диапазоне 1 500–2 300 ₽.
Какую цену поставить?
```

Неправильно:

```text
Мы поставили цену 1 800 ₽.
```

---

## 5. Acceptance criteria additions

1. Service can be created from enrichment draft.
2. Service can be created from uploaded price extraction.
3. Service can be created from template bootstrap.
4. Template-created services are draft/requires_review.
5. Region price benchmark is shown as recommendation only.
6. User can accept/edit/reject each generated service.
7. Service does not become customer-bookable until required fields are confirmed.
8. Field-level provenance is stored for generated services.
