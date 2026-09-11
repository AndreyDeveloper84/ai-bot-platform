# Ayla Salon Operations MVP Contract — summary

## Выполнено

Создан канонический кандидат продуктового документа:

`06 Product/Ayla Salon Operations MVP Contract.md`

Статус документа: `draft / proposed / canonical candidate`, версия `0.1`.

## Что зафиксировано

- operating model `Customer — Ayla — Salon Operations — Master — Service Delivery`;
- роли и ответственность Customer, Master, Administrator, Salon Owner и Ayla AI Assistant;
- разделение `Business Process -> Domain Capability -> Command -> Event -> Projection -> Screen requirement`;
- master operating journey: Start Day, Before Appointment, During Service, Complete Appointment;
- administrator journey: booking, same-ID reschedule, replacement appointment, cancellation и conflict resolution;
- customer journey: Discovery, Booking, Confirmation, Reschedule, Cancellation, Visit, Follow-up;
- границы AI: suggestion не является business action, AI не владеет доменными фактами;
- MVP capability matrix для Appointment Management, Schedule, Customer, Service, Notes, Notifications, Availability, Reviews/Feedback и Earnings;
- Master ↔ Ayla interaction model и automation boundary;
- provider boundary для `formula_tela`, privacy boundary и purpose-limited operational projections;
- failure semantics и distinction между delivery/integration failure и business success;
- owner decisions и open questions по completion, cancellation, replacement, availability, notes, feedback и earnings.

## Изменённые файлы

Изменён только целевой документ `06 Product/Ayla Salon Operations MVP Contract.md`.
Другие архитектурные и продуктовые документы не изменялись.

## Validation

- `git diff --check -- '06 Product/Ayla Salon Operations MVP Contract.md'` — passed.
- `python scripts/validate_knowledge.py` — exit code 1 из-за baseline-проблем в репозитории: 126 errors и 20 warnings в существующих документах; после исправления metadata у нового документа его собственных validator errors не осталось.

## Риски и follow-up

Документ остаётся proposed: требуется owner review и отдельные решения по cancellation authority, completed semantics, replacement appointment, Schedule/Availability ownership и deferred capabilities.