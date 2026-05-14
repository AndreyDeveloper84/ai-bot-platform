# mysite ground-truth captures

Captured replies from `mysite/maxbot/` for the platform's golden FAQ
fixtures. Consumed by `tests/e2e/test_replay_diff_vs_mysite.py` (Sprint 8
/ G3 / DRF-734).

## Status

Empty until Sprint 9 N4 staging soak — the test that consumes this
directory is `pytest.skip`-marked while no `golden_faq_replies.json`
exists.

## Capture procedure (Sprint 9)

1. SSH to the staging mysite host.
2. For each fixture under `apps/replay/fixtures/golden/faq/*.yaml`:
   - Send the fixture's `input.text` to the mysite/maxbot stack.
   - Capture the bot's reply, including the intent and action_type
     emitted by the legacy classifier.
3. Aggregate into one JSON file at this path with format below:

   ```json
   {
     "format_version": 1,
     "captured_at": "2026-05-XXTHH:MM:SSZ",
     "source": "mysite/maxbot/ commit <SHA>",
     "fixtures": [
       {
         "fixture": "kb_happy_hours",
         "user_text": "когда вы работаете?",
         "expected_intent": "faq",
         "expected_action_type": "faq"
       }
     ]
   }
   ```

4. Commit `golden_faq_replies.json` to the repo. The Sprint 8 / G3
   test auto-activates on next CI run.

## Why JSON and not CSV

The Sprint 8 / S3 ground-truth source (`SHADOW_GROUND_TRUTH_PATH`) is
CSV — exported from mysite's Postgres / Telegram-export. This file is
different: it's a curated, version-controlled snapshot of canonical
replies for our **own** golden fixtures, suitable for keeping under git.
JSON wins for diffability and structured-field access. The two formats
serve different lifecycles:

- CSV → daily shadow-mode delta (live traffic, S3/S4).
- JSON → offline regression gate (this directory, G3).
