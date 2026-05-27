"""payment_failed skill — детальная реализация (W2/Epsilon, pre-flip 2026-05-28).

### Контекст / UX (Variant C, tech-lead handoff 2026-05-24)

При payment.failed событии (Ayla → bot-platform через event contract)
бот делает **двойную нотификацию**:

* **Master DM (info-only):** короткий алерт «у клиента не прошла оплата».
  БЕЗ retry-кнопки. Только если ``MasterNotificationPrefs.personal_message
  == True``. Опциональные кнопки [Позвонить клиенту] / [Отменить запись]
  отложены на post-MVP (отдельный PR после flip).
* **Client DM (action):** короткое объяснение + inline-кнопка [Оплатить]
  с callback ``cb:payment:retry:<payment_id>``. При тапе бот зовёт
  Ayla retry endpoint и шлёт новую confirmation URL.

### Архитектура триггера

bot-platform НЕ использует pub/sub framework. Gamma's #443 consumer
обрабатывает ``payment.failed`` domain event и **прямо вызывает**
:func:`on_payment_failed_event` из этого модуля. Дополнительно consumer
emit-ит analytics ``payment_failed_skill_triggered`` событие (canonical
vocabulary) для observability.

### D3 consumer-side enrichment contract (tech-lead 2026-05-25)

Event-contract.md §3.7 envelope содержит ТОЛЬКО ``payment_id``,
``appointment_id``, ``reason``, ``failed_at`` — нет identity полей и
нет суммы. Skill требует enriched data для построения DM-текстов и
lookup BotUser-ов.

**Поэтому Gamma's #443 consumer enrich-ит** через ``Appointment``
lookup (или join) перед вызовом ``on_payment_failed_event(...)``.
Ожидаемая dict-форма зафиксирована в docstring :func:`on_payment_failed_event`
— это consumer-side контракт, НЕ event-contract.md surface.

### Master DM dispatch (Sequence #4 wire-up, 2026-05-27)

Master DM **actively fires** via local mirror lookup chain:

  envelope.appointment_id
    → RemoteBookingProxy.appointment_id (PK)
    → .specialist_id (Ayla Master UUID)
    → CatalogMaster.ayla_user_id == specialist_id
    → .linked_bot_user (OneToOne к BotUser)
    → BotUser.chat_id → send_message

All hops are bot-platform mirror tables — no cross-repo DB / REST call
per ADR-0009 §Hard rule #2. Mirror staleness gap (Ayla canonical may be
~30s ahead) is accepted SLO — Phase 1 follow-up tightens via event-
driven sync invalidation. See ``_try_send_master_dm`` for the full
flow + edge case handling.

The earlier α-mode skip behaviour (before 2026-05-27) was based on a
stale docstring assumption that W1's CatalogMaster.ayla_user_id +
linked_bot_user fields hadn't shipped yet. They have; α-mode is
removed.

### Retry endpoint — pending Alpha task #66

Tech-lead 2026-05-24: Alpha расширяет ``POST /payments/<id>/retry/``
с новым permission class ``IsBotServiceWithVerifiedClient`` (defense-
in-depth — bearer token + ``X-External-User-ID`` + body.client_id
cross-check). Тот PR — task #66. Этот skill пока имеет TODO-заглушку
в callback handler; финальная integration в task #67 (расширение
``apps/integrations/ayla_payments/client.py`` с ``retry_payment()``).

### Security focus (per §H.3)

* **Authorization callback:** проверяем что тапнувший каллбэк
  владеет payment-ом. Без проверки любой третий участник чата мог бы
  triggerнуть retry чужого платежа (forwarded chat, screenshot).
* **Idempotency retry:** Ayla endpoint требует ``Idempotency-Key``.
  Используем deterministic key ``payment_retry:<payment_id>`` —
  повторный tap не дублирует YooKassa session.
* **Race conditions:** между emit события и tap-ом клиента payment
  может уже captured / cancelled. Endpoint вернёт 409 — skill
  отвечает понятным «оплата уже прошла» / «запись отменена».
"""

