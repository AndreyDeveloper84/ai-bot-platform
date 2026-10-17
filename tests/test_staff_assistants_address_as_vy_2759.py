"""The staff assistants are told to address staff as «вы» (DRF-2759, H).

Owner ruling 02.10.2026: in every staff context — master, owner,
administrator, receptionist; MAX, Telegram, Mini App, notifications, AI —
Ayla says «вы»: приветливо, спокойно, профессионально, без канцелярита. The
client canon («ты») is separate and is not touched.

A scan of the bot's staff-facing strings found no «ты»-form addressed to
staff (89 files, 627 Russian strings). What it did find: neither of the two
staff assistants' system prompts said anything about the form of address, so
the model picked one itself. This is that sentence.

### What this file holds

* both prompts — the master's and the administrator's — carry the owner's
  sentence, verbatim, from one constant;
* the client concierge's prompt does NOT carry it: the client canon stays.

### What it cannot hold

That the model then actually writes «вы». CI has no model; the replay gate
runs the CLIENT handlers and never reaches a staff assistant, so it is not a
witness here either. The witness for the model's words is a live turn.
"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from apps.admin_api.services.assistant import AdminSubject
from apps.master_api.services import assistant as master_assistant
from apps.orchestrator.concierge import build_concierge_system_prompt

OWNERS_SENTENCE = (
    "Обращайся к человеку на «вы»: приветливо, спокойно, профессионально, без канцелярита."
)
TODAY = date(2026, 10, 2)


def _master_prompt() -> str:
    return master_assistant._system_prompt(
        SimpleNamespace(name="Ольга"), today=TODAY, tz_label="Europe/Moscow"
    )


def _admin_prompt() -> str:
    subject = AdminSubject(
        tenant=SimpleNamespace(name="Формула тела", slug="formula-tela"),
        bot_user=SimpleNamespace(id=1, display_name="Андрей Иванов"),
        role_ctx=SimpleNamespace(is_owner=True, is_admin=False),
    )
    return subject.system_prompt(today=TODAY, tz_label="Europe/Moscow")


def test_the_sentence_is_the_owners_verbatim() -> None:
    assert master_assistant.STAFF_ADDRESS_LINE == OWNERS_SENTENCE


def test_the_masters_assistant_is_told() -> None:
    prompt = _master_prompt()
    assert "помощник мастера" in prompt
    assert OWNERS_SENTENCE in prompt.split("\n\n")


def test_the_administrators_assistant_is_told() -> None:
    prompt = _admin_prompt()
    assert "помощник администратора" in prompt
    assert OWNERS_SENTENCE in prompt.split("\n\n")


def test_the_client_concierge_is_not() -> None:
    """The client canon is «ты»; this sentence must not leak into the client's prompt."""

    prompt = build_concierge_system_prompt(today=TODAY)
    # Positive pair: this really is the concierge's prompt, with its own date grounding.
    assert TODAY.isoformat() in prompt
    assert OWNERS_SENTENCE not in prompt
    assert "Обращайся к человеку на «вы»" not in prompt
    # …and the client canon is still stated in its own words: «ты», not «вы».
    assert "Форму «вы» в обращении к клиенту не используй." in prompt
