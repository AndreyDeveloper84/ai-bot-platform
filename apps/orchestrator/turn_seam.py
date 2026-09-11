"""Normalized orchestration boundary — `orchestrate_turn` (Migration Plan
Step 5 prep / OR-BOT-1..6).

ONE seam between the live ingress/guards and the live post-processing/
delivery. The channel handlers keep owning everything around the brain
(OR-BOT-1): webhook/dedup/idempotency, bot+conversation resolution,
safety gate, operator mute, callbacks, media, consent gates, memory
commands, persistence, keyboards, delivery. This module owns ONLY the
normalized call into the current production brains:

- ``surface="per_tenant"`` → the skill-registry dispatch (MAX + Telegram
  share the same brain contract);
- ``surface="global"``    → the concierge turn (nationwide tenant-less
  pilot bot, ``tenant=None`` by design — OR-BOT-3).

The seam itself has NO side effects: it never persists messages, never
sends outbound, never mutates booking/consent/MemoryEntry, never writes
metrics/audit. It does NOT call ``apps.orchestrator.pipeline.turn``
(OR-BOT-4: that pipeline bundles its own tenant/persist/delivery/audit
side effects). No new behaviour is introduced and no feature flag gates
it — the mapping is 1:1 with the pre-seam direct calls.

DRF-1419 — единственное, что шов делает СВЕРХ переноса: сторожит свой же
контракт полей. Поле, которое производитель заполнил, а перечисление не
переносит, роняет ход именованным ``seam_field_loss`` вместо тихого None.
Ни строки в БД это не пишет — только журнал (см. блок ниже).
"""

from __future__ import annotations

import dataclasses
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Literal

logger = logging.getLogger(__name__)

Surface = Literal["per_tenant", "global"]

SURFACE_PER_TENANT: Surface = "per_tenant"
SURFACE_GLOBAL: Surface = "global"


@dataclass(frozen=True)
class TurnContext:
    """Normalized brain input. Handles only — no ORM graph traversal here.

    ``tenant`` is informational (None is the VALID global-pilot value —
    OR-BOT-3); the seam never resolves, fabricates, or looks up a tenant.
    Consent state is not carried: consent guards run BEFORE the seam and
    neither brain needs them inside.
    """

    surface: Surface
    conversation: Any  # ORM handle (global: sentinel-scoped conversation)
    bot_user: Any  # ORM handle (global: sentinel BotUser)
    text: str
    channel: str = ""
    trace_id: str = ""
    tenant: Any = None  # tenant_or_none — None for the global pilot
    has_attachments: bool = False
    # Global-brain-only inputs (concierge call kwargs).
    user_message_id: Any = None
    memory_block: str = ""
    # DRF-1284: consent-gated weekly nutrition picture. "" when the gate is
    # closed or Ayla gave nothing — the seam stays a pure carrier and never
    # builds it (the handler owns every consent read, per this module's
    # contract).
    nutrition_block: str = ""
    extra_system: str = ""


@dataclass(frozen=True)
class TurnReply:
    """Normalized brain output. Pure data — the seam caller performs all
    persistence / delivery / state transitions.

    ``matched=False`` mirrors a None SkillResult (no skill matched) so the
    caller's legacy fallback (echo) still fires. ``assistant_persisted``
    mirrors DiscoveryReply.persisted (the concierge store already wrote
    the assistant turn — the caller must not double-record).
    """

    matched: bool = True
    reply_text: str = ""
    action_type: str = ""
    action_data: dict[str, Any] | None = None
    should_send: bool = True
    should_handoff: bool = False
    handoff_reason: str = ""
    new_state: Any = None
    should_close_conversation: bool = False
    assistant_persisted: bool = False
    meta: dict[str, Any] | None = None
    # DRF-1209 — the per-tenant skill's self-reported confidence, carried
    # 1:1 so the channel handler can enforce the confidence floor (pipeline
    # step 10.5) on the live path. None = the skill computed no score.
    confidence: float | None = None
    # DRF-1348 — mirrors DiscoveryReply.outage: the model could not be
    # reached at all. Carried, never interpreted: the seam has no opinion
    # about what a surface should draw for it.
    outage: bool = False
    # DRF-1385 — mirrors DiscoveryReply.tool_trace: the concierge's ordered
    # tool-choice trace for the turn (``({"tool": name, "arguments": {...}},
    # ...)``), so the post-reply intent resolver can record THAT choice
    # deterministically instead of paying a second model call. Carried,
    # never interpreted: None means a text-only turn or a legacy producer.
    tool_trace: tuple[dict[str, Any], ...] | None = None


