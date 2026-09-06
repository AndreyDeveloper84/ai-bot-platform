"""A refused dispatch must reach the owner, not only the log (DRF-1505).

``_dispatch_max_dm`` learned to refuse: when neither an ``open_app``
button nor a usable web address can be built, it sends nothing and
returns ``{"delivery": "failed", "error": "no_entry_configured"}``. That
was the right call — a message with no way into the onboarding is not a
delivered invitation.

But the verdict stopped at the audit row and an ERROR line. The response
carried ``max_dm_delivery`` alone, so the screen could say «не удалось»
and nothing about why, and had no way at all to distinguish «MAX
answered 404 for that username» (retype the handle) from «this contour
has no Mini App name configured» (nothing the owner can do; call the
platform team). The comment at the refusal branch said so in as many
words and filed it.

``max_dm_error`` closes that. The assertions here are paired the way
this contour requires: every claim that a cause is reported sits beside
a claim that the *successful* path reports none, so «the field is
present» cannot pass by the field being a constant.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
from django.test import Client
from django.urls import reverse

from apps.admin_api.tests.conftest import init_data_header
from apps.channels.max import outbound as max_outbound
from apps.identity.models import BotUser
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db


def _body(**over: Any) -> dict[str, Any]:
    return {
        "name": "Анна Петрова",
        "contact_method": "max_username",
        "contact_value": "@anna_styl",
        "services": [],
        "schedule_preset": "default_mon_fri_10_19",
        "mode": "invite",
        **over,
    }


def _post(client: Client, **over: Any):
    return client.post(
        reverse("admin_api:master_invite_create"),
        data=_body(**over),
        content_type="application/json",
        HTTP_AUTHORIZATION=init_data_header("5001"),
    )


class TestTheOwnerIsToldWhyNothingWasSent:
    def test_no_entry_configured_reaches_the_response(
        self, client: Client, owner_bot_user: BotUser, tenant: Tenant, settings
    ):
        """Neither a Mini App name nor a usable domain — nothing is sent.

        This is the pilot's own 30.08 shape one notch worse: there the
        DM went out carrying a dead address; here it does not go out at
        all. Either way the owner used to read «получит сообщение в
        течение минуты».
        """

        settings.DEBUG = False
        settings.MAX_BOT_WEB_APP = ""
        settings.SITE_DOMAIN = "http://localhost:5173"

        with patch("apps.admin_api.views_invite.max_outbound.send_message") as send:
            resp = _post(client)

        assert resp.status_code == 201, resp.content
        payload = resp.json()
        assert payload["max_dm_delivery"] == "failed"
        assert payload["max_dm_error"] == "no_entry_configured"
        # The refusal is a refusal: nothing was handed to MAX.
        send.assert_not_called()

    def test_a_max_rejection_reaches_the_response_with_its_status(
        self, client: Client, owner_bot_user: BotUser, tenant: Tenant, settings
    ):
        """404 from MAX means the handle is wrong — the owner can fix it.

        Which is why the cause matters and a bare «не удалось» does not:
        one of these failures is a typo in a field on the same screen,
        the other is a deployment variable the owner has never heard of.
        """

        settings.DEBUG = False
        settings.MAX_BOT_WEB_APP = "salon_bot"
        settings.SITE_DOMAIN = "https://miniapp-dev.example"

        with patch("apps.admin_api.views_invite.max_outbound.send_message") as send:
            send.side_effect = max_outbound.MaxAPIError(404, "chat not found")
            resp = _post(client)

        assert resp.status_code == 201, resp.content
        payload = resp.json()
        assert payload["max_dm_delivery"] == "failed"
        assert payload["max_dm_error"] == "max_status_404"

    def test_a_deliberate_skip_names_its_reason_too(
        self, client: Client, owner_bot_user: BotUser, tenant: Tenant, settings
    ):
        """``max_phone`` is not a failure — it is a path we never built.

        The screen must not word it as breakage, and it can only tell the
        two apart because ``max_dm_delivery`` says ``skipped`` while the
        cause still arrives.
        """

        settings.DEBUG = False
        settings.MAX_BOT_WEB_APP = "salon_bot"
        settings.SITE_DOMAIN = "https://miniapp-dev.example"

        with patch("apps.admin_api.views_invite.max_outbound.send_message"):
            resp = _post(client, contact_method="max_phone", contact_value="+79161234567")

        payload = resp.json()
        assert payload["max_dm_delivery"] == "skipped"
        assert payload["max_dm_error"] == "max_phone_lookup_deferred"


class TestTheCauseIsASlugAndNeverTheCredential:
    """An unexpected failure reports ``unexpected``, not the exception.

    The branch used to answer ``str(exc)[:200]``. That was tolerable
    while the value went only into an audit row nobody parsed; DRF-1505
    published it as ``max_dm_error``, a documented field the Mini App
    reads by prefix.

    It also published a leak. ``make_inline_keyboard_attachment`` raises
    ``ValueError`` whose message embeds the rejected payload verbatim
    (Guard 3 in ``apps/channels/max/outbound.py``), and that payload is
    ``master_invite_<uuid>`` — the invitation credential. Prefix plus the
    repr fits inside 200 characters, so the whole token survived the
    truncation and settled into the audit row, which is a place people
    look.
    """

    def test_an_unexpected_exception_leaks_neither_token_nor_text(
        self, client: Client, owner_bot_user: BotUser, tenant: Tenant, settings
    ):
        from apps.audit.models import AuditLog
        from apps.events.vocabulary import MASTER_INVITE_DISPATCHED

        settings.DEBUG = False
        settings.MAX_BOT_WEB_APP = "salon_bot"
        settings.SITE_DOMAIN = "https://miniapp-dev.example"

        secret = "master_invite_deadbeef-0000-4000-8000-00000000cafe"
        with patch("apps.admin_api.views_invite.max_outbound.send_message") as send:
            send.side_effect = RuntimeError(f"payload rejected: got {secret!r}")
            resp = _post(client)

        body = resp.content.decode()
        payload = resp.json()
        # Положительные стражи первыми: ответ непустой и содержит именно
        # тот вердикт, о котором идёт речь. Без них «токена в теле нет»
        # зеленело бы на пустом ответе — том самом классе дыры, который
        # эта оснастка и ловит.
        assert payload["max_dm_delivery"] == "failed"
        assert payload["max_dm_error"] == "unexpected"
        assert payload["master_id"] in body
        assert secret not in body

        # And the durable copy is clean too — the response is transient,
        # the audit row is the one somebody reads a week later.
        rows = AuditLog.all_tenants.filter(
            tenant_id=tenant.id, action=MASTER_INVITE_DISPATCHED
        ).values_list("payload", flat=True)
        stored = list(rows)
        assert stored, "no dispatch audit row — the absence checks below prove nothing"
        assert all(secret not in str(row) for row in stored)
        assert any(row.get("error") == "unexpected" for row in stored)


class TestSuccessConfessesNothing:
    """Positive guard: the field is empty when there is nothing to say.

    Without this, every assertion above would still pass if
    ``max_dm_error`` were hardcoded — which is the same class of holed
    check as a PII sweep over an empty response body.
    """

    def test_a_queued_dispatch_carries_an_empty_error(
        self, client: Client, owner_bot_user: BotUser, tenant: Tenant, settings
    ):
        settings.DEBUG = False
        settings.MAX_BOT_WEB_APP = "salon_bot"
        settings.SITE_DOMAIN = "https://miniapp-dev.example"

        with patch("apps.admin_api.views_invite.max_outbound.send_message") as send:
            send.return_value = {"ok": True}
            resp = _post(client)

        payload = resp.json()
        assert payload["max_dm_delivery"] == "queued"
        assert payload["max_dm_error"] == ""


class TestTheRepeatTapSaysTheSameThing:
    """The idempotent replay must not lose the cause.

    The owner's ordinary response to «не доставлено» is to tap again.
    That request matches the still-PENDING row and never dispatches, so
    the verdict has to come from the stored audit row — and so does the
    reason, or the second answer contradicts the first by omission on
    exactly the tap where the owner is looking hardest.
    """

    def test_the_second_call_repeats_delivery_and_cause(
        self, client: Client, owner_bot_user: BotUser, tenant: Tenant, settings
    ):
        settings.DEBUG = False
        settings.MAX_BOT_WEB_APP = ""
        settings.SITE_DOMAIN = "http://localhost:5173"

        with patch("apps.admin_api.views_invite.max_outbound.send_message"):
            first = _post(client)
            second = _post(client)

        assert first.status_code == 201
        assert second.status_code == 200
        assert second["X-Idempotent"] == "true"
        assert second.json()["max_dm_delivery"] == first.json()["max_dm_delivery"] == "failed"
        assert second.json()["max_dm_error"] == "no_entry_configured"
