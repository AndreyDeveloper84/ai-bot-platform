"""Opening the bot by an invitation link must open the invitation (DRF-1424).

## The fact this file is built on

Snapshot from the live pilot, 30.08, stream ``ingress:max_salon`` — the
owner opened ``https://max.ru/<bot>?start=master_invite_test``::

    {"update_type": "bot_started",
     "chat_id": 315714313,
     "user": {"user_id": 83146139, "first_name": "Андрей"},
     "payload": "master_invite_test",
     "user_locale": "ru"}

**MAX delivers ``?start=`` as the ``payload`` field of ``bot_started``.**
The tenant is already resolved by then — the stream entry carries
``resolved_tenant_id``.

## What DRF-1349 (#1332) left unfinished

#1332 made the invitation *enterable*: a DM with an ``open_app`` button
whose payload is ``master_invite_<uuid>``. But that DM can only be sent
to a MAX username the salon already knows, into a chat that already
exists. An owner who has the person's phone, or Telegram, or simply
wants to hand over a link, has nothing to hand over.

A start link works everywhere and needs no authentication to open. What
was missing is the other end: nothing in the bot read ``payload`` back
out. On plain ``dev`` the token arrives, is folded into the synthetic
``/start master_invite_<uuid>`` text by
:func:`apps.channels.max.parser._parse_bot_started`, fails
:func:`~apps.channels.max.salon_handler._extract_code` (which knows only
the ``inv_`` staff-code prefix), and the invited master is answered
«Пришлите код приглашения» — a code they were never given and that does
not exist for them.

## The shape of the assertions here

Rule of this contour, and the reason half these tests exist:

    **A negative claim needs a positive guard on the same data.**

«A plain ``bot_started`` still behaves as before» is green against a
handler that does nothing at all. So it sits next to «and one carrying a
token sends the button», reading the same mock, through the same entry
point. Same for every refusal: expired, spent and foreign tokens are
each paired with the same token in the state that must work.

Within a single test the same rule appears as a one-line presence
assertion ahead of each absence one — ``assert _text(sent)``, i.e. a
reply went out and it is not empty. «No invite button» is green against
a handler that sent nothing at all, and a handler that sends nothing is
the bug next door. ``tools/lint/negative_assert_guard.py`` (DRF-1411)
enforces exactly this, per function body.

## Why the payload is re-derived rather than restated

The slug is one contract written in two languages — Python in
``apps/admin_api/views_invite.py``, TypeScript in
``apps/miniapp/src/lib/max-sdk.ts`` — and
``apps/admin_api/tests/test_invite_entry.py`` already pins those two to
each other. This file adds the third corner: the button the *bot* builds
on the way in must be the same slug the *admin API* builds on the way
out. It is imported, never retyped.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any
from unittest.mock import patch

import pytest
from django.utils import timezone

from apps.catalog.models import CatalogMaster
from apps.channels.bot_registry import BotEntry
from apps.channels.max.salon_handler import handle_salon_max_event
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant, TenantStaff

pytestmark = pytest.mark.django_db

CHANNEL_USER_ID = "83146139"
CHAT_ID = "315714313"

#: The Mini App name of the salon bot. Without one no ``open_app``
#: button can be built at all, so it is part of the happy path's setup
#: rather than an extra.
WEB_APP = "id583_bot"

SALON_BOT = BotEntry(
    slug="salon",
    webhook_secret="wh-salon",  # pragma: allowlist secret
    api_token="token-salon",  # pragma: allowlist secret
    tenant_slug="formula-tela",
    stream="max_salon",
    web_app=WEB_APP,
)

#: A second salon, for the cross-tenant case. It has a bot of its own —
#: otherwise «the token is refused» could be explained by the handler
#: refusing to speak at all.
OTHER_BOT = BotEntry(
    slug="other",
    webhook_secret="wh-other",  # pragma: allowlist secret
    api_token="token-other",  # pragma: allowlist secret
    tenant_slug="other-salon",
    stream="max_salon",
    web_app="other_bot",
)


@pytest.fixture
def tenant() -> Tenant:
    obj, _ = Tenant.all_objects.get_or_create(
        slug="formula-tela", defaults={"name": "Формула тела"}
    )
    return obj


@pytest.fixture
def other_tenant() -> Tenant:
    obj, _ = Tenant.all_objects.get_or_create(slug="other-salon", defaults={"name": "Другой салон"})
    return obj


@pytest.fixture(autouse=True)
def _registry(settings):
    settings.MAX_BOT_REGISTRY = (SALON_BOT, OTHER_BOT)
    settings.MAX_BOT_TOKEN = "token-client"  # pragma: allowlist secret


@pytest.fixture
def sent():
    with patch("apps.channels.max.outbound.send_message") as mock:
        yield mock


def _invite_prefix() -> str:
    """The producer-side slug prefix, read from where #1332 declared it.

    Imported here rather than copied: ``test_invite_entry.py`` pins this
    same constant against the Mini App's TypeScript, so reading it makes
    this file the third corner of one contract instead of a fourth
    literal free to drift.
    """

    from apps.admin_api.views_invite import MASTER_INVITE_PAYLOAD_PREFIX

    return MASTER_INVITE_PAYLOAD_PREFIX


def _master(tenant: Tenant, **overrides: Any) -> CatalogMaster:
    """A PENDING invited master, expiring 7 days from now.

    No literal dates anywhere: every instant is an offset from ``now()``,
    so the row is in the same state on every run and in every timezone.
    """

    now = timezone.now()
    fields: dict[str, Any] = {
        "tenant": tenant,
        "external_id": CatalogMaster.all_tenants.filter(tenant=tenant).count() + 1,
        "external_updated_at": now,
        "name": "Мария",
        "max_handle": "@maria",
        "invite_status": CatalogMaster.InviteStatus.PENDING,
        "mode": CatalogMaster.Mode.INVITE,
        "invite_token": uuid.uuid4(),
        "invited_at": now,
        "invite_expires_at": now + timedelta(days=7),
    }
    fields.update(overrides)
    return CatalogMaster.all_tenants.create(**fields)


_next_offset = iter(range(1, 10_000))


def _bot_started(payload: str | None) -> dict:
    """One ``bot_started`` update, shaped as MAX actually sends it.

    Field-for-field from the pilot snapshot in the module docstring, with
    only the timestamp made to move: the parser derives
    ``channel_message_id`` from ``user_id`` plus timestamp, and the
    handler's idempotency claim lives 24h, so two events sharing a
    timestamp would make the second one vanish for reasons having
    nothing to do with what is under test.
    """

    event: dict[str, Any] = {
        "update_type": "bot_started",
        "timestamp": int(timezone.now().timestamp() * 1000) + next(_next_offset),
        "chat_id": int(CHAT_ID),
        "user": {
            "user_id": int(CHANNEL_USER_ID),
            "first_name": "Андрей",
            "last_name": "",
            "is_bot": False,
            "name": "Андрей",
        },
        "user_locale": "ru",
    }
    if payload is not None:
        event["payload"] = payload
    return event


def _open(tenant: Tenant, payload: str | None) -> None:
    with tenant_scope(tenant):
        handle_salon_max_event(_bot_started(payload))


def _buttons(mock) -> list[dict[str, Any]]:
    """Every button in the last dispatched message, flattened."""

    assert mock.called, "nothing was sent at all"
    attachments = mock.call_args.kwargs.get("attachments") or []
    out: list[dict[str, Any]] = []
    for attachment in attachments:
        for row in attachment.get("payload", {}).get("buttons", []):
            out.extend(row)
    return out


def _invite_buttons(mock) -> list[dict[str, Any]]:
    """Buttons that carry an invitation, and only those.

    Deliberately narrower than «any ``open_app`` button». The staff menu
    already builds one of its own (``cb:staff:open_app``,
    ``apps/channels/max/staff_menu.py``) whenever the bot has a
    ``web_app`` — which this file must configure, because the invite
    button cannot exist without one. Asserting on *open_app* rather than
    on *this contract* would make these tests read the neighbouring
    feature and fail for its reasons.
    """

    prefix = _invite_prefix()
    return [
        b
        for b in _buttons(mock)
        if b.get("type") == "open_app" and str(b.get("payload", "")).startswith(prefix)
    ]


def _text(mock) -> str:
    assert mock.called, "nothing was sent at all"
    return mock.call_args.kwargs["text"]


class TestInvitationOpensTheInvitation:
    """The link the owner hands over, and what it must produce."""

    def test_the_token_arrives_and_the_button_goes_back(self, tenant, sent):
        """The whole point: open the link, get the way in.

        Not «the handler did not crash» — the message must carry an
        ``open_app`` button whose payload is this invitation's token,
        because that button is the only entry into the Mini App that
        works (DRF-1349 / #1332: ``max://`` is not implemented and an
        https address opens a browser, which MAX gives no ``initData``).
        """

        master = _master(tenant)
        payload = f"{_invite_prefix()}{master.invite_token}"

        _open(tenant, payload)

        buttons = _invite_buttons(sent)
        assert len(buttons) == 1, f"expected exactly one open_app button, got {_buttons(sent)!r}"
        assert buttons[0]["payload"] == payload
        assert buttons[0]["web_app"] == WEB_APP
        assert tenant.name in _text(sent)

    def test_the_salon_bot_is_the_one_that_answers(self, tenant, sent):
        """Sent inside the salon bot's scope, not the client bot's.

        ``outbound.send_message`` takes its token from the surrounding
        ``bot_scope``; a reply escaping that scope falls back to
        ``MAX_BOT_TOKEN`` — the customer-facing bot. That failure is
        invisible in logs and unmistakable to the person who receives an
        invitation from the wrong avatar.
        """

        from apps.channels.bot_context import current_bot

        master = _master(tenant)
        seen: list[str] = []

        def _capture(**kwargs):
            entry = current_bot()
            seen.append(entry.slug if entry is not None else "")

        sent.side_effect = _capture

        _open(tenant, f"{_invite_prefix()}{master.invite_token}")

        assert seen == [SALON_BOT.slug]


class TestPlainStartIsUntouched:
    """``bot_started`` without a payload is an ordinary first contact.

    Both of these are negative claims about behaviour that must NOT
    change, so each one names the positive case it is measured against.
    """

    def test_no_payload_still_asks_for_a_code(self, tenant, sent):
        _open(tenant, None)

        assert "код приглашения" in _text(sent)
        # Positive guard on the same data: the branch under test is
        # reachable and does send a button when a token IS present.
        # Without this line the assertion above passes on a handler that
        # has no invite branch at all — which is exactly the state this
        # file was written against.
        master = _master(tenant)
        _open(tenant, f"{_invite_prefix()}{master.invite_token}")
        assert _invite_buttons(sent), "a real token must produce the button"

    def test_no_payload_carries_no_buttons(self, tenant, sent):
        _open(tenant, None)

        assert _text(sent), "silence is not an answer"  # presence, then absence
        assert _invite_buttons(sent) == []

    def test_an_unrelated_payload_is_left_alone(self, tenant, sent):
        """An attribution deeplink is not an invitation.

        ``ref_user_42`` and friends are the welcome skill's business
        (``_parse_bot_started``). Reading them here would break a flow
        this change has no opinion about.
        """

        _open(tenant, "ref_user_42")

        assert "код приглашения" in _text(sent)
        assert _invite_buttons(sent) == []


class TestARefusalSaysWhatIsWrong:
    """Expired, spent and foreign tokens each get their own sentence.

    Deliberately unlike the staff-code path, which hedges on purpose
    («возможно, использован или истёк»): a staff code is four characters
    and guessable, so telling an attacker which guess was close is a
    real leak. An invite token is a UUIDv4 — 122 bits — so anyone
    holding one already has it, and vagueness buys nothing while costing
    the invited master the one thing they need to know: whether to ask
    for a new link or to stop trying.
    """

    def test_expired_says_expired(self, tenant, sent):
        master = _master(tenant, invite_expires_at=timezone.now() - timedelta(seconds=1))

        _open(tenant, f"{_invite_prefix()}{master.invite_token}")

        assert "истек" in _text(sent).lower().replace("ё", "е")
        assert _invite_buttons(sent) == []

    def test_the_same_token_unexpired_works(self, tenant, sent):
        """Positive guard for the case above, on the same row shape."""

        master = _master(tenant, invite_expires_at=timezone.now() + timedelta(seconds=60))

        _open(tenant, f"{_invite_prefix()}{master.invite_token}")

        assert _invite_buttons(sent)

    def test_an_already_accepted_invite_is_not_re_offered(self, tenant, sent):
        master = _master(tenant, invite_status=CatalogMaster.InviteStatus.ACCEPTED)

        _open(tenant, f"{_invite_prefix()}{master.invite_token}")

        assert "принят" in _text(sent).lower()
        assert _invite_buttons(sent) == []

    def test_a_cancelled_invite_is_not_re_offered(self, tenant, sent):
        master = _master(tenant, invite_status=CatalogMaster.InviteStatus.CANCELLED)

        _open(tenant, f"{_invite_prefix()}{master.invite_token}")

        assert _text(sent), "silence is not an answer"  # presence, then absence
        assert _invite_buttons(sent) == []

    def test_a_token_from_another_salon_is_not_found_here(self, tenant, other_tenant, sent):
        """Cross-tenant: the same filter ``validate_invite_token`` applies.

        And the same deliberate collapse — «not found» rather than «not
        yours», so the answer cannot be used to discover that a token is
        live somewhere else.
        """

        foreign = _master(other_tenant)

        _open(tenant, f"{_invite_prefix()}{foreign.invite_token}")

        assert _text(sent), "silence is not an answer"  # presence, then absence
        assert _invite_buttons(sent) == []

    def test_that_same_token_works_in_its_own_salon(self, other_tenant, sent):
        """Positive guard: the refusal above is about the tenant, not the row."""

        foreign = _master(other_tenant)

        _open(other_tenant, f"{_invite_prefix()}{foreign.invite_token}")

        assert _invite_buttons(sent)

    @pytest.mark.parametrize(
        "tail",
        [
            "not-a-uuid",
            "",
            "00000000-0000-0000-0000-000000000000",
        ],
    )
    def test_a_token_that_names_nothing_is_refused(self, tenant, sent, tail):
        _open(tenant, f"{_invite_prefix()}{tail}")

        assert _text(sent), "silence is not an answer"  # presence, then absence
        assert _invite_buttons(sent) == []


class TestTheBotDoesNotDecideWhoseTokenItIs:
    """Opening a link proves possession of the link, and nothing else.

    ``bot_started`` carries ``user_id`` but no MAX username, and an
    invitation is addressed by ``max_handle``. So the bot has nothing to
    match on and must not pretend otherwise: it delivers the button and
    leaves the binding to ``/onboarding/claim`` and ``/onboarding/accept``,
    which run inside a session and already refuse a forwarded link
    (``wrong_recipient``, 403, when the row is linked to someone else).

    Which makes what the bot must NOT do the testable part.
    """

    def test_opening_the_link_does_not_spend_the_invitation(self, tenant, sent):
        master = _master(tenant)

        _open(tenant, f"{_invite_prefix()}{master.invite_token}")

        master.refresh_from_db()
        assert master.invite_status == CatalogMaster.InviteStatus.PENDING
        assert master.linked_bot_user_id is None
        # Positive guard: the run that changed nothing is the run that
        # DID send the button, not a run that returned early.
        assert _invite_buttons(sent)

    def test_opening_the_link_grants_no_role(self, tenant, sent):
        master = _master(tenant)

        _open(tenant, f"{_invite_prefix()}{master.invite_token}")

        assert not TenantStaff.all_tenants.filter(tenant=tenant).exists()
        assert _invite_buttons(sent)

    def test_a_second_person_opening_the_same_link_is_still_offered_it(self, tenant, sent):
        """Two openings, both answered — because neither is authenticated.

        The first opener is not proven to be the invitee either. Letting
        the first tap lock out everyone else would hand a stranger a way
        to burn an invitation they cannot use.
        """

        master = _master(tenant)
        payload = f"{_invite_prefix()}{master.invite_token}"

        _open(tenant, payload)
        assert _invite_buttons(sent)

        _open(tenant, payload)
        assert _invite_buttons(sent)


class TestTheInviteIsReadAboveTheRoleCascade:
    """An owner inviting himself must reach the invitation, not the menu.

    #1332 found exactly this shape on the Mini App side: the onboarding
    route was mounted under the master surface, the role cascade routed
    the owner elsewhere first, and the invitation vanished with no error.
    The bot has the same cascade — anyone already holding a role is sent
    to the staff menu before the text is looked at — and would reproduce
    the bug one layer down.
    """

    def test_an_owner_opening_an_invite_link_gets_the_invitation(self, tenant, sent):
        from apps.identity.services.resolver import resolve_or_create_bot_user

        with tenant_scope(tenant):
            person = resolve_or_create_bot_user(
                channel="max",
                channel_user_id=CHANNEL_USER_ID,
                display_name="Владелец",
                chat_id=CHAT_ID,
            )
            TenantStaff.all_tenants.create(tenant=tenant, bot_user=person, role="owner")

        master = _master(tenant)
        _open(tenant, f"{_invite_prefix()}{master.invite_token}")

        buttons = _invite_buttons(sent)
        assert buttons, "the role cascade swallowed the invitation"
        assert buttons[0]["payload"] == f"{_invite_prefix()}{master.invite_token}"

    def test_that_owner_still_gets_the_menu_without_a_token(self, tenant, sent):
        """Positive guard: the branch above did not replace ordinary use."""

        from apps.identity.services.resolver import resolve_or_create_bot_user

        with tenant_scope(tenant):
            person = resolve_or_create_bot_user(
                channel="max",
                channel_user_id=CHANNEL_USER_ID,
                display_name="Владелец",
                chat_id=CHAT_ID,
            )
            TenantStaff.all_tenants.create(tenant=tenant, bot_user=person, role="owner")

        _open(tenant, None)

        # The menu header names the salon — presence of the ordinary
        # reply, ahead of both claims about what it must NOT contain.
        assert tenant.name in _text(sent)
        assert not _invite_buttons(sent)
        assert "код приглашения" not in _text(sent)


class TestThePayloadIsAcceptableToMAX:
    """Guard 3: MAX answers an ``open_app`` payload with ``=``/``&``/``?``
    HTTP 400 ``proto.payload`` and the entry poisons the consumer PEL.

    The bot echoes back a payload it received from outside, so the
    producer-boundary check in ``outbound`` is the thing standing
    between a crafted start link and a poisoned stream.
    """

    def test_the_button_payload_is_a_flat_slug(self, tenant, sent):
        master = _master(tenant)

        _open(tenant, f"{_invite_prefix()}{master.invite_token}")

        payload = _invite_buttons(sent)[0]["payload"]
        assert not set("=&?") & set(payload), payload

    def test_a_crafted_querystring_payload_never_reaches_the_button(self, tenant, sent):
        """A start link is public: anyone can put anything after ``?start=``.

        ``master_invite_<uuid>?src=x`` must be refused as a token, not
        forwarded into a button that MAX rejects with a 400.
        """

        _open(tenant, f"{_invite_prefix()}{uuid.uuid4()}?src=x")

        assert _text(sent), "silence is not an answer"  # presence, then absence
        assert _invite_buttons(sent) == []
