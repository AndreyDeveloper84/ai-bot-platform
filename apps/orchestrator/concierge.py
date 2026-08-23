"""Global concierge — ayla-ai-core AIConcierge wiring (W5 / DRF-241 / pilot 2026-08-15).

The nationwide (tenant-less) concierge DM now runs on
:class:`ayla_ai_core.AIConcierge` instead of the hand-rolled completion in
:func:`apps.orchestrator.discovery.generate_discovery_reply`:

- **LLM client** — ``RouterLLMClient`` proxies ``chat.completions.create``
  onto ``apps.llm.router`` (tenant-less tier), so PII tokenization, cost
  tracking, retry and provider config stay on the platform runtime (152-ФЗ
  §6). The response is reshaped into the OpenAI duck-type AIConcierge parses.
- **ConversationStore** — ``GlobalConversationStore`` adapts the
  ``*_global_*`` conversation services (sentinel-scoped, ``current_tenant()``
  stays ``None``). The user turn is persisted by the channel handler BEFORE
  the concierge runs (all reply branches share that record), so
  ``save_message(role="user")`` is a no-op marker carrying the
  handler-recorded id for history exclusion; assistant turns persist here.
- **Tool dispatch** — DRF-241 hook. The dispatcher is pure validation +
  argument normalisation (no I/O, matching ai-core's "handlers are
  side-effect-free" contract); the sanctioned marketplace carve-outs
  (:func:`apps.marketplace.discovery.discover_masters` and, since DRF-1304,
  ``discover_salons`` / ``discover_services``) execute in the wrapper's SYNC
  scope after ``asyncio.run`` returns, so no sync-ORM call ever lands inside
  the event loop.
- **Prompt** — :func:`build_concierge_system_prompt` composes the frozen
  ``AYLA_MARKETPLACE_VOICE`` with the current-date grounding block
  (DRF-988), the boundary rules (no-sales, helpful
  restraint, S8 medical boundary — Constitution Art. X/XII, Journey Spec
  Stage 8) and the consent-gated ai-core memory block (W5 task 2).

The booking handoff (``cb:discover:book:{tenant}:{master}`` → per-tenant
BookingSkill → Ayla REST) is unchanged — this module owns only the
discovery dialog turn.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass
from datetime import date
from types import SimpleNamespace
from typing import Any

from ayla_ai_core import ActionType, AIConcierge, ToolResult
from asgiref.sync import sync_to_async
from django.conf import settings
from django.utils import timezone

from apps.conversations.services import (
    record_global_message,
    resolve_active_global_conversation,
)
from apps.identity.services.global_tenant import get_global_bot_tenant
from apps.llm.pricing import UnknownModelError, compute_cost
from apps.llm.router import get_router
from apps.marketplace.discovery import discover_masters
from apps.observability.ai_metrics import record_ai_request
from apps.observability.models import AIRequestMetric
from apps.orchestrator.discovery import (
    _MAX_MASTER_CARDS,
    _MAX_REPLY_CHARS,
    ASK_CLARIFICATION_TOOL_SPEC,
    CATALOG_TOOL_ACTIONS,
    SHOW_MASTERS_TOOL_SPEC,
    SHOW_SALONS_TOOL_SPEC,
    SHOW_SERVICES_TOOL_SPEC,
    DiscoveryReply,
    _discovery_voice_fields,
    _render_ask_clarification,
    _render_master_cards,
    execute_catalog_tool,
    has_discovery_criteria,
    render_no_criteria_clarification,
)
from apps.orchestrator.llm.templates import get_fallback
from apps.orchestrator.nutrition_global import (
    NUTRITION_TOOL_ACTIONS,
    NUTRITION_TOOL_SPECS,
    execute_nutrition_tool,
)
from apps.persona.voice import SURFACE_MARKETPLACE, assistant_identity

logger = logging.getLogger(__name__)

# Provider-routing tier slug — reuse the discovery tier so operator
# overrides (SKILL_LLM_PROVIDER["discovery"]) keep working unchanged.
CONCIERGE_SKILL = "discovery"

# Russian weekday names for the date-grounding block (DRF-988) — weekday()
# indexed, locale-independent (``strftime("%A")`` follows the OS locale).
_WEEKDAYS_RU = ("понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье")

# CandidateContext.tenant_id is mandatory non-empty in ai-core (v0.7.0).
# The concierge is tenant-less by design; this sentinel is a log/scope
# label only — no tenant-scoped read is ever made from it.
GLOBAL_TENANT_ID = "ayla-global"


@dataclass(frozen=True)
class ConciergeContext:
    """Minimal ``CandidateContext`` for the concierge.

    No candidates are pre-injected: master discovery happens on demand via
    the ``show_masters`` tool. The shape satisfies ai-core's structural
    Protocol (candidates / candidate_ids / summary_text / tenant_id).
    """

    candidates: list[Any]
    candidate_ids: frozenset
    summary_text: str
    tenant_id: str


def _to_openai_shape(result: Any) -> Any:
    """Reshape an ``apps.llm.protocol.CompletionResult`` into the OpenAI
    duck-type ``AIConcierge._parse_completion`` reads (choices[0].message
    with content + tool_calls[].function.{name,arguments}, usage tokens).
    """
    tool_calls = [
        SimpleNamespace(
            id=tc.id or "",
            type="function",
            function=SimpleNamespace(name=tc.name, arguments=json.dumps(tc.arguments or {})),
        )
        for tc in (result.tool_calls or [])
    ]
    message = SimpleNamespace(
        content=result.text or "",
        tool_calls=tool_calls or None,
    )
    usage = SimpleNamespace(
        prompt_tokens=result.prompt_tokens or 0,
        completion_tokens=result.completion_tokens or 0,
    )
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message, finish_reason=result.finish_reason or "")],
        usage=usage,
    )


# -- DRF-1286 - "promised and never called the tool" -------------------
#
# The concierge advertises exactly two tools (show_masters,
# ask_clarification) and its prompt pushes warm conversational prose. That
# combination produces a specific silent failure: the model writes
# "сейчас подберу вам мастеров" / "секундочку" and emits NO tool_call. The
# turn looks successful - text was produced, tokens were billed, latency
# was normal - but nothing happened, and the client waits for an action
# that will never come.
#
# This is the failure the DRF-1102 audit named as the reason for the
# regex short-circuit (:func:`generate_direct_show_masters_reply`: the
# model "would loop re-asking instead of ever calling it"). The bypass
# hides the symptom; this detector plus one forced-tool pass removes the
# cause.
#
# Detection is stem-based on the reply text, ported from the legacy bot
# (``legacy_maxbot/ai_concierge.py:163-196``), because Russian verbs
# inflect and no LLM-side signal distinguishes the case: ``finish_reason``
# is a plain "stop", the text is well-formed, and ``tool_calls`` is simply
# absent. The only evidence is the promise itself.
#
# NOTE on evidence: this stem list is inherited, NOT measured — there is
# no recorded concierge transcript in this repo to tune it against (the
# `replay` recorder writes ReplayTrace rows only on the live path, and the
# DRF-1102 audit these comments cite is not a committed document). Treat
# the list as a first cut and re-tune it from the
# `orchestrator.concierge.promise_without_tool` WARN + the
# `fallback_triggered` metric rows once the pilot has produced some.
#
# Deliberately NARROWER than the legacy list. Legacy matched bare stems
# ("подбер", "посмотр", "рассмотр"), which also fire on imperatives
# aimed at the CLIENT ("подберите удобное время", "посмотрите
# профиль") - and a false positive is not free: it forces a tool
# call onto a turn the model answered correctly in words. We therefore
# match first-person commitments and the wait-markers, both of which only
# make sense when the assistant is about to act itself.
_PROMISE_STEMS: tuple[str, ...] = (
    # first-person commitment to act
    "подберу",
    "подберем",
    "подберём",
    "подбираю",
    "подбираем",
    "посмотрю",
    "посмотрим",
    "гляну",
    "глянем",
    "уточню",
    "уточним",
    "найду",
    "поищу",
    "покажу",
    "покажем",
    "проверю",
    "проверим",
    "помогу подобрать",
    "помогу выбрать",
    # explicit wait - an assistant that asks the client to wait without
    # emitting a tool call is ALWAYS a bug: nothing is running.
    "секундочк",
    "минуточк",
    "минутку",
    "одну минут",
    "одну секунд",
    "подождит",
    "подожди",
    # "вот варианты" / "вот кто подойдёт" - announces a result
    # that, without a tool call, does not exist.
    "вот вариант",
    "вот кто",
    "вот подходящ",
    # joint-action framing of the same promise
    "давайте подбер",
    "давай подбер",
    "давайте уточн",
    "давай уточн",
    # DRF-1268 — the gate itself is tool-agnostic (it fires on "the model
    # called NO tool", not on a list of action types), but this LEXICON was
    # tuned on master-search vocabulary and missed "записываю 200 мл воды"
    # entirely. Recording verbs are the promise form the nutrition tools
    # (log_water, clarify_food_entry, start_nutrition_anketa,
    # health_screening) attract, so they belong here too.
    "запишу",
    "запишем",
    "записываю",
    "сохраню",
    "сохраним",
    "сохраняю",
    "зафиксирую",
    "зафиксируем",
    "оформлю",
    "оформим",
    "заполню",
    "заполним",
    "заведу",
    # Deliberately NOT here: "добавлю" / "отмечу". Both are ordinary Russian
    # discourse markers ("Добавлю, что цены могут отличаться") and would fire
    # on turns the model answered correctly in words — the same false-positive
    # cost that made this list narrower than the legacy one.
)


def _looks_like_promise_without_tool(content: str | None) -> bool:
    """True when assistant prose promises an action it never triggered.

    Substring match on lowercased text - stems, not whole words, so
    "подберу"/"подберём" and "секундочку"/"секундочка" all hit
    without a morphology dependency. Called ONLY when ``tool_calls`` is
    empty, so a reply that both promises AND calls the tool never reaches
    it.
    """
    if not content:
        return False
    text = content.lower()
    return any(stem in text for stem in _PROMISE_STEMS)


@dataclass(frozen=True)
class ForcedToolRetry:
    """Usage of the EXTRA, discarded LLM call in a forced retry (DRF-1286).

    A forced retry always makes two calls and uses one of them. Whichever
    answer loses, its tokens were still billed — so they get their own
    ``AIRequestMetric`` row instead of being folded into the winner's,
    which would make the feature read as free.

    Which call is the discarded one depends on the outcome, so this
    deliberately does NOT hard-code "the first attempt":

    * the forced pass produced a tool call → we use it, the promise
      attempt is discarded;
    * the forced pass produced no tool call (provider ignored the
      constraint) → we keep the promise attempt, the forced pass is the
      discarded one.
    """

    prompt_tokens: int
    completion_tokens: int
    latency_ms: int


class _RouterCompletions:
    """``chat.completions`` facade routed through ``apps.llm.router``.

    ``get_provider`` is sync and emits an audit row (sync ORM), so it is
    wrapped in ``sync_to_async`` — same pattern as
    ``apps.orchestrator.intent_router._classify_production_path``.
    """

    def __init__(self, *, skill: str) -> None:
        self._skill = skill
        # Telemetry of the most recent complete() call (DRF-1211). The
        # router's CompletionResult carries the RESOLVED provider/model;
        # the ai-core DTO does not surface them truthfully (dto.model is
        # ai-core's configured default, dto.provider the passthrough
        # adapter's constant) — so the metric reader picks them up here.
        self.last_provider = ""
        self.last_model = ""
        # DRF-1286 - promise-without-tool retry. Armed per pass by
        # `generate_concierge_reply`: forcing a tool call is only correct
        # while a tool call is still a legitimate outcome. On the
        # DRF-1266 follow-up pass the prompt explicitly says "инструмент
        # повторно не вызывай", so arming there would fight it.
        self.force_tool_retry_armed = False
        self._forced_retry: ForcedToolRetry | None = None

    def take_forced_retry(self) -> "ForcedToolRetry | None":
        """Pop the last pass's forced-retry telemetry (read-and-clear).

        Read-and-clear so a retry on pass 1 can never be re-counted
        against pass 2's metric row.
        """
        info, self._forced_retry = self._forced_retry, None
        return info

    async def create(
        self,
        *,
        model: str,
        messages: list[dict],
        tools: list[dict] | None = None,
        **_: Any,
    ) -> Any:
        def _resolve() -> Any:
            return get_router().get_provider(None, skill=self._skill, op="complete")

        provider = await sync_to_async(_resolve, thread_sensitive=False)()
        started = time.monotonic()
        result = await provider.complete(messages, model=model, tools=tools)

        # DRF-1286 - one forced-tool pass, never a loop. The retry calls
        # `provider.complete` DIRECTLY rather than re-entering `create`,
        # so a second promise-without-tool answer structurally cannot
        # trigger a third call: a model that ignored `tool_choice` twice
        # will ignore it again, and the tokens would triple.
        if (
            self.force_tool_retry_armed
            and tools
            and not result.tool_calls
            and _looks_like_promise_without_tool(result.text)
        ):
            first_latency_ms = int((time.monotonic() - started) * 1000)
            logger.warning(
                "orchestrator.concierge.promise_without_tool provider=%s "
                "model=%s content=%r — retrying with tool_choice=required",
                result.provider,
                result.model,
                (result.text or "")[:120],
            )
            retry_started = time.monotonic()
            try:
                forced = await provider.complete(
                    messages, model=model, tools=tools, tool_choice="required"
                )
            except Exception as exc:  # noqa: BLE001 — keep the first answer
                # The retry is an optimisation, not the turn. A failure
                # here must not cost the client the reply we already have.
                # No metric row either: nothing was billed.
                logger.warning("orchestrator.concierge.forced_tool_retry_failed err=%s", exc)
            else:
                retry_latency_ms = int((time.monotonic() - retry_started) * 1000)
                if forced.tool_calls:
                    discarded, discarded_latency_ms = result, first_latency_ms
                    result = forced
                else:
                    # Provider honoured the call but not the constraint (or
                    # has no forced mode at all). Keeping the first answer
                    # is strictly better than shipping a second tool-less
                    # reply — the FORCED pass is what gets discarded here,
                    # and it is its tokens that must be accounted for.
                    logger.warning(
                        "orchestrator.concierge.forced_tool_retry_no_tool_call "
                        "provider=%s model=%s — provider ignored "
                        "tool_choice=required",
                        forced.provider,
                        forced.model,
                    )
                    discarded, discarded_latency_ms = forced, retry_latency_ms
                self._forced_retry = ForcedToolRetry(
                    prompt_tokens=discarded.prompt_tokens or 0,
                    completion_tokens=discarded.completion_tokens or 0,
                    latency_ms=discarded_latency_ms,
                )

        self.last_provider = result.provider or ""
        self.last_model = result.model or ""
        return _to_openai_shape(result)


class RouterLLMClient:
    """AsyncOpenAI-shaped client over the platform LLM router (tenant-less)."""

    def __init__(self, *, skill: str) -> None:
        self.chat = SimpleNamespace(completions=_RouterCompletions(skill=skill))

    @property
    def last_provider(self) -> str:
        """Provider slug of the most recent completion (DRF-1211 telemetry)."""
        return self.chat.completions.last_provider

    @property
    def last_model(self) -> str:
        """Vendor-resolved model id of the most recent completion."""
        return self.chat.completions.last_model

    def arm_forced_tool_retry(self, armed: bool) -> None:
        """Enable/disable the DRF-1286 promise-without-tool retry.

        Per-pass, not per-client: see
        :meth:`_RouterCompletions.create` for why forcing a tool call is
        only correct while a tool call is still a legitimate outcome.
        """
        self.chat.completions.force_tool_retry_armed = armed

    def take_forced_retry(self) -> "ForcedToolRetry | None":
        """Pop telemetry of a forced retry made during the last pass."""
        return self.chat.completions.take_forced_retry()


class GlobalConversationStore:
    """``ayla_ai_core.ConversationStore`` over the ``*_global_*`` services.

    ``record_global_message`` carries no ``action_data`` / ``tool_call`` /
    ``tool_call_id`` kwargs (unlike its per-tenant sibling): those payloads
    are transient render data for the channel adapter, so the adapter
    accepts and drops them — persisted fields match today's global path
    exactly (content, action_type, token telemetry).
    """

    def __init__(self, *, user_message_id: Any = None) -> None:
        self._user_message_id = user_message_id

    def resolve_active_conversation(self, bot_user: Any) -> Any:
        return resolve_active_global_conversation(bot_user)

    def save_message(
        self,
        conversation: Any,
        *,
        role: str,
        content: str,
        action_type: str = "",
        action_data: dict | None = None,
        tool_call: dict | None = None,
        tool_call_id: str = "",
        tokens_in: int = 0,
        tokens_out: int = 0,
        latency_ms: int | None = None,
    ) -> Any:
        if role == "user":
            # Persisted upstream by the channel handler — return the marker
            # so AIConcierge can exclude this turn from LLM history.
            return SimpleNamespace(id=self._user_message_id)
        return record_global_message(
            conversation,
            role=role,
            content=content,
            rendered_text=content,
            action_type=action_type or "",
            tokens_in=tokens_in or 0,
            tokens_out=tokens_out or 0,
            latency_ms=latency_ms,
        )

    def load_recent_history(
        self,
        conversation: Any,
        *,
        exclude_id: Any | None = None,
        limit: int = 10,
    ) -> list[Any]:
        from apps.conversations.models import Message

        qs = Message.all_tenants.filter(conversation=conversation).order_by("-created_at")
        if exclude_id is not None:
            qs = qs.exclude(id=exclude_id)
        # Last N (DESC) reversed → chronological, per the Protocol contract.
        return list(reversed(qs[:limit]))


_KNOWN_TOOLS = frozenset(
    {SHOW_MASTERS_TOOL_SPEC["name"], ASK_CLARIFICATION_TOOL_SPEC["name"]}
    | NUTRITION_TOOL_ACTIONS
    | CATALOG_TOOL_ACTIONS
)


def _record_concierge_metric(
    *,
    bot_user: Any,
    conversation: Any,
    trace_id: str | None,
    message_text: str,
    pass_index: int | None,
    outcome: str,
    latency_total_ms: int,
    dto: Any = None,
    llm_client: "RouterLLMClient | None" = None,
    skill_selected: str = "concierge",
    fallback_triggered: bool = False,
) -> None:
    """DRF-1211 — emit one ``AIRequestMetric`` row per concierge LLM pass.

    The live global-pilot path (``generate_concierge_reply``) never wrote
    ``AIRequestMetric`` at all — the only writers were the dead tenant
    pipeline and the shadow turn. Multi-pass (DRF-1266) doubles-triples
    model calls per turn; without this row its cost would surface in the
    invoice, not in data. ``llm_pass_index`` separates the first call from
    follow-up passes so the cost of multi-pass is distinguishable from
    general traffic growth.

    DRF-1286 keeps that meaning intact rather than redefining it: the
    field counts LLM CALLS within the turn (its documented contract), and
    a forced-tool retry is one more such call, so it takes the next index
    and pushes any later multi-pass call along. What tells the two apart
    is ``fallback_triggered=True``, set on the DISCARDED
    promise-without-tool attempt. Among ``skill_selected='concierge'``
    rows that flag has exactly one meaning — the model promised an action
    and never called the tool. (DRF-1283's deterministic branch also sets
    it, but under ``skill_selected='concierge_direct'`` with a NULL
    ``llm_pass_index``, so the two never mix in one filter.) So::

        -- how often the model promises and never calls the tool
        WHERE skill_selected='concierge' AND fallback_triggered

        -- what those turns cost (discarded attempt + its forced retry)
        ... plus the row at llm_pass_index+1 of the same request_id

    «дорого» and «часто ошибается» read off separate columns.

    The metric row parks under the ``global_bot`` sentinel tenant — the
    same tenant that owns the global BotUser / Conversation rows — at
    ``current_tenant()=None``, exactly like the ``*_global_*`` services.

    DRF-1283 widens this past LLM passes. The deterministic show-masters
    branch (:func:`generate_direct_show_masters_reply`) answers the single
    most common booking turn WITHOUT calling a model, and wrote nothing here
    — so the busiest path in the funnel was missing from the very table the
    pilot thresholds are computed from. That is not a cost under-count (the
    turn genuinely cost nothing); it is a DENOMINATOR under-count, and it
    biases every per-request threshold — Cost per Request, Latency p95,
    Fallback Rate — by silently dropping the cheapest, fastest turns out of
    the sample. «What share of turns needs the model at all» was not
    answerable at all.

    Such a row carries ``llm_pass_index=None`` and leaves every LLM column at
    its no-call value — NULL tokens / NULL cost / empty provider, NOT zeros.
    The schema already defines that shape («NULL when no LLM call (cached /
    non-LLM skill path)» — see the model's help_text), so a non-LLM row is
    something the table was built to hold rather than something bolted on.
    Zeros would have been the wrong encoding: they would drag AVG(cost) and
    AVG(tokens) toward zero, replacing an under-count with a distortion.
    ``skill_selected`` separates the two writers so either can be isolated.

    Exactly one row is written per turn per path: the deterministic branch
    records only when it ANSWERS the turn. When it finds nobody it hands the
    turn to the concierge (DRF-1283) and stays silent, so the model's own
    rows are not double-counted against the same inbound message.

    Best-effort, mirroring ``pipeline._emit_ai_metric``: observability
    must never crash the turn — failures log WARN with trace_id.
    """
    try:
        try:
            request_uuid = uuid.UUID(str(trace_id))
        except (ValueError, TypeError, AttributeError):
            # Same deterministic fallback as pipeline._emit_ai_metric: keeps
            # log grep (raw trace_id string) and the metric row correlated
            # even for non-UUID trace ids.
            request_uuid = uuid.uuid5(
                uuid.NAMESPACE_DNS, str(trace_id) if trace_id else "concierge-no-trace"
            )

        llm_provider = ""
        llm_model = ""
        tokens_in: int | None = None
        tokens_out: int | None = None
        latency_llm_ms: int | None = None
        cost_usd = None
        if dto is not None:
            tokens_in = dto.tokens_in or None
            tokens_out = dto.tokens_out or None
            # Explicit None check — a sub-millisecond call legitimately
            # measures 0, and 0 must not become NULL («no LLM call»).
            latency_llm_ms = dto.latency_ms if dto.latency_ms is not None else None
        if llm_client is not None:
            llm_provider = llm_client.last_provider
            llm_model = llm_client.last_model
        if llm_model and tokens_in is not None:
            try:
                cost_usd = compute_cost(
                    llm_model, input_tokens=tokens_in, output_tokens=tokens_out or 0
                )
            except UnknownModelError:
                # Unpriced model — tokens + latency still recorded, cost NULL.
                cost_usd = None

        record_ai_request(
            tenant=get_global_bot_tenant(),
            bot_user=bot_user,
            conversation=conversation,
            request_id=request_uuid,
            message_text_length=len(message_text),
            skill_selected=skill_selected,
            fallback_triggered=fallback_triggered,
            latency_total_ms=latency_total_ms,
            latency_llm_ms=latency_llm_ms,
            llm_provider=llm_provider,
            llm_model=llm_model,
            llm_tokens_input=tokens_in,
            llm_tokens_output=tokens_out,
            llm_cost_usd=cost_usd,
            llm_pass_index=pass_index,
            outcome=outcome,
        )
    except Exception as emit_exc:  # noqa: BLE001 — observability never crashes the turn
        logger.warning(
            "orchestrator.concierge.ai_metric_emit_failed trace=%s outcome=%s err=%s",
            trace_id,
            outcome,
            emit_exc,
        )


def _dispatch_tool(tool_call: Any, context: Any) -> ToolResult:
    """DRF-241 dispatcher — pure validation + argument normalisation.

    No I/O: the marketplace carve-out executes in the wrapper's sync scope
    (see module docstring). Unknown tools and malformed arguments degrade
    to ``ask_clarification`` (question-less — ``generate_concierge_reply``
    treats that as an internal failure and falls back to the safe line, same
    as before this tool existed) so the concierge rephrases instead of
    crashing. A genuine ``ask_clarification`` call (DRF-1102) carries the
    model's own question + options through, distinct from that degrade path.
    """
    name = getattr(tool_call.function, "name", "")
    if name not in _KNOWN_TOOLS:
        return ToolResult(
            action_type=ActionType.ASK_CLARIFICATION,
            action_data={"reason": f"unknown_tool:{name}"},
        )
    raw = getattr(tool_call.function, "arguments", "") or "{}"
    try:
        args = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return ToolResult(
            action_type=ActionType.ASK_CLARIFICATION,
            action_data={"reason": "malformed_arguments"},
        )
    if not isinstance(args, dict):
        args = {}
    if name == ASK_CLARIFICATION_TOOL_SPEC["name"]:
        return ToolResult(
            action_type=ActionType.ASK_CLARIFICATION,
            action_data={
                "question": args.get("question", ""),
                "options": args.get("options") or [],
            },
        )
    if name in NUTRITION_TOOL_ACTIONS:
        # DRF-1268 — selection only. The skill executes in the wrapper's
        # sync scope after asyncio.run returns (same shape as show_masters).
        return ToolResult(action_type=name, action_data={"arguments": args})
    if name in CATALOG_TOOL_ACTIONS:
        # DRF-1304 — same selection-only shape: the marketplace read and the
        # deterministic render run in the wrapper's sync scope.
        return ToolResult(action_type=name, action_data={"arguments": args})
    return ToolResult(
        action_type=ActionType.SHOW_MASTERS,
        action_data={"arguments": args},
    )


def build_concierge_system_prompt(
    *,
    memory_block: str = "",
    nutrition_block: str = "",
    extra_system: str = "",
    today: date | None = None,
) -> str:
    """Compose the concierge system prompt.

    Frozen ``AYLA_MARKETPLACE_VOICE`` fields (consumed, never modified) +
    discovery framing + boundary rules (no-sales, helpful restraint, S8
    medical boundary) + optional consent-gated memory block (W5 task 2)
    + optional consent-gated weekly nutrition picture (DRF-1284).

    ``nutrition_block`` lands AFTER the boundary rules on purpose: the
    medical boundary must already be established when the model first
    sees the client's protein numbers, not argued afterwards.

    ``today`` grounds the model in the current date (DRF-988): without it
    the model lives at its training cutoff and rejects real near-future
    booking dates (e.g. август 2026) as «далёкое будущее». Defaults to the
    Django clock (``timezone.localdate()``, same as the legacy concierge);
    tests pass an explicit date for determinism.
    """
    if today is None:
        today = timezone.localdate()
    voice = _discovery_voice_fields()
    # The NAME comes from the surface table (apps.persona.voice), not from
    # the raw frozen dict: `_SURFACE_NAMES` is what makes renaming a surface
    # one product decision in one file (#1226). Reading `assistant_name`
    # here would have quietly ignored a marketplace override — the same
    # drift that put three different names in three files before. Same word
    # today; what changes is that it can no longer diverge in silence.
    identity = assistant_identity(SURFACE_MARKETPLACE)
    parts = [
        f"Ты — {identity.name}, AI-помощник «{voice['business_name']}».",
        # DRF-988 — date grounding (date + weekday + timezone).
        f"Сегодня: {today.isoformat()} ({_WEEKDAYS_RU[today.weekday()]}), "
        f"часовой пояс {timezone.get_current_timezone()}. Используй эту дату "
        "для парсинга относительных («завтра», «послезавтра») и конкретных "
        "дат записи.",
        "Ты помогаешь клиенту по всей стране подобрать подходящего "
        f"{voice['domain']}-мастера и записаться — конкретный салон выбирается "
        "только в момент записи.",
        "Это разговор-знакомство (discovery): отвечай тепло и кратко, "
        "задавай уточняющие вопросы про услугу, город и предпочтения. "
        # DRF-1304 — salons/prices/addresses exist now, behind the tools.
        # The boundary that survives is the older half of this sentence:
        # never INVENT them. Naming them from a tool result is the job.
        "Салон, цену или адрес называй ТОЛЬКО из ответов инструментов — "
        "ничего не выдумывай; нет данных в ответе — честно скажи, что нет.",
        # DRF-1102 — the tool exists now (see tool_definitions); without this
        # line the model has no reason to prefer it over the plain-text habit
        # the rest of this prompt otherwise establishes.
        "Если нужно уточнение — вызывай инструмент ask_clarification с "
        "вариантами ответа, а НЕ пиши уточняющий вопрос обычным текстом: "
        "так клиент отвечает одним тапом, а не гадает формулировку.",
        # DRF-1304 — the salon/service tools exist now (tool_definitions).
        "Инструменты каталога:\n"
        "- Вопрос про салоны или адреса («какие салоны у вас есть», «где вы "
        "находитесь», «куда можно прийти») — вызывай show_salons (город "
        "необязателен).\n"
        "- Вопрос про услуги, цены, длительность («какие услуги в салоне», "
        "«что есть по лицу», «сколько стоит массаж») — вызывай show_services "
        "с фильтром: салон, город или запрос.\n"
        "- Подбор конкретного мастера — show_masters, как раньше.",
        # DRF-1268 — the nutrition tools exist now (tool_definitions). The
        # load-bearing registry order of apps/skills/apps.py is restated
        # here as model-facing priority: the reasons for that order do not
        # disappear with the transfer, they become prompt requirements.
        "Инструменты питания (приоритет обязателен):\n"
        "- Жалоба на боль или симптомы («болит спина», «онемела рука») — "
        "вызывай health_screening ПЕРВЫМ, раньше любых других инструментов "
        "и раньше show_masters.\n"
        "- Напиток («стакан воды», «кофе 200 мл») — только log_water, "
        "никогда не clarify_food_entry.\n"
        "- Короткий текст про еду («борщ 300г») — clarify_food_entry.\n"
        "- Просьба заполнить или продолжить анкету питания — "
        "start_nutrition_anketa.",
        f"Если вопрос не про запись к мастеру — мягко верни в тему: "
        f"«{voice['off_topic_redirect']}»",
        # Boundaries (W5 task 4) — Constitution Art. X (helpful restraint),
        # Art. XII (competence boundary), Journey Spec Stage 8.
        "Границы (обязательно):\n"
        "- Ты не продаёшь: никаких акций, скидочного давления, «успей "
        "записаться», каталог-перечислений. Предлагаешь только то, что "
        "отвечает запросу клиента.\n"
        "- Иногда лучший ответ — ничего не предлагать. Если запроса нет "
        "или тема исчерпана, не выдумывай предложений.\n"
        "- Медицинские темы (диагнозы, лекарства, боль, травмы, опасные "
        "цели похудения): остановись, коротко назови границу без диагноза "
        "(«я не врач и не оцениваю здоровье»), спокойно обозначь риск "
        "простыми словами и предложи безопасный шаг — обратиться к "
        "профильному специалисту или сформулировать новое безопасное "
        "намерение. Не сохраняй медицинские выводы как факт о клиенте.",
        f"Ответ не длиннее {_MAX_REPLY_CHARS} символов.",
    ]
    if memory_block:
        parts.append(memory_block)
    if nutrition_block:
        parts.append(nutrition_block)
    if extra_system:
        parts.append(extra_system)
    return "\n\n".join(parts)


def _max_llm_passes() -> int:
    """Pass cap for the multi-pass concierge (DRF-1266).

    ``settings.CONCIERGE_MAX_LLM_PASSES``, default 2 (primary call + one
    tool-data pass), clamped to >= 1 so a misconfigured env can never mean
    «call the model zero times» or loop unbounded on live traffic.
    """
    try:
        return max(1, int(getattr(settings, "CONCIERGE_MAX_LLM_PASSES", 2)))
    except (TypeError, ValueError):
        return 2


def _build_tool_result_message(user_text: str, cards: list[Any], args: dict[str, Any]) -> str:
    """Second-pass input: the executed ``show_masters`` result as plain text.

    Deliberately a plain user-role message, NOT the OpenAI/Anthropic
    tool-result protocol: the Anthropic adapter in ayla-ai-core
    (``providers/anthropic.py``) does not assemble ``role="tool"`` blocks,
    so a classic tool loop would work on OpenAI only and silently break
    when an operator flips ``SKILL_LLM_PROVIDER`` — a decision of the
    operator, not the developer. A plain message rides the same path on
    every provider.

    The original user question is embedded because the ai-core store
    excludes the handler-persisted user turn from LLM history on every
    pass (it expects the turn's text to arrive as ``message_text``).
    """
    lines = [
        f"Клиент спросил: «{user_text}».",
        f"Инструмент show_masters (город: {args.get('city') or '—'}, "
        f"услуга: {args.get('specialization') or '—'}) вернул мастеров: {len(cards)}.",
    ]
    for card in cards[:_MAX_MASTER_CARDS]:
        parts = [str(card.name)]
        if getattr(card, "specialization", ""):
            parts.append(str(card.specialization))
        if getattr(card, "service_name", ""):
            parts.append(str(card.service_name))
        rating = getattr(card, "rating", None)
        if rating is not None and rating >= 1:
            parts.append(f"★ {rating}")
        if getattr(card, "city", ""):
            parts.append(str(card.city))
        lines.append("- " + ", ".join(parts))
    if not cards:
        lines.append("(по этому запросу никого не нашлось)")
        # DRF-1283 — the refusal is a distinct instruction, not a clause
        # tacked onto the happy path. The old one ended «предложи уточнить
        # город или услугу», which the model dutifully said back to a client
        # who had just named both, and «уточните город или услугу» in answer
        # to «покажи массажистов в пензе» reads as «я вас не понял». Name what
        # WAS understood; ask only for what was not given.
        lines.append(
            "Список пуст. Ответь честно и коротко: покажи, что запрос ПОНЯТ — "
            "назови своими словами услугу и город, о которых спросил клиент, — "
            "и скажи, что именно такого у наших мастеров сейчас нет. НЕ проси "
            "уточнить то, что клиент уже назвал: если и услуга, и город "
            "названы, предложи другую услугу или другой город. Ничего не "
            "выдумывай и не обещай перезвонить. Инструмент show_masters "
            "повторно не вызывай — данных достаточно."
        )
        return "\n".join(lines)
    lines.append(
        "Ответь клиенту словами, опираясь ТОЛЬКО на эти данные: коротко "
        "перечисли подходящих мастеров и предложи записаться. Ничего не "
        "выдумывай. Инструмент show_masters повторно не вызывай — данных "
        "достаточно."
    )
    return "\n".join(lines)


def generate_concierge_reply(
    message_text: str,
    *,
    bot_user: Any,
    conversation: Any,
    user_message_id: Any = None,
    memory_block: str = "",
    nutrition_block: str = "",
    extra_system: str = "",
    trace_id: str | None = None,
) -> DiscoveryReply:
    """One concierge turn through ayla-ai-core AIConcierge (W5 / DRF-241).

    Multi-pass (DRF-1266): when the model calls ``show_masters`` and the
    pass budget allows, the executed tool's result is fed back as a plain
    user message (see :func:`_build_tool_result_message` for why not the
    tool protocol) and the model gets one more pass to phrase the answer
    in words. Budget exhausted with the model still calling the tool →
    the deterministic card render, i.e. exactly the pre-DRF-1266 reply.

    Forced-tool retry (DRF-1286): on the FIRST pass only — the one where
    a tool call is still a legitimate outcome — the client is armed to
    detect «the model promised an action and emitted no tool_call» and
    repeat that single call with ``tool_choice="required"``. Armed per
    pass rather than globally because the DRF-1266 follow-up pass tells
    the model NOT to call the tool again; forcing there would fight the
    prompt. At most one extra call per turn, and it gets its own metric
    row (see :func:`_record_concierge_metric`).

    Persists the assistant turn via the store (``persisted=True`` on the
    returned reply so the handler does NOT double-record). On any LLM
    failure degrades to the same safe fallback line as the legacy
    discovery path — the concierge must never 500.
    """
    llm_client = RouterLLMClient(skill=CONCIERGE_SKILL)
    concierge = AIConcierge(
        openai_client=llm_client,
        store=GlobalConversationStore(user_message_id=user_message_id),
        context_builder=lambda: ConciergeContext(
            candidates=[],
            candidate_ids=frozenset(),
            summary_text="",
            tenant_id=GLOBAL_TENANT_ID,
        ),
        tool_definitions=[
            SHOW_MASTERS_TOOL_SPEC,
            SHOW_SALONS_TOOL_SPEC,
            SHOW_SERVICES_TOOL_SPEC,
            ASK_CLARIFICATION_TOOL_SPEC,
            *NUTRITION_TOOL_SPECS,
        ],
        tool_dispatcher=_dispatch_tool,
    )

    def _renderer(_ctx: Any) -> str:
        return build_concierge_system_prompt(
            memory_block=memory_block,
            nutrition_block=nutrition_block,
            extra_system=extra_system,
        )

    max_passes = _max_llm_passes()
    pass_index = 0
    # DRF-1286 — index written to `llm_pass_index`. Tracked separately
    # from `pass_index` because a forced-tool retry is an extra LLM CALL
    # but NOT an extra multi-pass pass: it must not eat the DRF-1266
    # budget (that budget counts tool round-trips, not model calls).
    # Without a retry the two counters stay identical, so existing rows
    # are unchanged.
    llm_call_index = 0
    current_text = message_text
    # Cards of the most recent show_masters execution — kept so a failing or
    # budget-exhausted follow-up pass can still render real data instead of
    # the generic fallback line.
    pending_cards: list[Any] | None = None
    # The tool arguments behind ``pending_cards`` — so a degraded render can
    # still say WHAT was searched for (DRF-1283 / render_no_match).
    pending_args: dict[str, Any] = {}
    dto: Any = None
    while pass_index < max_passes:
        pass_index += 1
        llm_call_index += 1
        # DRF-1286 — arm only while a tool call is still the right answer.
        llm_client.arm_forced_tool_retry(pass_index == 1)
        started = time.monotonic()
        try:
            dto = asyncio.run(
                concierge.send_message(
                    user_key=bot_user,
                    message_text=current_text,
                    prompt_renderer=_renderer,
                )
            )
        except Exception as exc:  # noqa: BLE001 — degrade to safe fallback
            logger.warning(
                "orchestrator.concierge.llm_error trace=%s pass=%d err=%s",
                trace_id,
                pass_index,
                exc,
            )
            _record_concierge_metric(
                bot_user=bot_user,
                conversation=conversation,
                trace_id=trace_id,
                message_text=message_text,
                pass_index=llm_call_index,
                outcome=AIRequestMetric.OUTCOME_ERROR,
                latency_total_ms=int((time.monotonic() - started) * 1000),
                llm_client=llm_client,
            )
            if pending_cards is not None:
                # A follow-up pass failed AFTER the tool already returned
                # data — render the cards deterministically rather than the
                # generic fallback: the user asked for masters, we have them.
                rendered = _render_master_cards(
                    pending_cards[:_MAX_MASTER_CARDS],
                    city=pending_args.get("city"),
                    specialization=pending_args.get("specialization"),
                )
                return DiscoveryReply(
                    text=rendered.text,
                    action_data=rendered.action_data,
                    persisted=True,
                )
            return DiscoveryReply(text=get_fallback("ru"))
        elapsed_ms = int((time.monotonic() - started) * 1000)
        # DRF-1286 — a forced-tool retry happened inside this pass: two
        # LLM calls were billed and one answer was thrown away. Give the
        # discarded call its own row, then hand the next index to the
        # answer we actually used.
        forced_retry = llm_client.take_forced_retry()
        if forced_retry is not None:
            _record_concierge_metric(
                bot_user=bot_user,
                conversation=conversation,
                trace_id=trace_id,
                message_text=message_text,
                pass_index=llm_call_index,
                outcome=AIRequestMetric.OUTCOME_FALLBACK,
                fallback_triggered=True,
                latency_total_ms=forced_retry.latency_ms,
                dto=SimpleNamespace(
                    tokens_in=forced_retry.prompt_tokens,
                    tokens_out=forced_retry.completion_tokens,
                    latency_ms=forced_retry.latency_ms,
                ),
                llm_client=llm_client,
            )
            llm_call_index += 1
            # Never negative: both readings come from the same monotonic
            # clock, but clamp anyway rather than write a bogus p95 input.
            elapsed_ms = max(0, elapsed_ms - forced_retry.latency_ms)
        _record_concierge_metric(
            bot_user=bot_user,
            conversation=conversation,
            trace_id=trace_id,
            message_text=message_text,
            pass_index=llm_call_index,
            outcome=AIRequestMetric.OUTCOME_SUCCESS,
            latency_total_ms=elapsed_ms,
            dto=dto,
            llm_client=llm_client,
        )

        if dto.action_type != ActionType.SHOW_MASTERS:
            break

        args = (dto.action_data or {}).get("arguments", {})
        limit = args.get("limit")
        city = args.get("city") or None
        specialization = args.get("specialization") or None
        if not has_discovery_criteria(city, specialization):
            # Criteria-less call → continue discovery, never the catalogue
            # (BOT-003 §9 / prohibition #22 — see has_discovery_criteria).
            logger.info("orchestrator.concierge.show_masters.no_criteria trace=%s", trace_id)
            rendered = render_no_criteria_clarification()
            return DiscoveryReply(
                text=rendered.text,
                action_data=rendered.action_data,
                persisted=True,
            )
        cards = discover_masters(
            city=city,
            specialization=specialization,
            limit=int(limit) if isinstance(limit, int) and limit > 0 else _MAX_MASTER_CARDS,
            resolve_service=True,
        )
        logger.info(
            "orchestrator.concierge.show_masters count=%d trace=%s pass=%d",
            len(cards),
            trace_id,
            pass_index,
        )
        if pass_index >= max_passes:
            # Pass budget exhausted with the model still asking for the tool:
            # the deterministic card render — byte-identical to the
            # pre-DRF-1266 reply. The user sees real data, never silence
            # or a raw tool dump.
            logger.info(
                "orchestrator.concierge.multipass_budget_exhausted trace=%s passes=%d",
                trace_id,
                pass_index,
            )
            rendered = _render_master_cards(
                cards[:_MAX_MASTER_CARDS], city=city, specialization=specialization
            )
            return DiscoveryReply(
                text=rendered.text,
                action_data=rendered.action_data,
                persisted=True,
            )
        pending_cards = cards
        pending_args = args
        current_text = _build_tool_result_message(message_text, cards, args)

    if dto.action_type in NUTRITION_TOOL_ACTIONS:
        # DRF-1268 — a nutrition skill selected by the model as a tool.
        # The deterministic reply comes from the skill itself (its own
        # product-approved text + keyboard); no extra LLM pass is spent
        # on rephrasing a log confirmation.
        args = (dto.action_data or {}).get("arguments", {})
        result = execute_nutrition_tool(
            dto.action_type,
            args if isinstance(args, dict) else {},
            bot_user=bot_user,
            conversation=conversation,
            trace_id=trace_id or "",
        )
        if result is not None and result.reply_text:
            return DiscoveryReply(
                text=result.reply_text[:_MAX_REPLY_CHARS],
                action_data=result.action_data,
                persisted=True,
            )
        # Parser refused the phrase the model passed (or the skill
        # declined): fall back to whatever text the model produced
        # alongside the call, else the safe line.
        text = (dto.content or "").strip()
        if text:
            return DiscoveryReply(text=text[:_MAX_REPLY_CHARS], persisted=True)
        return DiscoveryReply(text=get_fallback("ru"), persisted=True)

    if dto.action_type in CATALOG_TOOL_ACTIONS:
        # DRF-1304 — salons / services selected by the model as tools. The
        # deterministic reply is rendered from real mirror data (or an honest
        # «нет такого» when the mirror has none); no extra LLM pass is spent
        # rephrasing catalog rows, so the turn's cost does not grow.
        args = (dto.action_data or {}).get("arguments", {})
        rendered = execute_catalog_tool(dto.action_type, args if isinstance(args, dict) else {})
        if rendered is not None:
            # No re-clamp to _MAX_REPLY_CHARS here: the renderer already bounds
            # this text by the catalog budget, and 600 would cut a real card
            # list mid-word while its chips stayed (see _MAX_CATALOG_REPLY_CHARS).
            return DiscoveryReply(
                text=rendered.text,
                action_data=rendered.action_data,
                persisted=True,
            )
        # Unknown tool name is unreachable (_KNOWN_TOOLS gates dispatch), but
        # degrade exactly like the nutrition branch if it ever happens.
        text = (dto.content or "").strip()
        if text:
            return DiscoveryReply(text=text[:_MAX_REPLY_CHARS], persisted=True)
        return DiscoveryReply(text=get_fallback("ru"), persisted=True)

    if dto.action_type == ActionType.ASK_CLARIFICATION:
        data = dto.action_data or {}
        question = str(data.get("question") or "").strip()
        if not question:
            # No question text: either _dispatch_tool's internal degrade path
            # (unknown tool / malformed arguments — action_data carries only
            # "reason") or a genuine ask_clarification call with a blank
            # question. Same safe fallback as an LLM error — never send an
            # empty clarification.
            return DiscoveryReply(text=get_fallback("ru"), persisted=True)
        rendered = _render_ask_clarification(question, list(data.get("options") or []))
        return DiscoveryReply(
            text=rendered.text,
            action_data=rendered.action_data,
            persisted=True,
        )

    text = (dto.content or "").strip()
    if not text:
        return DiscoveryReply(text=get_fallback("ru"), persisted=True)
    return DiscoveryReply(text=text[:_MAX_REPLY_CHARS], persisted=True)


def generate_direct_show_masters_reply(
    message_text: str,
    *,
    trace_id: str | None = None,
    bot_user: Any = None,
    conversation: Any = None,
) -> DiscoveryReply | None:
    """Deterministic show-masters short-circuit for a general booking request.

    DRF-1102 — the missing 8th branch in the pre-LLM detector chain in
    :mod:`apps.channels.max.handler` (safety → human_handoff → visit-callbacks
    → personal booking lookup → onboarding → discover-callbacks →
    booking-callbacks → **this**). A turn like «запиши меня на массаж» names
    a service/availability signal (:func:`apps.skills.menu.matching.
    looks_like_booking_request`) but isn't any narrower intent above, so it
    used to fall all the way to the concierge LLM — which, advertising only
    ``show_masters`` and told to "ask clarifying questions", had no tool-call
    path that didn't require a full free-text round trip, and would loop
    re-asking instead of ever calling it (root cause per the DRF-1102 audit).

    Skips the LLM when it can: the deterministic path is faster and cheaper,
    and when it hits it is right. Mirrors the per-tenant ``MenuSkill``, which
    already treats the same signal as a last-resort booking catch-all
    (``apps/skills/menu/skill.py``) — this is that same catch-all, applied one
    level up (before the concierge instead of before echo) because the global
    path has no salon-scoped booking skill to hand off to yet; showing masters
    IS the equivalent next step here.

    ### Returning ``None`` (DRF-1283)

    ``None`` means the search matched NOBODY, and the caller must hand the
    turn to the concierge instead of sending anything.

    Zero results is not an answer — it is this layer admitting it could not
    resolve the request. Rendering it as «мастеров пока не нашлось» spends the
    turn on a non-answer and forecloses the one thing that could still rescue
    it. On the live pilot (23.08) «покажи массажистов в пензе» went out in
    66ms as exactly that non-answer, with four massage masters in the salon
    and no model call anywhere in the trace. The search bug behind that
    particular zero is fixed (see ``apps.marketplace.discovery``), but the
    structural point survives its fix: a deterministic matcher will always
    have a tail it cannot phrase, and the model is what that tail is for.

    Handing zero results back to the model was NOT safe when DRF-1102 wrote
    this branch: the concierge was single-pass, so a ``show_masters`` call
    consumed the whole turn and left the model nothing to say over the result
    — which is why it re-asked forever instead of calling the tool. DRF-1266
    (multi-pass, on the pilot since 23.08) removed that constraint: the tool
    result comes back as an ordinary second message and the model speaks over
    it, bounded by ``CONCIERGE_MAX_LLM_PASSES``. The fallback is safe now
    because that landed, not because zero results became less bad.

    The branch itself stays — a hit still answers here, without a model.
    """
    started = time.monotonic()
    if not has_discovery_criteria(None, message_text):
        # Same guard as the LLM path: a blank turn carries no criteria, and the
        # unfiltered read behind it is the catalogue fallback canon forbids.
        logger.info("orchestrator.concierge.direct_show_masters.no_criteria trace=%s", trace_id)
        reply = render_no_criteria_clarification()
        _record_direct_metric(
            bot_user=bot_user,
            conversation=conversation,
            trace_id=trace_id,
            message_text=message_text,
            started=started,
            outcome=AIRequestMetric.OUTCOME_FALLBACK,
            fallback_triggered=True,
        )
        return reply
    cards = discover_masters(
        specialization=message_text,
        limit=_MAX_MASTER_CARDS,
        resolve_service=True,
    )
    logger.info(
        "orchestrator.concierge.direct_show_masters count=%d trace=%s",
        len(cards),
        trace_id,
    )
    if not cards:
        # Decline the turn; the handler routes it to the concierge. No metric
        # row here on purpose — this path did not answer the inbound message,
        # and the concierge writes its own row(s) for the same turn.
        logger.info("orchestrator.concierge.direct_show_masters.empty_to_llm trace=%s", trace_id)
        return None
    _record_direct_metric(
        bot_user=bot_user,
        conversation=conversation,
        trace_id=trace_id,
        message_text=message_text,
        started=started,
        outcome=AIRequestMetric.OUTCOME_SUCCESS,
    )
    return _render_master_cards(cards, specialization=message_text)


def _record_direct_metric(
    *,
    bot_user: Any,
    conversation: Any,
    trace_id: str | None,
    message_text: str,
    started: float,
    outcome: str,
    fallback_triggered: bool = False,
) -> None:
    """One ``AIRequestMetric`` row for a turn the deterministic branch ANSWERED.

    See :func:`_record_concierge_metric` for why a model-less turn belongs in
    this table at all and why its LLM columns stay NULL rather than zero.

    Skipped without a ``bot_user``: the row's whole value is being countable
    alongside the concierge's rows for the same funnel, and a user-less row
    (only reachable from a direct unit-test call) is noise in that count.
    """
    if bot_user is None:
        return
    _record_concierge_metric(
        bot_user=bot_user,
        conversation=conversation,
        trace_id=trace_id,
        message_text=message_text,
        # No LLM pass happened — NULL, not 0. `llm_pass_index` counts model
        # calls within a turn, and 0 would read as a zeroth call.
        pass_index=None,
        outcome=outcome,
        latency_total_ms=int((time.monotonic() - started) * 1000),
        skill_selected="concierge_direct",
        fallback_triggered=fallback_triggered,
    )