# ---------------------------------------------------------------------------
# DRF-1419 — контракт полей шва и его ШУМНЫЙ отказ
# ---------------------------------------------------------------------------
#
# Шов копирует поля ПЕРЕЧИСЛЕНИЕМ, и до сих пор поле, которого в перечислении
# нет, не доезжало МОЛЧА. Дважды за одни сутки это стоило красного теста,
# написанного ДО реализации: `tool_trace` (DRF-1385, DiscoveryReply) и
# `confidence` (DRF-1209, SkillResult). Без такого теста потеря выглядит как
# «функция не работает по непонятной причине»: потребитель получает None и
# читает его как «пусто», а не как «не приехало».
#
# Оговорка отсюда важнее самого сторожа: у отсутствия ДВА разных источника, и
# схлопывать их в одно нельзя.
#
#   * поле НЕ БЫЛО ПОЛОЖЕНО — производитель ответа его не заполнил. Законно:
#     legacy-производитель без `tool_trace`, ход без уверенности. Молчит —
#     ровно как молчал до DRF-1419 (см. getattr в глобальном адаптере ниже).
#   * поле БЫЛО ПОЛОЖЕНО и потерялось на шве — дефект. Кричит.
#
# Различить их есть чем ровно в той мере, какую даёт dataclass: у поля,
# которого нет в контракте, значение сравнивается с его же УМОЛЧАНИЕМ.
# Отличается — производитель что-то в него положил, и это не доедет.
# Совпадает — положено ничего не было, терять сегодня нечего, но проводки всё
# равно нет: это отдельная строка в журнал, а не отказ. Больше механизм не
# даёт: производитель, не объявивший поля типом (не dataclass), никакого
# набора полей не декларирует, и сравнивать у него нечего.
#
# Наружу — ОДНО грубое имя (`seam_field_loss`), внутрь — РАЗДЕЛЬНЫЕ причины.
# Иначе через месяц по журналу нельзя будет сказать, теряем мы поля или их
# просто не кладут.

#: Единственное имя отказа, которое видит потребитель шва.
SEAM_FIELD_LOSS = "seam_field_loss"

#: Причина №1 — «мы ТЕРЯЕМ поля»: поле вне контракта, и в нём лежит не
#: умолчание. Отказ.
REASON_FIELD_DROPPED = "unmapped_field_populated"

#: Причина №2 — «их просто НЕ КЛАДУТ»: поле вне контракта, но держит своё
#: умолчание. Строка в журнал, НЕ отказ.
REASON_FIELD_UNWIRED = "unmapped_field_empty"


class TurnSeamFieldLoss(RuntimeError):
    """Именованное состояние шва: производитель положил в поле значение,
    а перечисление его не переносит.

    Отказ, а не деградация: молчаливое None на той стороне неотличимо от
    «пусто», и потребитель построит на нём ответ человеку. Возникнуть это
    может только на ошибке автора (новое поле у производителя без проводки
    в шве) — и раньше рантайма то же расхождение ловит сторож на CI
    (``apps/orchestrator/tests/test_turn_seam_field_guard.py``).
    """

    #: Грубое имя состояния — одно на оба внутренних счётчика.
    state = SEAM_FIELD_LOSS

    def __init__(self, producer: str, lost: tuple[str, ...]) -> None:
        self.producer = producer
        self.lost = lost
        super().__init__(
            f"{SEAM_FIELD_LOSS}: {producer} → TurnReply не переносит поля "
            f"со значением: {', '.join(lost)}"
        )


#: DiscoveryReply → TurnReply: что глобальный адаптер ПЕРЕНОСИТ.
DISCOVERY_TO_TURN: Mapping[str, str] = MappingProxyType(
    {
        "text": "reply_text",
        "action_data": "action_data",
        "persisted": "assistant_persisted",
        "outage": "outage",
        "tool_trace": "tool_trace",
    }
)

#: DiscoveryReply → TurnReply: что НАМЕРЕННО не переносится, и почему.
#: Пусто: у консьержа сегодня нет поля, которому нечего делать за швом.
DISCOVERY_NOT_CARRIED: Mapping[str, str] = MappingProxyType({})

#: SkillResult → TurnReply: что per-tenant адаптер ПЕРЕНОСИТ.
SKILL_RESULT_TO_TURN: Mapping[str, str] = MappingProxyType(
    {
        "reply_text": "reply_text",
        "action_type": "action_type",
        "action_data": "action_data",
        "should_send": "should_send",
        "should_handoff": "should_handoff",
        "handoff_reason": "handoff_reason",
        "new_state": "new_state",
        "should_close_conversation": "should_close_conversation",
        "meta": "meta",
        "confidence": "confidence",
    }
)