from __future__ import annotations

import logging
from typing import Any, ClassVar

from apps.events.services import emit
from apps.skills.base import SkillContext, SkillResult
from apps.skills.registry import register

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Callback prefix
# ---------------------------------------------------------------------------

# Префикс inline-кнопки [Оплатить] в client DM. Suffix = payment_id (UUID).
# Используем тот же ``cb:`` namespace что и остальные skill-callbacks
# (cb:rem:*, cb:book:*) — channel handler шлёт всё через одну skill
# dispatch pipeline (см. apps/skills/registry).
CALLBACK_PAYMENT_RETRY_PREFIX = "cb:payment:retry:"


# ---------------------------------------------------------------------------
# Text templates
# ---------------------------------------------------------------------------

# Master DM template — built dynamically by ``_format_master_dm_text``
# because the «Сумма» line is dropped entirely if amount is unavailable
# (per tech-lead Q3 verdict 2026-05-26 — cleaner than rendering «—»).
# Order of lines locked: header → identity → context → counter → status.
_MASTER_DM_HEADER = "⚠ Платёж не прошёл"
_MASTER_DM_CLIENT_LINE = "Клиент: {client_name}"
_MASTER_DM_SERVICE_LINE = "Услуга: {service_name}, {appointment_date}"
_MASTER_DM_AMOUNT_LINE = "Сумма: {amount} ₽"
# Counter context line — added per Q5 verdict 2026-05-26. Critical for
# master understanding (cascade pattern, not one-off transient fail).
# Parametric N — derives from skill payload, robust к threshold env changes
# (default 3, configurable via PAYMENT_FAILED_HANDOFF_THRESHOLD).
_MASTER_DM_COUNTER_LINE = "Это {n}-я попытка оплаты подряд — клиент не смог завершить бронирование."
_MASTER_DM_STATUS_LINE = "Статус брони: ожидает оплаты"

_CLIENT_DM_TEMPLATE = (
    "❌ Платёж за {service_name} не прошёл\n"
    "У мастера {master_name}, {appointment_date} — {amount} ₽\n\n"
    "Попробовать снова?"
)

_CLIENT_RETRY_BUTTON_LABEL = "Оплатить"

# Ответ клиенту когда retry успешен.
_CLIENT_RETRY_SUCCESS_TEMPLATE = "Готово! Открой ссылку для оплаты:\n{confirmation_url}"

# Заглушка пока Alpha task #66 не закроет endpoint extension.
_CLIENT_RETRY_PENDING_TEMPLATE = (
    "Сейчас оплата временно недоступна для повторной попытки через бот. "
    "Свяжись с салоном напрямую — администратор оформит новую ссылку."
)

# Ответ когда payment уже не в retry-eligible state (409 от Ayla).
_CLIENT_RETRY_OBSOLETE_TEMPLATE = (
    "Эта оплата уже неактуальна — возможно платёж прошёл, либо запись "
    "была отменена. Если есть вопросы — свяжись с салоном."
)

# Ответ при transient ошибке (5xx от Ayla).
_CLIENT_RETRY_TRANSIENT_ERROR = (
    "Не получилось создать новую ссылку — провайдер оплаты недоступен. "
    "Попробуй через пару минут или свяжись с салоном."
)

# Ответ при auth-fail (callback от не-владельца payment).
_CALLBACK_NOT_AUTHORIZED = "Эта кнопка не для тебя — попробуй открыть свой чат с ботом."

# Ответ при некорректном payload.
_CALLBACK_MALFORMED = "Не удалось распознать платёж. Если проблема повторится — свяжись с салоном."


# ---------------------------------------------------------------------------
# Entry point: triggered from Gamma's #443 consumer
# ---------------------------------------------------------------------------


