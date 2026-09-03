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
from dataclasses import dataclass, replace
from datetime import date
from types import SimpleNamespace
from typing import Any

from ayla_ai_core import ActionType, AIConcierge, ToolResult
from ayla_ai_core.orchestrator import DEFAULT_MODEL_NAME as _AI_CORE_DEFAULT_MODEL
from asgiref.sync import sync_to_async
from django.conf import settings
from django.utils import timezone

from apps.conversations.services import (
    record_global_message,
    resolve_active_global_conversation,
)
from apps.identity.services.global_tenant import get_global_bot_tenant
from apps.llm.model_tiers import TIER_SMART
from apps.llm.pricing import UnknownModelError, compute_cost
from apps.llm.router import get_router
from apps.marketplace.discovery import (
    discover_masters,
    find_masters_by_name,
    service_coverage,
)
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
    encode_query_ref,
    execute_catalog_tool,
    has_discovery_criteria,
    reground_specialization,
    render_no_criteria_clarification,
    requested_services,
)
from apps.orchestrator.fast_path import claims_direct_show_masters
from apps.orchestrator.handoff import handoff_to_booking
from apps.orchestrator.llm.templates import get_fallback
from apps.orchestrator.nutrition_global import (
    NUTRITION_TOOL_ACTIONS,
    NUTRITION_TOOL_SPECS,
    execute_nutrition_tool,
)
from apps.orchestrator.personal_surface import (
    PERSONAL_TOOL_ACTIONS,
    SHOW_MY_RECORDS_TOOL_SPEC,
    execute_personal_tool,
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

    #: DRF-1443 — the tier this client asks for when the caller did not
    #: choose a model itself. The concierge is the customer-facing reply
    #: path, same as every skill that reads
    #: ``provider.default_completion_model``, so it belongs on the same
    #: tier as those.
    default_tier = TIER_SMART

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

    def _model_for(self, model: str) -> str:
        """What to actually ask the provider for.

        ``AIConcierge`` always passes a ``model_name``. When the consumer
        did not configure one — and this repo never has — that value is
        ``ayla_ai_core.orchestrator.DEFAULT_MODEL_NAME``: an OpenAI id
        baked into a library with no idea which vendor this platform
        routed to. It carries no decision, so substituting our own tier
        is not overriding a caller; it is supplying the choice nobody
        made.

        That id reaching the vendor unexamined is the DRF-1443 outage:
        with ``LLM_PROVIDER=anthropic`` every concierge turn posted
        ``gpt-4o-mini`` to ``api.anthropic.com`` and came back
        ``404 not_found_error``. The provider-level resolver in
        ``apps.llm.model_tiers`` would also catch it — this method is
        what makes the tier a STATED choice of this module rather than
        an inference drawn from somebody else's constant.

        A model the caller genuinely chose (``intent_resolution`` sets
        ``INTENT_RESOLUTION_MODEL``) is forwarded untouched, so a wrong
        one still fails at the vendor instead of being quietly repaired.
        """
        if not model or model == _AI_CORE_DEFAULT_MODEL:
            return self.default_tier
        return model

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
        result = await provider.complete(messages, model=self._model_for(model), tools=tools)

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
                    messages,
                    model=self._model_for(model),
                    tools=tools,
                    tool_choice="required",
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
        # DRF-1354 — telemetry of the passes, accumulated for the ONE row
        # :func:`generate_concierge_reply` writes at the end of the turn.
        # Summed, not overwritten: a multi-pass turn really did spend all of
        # those tokens, and the row used to carry only the last pass's.
        self.tokens_in = 0
        self.tokens_out = 0
        self.latency_ms = 0
        #: The last non-empty ``action_type`` any pass selected — the tool
        #: this turn used, kept so the row stays as queryable as before.
        self.action_type = ""

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
        """Record NOTHING; accumulate telemetry and return a marker.

        DRF-1354 — one concierge turn now writes exactly one assistant row,
        and :func:`generate_concierge_reply` writes it, because only that
        function knows what was actually sent.

        This method used to write one row per ai-core pass (its orchestrator
        saves an assistant message after every completion). That produced the
        pilot trace of 24.08 verbatim:

        * a pass that SELECTS a tool carries no text, so it wrote a **blank**
          row — four of them stand in that trace, one before each answer, and
          they were also read back by :meth:`load_recent_history`, which takes
          the last N ROWS, so half of a ten-row window went to rows ai-core
          then drops from the prompt anyway;
        * a pass that produced prose AND a tool call wrote that prose — even
          when the branch went on to answer deterministically and the sentence
          was never sent;
        * and every deterministic answer (cards, catalog, nutrition, the
          DRF-1354 handoff) was text no pass ever produced, so no row held it.

        The result was a transcript that did not say what the bot said, and
        the transcript is the LLM history of the next turn.

        Nothing is lost by not writing here. Per-CALL cost and latency live in
        ``AIRequestMetric`` (DRF-1211, one row per LLM call); the tool
        selection is in the ``orchestrator.concierge.*`` log line; and the
        totals ride onto the single row through the counters above.

        The user role is unchanged: persisted upstream by the channel handler,
        so the marker carries the handler's id for history exclusion.
        """
        if role == "user":
            return SimpleNamespace(id=self._user_message_id)
        self.tokens_in += tokens_in or 0
        self.tokens_out += tokens_out or 0
        self.latency_ms += latency_ms or 0
        if action_type:
            self.action_type = action_type
        return SimpleNamespace(id=None)

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


# ── DRF-1354: the tool that STARTS a booking ───────────────────────────
#
# Live pilot, 24.08 07:52–07:53. The owner wrote «запиши к Архипкину Денису на
# завтра» — intent, person and day in one sentence — and got a list of three
# masters ending «Если хочешь записаться к Архипкину Денису на завтра, дай
# знать!». He answered «даю знать», verbatim, and got the same sentence back.
# Four turns, zero bookings.
#
# Nothing in that trace malfunctioned. The concierge's roster was
# show_masters / show_salons / show_services / ask_clarification plus the
# nutrition skills: every one of them SHOWS something. Booking was reachable
# only by tapping ``cb:discover:book:…`` on a master card — so a model asked to
# «предложи записаться» could only ever describe the act. The bot was not
# refusing to book; it had no verb for it.
#
# ``start_booking`` is that verb. It resolves the NAMED master through the
# marketplace carve-out (``find_masters_by_name`` — the catalog rules on who
# exists, never the model) and hands the turn to the SAME entrypoint the card
# button uses (``apps.orchestrator.handoff.handoff_to_booking``). Nothing about
# booking is reimplemented here, and the tap path is untouched: this adds a
# second door into one room.
#
# The description is written against the observed failure, not against the
# happy path — «дай знать» and a bare «запиши» after a name has been said are
# named in it, because those are the turns that died.
START_BOOKING_TOOL_SPEC: dict[str, Any] = {
    "name": "start_booking",
    "description": (
        "Начать запись к КОНКРЕТНОМУ мастеру, которого назвал клиент: "
        "платформа найдёт мастера и покажет его свободные даты. Вызывай, как "
        "только в разговоре прозвучало имя мастера и желание записаться — "
        "«запиши к Архипкину Денису на завтра», «давай к Денису», а также "
        "«запиши», «давай», «даю знать», «да» ПОСЛЕ того как имя уже "
        "прозвучало (в том числе если его назвал ты сам). Имя бери из всей "
        "истории разговора, а не только из последней фразы. Никогда не "
        "отвечай на такой запрос словами «дай знать» и не показывай список "
        "мастеров заново — запись начинает этот инструмент, а не текст."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "master": {
                "type": "string",
                "description": (
                    "Имя мастера так, как оно прозвучало в разговоре — "
                    "«Архипкин Денис», «Денис», «Татьяна Паламарчук». Только "
                    "имя: без «запиши к», без города и без услуги."
                ),
            },
            "service": {
                "type": "string",
                "description": (
                    "Услуга, если она известна из разговора («массаж», "
                    "«маникюр»). Оставь пустым, если клиент её не называл — "
                    "платформа спросит сама, вариантами."
                ),
            },
            "city": {
                "type": "string",
                "description": "Город, если он известен (необязательно).",
            },
        },
        "required": ["master"],
    },
}

