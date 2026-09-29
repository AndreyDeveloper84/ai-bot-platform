Ты работаешь как Senior Django/DRF Backend Engineer и Production Architect проекта Ayla.

Твоя задача — реализовать в репозитории:

AndreyDeveloper84/beautygo_backend

ветка:

dev

минимальный backend scope для Master MVP Appointment Flow.

Цель задачи — не расширять продукт, а закрыть только то, что блокирует первый рабочий сценарий мастера в MVP:

Operations Home → Appointment Detail → authorized CompleteAppointment → authoritative result → return to Operations Home.

Важно: работай строго по существующей архитектуре репозитория и не создавай новые доменные сущности без необходимости.

Обязательные canonical источники из ayla-knowledge, которым должна соответствовать реализация:

Ayla Repository Responsibility Matrix
Ayla MVP Appointment Contract
Ayla Domain Event Registry
Ayla Salon Operations MVP Contract
Ayla Master App Information Architecture
Ayla Master Operations Screen Contract
Ayla Appointment Detail Screen Contract
Ayla Master Appointment Flow MVP

Handoff-документы не использовать как canonical source.

Перед изменениями обязательно изучи текущую ветку dev и существующий код:

appointments/models.py
appointments/views.py
appointments/serializers.py
appointments/domain/*
appointments/application/*
appointments/infrastructure/outbox/*
appointments/tests/*
users/permissions.py
users/specialists_urls.py
users/schedule_api.py
services/models.py
связанные tests для SalonService persistence, event conformance, tenant isolation и idempotency

Главный принцип:

НЕ строить новый booking backend.

Максимально использовать существующие:

Appointment
Appointment state machine
version
snapshots
SpecialistProfile
tenant scoping
OutboxEvent
existing complete flow
existing permissions
existing schedule data

MVP first.

Если изменение не требуется для:

Operations Home → Appointment Detail → Complete → updated day

не делай его в этой задаче.

Задача состоит из четырёх обязательных частей.

Реализовать Master Operations Today Projection

Нужен отдельный read surface для главного экрана мастера.

Не заставлять mobile собирать Operations Home из универсального GET /api/v1/appointments/ и нескольких дополнительных endpoint'ов.

Создай specialist-only endpoint в существующей API-структуре репозитория.

Выбери URL в стиле текущей архитектуры. Не ломай существующие маршруты.

Назначение endpoint:

вернуть минимальную projection рабочего дня текущего специалиста.

Обязательные свойства:

только authenticated specialist;
только appointments текущего specialist;
tenant boundary должна сохраняться;
day window должен учитывать timezone мастера;
записи отсортированы хронологически;
данные read-only;
endpoint не является новым Source of Truth;
никаких новых таблиц ради projection.

Минимальный response должен содержать:

date;
timezone;
generated_at;
summary;
next_appointment;
appointments.

Минимальный summary:

total;
remaining;
completed.

Appointment item должен содержать минимум:

id;
version;
status;
start_datetime;
end_datetime;
service;
customer.

Service projection должна содержать минимум:

name;
duration_minutes.

Customer projection должна содержать минимум:

id;
display_name.

Не добавлять в MVP projection:

CRM history;
semantic memory;
AI inference;
медицинские данные;
полную customer profile;
earnings;
analytics;
notifications;
schedule editor data.

Правило next_appointment:

использовать текущие authoritative appointment data.

Не придумывать новый доменный статус.

Если точная бизнес-семантика "next" не зафиксирована, используй минимально безопасное правило и явно задокументируй его в коде/tests, не превращая его в domain rule.

Реализовать корректную Master Appointment Projection

Не расширяй существующие generic serializers хаотично.

Предпочтительно создать отдельные master-specific projection serializers/read serializers, если это лучше сохраняет separation of concerns.

Критически важно:

Appointment уже поддерживает два service source:

marketplace Service
SalonService

по правилу exactly one:

service XOR salon_service.

Поэтому master read projection НЕ должна зависеть только от service.name или service FK.

Для отображения фактически забронированной услуги используй immutable appointment snapshots как исторический/commercial source:

snapshot_service_name;
snapshot_duration_minutes;
snapshot_price;
snapshot_timezone;

если конкретное поле требуется response contract.

Не ломай marketplace bookings.

Не ломай SalonService bookings.

Не меняй существующий write path AMD-019.

Master Appointment Detail должна отдавать минимум:

id;
version;
authoritative status;
start_datetime;
end_datetime;
service snapshot;
minimal customer context;
is_first_visit, если уже используется текущим MVP;
freshness/update metadata, если это можно корректно получить из существующих полей без создания новой инфраструктуры.

Customer context в этой задаче ограничить:

id;
display_name.

Не отдавать:

hidden AI memory;
inference;
чужие customer data;
полную conversation history;
медицинские inference;
не относящиеся к визиту PII.
Усилить CompleteAppointment optimistic concurrency

Текущий complete flow сохранить:

transaction.atomic();
select_for_update();
specialist ownership check;
tenant check;
Appointment.complete();
outbox emit;
payment capture scheduling.

Не переписывать этот flow без необходимости.

Добавить optimistic concurrency для нового master MVP contract.

Complete endpoint должен принимать:

expected_version

Предпочтительно обязательный для нового master surface.

Если архитектурно проще сохранить backward compatibility существующих callers, разрешается сделать переходный compatibility path, но новый master client должен всегда отправлять expected_version.

Поведение:

если:

expected_version != appointment.version

то:

не выполнять complete;
не создавать completion event;
не менять состояние;
вернуть HTTP 409;
вернуть стабильный error code:

STALE_VERSION

Mobile после этого должен иметь возможность перечитать Appointment projection.

Проверку выполнить внутри transaction после select_for_update, до изменения state.

Не создавать новую concurrency систему: использовать уже существующий Appointment.version и существующий StaleVersionError pattern, если он подходит.

Проверь также, должен ли version увеличиваться при completion согласно текущему Appointment Contract и существующей модели.

Не меняй это молча.

Если сейчас version bump определён только для reschedule, зафиксируй это как найденный архитектурный факт в итоговом отчёте и не вводи новую семантику без canonical основания.

Не ломать Event/Outbox compatibility

Не делать массовый rename:

booking.*

→

appointment.*

Сейчас ai-bot-platform имеет существующий allowlist и consumer contract.

Сохраняй legacy integration topics, пока consumer migration не завершена.

Уже существующий pattern:

booking.rescheduled
+
appointment.rescheduled

используй как пример совместимой миграции.

Не вводить новый dual-emit для других событий без явной необходимости этой задачи.

Complete flow должен продолжать корректно создавать текущий OutboxEvent для завершения.

Не ломать:

event envelope;
event_version;
event_id;
tenant_id;
actor;
correlation_id;
external delivery gate.

Не менять no-show integration в этой задаче.

Не менять bot allowlist contract.

Не менять ai-bot-platform.

Обязательные тесты

Добавь focused test coverage именно для Master MVP.

Не переписывай существующий test suite.

Минимально проверить:

Master Today Projection:

specialist sees only own appointments;
foreign specialist appointments are absent;
tenant boundary preserved;
only selected/current local day is included;
appointments ordered by start_datetime;
SalonService booking renders service snapshot correctly;
marketplace Service booking renders correctly;
customer.id and customer.display_name returned;
next_appointment correct for defined MVP rule;
summary values correct.

Appointment Detail:

specialist can read own appointment;
specialist cannot read another specialist's appointment;
SalonService appointment detail does not break when service FK is NULL;
version is returned.

Complete:

matching expected_version → success;
stale expected_version → 409 STALE_VERSION;
stale request does not modify Appointment;
stale request does not emit completion event;
specialist cannot complete foreign appointment;
existing state-machine restrictions still apply;
successful complete still emits the existing completion outbox event;
existing payment capture behavior is not regressed.

Regression:

existing client appointment APIs remain compatible unless an explicit additive change is required;
existing SalonService persistence tests remain green;
emitter conformance tests remain green;
tenant tests remain green;
idempotency/reschedule tests remain green.

Не создавай тесты ради количества.

Если часть сценариев уже надёжно покрыта существующими tests, не дублируй их бессмысленно — добавь только недостающие проверки.

MVP scope — что НЕ делать

В этой задаче запрещено добавлять:

StartService;
новый in_progress UX flow;
procedure timer;
новые Appointment statuses;
новую state machine;
новую RBAC architecture;
новую permissions framework;
Customer CRM;
историю клиента;
AI memory;
AI inference;
notifications inbox;
Master Schedule UI/API redesign;
no-show UX changes;
reschedule UX changes;
cancellation UX changes;
earnings screen;
analytics screen;
profile/settings work;
новый event bus;
новые database tables ради read projection.

Если для выполнения задачи кажется необходимым что-то из этого списка — остановись и вынеси как blocker/open question вместо самостоятельного расширения scope.

Implementation guidance

Предпочтительная архитектура:

Existing Appointment Domain

↓

thin master read projection

↓

master-specific serializer

↓

existing DRF surface

и отдельно:

Master Complete request

↓

expected_version check

↓

existing complete domain method

↓

existing outbox

Не переносить бизнес-логику во view serializer.

Не вычислять authoritative state на клиенте.

Не использовать UI state как domain state.

Не использовать projection как Source of Truth.

Обрати внимание на query efficiency.

Для Today projection избегай N+1:

используй select_related / prefetch_related там, где нужно.

Особенно учти:

client;
specialist;
service;
salon_service при необходимости;
tenant.

Но не делай premature optimization.

API response должен соответствовать существующей success_response/error_response convention репозитория.

Используй существующий error handling style.

Не создавай новый формат ошибок.

Перед кодом

Сначала сделай короткий written preflight:

какие существующие endpoint/classes переиспользуются;
какие новые файлы или classes планируются;
какие существующие файлы будут изменены;
какие migrations ожидаются.

Правильный ответ по migrations для этой задачи, скорее всего:

нет migrations.

Если migration всё же требуется — объясни до внесения изменения, почему без неё невозможно закрыть MVP.

После preflight приступай к реализации без ожидания дополнительного подтверждения, если blocker не обнаружен.

Проверки после реализации

Обязательно выполнить:

targeted pytest нового Master MVP test set;
существующие appointments tests, затронутые изменениями;
SalonService persistence tests;
emitter conformance tests;
tenant isolation tests;
idempotency/reschedule tests;
lint/format checks согласно репозиторию;
git diff --check.

Если полный suite реалистично запустить — запускай.

Если полный suite слишком дорогой, сначала targeted suite, затем максимально широкий релевантный regression set.

Итоговый отчёт

Создай:

docs/REPLY_MASTER_MVP_BACKEND_GAP_IMPLEMENTATION.md

Отчёт должен содержать:

Preflight
что было до изменений;
какие gaps подтверждены.
Implemented
конкретные endpoint;
serializers;
concurrency changes;
tests.
API Contract
краткий shape Today projection;
краткий shape Appointment Detail;
complete expected_version behavior.
Preserved
Appointment ownership;
state machine;
SalonService persistence;
event compatibility;
tenant boundaries;
existing bot integration.
Not Implemented
всё сознательно Deferred.
Open Questions
Только реальные unresolved owner decisions, найденные в ходе реализации.
Validation
команды;
число tests passed/failed/skipped;
lint;
git diff --check.
Changed Files
Точный список изменённых и созданных файлов.

Ограничения

Работай только в:

beautygo_backend

ветка:

dev

Не менять ayla-knowledge.

Не менять ai-bot-platform.

Не создавать PR без отдельного указания.

Не пушить без отдельного указания.

Не менять canonical документы.

Не исправлять посторонние проблемы репозитория, найденные во время работы.

Если обнаружен unrelated defect — зафиксировать в отчёте, но не расширять задачу.

Главный критерий готовности:

После этой работы backend должен позволять мобильному приложению мастера реализовать минимальный сценарий:

Master opens Operations Home

→ получает свой актуальный рабочий день

→ видит клиента и услугу

→ открывает Appointment Detail

→ получает authoritative appointment version

→ отправляет CompleteAppointment с expected_version

→ при актуальной версии получает committed completion

→ при stale версии получает 409 STALE_VERSION и перечитывает данные

→ возвращается к обновлённому Operations Home.

Всё, что не требуется для этого сценария, не входит в эту задачу.