"""The master's assistant — one question, one answer (DRF-1061 step 1).

A master asks «когда у меня окно на два часа в четверг» in the salon bot
and gets an answer. Until now the bot answered staff with a menu, which is
right for actions and useless for questions that have no button.

### Why not the customer concierge

`AIConcierge` is single-pass: it parses a tool call, dispatches, and returns
an action. That suits the customer path, where a tool result is rendered as
cards. A master's question needs the opposite — data fetched, then put into
words. So this is a small loop of our own over `apps.llm.router`, the same
door `ai_drafts` already uses.

### Why the tool result does NOT go back as `role="tool"`

The obvious loop hands the result back in a tool message. The Anthropic
adapter cannot express that: `_split_system_message` only lifts `system`
out, and `tool_result` blocks are never assembled. Written the obvious way,
the assistant would work on OpenAI and break the moment an operator flipped
`SKILL_LLM_PROVIDER` — quietly, in production, on a surface staff rely on.

So the second call carries the data as an ordinary user message. Both
adapters handle that identically, and nothing in the vendor tool protocol
is load-bearing on the return path.

### One tool per turn

The loop runs at most one tool. A master is between clients; a chain of
calls costs seconds and money for questions that in practice need one
lookup. When the model asks for a second, we answer with what we have.

### Everything degrades to words

Rate limit, cost cap, safety, a provider outage, a malformed argument — each
ends in a short Russian sentence, never an exception. This runs inside the
MAX consumer: an unhandled error there is a message the person never gets
and a retry that repeats the failure.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from django.utils import timezone

logger = logging.getLogger(__name__)

#: Router tier. Separate from `master_draft` so operator overrides and the
#: cost split stay answerable per surface.
ASSISTANT_SKILL = "master_assistant"

#: Cap on what the person reads. Longer than a chat reply should be on a
#: phone between clients.
MAX_REPLY_CHARS = 700

MAX_TOOL_CALLS = 1

_WEEKDAYS_RU = (
    "понедельник",
    "вторник",
    "среда",
    "четверг",
    "пятница",
    "суббота",
    "воскресенье",
)

BUSY_TEXT = "Слишком много запросов подряд. Попробуйте через минуту."
COST_TEXT = "На сегодня лимит помощника исчерпан. Ваш день и заявки — кнопками ниже."
FAILED_TEXT = "Не получилось ответить. Попробуйте ещё раз или загляните в кабинет."
NO_MASTER_TEXT = "Не нашёл вашу карточку мастера — сообщите администратору салона."


@dataclass
class AssistantReply:
    """What to send, and what the transcript should remember."""

    text: str
    tool_name: str = ""
    llm_called: bool = False
    tokens_in: int = 0
    tokens_out: int = 0
    llm_provider: str = ""
    llm_model: str = ""
    llm_cost_usd: Decimal = field(default_factory=lambda: Decimal(0))
    blocked_categories: tuple[str, ...] = field(default_factory=tuple)


def _system_prompt(master, *, today: date, tz_label: str) -> str:
    from apps.persona.voice import SURFACE_SALON, assistant_identity

    identity = assistant_identity(SURFACE_SALON)
    name = getattr(master, "name", "") or "мастер"
    return "\n\n".join(
        [
            f"Ты — «{identity.name}», помощник мастера в салоне. "
            f"Отвечаешь мастеру {name}, это сотрудник, а не клиент.",
            # Same grounding the concierge needs (DRF-988): without it the
            # model lives at its training cutoff and rejects real dates.
            f"Сегодня {today.isoformat()} ({_WEEKDAYS_RU[today.weekday()]}), "
            f"часовой пояс {tz_label}. Относительные даты («завтра», «в четверг») "
            "считай от этой даты и передавай инструментам в формате ГГГГ-ММ-ДД.",
            "Отвечай коротко и по делу: человек между клиентами. "
            "Без приветствий и без «чем ещё могу помочь».",
            "У тебя есть инструменты для просмотра расписания. Если вопрос про "
            "день, загрузку или свободные окна — вызови инструмент, не угадывай. "
            "Если данных нет, так и скажи.",
            "Границы:\n"
            "- Ты видишь только данные ЭТОГО мастера. Про чужие дни и чужих "
            "клиентов отвечай, что это в кабинете салона у администратора.\n"
            "- Ты не врач: не ставишь диагнозов, не назначаешь препараты, не "
            "оцениваешь состояние кожи или здоровья клиента.\n"
            "- Ты не обещаешь за салон: ни скидок, ни возвратов, ни гарантий "
            "результата.\n"
            "- Не называй телефоны и контакты клиентов.",
            f"Ответ не длиннее {MAX_REPLY_CHARS} символов.",
        ]
    )


def _history_messages(history) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for row in history:
        body = (row.content or "").strip()
        if not body or row.role not in ("user", "assistant"):
            continue
        out.append({"role": row.role, "content": body})
    return out


def _complete(messages: list[dict[str, Any]], *, tenant, tools=None):
    """One provider round trip. Sync wrapper, same bridge `ai_drafts` uses."""

    from apps.llm.router import get_router

    provider = get_router().get_provider(tenant, skill=ASSISTANT_SKILL, op="complete")
    model = getattr(provider, "default_completion_model", "") or ""
    kwargs: dict[str, Any] = {"model": model}
    if tools:
        kwargs["tools"] = tools
    return asyncio.run(provider.complete(messages, **kwargs))


def _cost(result) -> Decimal:
    from apps.llm.pricing import compute_cost

    try:
        return Decimal(
            str(
                compute_cost(
                    getattr(result, "model", "") or "",
                    input_tokens=getattr(result, "prompt_tokens", 0) or 0,
                    output_tokens=getattr(result, "completion_tokens", 0) or 0,
                )
            )
        )
    except Exception:  # noqa: BLE001 — an unknown model must not cost an answer
        logger.warning("master_assistant.cost_unknown model=%s", getattr(result, "model", ""))
        return Decimal(0)


def answer_master_question(
    *,
    master,
    text: str,
    history=None,
    now: datetime | None = None,
) -> AssistantReply:
    """Answer one question from one master. Never raises."""

    from apps.master_api.services.ai_draft_limits import (
        check_and_consume_rate_limit,
        check_cost_cap,
    )
    from apps.master_api.services.assistant_tools import TOOL_SPECS, ToolError, run_tool
    from apps.orchestrator.safety.gate import evaluate_inbound
    from apps.orchestrator.safety.outbound import evaluate_outbound

    if master is None:
        return AssistantReply(text=NO_MASTER_TEXT)

    # Safety first, before the limiter: a person in crisis must not be told
    # to come back in a minute.
    inbound = evaluate_inbound(text)
    if not inbound.allowed:
        logger.info("master_assistant.safety_short_circuit master=%s", master.id)
        return AssistantReply(text=inbound.reply_text)

    limit = check_and_consume_rate_limit(master.id)
    if not limit.allowed:
        return AssistantReply(text=BUSY_TEXT)

    cost_guard = check_cost_cap(master.id, master.tenant_id)
    if not cost_guard.allowed:
        return AssistantReply(text=COST_TEXT)

    now = now or timezone.now()
    tz_label = getattr(getattr(master, "tenant", None), "timezone", "") or "Europe/Moscow"
    try:
        from zoneinfo import ZoneInfo

        today = now.astimezone(ZoneInfo(tz_label)).date()
    except Exception:  # noqa: BLE001
        today = now.date()

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": _system_prompt(master, today=today, tz_label=tz_label)},
        *_history_messages(history or []),
        {"role": "user", "content": text},
    ]

    reply = AssistantReply(text="")
    try:
        first = _complete(messages, tenant=master.tenant, tools=TOOL_SPECS)
    except Exception:  # noqa: BLE001 — provider outage is not a crash
        logger.exception("master_assistant.first_call_failed master=%s", master.id)
        return AssistantReply(text=FAILED_TEXT)

    reply.llm_called = True
    reply.llm_provider = getattr(first, "provider", "") or ""
    reply.llm_model = getattr(first, "model", "") or ""
    reply.tokens_in = getattr(first, "prompt_tokens", 0) or 0
    reply.tokens_out = getattr(first, "completion_tokens", 0) or 0
    reply.llm_cost_usd = _cost(first)

    calls = list(getattr(first, "tool_calls", None) or [])[:MAX_TOOL_CALLS]
    if not calls:
        return _finish(reply, getattr(first, "text", "") or "", evaluate_outbound)

    call = calls[0]
    try:
        outcome = run_tool(call.name, call.arguments or {}, master=master)
    except ToolError as exc:
        # The model asked for something it cannot have. Say so plainly —
        # inventing an answer here is how a master ends up trusting a
        # number nobody computed.
        return _finish(reply, f"Не смог посмотреть: {exc}", evaluate_outbound)
    except Exception:  # noqa: BLE001
        logger.exception("master_assistant.tool_failed tool=%s master=%s", call.name, master.id)
        return _finish(reply, FAILED_TEXT, evaluate_outbound)

    reply.tool_name = outcome.name

    # Second pass — data as an ordinary message, NOT a tool message. See the
    # module docstring: the Anthropic adapter cannot express `role="tool"`,
    # and a loop that only works on one vendor breaks silently on the other.
    messages.append(
        {
            "role": "user",
            "content": (
                f"Данные инструмента {outcome.name}:\n"
                f"{json.dumps(outcome.data, ensure_ascii=False)}\n\n"
                "Ответь мастеру по этим данным. Ничего не добавляй от себя."
            ),
        }
    )
    try:
        second = _complete(messages, tenant=master.tenant)
    except Exception:  # noqa: BLE001
        logger.exception("master_assistant.second_call_failed master=%s", master.id)
        return _finish(reply, FAILED_TEXT, evaluate_outbound)

    reply.tokens_in += getattr(second, "prompt_tokens", 0) or 0
    reply.tokens_out += getattr(second, "completion_tokens", 0) or 0
    reply.llm_cost_usd += _cost(second)
    reply.llm_model = getattr(second, "model", "") or reply.llm_model

    return _finish(reply, getattr(second, "text", "") or "", evaluate_outbound)


def _finish(reply: AssistantReply, text: str, checker) -> AssistantReply:
    """Trim, run the outbound check, and hand back what to send."""

    body = (text or "").strip()
    if not body:
        reply.text = FAILED_TEXT
        return reply

    verdict = checker(body[:MAX_REPLY_CHARS])
    reply.text = verdict.text
    reply.blocked_categories = verdict.categories
    return reply


__all__ = [
    "ASSISTANT_SKILL",
    "BUSY_TEXT",
    "COST_TEXT",
    "FAILED_TEXT",
    "MAX_REPLY_CHARS",
    "NO_MASTER_TEXT",
    "AssistantReply",
    "answer_master_question",
]
