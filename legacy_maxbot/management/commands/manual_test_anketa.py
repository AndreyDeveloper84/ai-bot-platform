"""Manual smoke-test для TIER-A anketa (Phase 3.1 Part 1).

Не запускает реальный бот — просто прогоняет все handler'ы синхронно
с realистичными данными и печатает state transitions + Ayla calls.

Не использует ayla_mock (это unit-test fixture). Делает реальные HTTP
вызовы к Ayla — поэтому требует AYLA_BASE_URL и NUTRITION_SERVICE_TOKEN
в env. Если Ayla недоступна — увидим NutritionUnavailableError, что
само по себе valid smoke (доказывает что код добегает до HTTP-слоя).

Usage:
    python manage.py manual_test_anketa --max-user-id 999
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Smoke-test TIER-A anketa без MAX SDK runtime"

    def add_arguments(self, parser):
        parser.add_argument("--max-user-id", type=int, default=999)

    def handle(self, *args, max_user_id, **options):
        async def run():
            from maxapi.context.context import MemoryContext
            from maxbot.handlers import nutrition_anketa, nutrition_entry
            from maxbot.states import NutritionAnketaStates

            ctx = MemoryContext(chat_id=max_user_id, user_id=max_user_id)

            def cb(payload):
                m = MagicMock()
                m.callback.payload = payload
                m.callback.user.user_id = max_user_id
                m.message.recipient.chat_id = max_user_id
                m.bot.send_message = AsyncMock(side_effect=lambda **kw: print(
                    f"[bot.send] {kw['text'][:80]}",
                ))
                return m

            def msg(text):
                m = MagicMock()
                m.message.body.text = text
                m.message.sender.user_id = max_user_id
                m.message.recipient.chat_id = max_user_id
                m.bot.send_message = AsyncMock(side_effect=lambda **kw: print(
                    f"[bot.send] {kw['text'][:80]}",
                ))
                return m

            steps = [
                ("entry", nutrition_entry.on_start_anketa, cb("cb:nutrition:start_anketa")),
                ("consent", nutrition_anketa.on_consent_ok, cb("cb:anketa:consent:ok")),
                ("gender", nutrition_anketa.on_gender_female, cb("cb:anketa:gender:female")),
                ("age", nutrition_anketa.on_age_text, msg("30")),
                ("height", nutrition_anketa.on_height_text, msg("165")),
                ("weight", nutrition_anketa.on_weight_text, msg("60")),
                ("goal", nutrition_anketa.on_goal_lose, cb("cb:anketa:goal:lose")),
                ("pace", nutrition_anketa.on_pace_moderate, cb("cb:anketa:pace:moderate")),
            ]

            for label, handler, event in steps:
                print(f"\n=== {label} ===")
                try:
                    await handler(event, ctx)
                    state = await ctx.get_state()
                    print(f"  → state = {state}")
                except Exception as exc:
                    print(f"  ✗ FAILED: {type(exc).__name__}: {exc}")
                    return

            print("\n✓ Smoke complete — TIER-A flow прошёл без exceptions.")

        asyncio.run(run())
