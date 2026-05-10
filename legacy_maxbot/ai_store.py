"""BotConversationStore — Django-ORM реализация ConversationStore Protocol.

DRF-243.2 — адаптер `services_app.models.{Conversation, Message}` под
`ayla_ai_core.orchestrator.ConversationStore`. Sync-методы; AIConcierge
оборачивает их в sync_to_async автоматически.

Поведение 1:1 с приватными функциями из maxbot/ai_concierge.py
(_resolve_conversation / _save_message / _load_recent_history) — после
DRF-243.4 эти приватные функции удаляются, ai_concierge.py становится
shim'ом над AIConcierge с этим store.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID

from services_app.models import BotUser, Conversation, Message


class BotConversationStore:
    """Sync ConversationStore для бота Формула тела (single-tenant)."""

    def resolve_active_conversation(self, user_key: BotUser) -> Conversation:
        existing = (
            Conversation.objects
            .filter(bot_user=user_key, is_active=True)
            .order_by("-created_at")
            .first()
        )
        if existing is not None:
            return existing
        return Conversation.objects.create(bot_user=user_key, is_active=True)

    def save_message(
        self,
        conversation: Conversation,
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
    ) -> Message:
        msg = Message.objects.create(
            conversation=conversation,
            role=role,
            content=content,
            action_type=action_type,
            action_data=action_data,
            tool_call=tool_call,
            tool_call_id=tool_call_id,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            latency_ms=latency_ms,
        )
        conversation.last_message_at = msg.created_at
        conversation.save(update_fields=["last_message_at"])
        return msg

    def load_recent_history(
        self,
        conversation: Conversation,
        *,
        exclude_id: UUID | Any | None = None,
        limit: int = 10,
    ) -> list[Message]:
        qs = Message.objects.filter(conversation=conversation)
        if exclude_id is not None:
            qs = qs.exclude(id=exclude_id)
        recent = list(qs.order_by("-created_at")[:limit])
        recent.reverse()
        return recent
