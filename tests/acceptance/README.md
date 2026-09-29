# tests/acceptance — ручные приёмочные harness'ы

Это НЕ pytest-тесты и НЕ часть CI: имена не матчатся `test_*.py`, и им нужна живая инфраструктура пилота (deployed BOT + Ayla, реальные секреты из env).
Здесь лежат приёмочные прогоны по живому контуру: DRF-915 (reminders), DRF-916 (booking E2E: create/lookup/reschedule/cancel), DRF-980/1007 (post-deploy smoke, read-only с rollback).
Запуск — вручную из корня репозитория в окружении с доступом к пилоту: `python tests/acceptance/drf916_e2e.py` (см. docstring каждого файла).
DRF-916 умеет брать названного мастера/услугу, заведённых оператором, а не первую пару по имени: `python tests/acceptance/drf916_e2e.py --tenant <slug> --master <uuid|имя> --service <uuid|имя>`; при отказе FAIL называет первый непройденный фильтр допуска (активен / приглашение принято / связан с Ayla / продаётся / мастер оказывает услугу).
Не добавляйте сюда обычные unit/integrационные тесты — им место в `tests/` или `apps/<app>/tests/`, где их подберёт CI.
