"""Имя человека из события MAX — для каждого вида события своё место (DRF-1503).

Ручной проход соло-регистрации на стенде 13.11.2026: после «Я работаю сам»
бот предложил «Оставить «Ayla Бот для мастеров»» — имя БОТА. Согласившись,
человек стал мастером «Ayla Бот для мастеров» (кружок «АБ»), а его салон —
«Студия Ayla» (название берёт первое слово имени).

Причина: имя читалось из ``message.sender``. У нажатия кнопки
(``message_callback``) ``message`` — это сообщение бота с клавиатурой, и в
его ``sender`` стоит бот; человек — в ``callback.user``. У ``bot_started``
(диплинк ``inv_<код>``) ``message`` нет вовсе, человек — в верхнем ``user``.

Узлы ниже прогоняют настоящие формы через настоящий парсер
(``parse_max_webhook``), а не фейки: прежние фейки клали имя человека в
``message.sender`` и для нажатий, и потому дефект был зелёным.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from apps.channels.bot_registry import BotEntry
from apps.channels.max import salon_handler
from apps.channels.max.parser import parse_max_webhook
from apps.identity.models import BotUser
from apps.identity.services.staff_invites import issue_staff_invite
from apps.tenancy.models import StaffInvite, Tenant, TenantStaff

PERSON = 12345
BOT = 9001
BOT_NAME = "Ayla Бот для мастеров"


def _tap(payload: str, *, user: dict) -> dict:
    return {
        "update_type": "message_callback",
        "timestamp": 1731320000000,
        "callback": {
            "timestamp": 1731320000000,
            "callback_id": "cb-uuid",
            "payload": payload,
            "user": user,
        },
        "message": {
            "sender": {"user_id": BOT, "name": BOT_NAME, "is_bot": True},
            "recipient": {"chat_id": 67890, "chat_type": "dialog"},
            "body": {"mid": "msg-uuid", "text": "Здравствуйте!"},
        },
    }


def _said(user: dict) -> dict:
    return {
        "update_type": "message_created",
        "timestamp": 1731320000000,
        "message": {
            "sender": user,
            "recipient": {"chat_id": 67890, "chat_type": "dialog"},
            "body": {"mid": "msg-2", "seq": 1, "text": "Привет"},
        },
    }


def _started(user: dict, deeplink: str = "") -> dict:
    payload = {
        "update_type": "bot_started",
        "timestamp": 1731320000000,
        "chat_id": 67890,
        "user": user,
    }
    if deeplink:
        payload["payload"] = deeplink
    return payload


def _name(payload: dict) -> str:
    return salon_handler._Identity.of(parse_max_webhook(payload)).display_name


class TestTheNameComesFromThePerson:
    def test_a_tap_names_the_person_not_the_bot(self) -> None:
        event = parse_max_webhook(
            _tap("cb:solo:register", user={"user_id": PERSON, "name": "Ольга"})
        )
        # Присутствие: бот действительно стоит в message.sender этого события.
        assert event.raw["message"]["sender"]["name"] == BOT_NAME
        assert event.channel_user_id == str(PERSON)
        assert salon_handler._Identity.of(event).display_name == "Ольга"

    def test_a_tap_without_a_name_is_nameless_never_the_bot(self) -> None:
        assert _name(_tap("cb:solo:register", user={"user_id": PERSON})) == ""

    def test_a_message_names_its_sender(self) -> None:
        assert _name(_said({"user_id": PERSON, "name": "Ольга"})) == "Ольга"

    def test_a_deeplink_start_names_the_person(self) -> None:
        # «inv_<код>» приходит bot_started: message нет, человек — в user.
        assert _name(_started({"user_id": PERSON, "name": "Ольга"}, "inv_AYLA-7K3M")) == "Ольга"

    def test_first_and_last_name_when_there_is_no_name(self) -> None:
        user = {"user_id": PERSON, "first_name": "Ольга", "last_name": "Петрова"}
        assert _name(_tap("cb:solo:register", user=user)) == "Ольга Петрова"
        assert _name(_started({"user_id": PERSON, "first_name": "Ольга"})) == "Ольга"


class TestEdges:
    """Защитные ветки — на прямых словарях: через парсер их не получить."""

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            # Имя из пробелов — к first_name / last_name.
            ({"callback": {"user": {"name": "  ", "first_name": "Ольга"}}}, "Ольга"),
            # Искажённое нажатие НИКОГДА не уходит в message.sender (там бот).
            ({"callback": None, "message": {"sender": {"name": BOT_NAME}}}, ""),
            ({"callback": {"user": "Ольга"}, "message": {"sender": {"name": BOT_NAME}}}, ""),
            # Не словарь вовсе.
            (None, ""),
        ],
    )
    def test_unusable_shapes_name_nobody(self, raw, expected) -> None:
        event = SimpleNamespace(raw=raw)
        assert salon_handler._sender_name(event) == expected  # type: ignore[arg-type]


@pytest.mark.django_db
class TestIWorkAloneOffersMyName:
    """Сквозной: «Я работаю сам» настоящей формы → бот предлагает имя человека."""

    @pytest.fixture(autouse=True)
    def _cities(self, settings) -> None:
        settings.SOLO_REGISTRATION_CITIES = ["Пенза"]
        settings.SOLO_REGISTRATION_DRAFT_TTL_HOURS = 72

    def test_the_offer_carries_the_persons_name(self, monkeypatch) -> None:
        said: list[str] = []
        monkeypatch.setattr(
            salon_handler,
            "_reply",
            lambda event, text, attachments=None: said.append(text),
        )
        event = parse_max_webhook(
            _tap(
                salon_handler.SOLO_REGISTER_CALLBACK,
                user={"user_id": PERSON, "name": "Ольга"},
            )
        )

        assert salon_handler._solo_registration_step(event, entry=None) is True
        assert said, "бот ответил на «Я работаю сам»"
        assert "Оставить «Ольга»" in said[-1]
        assert BOT_NAME not in said[-1]


SALON_BOT = BotEntry(
    slug="salon",
    webhook_secret="wh-salon",  # pragma: allowlist secret
    api_token="token-salon",  # pragma: allowlist secret
    tenant_slug="formula-tela",
    stream="max_salon",
)


@pytest.mark.django_db
class TestTheStaffCodeLinkNamesTheRow:
    """Погашение кода по ссылке ``inv_<код>`` — НАСТОЯЩИЙ ``bot_started``.

    Изменение поведения этой правки, закреплённое здесь: до неё у
    ``bot_started`` имени не было (``message`` в нём нет), и строка
    сотрудника рождалась безымянной навсегда — резолвер дописывает только
    пустые поля, а заполнять было нечем. Теперь имя берётся из ``user``.
    """

    @pytest.fixture(autouse=True)
    def _bot(self, settings, monkeypatch) -> None:
        settings.MAX_BOT_REGISTRY = (SALON_BOT,)
        settings.MAX_BOT_TOKEN = "token-client"  # pragma: allowlist secret
        # Связь с каталогом — не предмет этого узла (как в test_salon_handler).
        monkeypatch.setattr("apps.channels.max.salon_entry.unlinked_reason", lambda *a, **kw: "")

    def test_the_new_staff_row_carries_the_persons_name(self) -> None:
        tenant, _ = Tenant.all_objects.get_or_create(
            slug="formula-tela", defaults={"name": "Формула тела"}
        )
        _, code = issue_staff_invite(tenant=tenant, role=StaffInvite.Role.ADMIN)
        payload = _started({"user_id": PERSON, "name": "Ольга"}, f"inv_{code.replace('-', '')}")

        with patch("apps.channels.max.outbound.send_message"):
            salon_handler.handle_salon_max_event(payload)

        person = BotUser.all_tenants.get(channel_user_id=str(PERSON))
        # Присутствие: код правда погашен этим событием.
        assert TenantStaff.all_tenants.filter(bot_user=person, role="admin").exists()
        assert person.display_name == "Ольга"