def on_payment_failed_event(data: dict[str, Any]) -> None:
    """Точка входа из Gamma's #443 payment.failed consumer.

    Consumer вызывает это синхронно после идемпотентной dedupe-проверки
    и обновления Conversation context (см. `apps/eventbus/consumers/
    payment.py` когда Gamma запушит).

    Args:
      data: D3 consumer-enriched dict (НЕ raw envelope per event-contract
            §3.7 — consumer обогащает перед вызовом).

            **Phase 1 (Option C minimum, current — founder verdict
            2026-05-26):** consumer derives payload from envelope +
            Conversation context only (no cross-repo DB lookup per
            ADR-0009 §Hard rule #2). Phase 2 (task #93, post-pilot)
            extends Ayla envelope to event_version=2 with the enriched
            fields. Skill α-mode graceful-degrades any missing field.

            Phase 1 fields (always populated):

                * ``payment_id`` (UUID str) — Ayla Payment row id;
                * ``appointment_id`` (UUID str) — Ayla Appointment id;
                * ``client_user_id`` (UUID str) — Ayla User id клиента;
                * ``tenant_id`` (UUID str) — tenant scope of the event;
                * ``failure_code`` (str) — MAPPED closed-enum value
                  (PII §7 — raw provider free-text NEVER reaches the
                  skill); same value as ``Conversation.last_payment_
                  failure_code``. (#738 N7 — was previously documented
                  as ``reason``; that was the pre-merge plan, but the
                  consumer maps it before dispatch.)
                * ``consecutive_failures`` (int) — counter value at the
                  threshold-crossing moment;
                * ``failed_at`` (str ISO8601, may be None) —
                  pass-through from envelope.data;
                * ``payment_event_id`` (UUID str) — envelope.event_id;
                  needed for audit traceability;
                * ``client_name`` (str | None) — from BotUser.client_name.

            Phase 2 fields (currently None, populated post-task-#93):

                * ``master_user_id`` (UUID str) — Ayla User id мастера;
                * ``amount`` (Decimal/float/str) — рубли;
                * ``service_name`` (str) — название услуги;
                * ``appointment_date`` (str) — для отображения;
                * ``master_name`` (str) — display name мастера;
                * ``reason`` (str) — pass-through raw envelope field
                  (Phase 2 only; skip in Phase 1 — see ``failure_code``).

    Поведение (α-mode):

    1. Шлёт actionable DM клиенту с inline-кнопкой [Оплатить] (если
       BotUser клиента найден через ayla_user_id).
    2. Master DM пропускается с audit-row (см. α-mode docstring модуля).
       Wire-up master DM = отдельный follow-up PR после W1 добавит
       ``CatalogMaster.ayla_user_id``.

    Графefully tolerates отсутствующие BotUser (клиент никогда не писал
    в бот) — log + skip без raise. Никогда НЕ raise: ошибки логируются,
    consumer dedupe запись остаётся (event считается обработанным даже
    если DM не дошёл — повторный retry consumer-а только дублировал бы
    audit row).
    """
    payment_id = str(data.get("payment_id") or "").strip()
    if not payment_id:
        logger.warning(
            "payment_failed.payload_missing_payment_id keys=%s",
            sorted(data.keys()),
        )
        return

    # Emit analytics для observability (canonical event vocabulary).
    # `emit` сам обернут в try/except — не raise.
    emit(
        "payment_failed_skill_triggered",
        properties={"payment_id": payment_id},
    )

    # Master DM (active dispatch — α-mode skip removed 2026-05-27 per
    # founder pilot sequence #4). W1's CatalogMaster bridge fields
    # (``ayla_user_id`` + ``linked_bot_user``) are shipped + local
    # RemoteBookingProxy mirror provides specialist_id, so we can
    # resolve master from envelope.appointment_id without cross-repo
    # calls per ADR-0009.
    _try_send_master_dm(data, payment_id)

    # Client DM (with retry button).
    _try_send_client_dm(data, payment_id)


