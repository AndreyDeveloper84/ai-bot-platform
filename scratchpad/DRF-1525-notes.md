# DRF-1525 — экран подключения и настройки салона

Ветка: `feat/drf1525-salon-onboarding`, worktree `.claude/worktrees/drf1525-salon-onboarding`.

## Что сделано

- `apps/tenancy/onboarding.py` — один путь подключения (`connect_salon`):
  строка, идентификатор (проверяется по Ayla ДО сохранения: probe услуг и
  мастеров по `?tenant=<UUID>`), город, синхронизация сразу после создания,
  проверка исхода (`assess_salon`). Отказы — `ConnectError` с объяснением.
- Закрытый перечень причин невидимости (окно в DRF-1511): `tenant_inactive`,
  `city_missing`, `never_synced`, `no_active_services`, `no_bookable_masters`.
  Совпадает с реальным читателем: `_known_cities` в `apps/marketplace/discovery.py`
  читает города только по бронируемым мастерам.
- `apps/tenancy/admin.py` — экран `connect/` (только суперпользователь, §27),
  ссылка из changelist, на форме настройки добавлены `city` и read-only блок
  «Видимость для клиентов» (последняя синхронизация, услуги, бронируемые
  мастера, названные причины). Секреты не тронуты (DRF-1495) — проверено
  рендером трёх страниц с заданным токеном: утечки нет.
- Сбой синхронизации (в т.ч. HTTP 429) не откатывает строку и показывается
  отдельно: «каталог не доехал» ≠ «салон не подключён».

## Прогоны

- `pytest apps/tenancy apps/adminconsole` — 187 passed (из них 17 новых).
- ruff 0.15.12 check/format — чисто. mypy onboarding.py/admin.py — чисто.
- negative_assert_guard apps/tenancy — clean.
- Краснота: отключена ветка `no_bookable_masters` при сохранённых тестах →
  `test_salon_without_bookable_masters_reason_named` FAILED; после отката — зелёные.

## Для боя (живой проход)

Локально проход покрыт интеграционными тестами с настоящим
`CatalogSyncService`. На бою осталось: подключить салон через экран против
настоящей Ayla, увидеть «Салон виден клиентам», проверить поиск клиентом.

Миграций нет.
