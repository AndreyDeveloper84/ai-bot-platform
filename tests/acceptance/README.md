# tests/acceptance — ручные приёмочные harness'ы

Это НЕ pytest-тесты и НЕ часть CI: имена не матчатся `test_*.py`, и им нужна живая инфраструктура пилота (deployed BOT + Ayla, реальные секреты из env).
Здесь лежат приёмочные прогоны по живому контуру: DRF-915 (reminders), DRF-916 (booking E2E: create/lookup/reschedule/cancel), DRF-980/1007 (post-deploy smoke, read-only с rollback).
Запуск — вручную из корня репозитория в окружении с доступом к пилоту: `python tests/acceptance/drf916_e2e.py` (см. docstring каждого файла).
Не добавляйте сюда обычные unit/integrационные тесты — им место в `tests/` или `apps/<app>/tests/`, где их подберёт CI.
