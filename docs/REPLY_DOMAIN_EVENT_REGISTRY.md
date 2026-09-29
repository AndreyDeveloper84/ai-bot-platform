# Domain Event Registry — выполненное изменение

## Created

Обновлён канонический документ:

`05 Architecture/Ayla Domain Event Registry.md`

Версия документа установлена как `1.0-draft`.

## Canonical decisions used

- Domain event — committed business fact, а не command, API request, tool invocation, click, retry или notification delivery result.
- Для каждого события сохраняются один authoritative owner и один authorized producer.
- Событие появляется только после commit изменения состояния владельца домена.
- `event_name` остаётся canonical registry field; mapping для prompt-level `event_type` добавлен без создания второго envelope.
- `appointment.created` означает persistence Appointment, `appointment.confirmed` — authoritative confirmation.
- `appointment.no_show` добавлен только как `proposed` / `incomplete`; `no_show` не равен `cancelled`.
- AI, projections, analytics, notification workers, transport relays и provider integrations не становятся владельцами Appointment или других business facts.
- `formula_tela` зафиксирован как pilot provider integration, а не глобальный Ayla operational SoR.
- Provider sync failure/success, timeout и API response не являются успехом бизнес-операции.

## Proposed items

- `appointment.completed` остаётся `semantic_status: incomplete` согласно существующему OQ-E3.
- `appointment.no_show` требует решения об authority, evidence, correction и provider reconciliation.
- Notification events, analytics events, delivery infrastructure, consumer compatibility IDs и retention policy не превращены в новые canonical domain events без owner decision.
- Существующие OQ и compatibility/migration rules сохранены.

## Added sections

- Event categories and boundaries.
- Common event contract mapping.
- Detailed candidate record for `appointment.no_show`.
- Event Consumer Matrix.
- Event vs Timeline.
- Provider, failure, and privacy boundaries.
- Validation Checklist.

## Validation

- `git diff --check -- "05 Architecture/Ayla Domain Event Registry.md"` — passed.
- `python scripts/validate_knowledge.py` — exit code `1` из-за существующих ошибок baseline в других документах (missing YAML frontmatter, unresolved links и legacy metadata issues). Новый Event Registry в выводе validator не отмечен.
- Проверена структура документа и наличие обязательных разделов/entries.

## Scope

Изменён только `05 Architecture/Ayla Domain Event Registry.md`. Не изменялись Repository Responsibility Matrix, MVP Appointment Contract, Decision Log, Consent Registry и другие архитектурные документы.