#: SkillResult → TurnReply: что НАМЕРЕННО не переносится, и почему.
SKILL_RESULT_NOT_CARRIED: Mapping[str, str] = MappingProxyType(
    {
        # Замер DRF-1419: единственный читатель — `apps.skills.registry.dispatch`,
        # который пишет имена вызовов в журнал ДО шва; за швом (обработчики MAX
        # и Telegram, `turn_reply_to_skill_result` и его потребители) поле не
        # читает никто. Запись tool_calls на Message живёт на пути
        # `apps.orchestrator.pipeline`, куда шов по OR-BOT-4 не ходит.
        # Объявлено намеренно НЕ переносимым, а не «переносится», потому что
        # сегодня оно и правда не переносится: притворяться обратным значило бы
        # завести вторую фикцию рядом с той, которую здесь чинят.
        "tool_calls_made": "прочитан внутри skills.registry.dispatch, ДО шва (замер DRF-1419)",
    }
)

_NO_DEFAULT = object()


def _declared_default(f: dataclasses.Field) -> Any:
    """Умолчание поля dataclass, или ``_NO_DEFAULT`` для обязательного."""

    if f.default is not dataclasses.MISSING:
        return f.default
    if f.default_factory is not dataclasses.MISSING:
        try:
            return f.default_factory()
        except Exception:  # noqa: BLE001 — фабрика умолчания не наш предмет
            return _NO_DEFAULT
    return _NO_DEFAULT


def _holds_default(value: Any, default: Any) -> bool:
    if default is _NO_DEFAULT:
        return False
    if value is default:
        return True
    try:
        return bool(value == default)
    except Exception:  # noqa: BLE001 — несравнимое считаем «положено»
        return False


def unmapped_fields(
    source_type: type,
    *,
    carried: Mapping[str, str],
    not_carried: Mapping[str, str],
) -> tuple[str, ...]:
    """Поля ТИПА-производителя, которых нет в контракте шва.

    Про набор полей ТИПА, а не про конкретный экземпляр — оговорка DRF-1419:
    глобальный адаптер читает legacy-производителей через ``getattr``, и
    сторож не вправе требовать поля от того, кто его не объявлял.
    """

    if not dataclasses.is_dataclass(source_type):
        return ()
    known = set(carried) | set(not_carried)
    return tuple(f.name for f in dataclasses.fields(source_type) if f.name not in known)


def _guard_carried_fields(
    source: Any,
    *,
    producer: str,
    carried: Mapping[str, str],
    not_carried: Mapping[str, str],
    trace_id: str = "",
) -> None:
    """Шумный отказ вместо тишины. Два счётчика внутрь, одно имя наружу."""

    if not dataclasses.is_dataclass(source) or isinstance(source, type):
        # Производитель не объявил набора полей — сравнивать не с чем. Это
        # честный предел механизма, а не разрешение молчать.
        return

    dropped: list[str] = []
    unwired: list[str] = []
    known = set(carried) | set(not_carried)
    for f in dataclasses.fields(source):
        if f.name in known:
            continue
        value = getattr(source, f.name, _NO_DEFAULT)
        if _holds_default(value, _declared_default(f)):
            unwired.append(f.name)
        else:
            dropped.append(f.name)

    if unwired:
        logger.warning(
            "orchestrator.turn_seam.field_unwired reason=%s producer=%s fields=%s trace_id=%s",
            REASON_FIELD_UNWIRED,
            producer,
            ",".join(unwired),
            trace_id,
        )
    if dropped:
        logger.error(
            "orchestrator.turn_seam.field_lost reason=%s state=%s producer=%s fields=%s trace_id=%s",
            REASON_FIELD_DROPPED,
            SEAM_FIELD_LOSS,
            producer,
            ",".join(dropped),
            trace_id,
        )
        raise TurnSeamFieldLoss(producer, tuple(dropped))


def orchestrate_turn(context: TurnContext) -> TurnReply:
    """Route the normalized turn to the legacy brain for ``context.surface``.

    Adapter selection uses the surface the caller already knows — NO
    tenant resolution happens here (OR-BOT-3: no fake/default tenant, no
    unknown_tenant short-circuit). The per-tenant brain is a tenant-scoped
    operation and fails closed when no tenant is in scope; the global
    brain legitimately runs with ``tenant=None``.

    OR-SHADOW-1: the legacy reply is computed FIRST and is the only one
    that matters. When ``ORCHESTRATOR_SHADOW_ENABLED`` is on (default
    off), ONE shadow job is enqueued for async observe-only execution —
    a failure here never affects the legacy turn.
    """

    if context.surface == SURFACE_GLOBAL:
        reply = _global_legacy_adapter(context)
    else:
        reply = _per_tenant_legacy_adapter(context)

    from apps.orchestrator.shadow_turn import shadow_enabled

    if shadow_enabled():
        try:
            from apps.orchestrator.shadow_turn import dispatch_shadow_turn

            dispatch_shadow_turn(context, reply)
        except Exception:  # noqa: BLE001 — shadow must never break the turn
            logger.exception("orchestrator.turn_seam.shadow_dispatch_failed")

    return reply


