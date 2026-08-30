"""MAX outbound REST send_message tests (DRF-441 / Sprint 2 / D2).

Uses pytest-httpx to mock httpx.post. Tests pin the wire format
(Authorization header raw token, chat_id as query param, body shape)
plus error handling on 4xx/5xx and network failures.
"""

from __future__ import annotations

import re

import pytest

from apps.channels.max.outbound import (
    MAX_KEYBOARD_ROWS,
    MaxAPIError,
    make_inline_keyboard_attachment,
    make_inline_keyboard_attachment_rows,
    send_message,
)


@pytest.fixture(autouse=True)
def _max_token(settings):
    settings.MAX_BOT_TOKEN = "test-token-xyz"
    settings.MAX_API_BASE = "https://botapi.max.ru"
    return settings


class TestSendMessageHappyPath:
    def test_returns_response_json_on_200(self, httpx_mock):
        httpx_mock.add_response(
            method="POST",
            url="https://botapi.max.ru/messages?chat_id=67890",
            json={"message": {"body": {"mid": "out-1"}}},
            status_code=200,
        )
        result = send_message(chat_id="67890", text="Привет")
        assert result == {"message": {"body": {"mid": "out-1"}}}

    def test_authorization_header_raw_token_not_bearer(self, httpx_mock):
        """MAX uses raw access token in Authorization, NOT Bearer prefix.
        Regression guard: the legacy code in
        legacy_notifications.max_bot.send_max_message has been working
        with raw token since 2026-04. If someone "fixes" it to Bearer,
        prod breaks silently.
        """

        httpx_mock.add_response(json={"ok": True}, status_code=200)
        send_message(chat_id="1", text="x")
        req = httpx_mock.get_request()
        assert req.headers["Authorization"] == "test-token-xyz"
        # Defensive: ensure no "Bearer " prefix snuck in.
        assert not req.headers["Authorization"].lower().startswith("bearer")

    def test_chat_id_as_query_param_not_body(self, httpx_mock):
        httpx_mock.add_response(json={"ok": True}, status_code=200)
        send_message(chat_id="42", text="hi")
        req = httpx_mock.get_request()
        assert "chat_id=42" in str(req.url)
        # Body must NOT contain chat_id (it lives in the query string).
        import json

        body = json.loads(req.content)
        assert "chat_id" not in body
        assert body["text"] == "hi"

    def test_attachments_pass_through_to_body(self, httpx_mock):
        httpx_mock.add_response(json={"ok": True}, status_code=200)
        send_message(
            chat_id="1",
            text="",
            attachments=[{"type": "image", "payload": {"url": "https://x/y.jpg"}}],
        )
        req = httpx_mock.get_request()
        import json

        body = json.loads(req.content)
        assert body["attachments"][0]["type"] == "image"


class TestSendMessageErrors:
    def test_4xx_raises_max_api_error(self, httpx_mock):
        httpx_mock.add_response(status_code=400, text="bad chat_id")
        with pytest.raises(MaxAPIError) as exc_info:
            send_message(chat_id="bad", text="x")
        assert exc_info.value.status_code == 400
        assert "bad chat_id" in exc_info.value.body

    def test_5xx_raises_max_api_error(self, httpx_mock):
        httpx_mock.add_response(status_code=502, text="upstream down")
        with pytest.raises(MaxAPIError) as exc_info:
            send_message(chat_id="1", text="x")
        assert exc_info.value.status_code == 502

    def test_network_error_raises_max_api_error_with_status_zero(self, httpx_mock):
        import httpx as _httpx

        httpx_mock.add_exception(_httpx.ConnectError("connection refused"))
        with pytest.raises(MaxAPIError) as exc_info:
            send_message(chat_id="1", text="x")
        assert exc_info.value.status_code == 0
        assert "connection refused" in exc_info.value.body

    def test_missing_token_raises(self, settings, httpx_mock):
        settings.MAX_BOT_TOKEN = ""
        with pytest.raises(MaxAPIError) as exc_info:
            send_message(chat_id="1", text="x")
        assert exc_info.value.status_code == 0
        assert "MAX_BOT_TOKEN" in exc_info.value.body
        # No HTTP call should have been attempted.
        assert httpx_mock.get_requests() == []