def _try_send_master_dm(data: dict[str, Any], payment_id: str) -> None:
    """Master notification — active dispatch (Sequence #4, 2026-05-27).

    Lookup chain (all local mirror, no cross-repo per ADR-0009):

      envelope.appointment_id
        → RemoteBookingProxy.appointment_id (PK, exact match)
        → .specialist_id (Ayla Master UUID)
        → CatalogMaster.ayla_user_id == specialist_id
        → .linked_bot_user (OneToOne к BotUser, lazy-onboarded)
        → BotUser.chat_id → send_message(chat_id, text)

    Enrichment fields from local mirror (Phase 1 envelope is Option C
    minimum — only payment/appointment/client ids + counter):

    * service_name ← CatalogService(ayla_service_id=proxy.service_id).name
    * appointment_date ← format(proxy.start_at) в МСК
    * client_name ← already в payload (from BotUser.client_name)
    * amount ← NOT в Phase 1 payload, NOT в local mirror; line is
      dropped entirely from rendered DM if absent (per Q3 verdict)
    * consecutive_failures ← already в payload (counter value
      at threshold-crossing — exactly N=3 default unless threshold
      env-tuned)

    **NO MasterNotificationPrefs gate** per Q1 verdict — payment
    cascade = «urgent» class (CheckConstraint-forced ON в the prefs
    model). N=3 threshold already filtered spam; no per-master opt-out.

    **Quiet hours ignored** per Q2 verdict (Phase 1 follow-up).

    **Edge cases (Q4 verdict — defensive consistent с #851/#874):**

    * RemoteBookingProxy missing (mirror lag race, или Ayla booking
      arrived after payment.failed) → audit skip + log
    * CatalogMaster missing (mirror lag, или specialist_id orphan)
      → audit skip + log
    * CatalogMaster.linked_bot_user IS NULL (master не onboarded к
      bot yet) → audit skip + log
    * MasterNotificationPrefs row не existing → defaults applied
      (urgent=True forced) — no special handling needed since we
      don't gate on prefs anyway
    * send_message raises → caught in :func:`_send_dm`, не breaks
      consumer batch
    """
    appointment_id = data.get("appointment_id")
    if not appointment_id:
        # Defensive — Gamma's consumer envelope always populates this,
        # но guard against future schema drift.
        logger.info(
            "payment_failed.master_dm.skip reason=no_appointment_id payment_id=%s",
            payment_id,
        )
        return

    tenant_id = data.get("tenant_id")
    if not tenant_id:
        logger.info(
            "payment_failed.master_dm.skip reason=no_tenant_id payment_id=%s",
            payment_id,
        )
        return

    # Step 1: resolve RemoteBookingProxy mirror.
    proxy = _resolve_remote_booking_proxy(appointment_id, tenant_id)
    if proxy is None:
        _audit_master_dm_skip(data, payment_id, "remote_booking_proxy_missing")
        return

    # Step 2: resolve CatalogMaster from proxy.specialist_id.
    if not proxy.specialist_id:
        _audit_master_dm_skip(data, payment_id, "proxy_no_specialist_id")
        return

    master = _resolve_catalog_master(proxy.specialist_id, tenant_id)
    if master is None:
        _audit_master_dm_skip(data, payment_id, "catalog_master_missing")
        return

    # Step 3: confirm master is onboarded to bot.
    if master.linked_bot_user_id is None:
        _audit_master_dm_skip(data, payment_id, "master_not_onboarded")
        return

    master_bot_user = master.linked_bot_user
    chat_id = (master_bot_user.chat_id or "").strip()
    if not chat_id:
        _audit_master_dm_skip(data, payment_id, "master_no_chat_id")
        return

    # Step 4: enrichment from mirror.
    service_name = _resolve_service_name(proxy.service_id, tenant_id) or "услугу"
    appointment_date = _format_appointment_date(proxy.start_at)

    # CR #881 M1: coerce consecutive_failures defensively before passing
    # к _format_master_dm_text. Raw payload value comes from JSON / event
    # stream — string-shaped values (e.g. event_version drift, Redis
    # round-trip) would raise ValueError inside int() и escape skill,
    # breaking the consumer batch. Sane default = 3 (the pilot threshold).
    raw_counter = data.get("consecutive_failures")
    try:
        consecutive_failures = int(raw_counter) if raw_counter is not None else 3
    except (TypeError, ValueError):
        logger.warning(
            "payment_failed.master_dm.bad_consecutive_failures value=%r payment_id=%s",
            raw_counter,
            payment_id,
        )
        consecutive_failures = 3

    text = _format_master_dm_text(
        client_name=data.get("client_name") or "—",
        service_name=service_name,
        appointment_date=appointment_date,
        amount=data.get("amount"),
        consecutive_failures=consecutive_failures,
    )

    _send_dm(
        chat_id=chat_id,
        text=text,
        attachments=None,  # info-only, no buttons per Variant C scope
        log_context={
            "side": "master",
            "payment_id": payment_id,
            "master_id": str(master.pk),
            "tenant_id": str(tenant_id),
        },
    )

    # Forensic audit on successful dispatch — symmetric с skip path.
    _audit_master_dm_dispatch(data, payment_id, master_id=str(master.pk))