#: action_type of the booking-start tool — the concierge executes it in the
#: wrapper's SYNC scope, same carve-out shape as the catalog tools.
START_BOOKING_ACTION = START_BOOKING_TOOL_SPEC["name"]


#: Every tool the concierge is armed with, in declaration order (DRF-1328).
#:
#: Lifted out of :func:`generate_concierge_reply` so the roster has ONE name
#: that other code can point at. Two things read it:
#:
#: * the concierge itself, as ``tool_definitions`` — unchanged behaviour;
#: * ``apps/orchestrator/tests/test_fast_path_claim.py``, which fails when a
#:   tool listed here has no entry in
#:   ``apps.orchestrator.fast_path.FAST_PATH_TOOL_CLAIMS``. That guard is why
#:   this is a module constant and not an inline literal: an inline list can
#:   grow without anything noticing, and it did — twice in two days
#:   (DRF-1312, DRF-1328).
#:
#: Nutrition specs stay LAST and in their own order: that order is
#: load-bearing (``apps.orchestrator.nutrition_global`` — screening is read
#: first by the model).
CONCIERGE_TOOL_SPECS: list[dict[str, Any]] = [
    SHOW_MASTERS_TOOL_SPEC,
    START_BOOKING_TOOL_SPEC,
    SHOW_SALONS_TOOL_SPEC,
    SHOW_SERVICES_TOOL_SPEC,
    ASK_CLARIFICATION_TOOL_SPEC,
    *NUTRITION_TOOL_SPECS,
    SHOW_MY_RECORDS_TOOL_SPEC,
]

# Cap on a tool argument written to the turn log. Both values are bounded by
# the model's own output, not by anything upstream, and a log line is not the
# place to find that out.
_MAX_LOGGED_ARG_CHARS = 64

