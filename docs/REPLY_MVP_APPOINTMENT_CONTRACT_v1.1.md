# Reply: Ayla MVP Appointment Contract v1.1

## Result

Обновлён канонический документ:

`ayla-knowledge/05 Architecture/Ayla MVP Appointment Contract.md`

Версия изменена на `1.1-draft`.

## Added

- Operational Appointment Projections:
  - Master Today Projection;
  - Admin Calendar Projection;
  - Customer Appointment Projection.
- Appointment Operational Exceptions как отдельная модель, не являющаяся Appointment status.
- Extended Permission Model с границами `READ / PROPOSE / COMMAND / WRITE`.
- Idempotency Contract для `CreateAppointment`, `CancelAppointment`, `RescheduleAppointment`, включая double-click, mobile retry и timeout retry.
- Appointment Timeline Model, отделённая от lifecycle и собранная из revisions, domain events и разрешённых audit records.
- Operational Notes Model с purpose и явной visibility policy; unrestricted `Appointment.notes` не добавлен.
- Customer Arrival / Check-in boundary; `checked_in` не добавлен в Appointment status.
- Schedule Influence Model: Master schedule → Availability → Bookable slots/Slot Hold → Appointment command/commit.
- Новые open questions `OQ-AC-12..14` для arrival/check-in, Operational Exception и Operational Note ownership/semantics.

## Preserved

- Appointment Domain владеет Appointment lifecycle.
- `beautygo_backend` остаётся Proposed operational transactional SoR согласно `OD-RRM-1`.
- AI, conversation runtime, screen projections и provider raw state не получают Appointment ownership или WRITE authority.
- Provider boundary не создаёт второго Ayla Appointment SoR.
- Разделение Domain Contract / Implementation / API Representation / UI Projection / AI Tool Representation.
- Existing canonical event registry не изменён; `customer_arrived` не добавлялся.
- Existing Repository Responsibility Matrix, Decision Log, Domain Event Registry и другие архитектурные документы не изменялись.

## Validation

- `git diff --check` — пройден.
- `python scripts/validate_knowledge.py` — общий exit code `1` из-за baseline-ошибок/предупреждений в других документах; для обновлённого Appointment Contract отдельные ошибки и предупреждения отсутствуют.
- Проверены headings, version, open questions и отсутствие нового canonical event.

## Open Questions

Основные нерешённые вопросы сохранены и расширены: lifecycle naming, completion/no-show authority, admin/specialist permissions, provider mapping, API/event wire schema, idempotency implementation, arrival/check-in semantics, Operational Exception ownership и Operational Note governance.

## Scope

Изменён только `05 Architecture/Ayla MVP Appointment Contract.md`. Summary создан в соответствии с заданием:

`C:\Users\user\PycharmProjects\Ayla\docs\REPLY_MVP_APPOINTMENT_CONTRACT_v1.1.md`