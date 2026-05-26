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

### α-mode (current, 2026-05-25)

Master DM сейчас **всегда пропускается** с audit-row, потому что:

1. CatalogMaster не имеет ``ayla_user_id`` bridge (apps/catalog/ —
   W1 territory, добавление в отдельном W1 PR).
2. ``yclients_staff_id`` отсутствует в D3 consumer-enriched dict
   (нет fallback path).

Когда W1 откроет bridge → отдельный мелкий PR (W2 follow-up) wire-up
``_master_wants_personal_messages`` через lookup
``CatalogMaster.ayla_user_id`` + revisit master DM. См. follow-up
issue после ship α.

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

_MASTER_DM_TEMPLATE = (
    "⚠ Платёж не прошёл\n"
    "Клиент: {client_name}\n"
    "Услуга: {service_name}, {appointment_date}\n"
    "Сумма: {amount} ₽\n"
    "Статус брони: ожидает оплаты"
)

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

    # Master DM — α-mode skip с audit row для будущего backfill.
    _record_master_dm_skip(data, payment_id)

    # Client DM (with retry button).
    _try_send_client_dm(data, payment_id)


def _record_master_dm_skip(data: dict[str, Any], payment_id: str) -> None:
    """α-mode: master DM пропускается с audit-row для будущего backfill.

    После W1 добавит ``CatalogMaster.ayla_user_id`` — отдельный follow-up
    PR делает запрос-backfill (опционально, founder/W2 решают) который
    проходит по этим audit rows и шлёт ретро-нотификации мастерам,
    если оператор хочет.

    Tech-lead-fixed audit row schema (2026-05-25):

      {
        "event": "payment_failed.master_dm_skipped_no_bridge",
        "payment_event_id": <uuid>,
        "master_user_id": <ayla_uuid>,
        "tenant_id": <uuid>,
        "yclients_staff_id": <int|null>,
        "timestamp": <iso>,
        "reason": "catalogmaster_ayla_user_id_missing",
      }
    """
    from datetime import datetime, timezone

    from django.db import IntegrityError

    master_ayla_user_id = data.get("master_user_id")
    if not master_ayla_user_id:
        # Без master_user_id audit row тоже теряет ценность (нечем
        # ретро-нотифицировать). Логируем, не пишем audit.
        logger.info(
            "payment_failed.master_dm.skip reason=no_master_user_id payment_id=%s",
            payment_id,
        )
        return

    payload = {
        "event": "payment_failed.master_dm_skipped_no_bridge",
        "payment_event_id": data.get("payment_event_id"),
        "payment_id": payment_id,
        # appointment_id зафиксирован для backfill walker — без него
        # retro-notification не сможет показать «какой именно приём»
        # мастеру (только payment_id мало для контекста).
        "appointment_id": data.get("appointment_id"),
        "master_user_id": str(master_ayla_user_id),
        "tenant_id": str(data.get("tenant_id")) if data.get("tenant_id") else None,
        # Tech-lead schema требует yclients_staff_id slot даже если null —
        # backfill replay walker может искать по нему когда bridge активен.
        "yclients_staff_id": data.get("yclients_staff_id"),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "reason": "catalogmaster_ayla_user_id_missing",
    }

    try:
        from apps.audit.services import write_audit

        write_audit(
            action="payment_failed.master_dm_skipped_no_bridge",
            payload=payload,
        )
    except (IntegrityError, Exception) as exc:  # noqa: BLE001
        # Если audit недоступен — не валим flow. Дублируем в log с тем
        # же payload-ом чтобы grep по reason work-ал.
        logger.warning(
            "payment_failed.master_dm.audit_failed err=%s payload=%s",
            exc,
            payload,
        )
    else:
        logger.info(
            "payment_failed.master_dm.skipped_no_bridge master=%s payment_id=%s",
            master_ayla_user_id,
            payment_id,
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
