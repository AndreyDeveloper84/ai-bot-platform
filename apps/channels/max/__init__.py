"""MAX messenger channel adapter (Sprint 2 / track D).

- `parser.parse_max_webhook(payload) → CanonicalEvent`
- `outbound.send_message(chat_id=… | user_id=…, text, ...)` — ответ в диалог
  события идёт по `chat_id`, письмо первым по `user_id` (DRF-1558)
- `handler.handle_max_event(payload, trace_id)` — full pipeline echo

Sprint 2 ships the parser + outbound + echo handler skeleton. Real
production-grade MAX adapter (SDK-based polling, callback buttons,
media handling) stays in `legacy_maxbot/` for now and gets drained
into here in Sprint 3+ alongside the AI orchestrator.
"""