def _resolve_remote_booking_proxy(appointment_id: Any, tenant_id: Any) -> Any:
    """Fetch the local RemoteBookingProxy by appointment_id, scoped к tenant.

    ``all_tenants`` manager because consumer-side flow may not have
    tenant context active. Strict ``tenant_id`` filter prevents cross-
    tenant lookup (Phase F adversarial concern #1).
    """
    from apps.booking.models import RemoteBookingProxy

    return (
        RemoteBookingProxy.all_tenants.filter(
            appointment_id=appointment_id,
            tenant_id=tenant_id,
        )
        .only("appointment_id", "specialist_id", "service_id", "start_at", "tenant_id")
        .first()
    )


def _resolve_catalog_master(specialist_id: Any, tenant_id: Any) -> Any:
    """Fetch CatalogMaster by ayla_user_id == specialist_id, tenant-scoped.

    ``select_related("linked_bot_user")`` pre-fetches the BotUser ref
    so chat_id lookup doesn't issue a second query. Tenant scoping
    same as proxy lookup — adversarial defence against cross-tenant
    enumeration.
    """
    from apps.catalog.models import CatalogMaster

    return (
        CatalogMaster.all_tenants.filter(
            ayla_user_id=specialist_id,
            tenant_id=tenant_id,
        )
        .select_related("linked_bot_user")
        .first()
    )


def _resolve_service_name(service_id: Any, tenant_id: Any) -> str | None:
    """Fetch CatalogService.name by ayla_service_id, tenant-scoped.

    Returns None when service_id is None (event update without
    service ref) or no matching service row exists in mirror —
    caller falls back to generic «услугу».
    """
    if not service_id:
        return None
    from apps.catalog.models import CatalogService

    service = (
        CatalogService.all_tenants.filter(
            ayla_service_id=service_id,
            tenant_id=tenant_id,
        )
        .only("name")
        .first()
    )
    if service is None:
        return None
    return service.name or None


def _format_appointment_date(start_at: Any) -> str:
    """Render appointment start в MSK timezone for the master DM.

    Format: ``DD.MM в HH:MM`` (same convention as
    ``apps/bookings/tasks.py::_format_day_before_text``). MSK because
    the master operates on salon-local time, not UTC.

    **Invariant:** ``RemoteBookingProxy.start_at`` is ``db_index=True``
    + non-nullable (``apps/booking/models.py``). Callers always pass a
    non-None value via the mirror lookup chain. CR #881 P1: defensive
    None-check dropped — would mask a real model invariant violation
    if it ever fires.

    TODO Phase 2 multi-region: hardcoded MSK works для pilot (Penza =
    MSK); future tenants в other timezones would read ``tenant.timezone``
    field (CR #881 F1).
    """
    from zoneinfo import ZoneInfo

    msk = start_at.astimezone(ZoneInfo("Europe/Moscow"))
    return msk.strftime("%d.%m в %H:%M")