_KNOWN_TOOLS = frozenset(
    {
        SHOW_MASTERS_TOOL_SPEC["name"],
        START_BOOKING_TOOL_SPEC["name"],
        ASK_CLARIFICATION_TOOL_SPEC["name"],
    }
    | NUTRITION_TOOL_ACTIONS
    | CATALOG_TOOL_ACTIONS
    | PERSONAL_TOOL_ACTIONS
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
                # DRF-1362 — how the user is meant to answer. Carried raw and
                # unvalidated on purpose: this dispatcher does pure argument
                # normalisation, and deciding what an unrecognised mode string
                # means is the renderer's job (``normalize_clarification_mode``,
                # which never lets one escape the enum). Absent when the model
                # does not fill it, which is the pre-DRF-1362 shape exactly.
                "mode": args.get("mode"),
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
    if name in PERSONAL_TOOL_ACTIONS:
        # DRF-1302/1305 — selection only again: the Ayla GETs and the memory
        # read are I/O and belong in the wrapper's sync scope, not in a
        # dispatcher the ai-core contract requires to be side-effect-free.
        return ToolResult(action_type=name, action_data={"arguments": args})
    if name == START_BOOKING_ACTION:
        # DRF-1354 — selection only, like every carve-out above. The name
        # resolution and the handoff dispatch are I/O and run after
        # ``asyncio.run`` returns (see :func:`_execute_start_booking`).
        return ToolResult(action_type=name, action_data={"arguments": args})
    return ToolResult(
        action_type=ActionType.SHOW_MASTERS,
        action_data={"arguments": args},
    )


def _tool_trace_entry(dto: Any) -> dict[str, Any]:
    """One element of the DRF-1385 tool trace: the tool's name + arguments.

    The concierge already CLASSIFIES the intent by picking a tool; the trace
    carries that choice out of the turn so the post-reply resolver
    (``apps.orchestrator.intent_resolution``) can record it deterministically
    instead of paying a second model call to re-derive it.

    ``ask_clarification`` keeps its payload (question/options/mode) directly
    in ``action_data``; every other tool nests it under ``"arguments"`` —
    the shapes :func:`_dispatch_tool` returns. The internal dispatcher
    degrade (unknown tool / malformed arguments — ``action_data`` holds only
    ``"reason"``) is traced as-is: it is NOT the model's choice, and the
    resolver's mapping table honestly has no row for it (LLM fallback).
    """

    data = dto.action_data if isinstance(dto.action_data, dict) else {}
    if dto.action_type == ActionType.ASK_CLARIFICATION:
        arguments = data
    else:
        arguments = data.get("arguments") or {}
    return {"tool": str(dto.action_type), "arguments": arguments}


# Wording for the two outcomes of ``start_booking`` that are NOT a handoff.
# Both are answers, not refusals: one says who we could not find, the other
# asks the ONE question that is still open, with the names in it.
_BOOKING_NO_MASTER = (
    "Не нашла мастера с таким именем — {name}. Проверьте написание или "
    "назовите услугу, и я покажу, кто её делает."
)
_BOOKING_WHICH_ONE = "Уточните, к кому именно — напишите фамилию или нажмите кнопку:"


#: How far back a salon name may have been said and still count (DRF-1355).
#: The same window ``GlobalConversationStore.load_recent_history`` gives the
#: model, so «the person could have seen this name» means the same thing to
#: the grounding check as «the model could have read this name» — a shorter
#: window would refuse salons the model legitimately picked up from the
#: transcript, a longer one would ground a name from a conversation the model
#: can no longer see.
_SAID_HISTORY_TURNS = 10


def _conversation_text(conversation: Any, message_text: str) -> str:
    """This turn plus the recent transcript, as one blob of words.

    The evidence side of DRF-1355: what a salon name in a tool call is checked
    against. BOTH roles are included — the assistant's own messages are where
    a follow-up «а что в Люмине» gets the name from, because the bot itself
    rendered the salon list a turn earlier.

    Best-effort: a transcript read must never cost the turn, and losing it
    degrades to «only this turn was said», which is this check's safe
    direction (more calls treated as ungrounded, never fewer).
    """
    parts = [message_text or ""]
    try:
        from apps.conversations.models import Message

        rows = (
            Message.all_tenants.filter(conversation=conversation)
            .order_by("-created_at")
            .values_list("content", flat=True)[:_SAID_HISTORY_TURNS]
        )
        parts.extend(row for row in rows if row)
    except Exception:  # noqa: BLE001 — evidence is best-effort, never fatal
        logger.warning("orchestrator.concierge.said_history_failed", exc_info=True)
    return "\n".join(parts)


def _remember_when_before_handoff(
    conversation: Any,
    bot_user: Any,
    text: str,
) -> None:
    """Store the «на завтра» of THIS turn before the handoff reads it.

    DRF-1325 already parses and stores a time preference, and
    :func:`apps.orchestrator.handoff.carry_time_preference` already copies it
    across the tenant boundary so the booking flow opens on the day the person
    named. But the storing happens in the MAX handler AFTER the reply is
    built — written for the tap path, where the person says «завтра» on one
    turn and taps a card on the next.

    ``start_booking`` collapses those two turns into one. «запиши к Архипкину
    Денису на завтра» names the master and the day in the same sentence, so by
    the handler's turn the handoff has already run and read an empty
    preference: the person would get a bare calendar after naming the day.

    Same parser, same store, same key — only earlier. The handler's later
    write then sets the identical value.

    Best-effort by contract, like every other reader of this module: losing
    the hint costs the day chips, never the booking.
    """
    try:
        from apps.orchestrator.time_preference import (
            local_today,
            parse_time_preference,
            save_time_preference,
        )

        today = local_today(getattr(bot_user, "tenant", None))
        pref = parse_time_preference(text, weekday_today=today.weekday())
        if pref is not None:
            save_time_preference(conversation, pref)
    except Exception:  # noqa: BLE001 — a hint must never break a booking turn
        logger.warning("orchestrator.concierge.start_booking.time_pref_failed", exc_info=True)


def _execute_start_booking(
    args: dict[str, Any],
    *,
    bot_user: Any,
    conversation: Any,
    message_text: str,
    trace_id: str | None,
) -> DiscoveryReply | None:
    """Resolve the named master and enter booking. SYNC scope only (DRF-1354).

    Three outcomes, and only one of them is a question:

    * **nobody** — say so, naming what was looked for. ``None`` is NOT returned
      here: a miss is a real answer, and falling back to the model's own prose
      would put us back in the «дай знать» loop the ticket is about.
    * **several** — the disambiguation the ticket asks for by name: «запиши к
      Денису» with two Денисов must be closeable in ONE word. The reply carries
      their names as text (type the surname) AND the ordinary
      ``cb:discover:book:…`` keyboard (tap once) — the same callback grammar the
      master cards have emitted since #1020, so nothing new can go stale.
    * **exactly one** — hand off. ``handoff_to_booking`` is called, not copied:
      it owns tenant scoping, the service-context guard (DRF-962), the
      time-preference carry (DRF-1325) and the escalation path, and a second
      implementation of any of those would drift within the week.

    ``None`` means only «the model named nobody» — the caller degrades to
    whatever prose came alongside the call.
    """
    master_query = str(args.get("master") or "").strip()
    if not master_query:
        logger.info("orchestrator.concierge.start_booking.no_master trace=%s", trace_id)
        return None
    service = str(args.get("service") or "").strip()
    city = str(args.get("city") or "").strip() or None
    cards = find_masters_by_name(
        master_query, city=city, service=service or None, limit=_MAX_MASTER_CARDS
    )
    logger.info(
        "orchestrator.concierge.start_booking master=%r city=%r service=%r matched=%d trace=%s",
        master_query[:40],
        city,
        service[:40],
        len(cards),
        trace_id,
    )
    if not cards:
        return DiscoveryReply(
            text=_BOOKING_NO_MASTER.format(name=master_query[:60])[:_MAX_REPLY_CHARS],
            persisted=True,
        )
    if len(cards) > 1:
        # Reuse the card renderer for the KEYBOARD only — its callbacks are the
        # handoff seam, and rebuilding them here would be a second copy of the
        # one contract that must never drift. The text is this function's own:
        # «Вот мастера, которые могут подойти» is a search result, and this is a
        # question about a name the person already gave.
        rendered = _render_master_cards(cards, city=city, specialization=service or None)
        lines = [_BOOKING_WHICH_ONE]
        lines.extend(f"• {card.name}" for card in cards)
        return DiscoveryReply(
            text="\n".join(lines)[:_MAX_REPLY_CHARS],
            action_data=rendered.action_data,
            persisted=True,
        )
    card = cards[0]
    # The day the person named is in THIS sentence, and the handoff is about to
    # read it (see :func:`_remember_when_before_handoff`).
    _remember_when_before_handoff(conversation, bot_user, message_text)
    reply = handoff_to_booking(
        global_bot_user=bot_user,
        tenant_id=card.tenant_id,
        master_id=card.master_id,
        service_id=card.service_id,
        query_ref=encode_query_ref(service) if service else "",
        chat_id=str(getattr(bot_user, "chat_id", "") or ""),
        trace_id=trace_id,
    )
    return DiscoveryReply(text=reply.text, action_data=reply.action_data, persisted=True)


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
        # DRF-1355 — this sentence used to end «конкретный салон выбирается
        # только в момент записи», which was true until DRF-1304 shipped
        # ``show_salons`` the day before the pilot trace. Left in place it told
        # the model, in the second sentence of its own prompt, that a salon is
        # not something this conversation shows — while the tool roster said
        # the opposite. A prompt that contradicts the tool list is not a fix
        # this ticket rests on (the platform check below is), but a false
        # statement about our own product has no business staying in it.
        "Ты помогаешь клиенту по всей стране подобрать подходящего "
        f"{voice['domain']}-мастера и записаться, а также рассказываешь про "
        "подключённые салоны, их адреса и услуги — про них отвечают "
        "инструменты каталога.",
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
        # DRF-1312 — the tool takes a `services` array now, and the model is
        # the only party that can split «массаж и маникюр» into two names.
        # Without this line it keeps folding a composite request into one
        # `specialization` substring, the half nobody offers scores zero in
        # the ranking, and the answer silently covers half the question.
        # The second sentence is a boundary, not politeness: dropping a
        # service the model believes is missing would make it the authority
        # on what the catalog holds (AYLA-DEC-0045 / OD-9).
        "Если клиент назвал НЕСКОЛЬКО услуг («массаж и маникюр»), перечисли "
        "их ВСЕ в параметре services инструмента show_masters — каждую "
        "отдельным элементом, словами клиента. Не решай сам, есть ли услуга "
        "у мастеров, и не выбрасывай ту, которой, по-твоему, нет: платформа "
        "проверит каждую по каталогу и сама скажет клиенту про отсутствующие.",
        # DRF-1354 — the booking verb. Without this line the model has the
        # tool but keeps the habit the rest of the prompt taught it: describe
        # the next step and wait. The pilot trace of 24.08 is that habit — the
        # bot asked the owner to «дать знать» three times running, and
        # once more after he did, in those words.
        "Запись начинает ИНСТРУМЕНТ start_booking, а не текст. Как только в "
        "разговоре есть имя мастера и желание записаться — вызывай "
        "start_booking с этим именем. Это относится и к коротким ответам "
        "(«запиши», «давай», «даю знать», «да») после того, как имя "
        "уже прозвучало: имя ищи по всей истории разговора, включая свои "
        "собственные реплики. НИКОГДА не пиши «дай знать» или «напиши, "
        "если хочешь записаться» — это тупик: клиент уже сказал, чего "
        "хочет. Не показывай список мастеров второй раз, если нужный "
        "мастер в нём уже был.",
        # DRF-1304 — the salon/service tools exist now (tool_definitions).
        # DRF-1355 sharpens the first two lines against the live failure:
        # «покажи мне салоны» went to show_services with an invented salon.
        # The platform now refuses that argument outright (the answer is the
        # salon list either way), so this is the cheap half of the fix, not
        # the load-bearing one.
        "Инструменты каталога:\n"
        "- Просьба показать САЛОНЫ или спросить про адреса («покажи салоны», "
        "«покажи мне салоны», «какие салоны у вас есть», «где вы находитесь», "
        "«куда можно прийти») — вызывай show_salons (город необязателен). "
        "НЕ show_services: это вопрос про места, а не про услуги.\n"
        "- Вопрос про услуги, цены, длительность («какие услуги в салоне», "
        "«что есть по лицу», «сколько стоит массаж») — вызывай show_services "
        "с фильтром: салон, город или запрос. Салон в параметре salon "
        "указывай ТОЛЬКО если клиент назвал его сам или ты показал его "
        "раньше в этом разговоре; не подставляй салон от себя.\n"
        "- Подбор конкретного мастера — show_masters, как раньше.\n"
        "- Клиент назвал мастера по имени и хочет записаться — "
        "start_booking, а не show_masters: show_masters ищет по услуге и "
        "снова покажет список, в котором этот мастер уже был.",
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
        "start_nutrition_anketa.\n"
        # DRF-1302/1305 — the READ tool. Named apart from the four writing
        # tools above because the failure it prevents is the model ANSWERING
        # «что я ел сегодня» from its own head: without a tool call there is
        # no data, and a warm invented answer about the person's food is the
        # exact thing the boundary below forbids.
        "- Вопрос про СВОИ записи или про то, что ты о нём помнишь («что я "
        "ел сегодня», «мой дневник», «что ты про меня помнишь») — "
        "show_my_records. Никогда не отвечай на такой вопрос по памяти "
        "разговора: числа и факты берутся только из ответа инструмента.",
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


def _build_tool_result_message(
    user_text: str,
    cards: list[Any],
    args: dict[str, Any],
    *,
    missing: list[str] | None = None,
) -> str:
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
    if missing:
        # DRF-1312 — the services the CATALOG says nobody offers, verified by
        # `service_coverage`, not by the model. Stated as a fact it may not
        # revise: with cards present the reply is rendered deterministically
        # and never reaches here, so this is the zero-result composite («и
        # массажа, и маникюра нет»), where the model still does the phrasing
        # and needs to know WHICH parts were checked.
        quoted = ", ".join(f"«{str(name)}»" for name in missing)
        lines.append(
            f"Проверено по каталогу: {quoted} — этого нет ни у одного мастера. "
            "Скажи об этом прямо и не предлагай эту услугу."
        )
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
    # DRF-1354 — «предложи записаться» above used to be a promise
    # with nothing behind it: the roster had no booking verb, so the model
    # invented one out of words («дай знать»), and the person who did
    # exactly that got the same sentence back. Two things changed and both
    # belong in the instruction the model reads: under each card there is now
    # a real button, and if the person answers with a NAME the next turn has
    # a tool for it.
    lines.append(
        "Под списком клиенту уже показаны кнопки «Записаться к …» — можешь "
        "прямо позвать нажать на нужную. Не проси «дать знать» и не обещай "
        "записать сам: если клиент назовёт мастера, запись начнёт "
        "инструмент start_booking на следующем ходу."
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
    """One concierge turn, writing exactly one assistant row: the reply.

    The turn itself is :func:`_concierge_turn`. This wrapper owns one
    invariant that used to hold only by accident: what the transcript says the
    bot said is what the bot said.

    ``persisted=True`` USED to mean only "the store wrote something for this
    turn", which for a prose answer was the reply and for everything else was
    not. Every DETERMINISTIC branch below (the card render, the catalog
    tables, a nutrition confirmation, the DRF-1354 handoff) returns text the
    model never produced, while the row ai-core wrote for that pass carried
    the model's EMPTY tool-selection content. So the reply went to the user
    and a blank row went to the transcript — which is also the LLM history the
    next turn reads. The bot could not see what it had just shown, which is
    one way a conversation walks backwards.

    Since DRF-1354 the store writes nothing at all
    (:meth:`GlobalConversationStore.save_message` explains what that removes)
    and the row is written HERE, from the reply, with the turn's accumulated
    token and latency totals. ``persisted=True`` therefore means what it says
    on every branch, not only on the prose one.

    Best-effort: a transcript write must never cost the reply.
    """
    store = GlobalConversationStore(user_message_id=user_message_id)
    reply = _concierge_turn(
        message_text,
        store=store,
        bot_user=bot_user,
        conversation=conversation,
        memory_block=memory_block,
        nutrition_block=nutrition_block,
        extra_system=extra_system,
        trace_id=trace_id,
    )
    # DRF-1210 — the outbound guard runs HERE, above the transcript write, and
    # not only at the channel's send.
    #
    # The channel guards the send too (that is what covers the deterministic
    # branches this function never sees), and running twice is free: the
    # replacement line passes the check, so the second call is a no-op.
    #
    # But only this side can keep the invariant this function exists for —
    # «what the transcript says the bot said is what the bot said». The row
    # below is the LLM history of the NEXT turn. Written from the blocked
    # draft, it would hand the model back its own medical claim as an
    # established fact of the conversation, and the guard would then have to
    # win again on every subsequent turn to keep it off the screen. Guarding
    # after the write would stop the sentence reaching the person and still
    # let it reach the prompt.
    from apps.orchestrator.safety.gate import guard_outbound

    _guarded = guard_outbound(reply.text, surface="concierge", bot_user=bot_user, trace_id=trace_id)
    if _guarded.blocked:
        # action_data goes with the text (the channel drops keyboards on a
        # block for the same reason). ``persisted`` is preserved so the row
        # below still gets written — with the replacement. ``tool_trace``
        # (DRF-1385) is preserved too: it records which TOOL the model
        # chose, a fact the text replacement does not undo.
        reply = DiscoveryReply(
            text=_guarded.text,
            action_data=None,
            persisted=reply.persisted,
            outage=reply.outage,
            tool_trace=reply.tool_trace,
        )
    if reply.persisted and (reply.text or "").strip():
        try:
            record_global_message(
                conversation,
                role="assistant",
                content=reply.text,
                rendered_text=reply.text,
                action_type=store.action_type,
                tokens_in=store.tokens_in,
                tokens_out=store.tokens_out,
                latency_ms=store.latency_ms or None,
                trace_id=trace_id,
            )
        except Exception as exc:  # noqa: BLE001 — never cost the turn
            logger.warning(
                "orchestrator.concierge.reply_record_failed trace=%s err=%s", trace_id, exc
            )
    return reply


def _concierge_turn(
    message_text: str,
    *,
    store: "GlobalConversationStore",
    bot_user: Any,
    conversation: Any,
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
        store=store,
        context_builder=lambda: ConciergeContext(
            candidates=[],
            candidate_ids=frozenset(),
            summary_text="",
            tenant_id=GLOBAL_TENANT_ID,
        ),
        tool_definitions=CONCIERGE_TOOL_SPECS,
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
    # DRF-1385 — the ordered trace of tools the model picked this turn, one
    # element per pass that ended in a tool call. The concierge classified
    # the intent BY choosing; the post-reply resolver reads THIS choice
    # instead of re-deriving it with a second model call.
    tool_trace: list[dict[str, Any]] = []

    def _reply(**kwargs: Any) -> DiscoveryReply:
        # Every return AFTER the passes ran carries the accumulated trace.
        # A text-only turn (no tool was ever picked) and a turn that never
        # reached the model (the outage fallback) both leave it None —
        # an empty trace is spelled None, never an empty tuple.
        return DiscoveryReply(tool_trace=tuple(tool_trace) or None, **kwargs)

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
                return _reply(
                    text=rendered.text,
                    action_data=rendered.action_data,
                    persisted=True,
                )
            # DRF-1348 — единственная точка, где ход потерян не потому, что
            # модель плохо ответила, а потому, что до неё не дошли. Канал
            # рисует по этому флагу состояние «AI недоступна» с «Повторить»
            # (макет C01), вместо обещания «отвечу через минуту», которое
            # никто не выполнит: к этому ходу никто не вернётся.
            return _reply(text=get_fallback("ru"), outage=True)
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

        # DRF-1385 — a pass that ended in a tool call leaves a trace element;
        # a prose pass (action_type is None) leaves none.
        if dto.action_type:
            tool_trace.append(_tool_trace_entry(dto))

        if dto.action_type != ActionType.SHOW_MASTERS:
            break

        args = (dto.action_data or {}).get("arguments", {})
        limit = args.get("limit")
        city = args.get("city") or None
        specialization = args.get("specialization") or None
        # DRF-1312 — the services the turn asked for, model-split. A model
        # that fills `services` but leaves `specialization` empty has still
        # named a service, and treating that as «no criteria» would answer a
        # perfectly clear composite request with «какая услуга нужна?».
        requested = requested_services(args if isinstance(args, dict) else {}, specialization)
        if not specialization and requested:
            specialization = ", ".join(requested)
        # DRF-968 — the second half of the ticket. The model may be answering
        # an EARLIER turn: «Кавитация» was answered with «классический
        # массаж» on the 09.08 pilot. When the person types a service's NAME,
        # that name is what gets searched for — the catalog, not the model
        # and not this line, decides that a name was typed (see
        # ``reground_specialization``, which is deliberately narrow enough
        # that a qualifier like «а можно на дому?» cannot trigger it).
        grounded = reground_specialization(
            message_text=message_text, city=city, specialization=specialization
        )
        if grounded != specialization:
            # ``grounded`` is safe to log and safe to put in ``spec`` below:
            # the guard only returns the turn when its content words ARE some
            # service's name, so a turn carrying anything else — a phone
            # number, an address, a sentence — cannot reach this line.
            logger.info(
                "orchestrator.concierge.show_masters.regrounded "
                "model_spec=%r said=%r trace=%s pass=%d",
                (specialization or "")[:_MAX_LOGGED_ARG_CHARS],
                (grounded or "")[:_MAX_LOGGED_ARG_CHARS],
                trace_id,
                pass_index,
            )
            specialization = grounded
            # The model's split came from the same stale read, so it cannot
            # be trusted to describe this turn either. Dropping it costs the
            # DRF-1312 «а маникюра ни у кого нет» line on this one turn and
            # never states a service the person did not ask for. The turn is
            # a single service name by construction here, so there is no
            # composite request left to half-answer.
            requested = []
        if not has_discovery_criteria(city, specialization):
            # Criteria-less call → continue discovery, never the catalogue
            # (BOT-003 §9 / prohibition #22 — see has_discovery_criteria).
            logger.info("orchestrator.concierge.show_masters.no_criteria trace=%s", trace_id)
            rendered = render_no_criteria_clarification()
            return _reply(
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
        # DRF-1312 — which of the requested services the CATALOG can serve.
        # Names come from the model, verdicts come from the catalog: the model
        # is not the authority on what exists (AYLA-DEC-0045 / OD-9).
        available, missing = service_coverage(requested, city=city)
        # DRF-968 asked for the ARGUMENTS, not just the count: the live
        # 09.08 dialogue («Кавитация» answered about классический массаж) had
        # to be diagnosed by inference, because the only evidence a tool call
        # left behind was «count=4». City and specialization are what the
        # model chose to search for, and the difference between them and what
        # the person just typed is the whole of a sticky-intent bug.
        logger.info(
            "orchestrator.concierge.show_masters count=%d missing=%d "
            "city=%r spec=%r trace=%s pass=%d",
            len(cards),
            len(missing),
            (city or "")[:_MAX_LOGGED_ARG_CHARS],
            (specialization or "")[:_MAX_LOGGED_ARG_CHARS],
            trace_id,
            pass_index,
        )
        if cards and missing:
            # Half the request has masters and half has nobody. This is the
            # DRF-1312 turn, and it is answered DETERMINISTICALLY rather than
            # handed to the follow-up pass: the sentence «маникюра у наших
            # мастеров нет» is the whole point of the fix, and a prompt that
            # asks a model to include it is a request, not a guarantee. The
            # renderer states it, then the cards for the half we can serve.
            #
            # The pass is not spent, so this is also cheaper than the prose
            # path it replaces — the model's warmth is worth less here than
            # the user not walking into a salon that cannot do their nails.
            logger.info(
                "orchestrator.concierge.show_masters.partial_coverage "
                "available=%d missing=%d trace=%s",
                len(available),
                len(missing),
                trace_id,
            )
            rendered = _render_master_cards(
                cards[:_MAX_MASTER_CARDS],
                city=city,
                specialization=specialization,
                available_services=available,
                missing_services=missing,
            )
            return _reply(
                text=rendered.text,
                action_data=rendered.action_data,
                persisted=True,
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
            return _reply(
                text=rendered.text,
                action_data=rendered.action_data,
                persisted=True,
            )
        pending_cards = cards
        pending_args = args
        current_text = _build_tool_result_message(message_text, cards, args, missing=missing)

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
            return _reply(
                text=result.reply_text[:_MAX_REPLY_CHARS],
                action_data=result.action_data,
                persisted=True,
            )
        # Parser refused the phrase the model passed (or the skill
        # declined): fall back to whatever text the model produced
        # alongside the call, else the safe line.
        text = (dto.content or "").strip()
        if text:
            return _reply(text=text[:_MAX_REPLY_CHARS], persisted=True)
        return _reply(text=get_fallback("ru"), persisted=True)

    if dto.action_type in PERSONAL_TOOL_ACTIONS:
        # DRF-1302/1305 — the person's own diary + memory. Deterministic
        # render, no second model pass: these are the person's own numbers,
        # and a rephrasing pass is a chance for a model to round one.
        args = (dto.action_data or {}).get("arguments", {})
        personal_reply = execute_personal_tool(
            dto.action_type, args if isinstance(args, dict) else {}, bot_user=bot_user
        )
        if personal_reply is not None:
            return _reply(
                text=personal_reply.text,
                action_data=personal_reply.action_data,
                persisted=True,
            )
        # Unreachable (_KNOWN_TOOLS gates dispatch); degrade like its siblings.
        text = (dto.content or "").strip()
        if text:
            return _reply(text=text[:_MAX_REPLY_CHARS], persisted=True)
        return _reply(text=get_fallback("ru"), persisted=True)

    if dto.action_type == START_BOOKING_ACTION:
        # DRF-1354 — the model named a master and asked to book. Resolution
        # + handoff are I/O, so they run HERE, in the wrapper's sync scope,
        # after ``asyncio.run`` has returned — the same rule every other
        # carve-out in this module follows.
        args = (dto.action_data or {}).get("arguments", {})
        booking_reply = _execute_start_booking(
            args if isinstance(args, dict) else {},
            bot_user=bot_user,
            conversation=conversation,
            message_text=message_text,
            trace_id=trace_id,
        )
        if booking_reply is not None:
            # DRF-1385 — the reply comes from the handoff helper, which knows
            # nothing about the trace; attach it at the boundary like every
            # other post-pass return.
            return replace(booking_reply, tool_trace=tuple(tool_trace) or None)
        # The call named nobody (a model that emitted ``start_booking`` with an
        # empty ``master``). Keep whatever it said alongside the call rather
        # than replacing a possibly fine sentence with the generic line.
        text = (dto.content or "").strip()
        if text:
            return _reply(text=text[:_MAX_REPLY_CHARS], persisted=True)
        return _reply(text=get_fallback("ru"), persisted=True)

    if dto.action_type in CATALOG_TOOL_ACTIONS:
        # DRF-1304 — salons / services selected by the model as tools. The
        # deterministic reply is rendered from real mirror data (or an honest
        # «нет такого» when the mirror has none); no extra LLM pass is spent
        # rephrasing catalog rows, so the turn's cost does not grow.
        args = (dto.action_data or {}).get("arguments", {})
        catalog_reply = execute_catalog_tool(
            dto.action_type,
            args if isinstance(args, dict) else {},
            # DRF-1355 — what the person could actually have named. The
            # ``salon`` argument is checked against it before the platform
            # answers for a salon (see ``discovery.salon_named_in``).
            said=_conversation_text(conversation, message_text),
        )
        if catalog_reply is not None:
            # No re-clamp to _MAX_REPLY_CHARS here: the renderer already bounds
            # this text by the catalog budget, and 600 would cut a real card
            # list mid-word while its chips stayed (see _MAX_CATALOG_REPLY_CHARS).
            return _reply(
                text=catalog_reply.text,
                action_data=catalog_reply.action_data,
                persisted=True,
            )
        # Unknown tool name is unreachable (_KNOWN_TOOLS gates dispatch), but
        # degrade exactly like the nutrition branch if it ever happens.
        text = (dto.content or "").strip()
        if text:
            return _reply(text=text[:_MAX_REPLY_CHARS], persisted=True)
        return _reply(text=get_fallback("ru"), persisted=True)

    if dto.action_type == ActionType.ASK_CLARIFICATION:
        data = dto.action_data or {}
        question = str(data.get("question") or "").strip()
        if not question:
            # No question text: either _dispatch_tool's internal degrade path
            # (unknown tool / malformed arguments — action_data carries only
            # "reason") or a genuine ask_clarification call with a blank
            # question. Same safe fallback as an LLM error — never send an
            # empty clarification.
            return _reply(text=get_fallback("ru"), persisted=True)
        rendered = _render_ask_clarification(
            question,
            list(data.get("options") or []),
            data.get("mode"),
        )
        return _reply(
            text=rendered.text,
            action_data=rendered.action_data,
            persisted=True,
        )

    text = (dto.content or "").strip()
    if not text:
        return _reply(text=get_fallback("ru"), persisted=True)
    # DRF-1354 — the multi-pass prose reply carried NO keyboard. DRF-1266
    # feeds the executed ``show_masters`` result back so the model can phrase
    # it warmly, and the deterministic card render — the only thing that ever
    # attached ``cb:discover:book:`` buttons — is skipped on that path. The
    # pilot trace of 24.08 is what that looks like from the outside: a tidy
    # paragraph naming three masters, no buttons under it, and an invitation
    # to let the bot know. The person had nothing to tap, and nothing they
    # could say worked either.
    #
    # Only the KEYBOARD is taken from the renderer; the model keeps the words.
    # Same cards, same callbacks, same order — the tap path is identical to
    # the pre-DRF-1266 reply.
    action_data = None
    if pending_cards:
        action_data = _render_master_cards(
            pending_cards[:_MAX_MASTER_CARDS],
            city=pending_args.get("city"),
            specialization=pending_args.get("specialization"),
        ).action_data
    return _reply(text=text[:_MAX_REPLY_CHARS], action_data=action_data, persisted=True)


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

    ### Declining a COMPOSITE request (DRF-1312)

    ``None`` also when the turn enumerates two or more services («массаж
    классика, и маникюр»). This branch forwards the RAW turn to the catalog as
    one substring, and since DRF-1283 that substring is OR-matched and ranked
    — so a part nobody offers scores zero and vanishes, and five massage
    masters go out under «Вот мастера, которые могут подойти» as the answer to
    a request half of which cannot be served. That was the live turn of 23.08.

    Answering it here would mean splitting the user's own sentence into
    service names, and the only tools this layer has for that are a literal
    filler list and a separator regex. They are enough to COUNT the parts —
    miscounting costs an LLM call — but not to QUOTE one back as a service we
    do not offer, which is what an honest partial answer has to do. Get that
    wrong and the bot announces that «давай будет несколько» is not on the
    menu: a confident lie, strictly worse than the silence being fixed.

    So the model splits (``show_masters.services``), the catalog rules on each
    part (``service_coverage``), and the concierge renders the partial answer
    deterministically. Same shape as the zero-result decline above: the
    deterministic layer answers what it can be right about and hands over the
    rest.
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
    if not claims_direct_show_masters(message_text):
        # DRF-1328 — the inverted default. This branch answers a turn only
        # when it PARSES as exactly «покажи мастеров по услуге»; anything it
        # cannot account for belongs to the concierge, which has the salon,
        # service, screening and clarification tools this layer does not.
        #
        # The DRF-1312 composite decline is now one clause of that parse
        # (``apps.orchestrator.fast_path``, reason ``composite_request``)
        # rather than a hand-added exception — same outcome, one rule.
        #
        # Checked HERE and not only at the handler gate on purpose: the gate
        # can be bypassed by any future caller, and the decision about who
        # owns the turn must not depend on which door it came through.
        #
        # No metric row, same reason as the zero-result decline below: this
        # path did not answer the inbound message.
        logger.info("orchestrator.concierge.direct_show_masters.not_claimed trace=%s", trace_id)
        return None
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