class TestInlineKeyboardAttachment:
    """make_inline_keyboard_attachment builds the MAX wire shape from
    the channel-agnostic ``[{label, callback}]`` list. The output must
    match the dev.max.ru spec exactly: top-level ``type``+``payload``,
    nested ``buttons`` as a 2-D matrix, each button with ``type`` discriminator.
    """

    def test_callback_button_wire_shape(self):
        att = make_inline_keyboard_attachment(
            [{"label": "📅 Записаться", "callback": "cb:welcome:book"}],
        )
        assert att["type"] == "inline_keyboard"
        assert att["payload"]["buttons"] == [
            [{"type": "callback", "text": "📅 Записаться", "payload": "cb:welcome:book"}],
        ]

    def test_link_button_wire_shape(self):
        att = make_inline_keyboard_attachment(
            [{"label": "Сайт", "url": "https://example.com"}],
        )
        assert att["payload"]["buttons"] == [
            [{"type": "link", "text": "Сайт", "url": "https://example.com"}],
        ]

    def test_open_app_button_wire_shape(self):
        att = make_inline_keyboard_attachment(
            [
                {
                    "label": "📅 Записаться",
                    # MAX-hardening Guard 3 — flat slug required; updated
                    # from legacy `route=catalog` form which would now raise.
                    "callback": "catalog",
                    "web_app": "id583_bot",
                },
            ],
        )
        btn = att["payload"]["buttons"][0][0]
        assert btn["type"] == "open_app"
        assert btn["text"] == "📅 Записаться"
        assert btn["web_app"] == "id583_bot"
        # Channel-agnostic ``callback`` reused as MAX ``payload`` for open_app.
        assert btn["payload"] == "catalog"

    def test_columns_layout_pairs_buttons(self):
        att = make_inline_keyboard_attachment(
            [
                {"label": "A", "callback": "cb:a"},
                {"label": "B", "callback": "cb:b"},
                {"label": "C", "callback": "cb:c"},
            ],
            columns=2,
        )
        rows = att["payload"]["buttons"]
        assert len(rows) == 2
        assert [b["text"] for b in rows[0]] == ["A", "B"]
        assert [b["text"] for b in rows[1]] == ["C"]

    def test_columns_default_is_one_per_row(self):
        att = make_inline_keyboard_attachment(
            [{"label": x, "callback": f"cb:{x}"} for x in ("A", "B", "C")],
        )
        assert [len(row) for row in att["payload"]["buttons"]] == [1, 1, 1]

    def test_invalid_columns_raises(self):
        with pytest.raises(ValueError, match="columns must be >= 1"):
            make_inline_keyboard_attachment(
                [{"label": "A", "callback": "cb:a"}],
                columns=0,
            )

    def test_rows_form_preserves_grouping(self):
        att = make_inline_keyboard_attachment_rows(
            [
                [{"label": "Да", "callback": "cb:yes"}, {"label": "Нет", "callback": "cb:no"}],
                [{"label": "Назад", "callback": "cb:back"}],
            ],
        )
        rows = att["payload"]["buttons"]
        assert [b["text"] for b in rows[0]] == ["Да", "Нет"]
        assert [b["text"] for b in rows[1]] == ["Назад"]

    def test_empty_buttons_emits_empty_grid(self):
        att = make_inline_keyboard_attachment([])
        assert att == {"type": "inline_keyboard", "payload": {"buttons": []}}