def _format_master_dm_text(
    *,
    client_name: str,
    service_name: str,
    appointment_date: str,
    amount: Any,
    consecutive_failures: int,
) -> str:
    """Compose master DM with conditional amount line.

    Per Q3 verdict — drop «Сумма» line entirely if amount missing
    (cleaner than rendering «—»). Counter line is always present (skill
    fires only at threshold-crossing so consecutive_failures ≥ N=3).
    """
    lines = [
        _MASTER_DM_HEADER,
        _MASTER_DM_CLIENT_LINE.format(client_name=client_name),
        _MASTER_DM_SERVICE_LINE.format(
            service_name=service_name,
            appointment_date=appointment_date,
        ),
    ]
    amount_str = _format_amount(amount) if amount is not None else None
    if amount_str and amount_str != "—":
        lines.append(_MASTER_DM_AMOUNT_LINE.format(amount=amount_str))
    # `consecutive_failures` is already int-coerced at the
    # `_try_send_master_dm` boundary (CR #881 M1 defensive guard).
    lines.append(_MASTER_DM_COUNTER_LINE.format(n=consecutive_failures))
    lines.append(_MASTER_DM_STATUS_LINE)
    return "\n".join(lines)


def _audit_master_dm_skip(data: dict[str, Any], payment_id: str, reason: str) -> None:
    """Forensic audit row for skip paths — preserves operator visibility
    on why master DM didn't fire. Best-effort try/except per #851 pattern."""
    from datetime import datetime, timezone

    payload = {
        "event": "payment_failed.master_dm_skipped",
        "payment_event_id": data.get("payment_event_id"),
        "payment_id": payment_id,
        "appointment_id": data.get("appointment_id"),
        "tenant_id": str(data.get("tenant_id")) if data.get("tenant_id") else None,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "reason": reason,
    }
    try:
        from apps.audit.services import write_audit

        write_audit(action="payment_failed.master_dm_skipped", payload=payload)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "payment_failed.master_dm.audit_failed reason=%s err=%s payload=%s",
            reason,
            exc,
            payload,
        )
    logger.info(
        "payment_failed.master_dm.skipped reason=%s payment_id=%s",
        reason,
        payment_id,
    )


def _audit_master_dm_dispatch(
    data: dict[str, Any],
    payment_id: str,
    *,
    master_id: str,
) -> None:
    """Forensic audit row для successful master DM dispatch. Best-effort.

    CR #881 F3: also log INFO для grep-parity с the skip path. Operators
    grepping «payment_failed.master_dm.sent» now see both audit + log
    signals (skip path emits both via logger.info + write_audit).
    """
    logger.info(
        "payment_failed.master_dm.sent master=%s payment_id=%s",
        master_id,
        payment_id,
    )
    from datetime import datetime, timezone

    payload = {
        "event": "payment_failed.master_dm_sent",
        "payment_event_id": data.get("payment_event_id"),
        "payment_id": payment_id,
        "appointment_id": data.get("appointment_id"),
        "tenant_id": str(data.get("tenant_id")) if data.get("tenant_id") else None,
        "master_id": master_id,
        "consecutive_failures": data.get("consecutive_failures"),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    try:
        from apps.audit.services import write_audit

        write_audit(action="payment_failed.master_dm_sent", payload=payload)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "payment_failed.master_dm.audit_failed_on_dispatch err=%s payload=%s",
            exc,
            payload,
        )


def _try_send_client_dm(data: dict[str, Any], payment_id: str) -> None:
    """Client notification — текст + inline кнопка [Оплатить]."""
    client_ayla_user_id = data.get("client_user_id")
    if not client_ayla_user_id:
        logger.info(
            "payment_failed.client_dm.skip reason=no_client_user_id payment_id=%s",
            payment_id,
        )
        return

    try:
        bot_user = _resolve_bot_user(client_ayla_user_id)
    except _BotUserMissing:
        # Клиент никогда не писал в бот — не можем достичь его в MAX.
        # Это известный edge case (см. memory project_identity_bridging_pattern):
        # canonical Ayla User существует, BotUser мост ещё не создан.
        logger.info(
            "payment_failed.client_dm.skip reason=bot_user_missing ayla_user_id=%s payment_id=%s",
            client_ayla_user_id,
            payment_id,
        )
        return

    text = _CLIENT_DM_TEMPLATE.format(
        service_name=data.get("service_name") or "услугу",
        master_name=data.get("master_name") or "мастера",
        appointment_date=data.get("appointment_date") or "—",
        amount=_format_amount(data.get("amount")),
    )
    action_data = _build_retry_button_envelope(payment_id)
    _send_dm(
        bot_user.chat_id,
        text,
        attachments=action_data,
        log_context={
            "side": "client",
            "payment_id": payment_id,
        },
    )


