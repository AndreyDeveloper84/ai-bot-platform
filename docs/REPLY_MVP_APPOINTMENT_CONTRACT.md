# Summary: Ayla MVP Appointment Contract

## Создано

Обновлён канонический документ:

- `ayla-knowledge/05 Architecture/Ayla MVP Appointment Contract.md`

## Принятые границы

- Appointment Domain владеет lifecycle Appointment.
- `beautygo_backend` указан как operational transactional SoR только со статусом Proposed через `OD-RRM-1`.
- AI, conversation runtime, UI, projection, provider raw state и tool invocation не являются источниками истины Appointment.
- Domain Contract отделён от implementation, API representation, UI projection и AI tool representation.
- Same-ID reschedule отделён от replacement; `rescheduled` не является domain state.
- Timeout, tool acknowledgement, HTTP 201 и notification failure отделены от business success.
- Formula Tela/provider boundary не создаёт второй Ayla Appointment SoR.

## Нерешённые вопросы

Сохранены как `Open question` или `Pending owner decision`:

- `requested` versus `pending_confirmation`;
- authoritative completion и no-show authority;
- semantics `appointment.rejected`;
- specialist/admin permissions и override;
- customer PII projection;
- late cancellation policy;
- provider mapping и API/event/idempotency contracts.

## Проверки

- `git diff --check` — пройден.
- `python scripts/validate_knowledge.py` — общий exit code `1` из-за существующих baseline-ошибок в других документах; ошибок, относящихся к Appointment Contract, не обнаружено.
- Другие документы `ayla-knowledge` не изменялись.