class TestMaxHardeningGuards:
    """MAX-hardening 3-guards bundle (2026-06-02, founder pilot scope).

    Defends against three production-observed gotchas:

    1. ``request_contact`` button type — outbound producer support.
    2. ``MAX_KEYBOARD_ROWS=29`` cap — silent truncation prevention.
    3. ``open_app`` flat-slug payload — memory `max_open_app_payload_format`
       querystring shape gets HTTP 400 + poisons consumer PEL.
    """

    def test_guard1_request_contact_button_wire_shape(self):
        att = make_inline_keyboard_attachment(
            [{"label": "📱 Поделиться номером", "request_contact": True}],
        )
        rows = att["payload"]["buttons"]
        assert rows == [
            [{"type": "request_contact", "text": "📱 Поделиться номером"}],
        ]

    def test_guard1_request_contact_has_no_payload_field(self):
        # Per MAX docs, request_contact buttons DON'T carry payload —
        # user's tap returns the contact via `message_created` attachment.
        # Producer MUST NOT emit a `payload` key (MAX rejects unknown keys).
        att = make_inline_keyboard_attachment(
            [
                {
                    "label": "Поделиться",
                    "request_contact": True,
                    "callback": "should-not-leak",
                },
            ],
        )
        btn = att["payload"]["buttons"][0][0]
        assert btn["type"] == "request_contact"
        assert "payload" not in btn
        assert "callback" not in btn

    def test_guard2_keyboard_rows_clamped_at_cap_flat_form(self):
        # 35 vertical buttons (one per row by default columns=1) →
        # clamped to MAX_KEYBOARD_ROWS=29.
        buttons = [{"label": f"B{i}", "callback": f"cb:{i}"} for i in range(35)]
        att = make_inline_keyboard_attachment(buttons)
        rows = att["payload"]["buttons"]
        assert len(rows) == MAX_KEYBOARD_ROWS
        # First 29 preserved; last 6 dropped.
        assert rows[0][0]["text"] == "B0"
        assert rows[-1][0]["text"] == f"B{MAX_KEYBOARD_ROWS - 1}"

    def test_guard2_keyboard_rows_clamped_at_cap_rows_form(self):
        rows_in = [[{"label": f"R{i}", "callback": f"cb:{i}"}] for i in range(40)]
        att = make_inline_keyboard_attachment_rows(rows_in)
        out_rows = att["payload"]["buttons"]
        assert len(out_rows) == MAX_KEYBOARD_ROWS

    def test_guard2_under_cap_passes_through_unchanged(self):
        buttons = [{"label": f"B{i}", "callback": f"cb:{i}"} for i in range(MAX_KEYBOARD_ROWS)]
        att = make_inline_keyboard_attachment(buttons)
        assert len(att["payload"]["buttons"]) == MAX_KEYBOARD_ROWS

    def test_guard3_open_app_payload_rejects_querystring_form(self):
        # Memory `max_open_app_payload_format` — payload `route=catalog`
        # would produce HTTP 400 + poison the consumer PEL. Reject at
        # producer boundary.
        with pytest.raises(ValueError, match="flat slug"):
            make_inline_keyboard_attachment(
                [
                    {
                        "label": "Open",
                        "callback": "route=catalog",
                        "web_app": "id583_bot",
                    },
                ],
            )

    def test_guard3_open_app_payload_rejects_ampersand(self):
        with pytest.raises(ValueError, match="flat slug"):
            make_inline_keyboard_attachment(
                [
                    {
                        "label": "Open",
                        "callback": "a=1&b=2",
                        "web_app": "id583_bot",
                    },
                ],
            )

    def test_guard3_open_app_payload_rejects_question_mark(self):
        # Bonus: querystring intro `?` is also forbidden — same poisoning
        # class as `=` / `&`.
        with pytest.raises(ValueError, match="flat slug"):
            make_inline_keyboard_attachment(
                [
                    {
                        "label": "Open",
                        "callback": "path?q=x",
                        "web_app": "id583_bot",
                    },
                ],
            )

    def test_guard3_open_app_payload_accepts_flat_slug(self):
        # Sanity — the shape MAX actually accepts MUST still pass through:
        # letters, digits, `_` and `-`, and nothing else. See
        # `test_guard3_open_app_payload_rejects_colon` for how that was
        # measured.
        att = make_inline_keyboard_attachment(
            [
                {
                    "label": "Open",
                    "callback": "master_invite_10c1ae97-ca49-4eca-b662-cc53ef76f27d",
                    "web_app": "id583_bot",
                },
            ],
        )
        assert (
            att["payload"]["buttons"][0][0]["payload"]
            == "master_invite_10c1ae97-ca49-4eca-b662-cc53ef76f27d"
        )

    def test_guard3_open_app_payload_rejects_colon(self):
        """The colon is NOT legal in an `open_app` payload.

        Measured, not assumed. `https://botapi.max.ru` was asked directly
        on 2026-08-30 with a live bot token: MAX validates this field
        before it resolves `web_app` or the chat, so a probe never
        delivers anything — a bad payload answers HTTP 400
        `proto.payload`, a good one falls through to a 404 about the
        `web_app` link. Sweeping `a<c>b` over printable ASCII, plus
        length and non-ASCII sweeps, gives exactly:

            ^[A-Za-z0-9_-]{0,512}$

        Until 2026-08-30 this module claimed only `=`, `&` and `?` were
        forbidden and `apps/channels/max/staff_menu.py` said in so many
        words that «colons are legal». Setting `MAX_BOT_SALON_WEB_APP` on
        the pilot turned that belief into a `cb:staff:open_app` payload on
        the wire and MAX answered 400 to every staff reply, so the salon
        bot stopped answering at all.
        """

        with pytest.raises(ValueError, match="flat slug"):
            make_inline_keyboard_attachment(
                [
                    {
                        "label": "Open",
                        "callback": "cb:staff:open_app",
                        "web_app": "id583_bot",
                    },
                ],
            )

    def test_guard3_open_app_payload_rejects_dot_space_and_cyrillic(self):
        for bad in ("a.b", "a b", "привет", "a/b", "a+b", "a@b"):
            with pytest.raises(ValueError, match="flat slug"):
                make_inline_keyboard_attachment(
                    [{"label": "Open", "callback": bad, "web_app": "id583_bot"}],
                )

    def test_guard3_open_app_payload_rejects_over_512_chars(self):
        # 512 accepted, 513 rejected — measured by binary search against
        # the live API on the same run as the character sweep.
        att = make_inline_keyboard_attachment(
            [{"label": "Open", "callback": "a" * 512, "web_app": "id583_bot"}],
        )
        assert len(att["payload"]["buttons"][0][0]["payload"]) == 512

        with pytest.raises(ValueError, match="flat slug"):
            make_inline_keyboard_attachment(
                [{"label": "Open", "callback": "a" * 513, "web_app": "id583_bot"}],
            )

    def test_guard3_every_open_app_payload_this_repo_builds_is_legal(self):
        """Sweep of the producers, so a new one cannot repeat this.

        Three places build an `open_app` button; all three feed their
        payload through `_button_to_max`, so it is their CONSTANTS that
        have to be legal:

        * `apps/skills/welcome/skill.py` — the Mini App route slugs;
        * `apps/admin_api/views_invite.py` — the master invitation;
        * `apps/channels/max/staff_menu.py` — the salon staff menu, which
          is covered where it is built (`apps/channels/tests/
          test_staff_menu.py::TestMiniAppButton`).
        """

        from apps.admin_api.views_invite import MASTER_INVITE_PAYLOAD_PREFIX
        from apps.skills.welcome.skill import MINIAPP_ROUTES

        pattern = re.compile(r"[A-Za-z0-9_-]{0,512}")

        assert MINIAPP_ROUTES, "no routes — the sweep below would pass vacuously"
        for slug in MINIAPP_ROUTES:
            assert pattern.fullmatch(slug), slug

        # A UUID is hex plus hyphens, so the prefix is the only part that
        # could go wrong.
        sample = f"{MASTER_INVITE_PAYLOAD_PREFIX}10c1ae97-ca49-4eca-b662-cc53ef76f27d"
        assert pattern.fullmatch(sample), sample

    def test_guard3_callback_button_unaffected_by_slug_check(self):
        # Slug check ONLY applies to open_app payloads (the MAX-specific
        # poisoning class). Plain callback buttons still accept any text.
        att = make_inline_keyboard_attachment(
            [{"label": "X", "callback": "cb:any=thing&with=stuff"}],
        )
        # Callback buttons still emit, no exception.
        assert att["payload"]["buttons"][0][0]["payload"] == "cb:any=thing&with=stuff"