# ---------------------------------------------------------------------------
# Callback skill — реагирует на [Оплатить] tap
# ---------------------------------------------------------------------------


@register
class PaymentRetryCallbackSkill:
    """Inline-button callback handler для ``cb:payment:retry:<payment_id>``.

    Срабатывает когда клиент тапает [Оплатить] в client DM. Делает HTTP
    к Ayla retry endpoint (через ``apps/integrations/ayla_payments/``
    после Alpha task #66) и шлёт обновлённую confirmation URL.

    Сейчас (до Alpha task #66 merged) — TODO-заглушка с graceful reply.
    Поведение, которое будет после интеграции, описано в docstring
    :meth:`handle`.
    """

    name: ClassVar[str] = "payment_failed_retry"

    def matches(self, context: SkillContext) -> bool:
        text = (context.message_text or "").strip()
        return text.startswith(CALLBACK_PAYMENT_RETRY_PREFIX)

    def handle(self, context: SkillContext) -> SkillResult:
        text = (context.message_text or "").strip()
        raw_payment_id = text[len(CALLBACK_PAYMENT_RETRY_PREFIX) :].strip()

        # 1. Парсим payment_id (форма UUID, но не валидируем строго —
        #    Ayla endpoint сам ответит 404 на bogus pk).
        if not raw_payment_id:
            logger.warning(
                "payment_retry.malformed_callback raw=%r",
                text,
            )
            return _build_reply(_CALLBACK_MALFORMED)

        # 2. Authorization — bot_user из текущего context должен быть
        #    клиентом (`ayla_user_id` соответствует Ayla payment.appointment.client).
        #    Без этой проверки можно было бы дёрнуть чужой retry через
        #    скопированный callback (forwarded chat, screenshot, web share).
        bot_user = context.bot_user
        if not getattr(bot_user, "ayla_user_id", None):
            # Bot_user ещё не bridge-нут к Ayla User. Authorization невозможна;
            # отказываем gracefully.
            logger.info(
                "payment_retry.no_ayla_bridge bot_user_id=%s payment_id=%s",
                getattr(bot_user, "id", None),
                raw_payment_id,
            )
            return _build_reply(_CALLBACK_NOT_AUTHORIZED)

        # 3. Вызов Ayla retry endpoint — TODO после Alpha task #66.
        #    Финальная integration — task #67. Сейчас отвечаем заглушкой.
        return _try_call_ayla_retry(
            payment_id=raw_payment_id,
            ayla_user_id=str(bot_user.ayla_user_id),
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _BotUserMissing(Exception):
    """Raised by ``_resolve_bot_user`` когда нет BotUser для данного
    ``ayla_user_id``. Caller трактует как «не можем достичь пользователя
    в MAX» и graceful skip."""


def _resolve_bot_user(ayla_user_id: Any) -> Any:
    """Поиск BotUser по Ayla User UUID.

    Использует ``BotUser.all_tenants`` (escape-hatch manager) потому
    что event-handler выполняется в bot-platform consumer-е, где
    tenant контекст может быть не выставлен на этот hop (consumer
    обработка cross-tenant события).
    """
    from apps.identity.models import BotUser

    bot_user = BotUser.all_tenants.filter(ayla_user_id=ayla_user_id).first()
    if bot_user is None:
        raise _BotUserMissing(str(ayla_user_id))
    return bot_user


def _format_amount(raw: Any) -> str:
    """Render amount → human-readable строка. Tolerates Decimal/float/str/None."""
    if raw is None:
        return "—"
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return str(raw)
    if value == int(value):
        return f"{int(value)}"
    return f"{value:.2f}"


def _build_retry_button_envelope(payment_id: str) -> list[dict[str, Any]]:
    """Channel-agnostic inline keyboard envelope.

    Channel adapter (apps/channels/max/handler.py::_build_attachments)
    конвертирует это в MAX wire format. Тот же envelope-shape как у
    apps/bookings/keyboards.py — single source of truth.
    """
    return [
        {
            "type": "inline_keyboard",
            "payload": {
                "buttons": [
                    {
                        "label": _CLIENT_RETRY_BUTTON_LABEL,
                        "callback": f"{CALLBACK_PAYMENT_RETRY_PREFIX}{payment_id}",
                    }
                ],
            },
        }
    ]


def _send_dm(
    chat_id: str,
    text: str,
    *,
    attachments: list[dict[str, Any]] | None,
    log_context: dict[str, Any],
) -> None:
    """Wrapper над apps/channels/max/outbound.send_message с graceful errors.

    Не raise — лог-и-уходи: payment_failed flow никогда не должен сломать
    consumer-цикл Gamma's #443.
    """
    if not chat_id:
        logger.warning(
            "payment_failed.send_dm.empty_chat_id ctx=%s",
            log_context,
        )
        return

    try:
        from apps.channels.max.outbound import send_message

        send_message(chat_id=chat_id, text=text, attachments=attachments)
        logger.info(
            "payment_failed.dm_sent ctx=%s text_len=%d",
            log_context,
            len(text),
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "payment_failed.dm_send_failed ctx=%s err=%s",
            log_context,
            exc,
        )


def _try_call_ayla_retry(*, payment_id: str, ayla_user_id: str) -> SkillResult:
    """Вызов Ayla retry endpoint.

    **Сейчас (Alpha task #66 в работе):** заглушка — отвечает клиенту
    что retry временно недоступен через бот.

    **После Alpha task #66 + W2 task #67:** делает POST к Ayla
    через расширенный ``apps/integrations/ayla_payments/client.py
    ::retry_payment(payment_id, ayla_user_id, idempotency_key)``.

    Обработка ответов:

    * 201 → :data:`_CLIENT_RETRY_SUCCESS_TEMPLATE` + URL
    * 409 (INVALID_STATUS) → :data:`_CLIENT_RETRY_OBSOLETE_TEMPLATE`
    * 502/503 → :data:`_CLIENT_RETRY_TRANSIENT_ERROR`
    * 404 / other auth/permission → :data:`_CALLBACK_NOT_AUTHORIZED`
    """
    # TODO(#67): после merge Alpha task #66 заменить на реальный вызов:
    #
    #   from apps.integrations.ayla_payments import get_ayla_payments_client
    #   client = get_ayla_payments_client()
    #   try:
    #       result = client.retry_payment(
    #           payment_id=payment_id,
    #           ayla_user_id=ayla_user_id,
    #           idempotency_key=f"payment_retry:{payment_id}",
    #       )
    #   except AylaInvalidStatus:
    #       return _build_reply(_CLIENT_RETRY_OBSOLETE_TEMPLATE)
    #   except AylaTransientError:
    #       return _build_reply(_CLIENT_RETRY_TRANSIENT_ERROR)
    #   except AylaAuthError:
    #       return _build_reply(_CALLBACK_NOT_AUTHORIZED)
    #   return _build_reply(_CLIENT_RETRY_SUCCESS_TEMPLATE.format(
    #       confirmation_url=result.confirmation_url,
    #   ))
    logger.info(
        "payment_retry.stubbed payment_id=%s ayla_user=%s "
        "(awaiting Alpha task #66 endpoint extension)",
        payment_id,
        ayla_user_id,
    )
    return _build_reply(_CLIENT_RETRY_PENDING_TEMPLATE)


def _build_reply(text: str) -> SkillResult:
    """Минимальный SkillResult с reply_text. Конвенция как у
    HumanHandoffSkill — confidence=None для callback-skill (не AI-skill)."""
    return SkillResult(
        reply_text=text,
        action_type="payment_retry",
        action_data=None,
        tool_calls_made=[],
        confidence=None,
    )