def _per_tenant_legacy_adapter(context: TurnContext) -> TurnReply:
    """Per-tenant brain: ``apps.skills.registry.dispatch`` (MAX + Telegram)."""

    from apps.tenancy.context import current_tenant

    if current_tenant() is None:
        # Tenant-scoped operation (skills read commercial data) — fail
        # closed. Reaching here means a per-tenant caller ran outside
        # tenant_scope: a wiring bug, never the global pilot (it uses
        # surface="global").
        raise RuntimeError("turn_seam: per_tenant surface requires an active tenant_scope")

    from apps.skills.base import SkillContext
    from apps.skills.registry import dispatch

    result = dispatch(
        SkillContext(
            conversation=context.conversation,
            bot_user=context.bot_user,
            message_text=context.text,
            trace_id=context.trace_id,
            has_attachments=context.has_attachments,
        )
    )
    if result is None:
        return TurnReply(matched=False)
    # DRF-1419: перечисление ниже — контракт, а не догадка. Поле, которое
    # навесили на SkillResult и не провели сюда, отказывает ИМЕНЕМ.
    _guard_carried_fields(
        result,
        producer="SkillResult",
        carried=SKILL_RESULT_TO_TURN,
        not_carried=SKILL_RESULT_NOT_CARRIED,
        trace_id=context.trace_id,
    )
    return TurnReply(
        matched=True,
        reply_text=result.reply_text,
        action_type=result.action_type or "",
        action_data=result.action_data,
        should_send=result.should_send,
        should_handoff=result.should_handoff,
        handoff_reason=result.handoff_reason or "",
        new_state=result.new_state,
        should_close_conversation=result.should_close_conversation,
        meta=result.meta,
        confidence=result.confidence,
    )


def _global_legacy_adapter(context: TurnContext) -> TurnReply:
    """Global tenant-less brain: ``apps.orchestrator.concierge`` concierge turn.

    ``tenant=None`` is the designed input (OR-BOT-3) — no tenant check,
    no sentinel fabrication, the concierge brain is unchanged.
    """

    from apps.orchestrator.concierge import generate_concierge_reply

    reply = generate_concierge_reply(
        context.text,
        bot_user=context.bot_user,
        conversation=context.conversation,
        user_message_id=context.user_message_id,
        memory_block=context.memory_block,
        nutrition_block=context.nutrition_block,
        extra_system=context.extra_system,
        trace_id=context.trace_id or None,
    )
    # DRF-1419: та же проверка на другой стороне шва — новое поле
    # DiscoveryReply не доезжает ШУМНО, а не молча.
    _guard_carried_fields(
        reply,
        producer="DiscoveryReply",
        carried=DISCOVERY_TO_TURN,
        not_carried=DISCOVERY_NOT_CARRIED,
        trace_id=context.trace_id,
    )
    return TurnReply(
        matched=True,
        reply_text=reply.text,
        action_data=reply.action_data,
        assistant_persisted=reply.persisted,
        # getattr, а не атрибут: шов — переносчик, и он не вправе требовать
        # от мозга поля, которого у прежних производителей ответа не было.
        # Умолчание False означает «сбоя связи не заявлено» — ровно то же
        # поведение, что до DRF-1348.
        outage=bool(getattr(reply, "outage", False)),
        # getattr по той же причине, что и outage выше: шов — переносчик,
        # и прежние производители ответа поля tool_trace не знают (DRF-1385).
        tool_trace=getattr(reply, "tool_trace", None),
    )


def turn_reply_to_skill_result(reply: TurnReply) -> Any:
    """Rebuild the legacy ``SkillResult`` (or None) for downstream code that
    already consumes it (handoff helper, reply-kind analytics, silence log).

    Inverse of the per-tenant adapter — keeps every post-seam code path
    byte-identical to the pre-seam direct dispatch.
    """

    if not reply.matched:
        return None
    from apps.skills.base import SkillResult

    return SkillResult(
        reply_text=reply.reply_text,
        action_type=reply.action_type,
        action_data=reply.action_data,
        should_send=reply.should_send,
        should_close_conversation=reply.should_close_conversation,
        new_state=reply.new_state,
        should_handoff=reply.should_handoff,
        handoff_reason=reply.handoff_reason,
        # SkillResult.meta is a required dict on dev (default_factory=dict);
        # TurnReply keeps None as "no meta" — normalise at the boundary.
        meta=reply.meta or {},
        confidence=reply.confidence,
    )
