"""Вход салонного бота: пре-чек ×5, три исхода, клиентского пути нет (DRF-2113, §50 п.1–3).

Салонный бот — только для персонала. На входе — пять проверок по порядку
(личность → салон и роль → связь с каталогом → активность салона), и только
после них — рабочее меню. Три исхода для человека: STAFF / NOT_LINKED /
STRANGER; неактивный салон и пустая личность — именованные коды со своим
честным состоянием.

* e1 — по одному узлу на каждый код пре-чека, с именем;
* e2 — NOT_LINKED: текст листа дословно, «Повторить проверку» и
  «Обратиться в поддержку», ``open_app`` нет; после появления связи
  «Повторить проверку» открывает меню;
* e3 — STRANGER: текст листа дословно (с именем салона из слага записи и
  без), кнопка ``link`` на клиентского бота из реестра, без записи — без
  кнопки, текст тот же;
* e4 — незнакомец по-прежнему проходит по коду сотрудника;
* e5 — три ответа незнакомцу без кода, четвёртый — молчание; код после
  молчания погашается;
* e6 — перепись-страж: в потоке ``max_salon`` нет клиентского пути
  (``WelcomeSkill``, меню витрины, реестр навыков, клиентские хендлеры) —
  ложный вход: подсаженный модуль с вызовом → красный. На базе 37325b0d
  страж зелёный: снимать было нечего, посылка листа не подтвердилась
  (клиентский S1 у тенантного бота живёт на legacy-стриме ``max``);
* e7 — «Обратиться в поддержку»: контакт из настроек, без — общий текст;
* e8 — админский код: роль выдана, каталожной половины нет → честное
  состояние вместо меню (дверь кода не связывает каталог — это делает
  оператор «Выдать роль», DRF-2085).
"""

from __future__ import annotations

import ast
import re
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest
from django.core.cache import cache
from django.utils import timezone

from apps.catalog.models import CatalogMaster
from apps.channels.bot_registry import BotEntry
from apps.channels.max import salon_entry, salon_handler
from apps.channels.max.salon_handler import handle_salon_max_event
from apps.identity.models import BotUser
from apps.identity.services.staff_invites import issue_staff_invite
from apps.tenancy.models import StaffInvite, Tenant, TenantStaff

pytestmark = pytest.mark.django_db

CHANNEL_USER_ID = "2113001"
CHAT_ID = "2113"

SALON_BOT = BotEntry(
    slug="salon",
    webhook_secret="secret-salon",  # pragma: allowlist secret
    api_token="token-salon",  # pragma: allowlist secret
    tenant_slug="formula-tela",
    stream="max_salon",
)
CLIENT_BOT = BotEntry(
    slug="client",
    webhook_secret="secret-client",  # pragma: allowlist secret
    api_token="token-client",  # pragma: allowlist secret
    stream="max_global",
    link="https://max.ru/ayla_client_bot",
)
CLIENT_BOT_NO_LINK = BotEntry(
    slug="client",
    webhook_secret="secret-client",  # pragma: allowlist secret
    api_token="token-client",  # pragma: allowlist secret
    stream="max_global",
)


@pytest.fixture
def tenant() -> Tenant:
    obj, _ = Tenant.all_objects.get_or_create(
        slug="formula-tela", defaults={"name": "Формула тела"}
    )
    return obj


@pytest.fixture(autouse=True)
def _registry(settings):
    settings.MAX_BOT_REGISTRY = (SALON_BOT, CLIENT_BOT)
    settings.MAX_BOT_TOKEN = "token-client"  # pragma: allowlist secret
    settings.AYLA_SUPPORT_CONTACT = ""


@pytest.fixture(autouse=True)
def _no_catalog(monkeypatch):
    """Каталог в тестах не спрашивается: связь — состояние строки, не сеть."""
    monkeypatch.setattr(
        "apps.identity.services.ayla_link.ensure_ayla_link", lambda bot_user, **kw: None
    )


@pytest.fixture(autouse=True)
def _clean_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def sent():
    with patch("apps.channels.max.outbound.send_message") as mock:
        yield mock


def _payload(text: str, *, update_id: int = 1, user_id: str = CHANNEL_USER_ID) -> dict:
    return {
        "update_type": "message_created",
        "update_id": update_id,
        "timestamp": 1_700_000_000_000,
        "message": {
            "sender": {"user_id": int(user_id), "name": "Гость", "is_bot": False},
            "recipient": {"chat_id": int(CHAT_ID), "user_id": 999, "chat_type": "dialog"},
            "body": {"mid": f"mid-{update_id}", "seq": 1, "text": text, "attachments": []},
        },
    }


def _handle(text: str, *, update_id: int = 1) -> None:
    handle_salon_max_event(_payload(text, update_id=update_id))


def _texts(sent) -> list[str]:
    return [c.kwargs["text"] for c in sent.call_args_list]


def _buttons(call) -> list[dict]:
    """Кнопки в проводном формате MAX: ``{type, text, payload|url|…}``."""
    out: list[dict] = []
    for att in call.kwargs.get("attachments") or []:
        for row in (att.get("payload") or {}).get("buttons") or []:
            out.extend(row)
    return out


def _labels(call) -> list[str]:
    return [b["text"] for b in _buttons(call)]


def _payloads(call) -> list[str]:
    return [b["payload"] for b in _buttons(call) if b.get("type") == "callback"]


def _admin(tenant: Tenant, *, linked: bool, proxy: bool = False) -> BotUser:
    user = BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id=CHANNEL_USER_ID,
        chat_id=CHAT_ID,
        ayla_user_id=uuid.uuid4() if (linked or proxy) else None,
        ayla_user_id_is_proxy=(False if linked else (True if proxy else None)),
    )
    TenantStaff.all_tenants.create(tenant=tenant, bot_user=user, role=TenantStaff.Role.ADMIN)
    return user


def _master(tenant: Tenant, *, keyed: bool) -> BotUser:
    user = BotUser.all_tenants.create(
        tenant=tenant, channel="max", channel_user_id=CHANNEL_USER_ID, chat_id=CHAT_ID
    )
    CatalogMaster.all_tenants.create(
        tenant=tenant,
        external_id=-2113,
        external_updated_at=timezone.now(),
        name="Анна",
        linked_bot_user=user,
        invite_status=CatalogMaster.InviteStatus.ACCEPTED,
        is_active=True,
        ayla_user_id=uuid.uuid4() if keyed else None,
    )
    return user


class _Event:
    def __init__(self, channel_user_id: str = CHANNEL_USER_ID, text: str = "привет") -> None:
        self.channel = "max"
        self.channel_user_id = channel_user_id
        self.text = text
        self.chat_id = CHAT_ID
        self.raw: dict = {}


class TestE1EveryVerdictHasAName:
    def test_no_identity(self) -> None:
        assert salon_entry.precheck(_Event(channel_user_id="")).code == salon_entry.NO_IDENTITY

    def test_stranger_without_a_row(self) -> None:
        assert salon_entry.precheck(_Event()).code == salon_entry.STRANGER

    def test_stranger_with_a_customer_row(self, tenant) -> None:
        BotUser.all_tenants.create(
            tenant=tenant, channel="max", channel_user_id=CHANNEL_USER_ID, chat_id=CHAT_ID
        )
        assert salon_entry.precheck(_Event()).code == salon_entry.STRANGER

    @pytest.mark.parametrize(
        ("linked", "proxy", "reason"),
        [
            (False, False, salon_entry.REASON_NO_AYLA_USER),
            (False, True, salon_entry.REASON_PROXY_ONLY),
        ],
    )
    def test_admin_without_a_real_link_is_not_linked(self, tenant, linked, proxy, reason) -> None:
        _admin(tenant, linked=linked, proxy=proxy)
        verdict = salon_entry.precheck(_Event())
        assert verdict.code == salon_entry.NOT_LINKED and verdict.reason == reason
        assert verdict.tenant == tenant  # салон и роль найдены — не незнакомец

    def test_master_without_an_ayla_key_is_not_linked(self, tenant) -> None:
        _master(tenant, keyed=False)
        assert salon_entry.precheck(_Event()).reason == salon_entry.REASON_MASTER_CARD_UNLINKED

    def test_master_with_a_key_is_staff(self, tenant) -> None:
        _master(tenant, keyed=True)
        verdict = salon_entry.precheck(_Event())
        assert verdict.is_staff and verdict.role_ctx.is_master

    def test_inactive_salon_after_the_link(self, tenant) -> None:
        _admin(tenant, linked=True)
        tenant.is_active = False
        tenant.save(update_fields=["is_active"])
        verdict = salon_entry.precheck(_Event())
        assert verdict.code == salon_entry.SALON_INACTIVE
        # Порядок владельца: связь раньше активности — неподключённый в
        # неактивном салоне слышит «связь не завершена», не «салон отключён».
        BotUser.all_tenants.filter(channel_user_id=CHANNEL_USER_ID).update(
            ayla_user_id=None, ayla_user_id_is_proxy=None
        )
        assert salon_entry.precheck(_Event()).code == salon_entry.NOT_LINKED

    def test_staff(self, tenant) -> None:
        _admin(tenant, linked=True)
        verdict = salon_entry.precheck(_Event())
        assert verdict.is_staff and verdict.role_ctx.is_admin
        _master(Tenant.objects.create(slug="other-2113", name="Другой"), keyed=True)


class TestE2NotLinkedIsAnHonestStateNotAnAdminPanel:
    def test_text_buttons_and_no_open_app(self, tenant, sent) -> None:
        _admin(tenant, linked=False)
        _handle("привет")
        assert sent.call_count == 1
        assert sent.call_args.kwargs["text"] == salon_handler.NOT_LINKED_TEXT
        assert _labels(sent.call_args) == [
            salon_handler.RECHECK_BUTTON,
            salon_handler.SUPPORT_BUTTON,
        ]
        assert _payloads(sent.call_args) == [
            salon_handler.CB_SALON_RECHECK,
            salon_handler.CB_SALON_SUPPORT,
        ]
        # Ни open_app (админка), ни link.
        assert {b["type"] for b in _buttons(sent.call_args)} == {"callback"}

    def test_recheck_opens_the_menu_once_linked(self, tenant, sent) -> None:
        user = _admin(tenant, linked=False)
        _handle(salon_handler.CB_SALON_RECHECK, update_id=1)
        assert sent.call_args.kwargs["text"] == salon_handler.NOT_LINKED_TEXT
        user.ayla_user_id = uuid.uuid4()
        user.ayla_user_id_is_proxy = False
        user.save(update_fields=["ayla_user_id", "ayla_user_id_is_proxy"])
        _handle(salon_handler.CB_SALON_RECHECK, update_id=2)
        assert sent.call_count == 2
        assert salon_handler.NOT_LINKED_TEXT not in sent.call_args.kwargs["text"]
        assert _buttons(sent.call_args), "меню персонала — с кнопками"

    def test_inactive_salon_text_and_support(self, tenant, sent) -> None:
        _admin(tenant, linked=True)
        tenant.is_active = False
        tenant.save(update_fields=["is_active"])
        _handle("привет")
        assert sent.call_args.kwargs["text"] == salon_handler.SALON_INACTIVE_TEXT.format(
            name="Формула тела"
        )
        assert _payloads(sent.call_args) == [salon_handler.CB_SALON_SUPPORT]


class TestE3StrangerIsToldWhoIsHereAndWhereToGo:
    def test_named_text_and_the_client_bot_link(self, tenant, sent) -> None:
        _handle("привет")
        text = sent.call_args.kwargs["text"]
        assert text.startswith(salon_handler.STRANGER_TEXT_NAMED.format(name="Формула тела"))
        assert salon_handler.SOLO_OFFER.strip() in text  # дверь соло остаётся (D2 12.09)
        link = [b for b in _buttons(sent.call_args) if b.get("type") == "link"]
        assert link == [
            {"type": "link", "text": salon_handler.CLIENT_BOT_BUTTON, "url": CLIENT_BOT.link}
        ]
        assert salon_handler.SOLO_OFFER_BUTTON in _labels(sent.call_args)

    def test_without_a_link_in_the_registry_no_button_same_text(
        self, tenant, sent, settings
    ) -> None:
        settings.MAX_BOT_REGISTRY = (SALON_BOT, CLIENT_BOT_NO_LINK)
        _handle("привет")
        text = sent.call_args.kwargs["text"]
        assert text.startswith(salon_handler.STRANGER_TEXT_NAMED.format(name="Формула тела"))
        assert not [b for b in _buttons(sent.call_args) if b.get("type") == "link"]

    def test_without_a_tenant_slug_no_name(self, sent, settings) -> None:
        settings.MAX_BOT_REGISTRY = (
            BotEntry(
                slug="salon",
                webhook_secret="secret-salon",  # pragma: allowlist secret
                api_token="token-salon",  # pragma: allowlist secret
                stream="max_salon",
            ),
            CLIENT_BOT,
        )
        _handle("привет")
        assert sent.call_args.kwargs["text"].startswith(salon_handler.STRANGER_TEXT)
        assert "«" not in salon_handler.STRANGER_TEXT


class TestE4TheCodeStillOpensTheDoor:
    def test_a_master_code_leads_to_the_menu(self, tenant, sent) -> None:
        with_card = CatalogMaster.all_tenants.create(
            tenant=tenant,
            external_id=-2114,
            external_updated_at=timezone.now(),
            name="Анна",
            invite_status=CatalogMaster.InviteStatus.PENDING,
            is_active=True,
            ayla_user_id=uuid.uuid4(),
        )
        _, code = issue_staff_invite(
            tenant=tenant, role=StaffInvite.Role.MASTER, catalog_master=with_card
        )
        _handle(code)
        assert sent.call_count == 1
        assert salon_handler.NOT_LINKED_TEXT not in sent.call_args.kwargs["text"]
        assert _buttons(sent.call_args), "рабочее меню мастера"


class TestE5ThreeRepliesThenSilence:
    def test_the_fourth_message_is_not_answered_but_a_code_is(self, tenant, sent) -> None:
        for i in range(1, 5):
            _handle("здравствуйте", update_id=i)
        assert sent.call_count == salon_handler.STRANGER_REPLY_LIMIT
        with_card = CatalogMaster.all_tenants.create(
            tenant=tenant,
            external_id=-2115,
            external_updated_at=timezone.now(),
            name="Анна",
            invite_status=CatalogMaster.InviteStatus.PENDING,
            is_active=True,
            ayla_user_id=uuid.uuid4(),
        )
        _, code = issue_staff_invite(
            tenant=tenant, role=StaffInvite.Role.MASTER, catalog_master=with_card
        )
        _handle(code, update_id=9)
        assert sent.call_count == salon_handler.STRANGER_REPLY_LIMIT + 1


_CLIENT_PATH_NAMES = frozenset(
    {
        "WelcomeSkill",
        "marketplace_menu_reply",
        "marketplace_fallback_reply",
        "handle_max_event",
        "handle_global_max_event",
        "generate_concierge_reply",
        "orchestrate_turn",
    }
)


def _client_path_calls(source: str) -> list[str]:
    """Имена клиентского пути, которые модуль ЗОВЁТ или ИМПОРТИРУЕТ (не докстринги)."""
    tree = ast.parse(source)
    hits: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in _CLIENT_PATH_NAMES:
            hits.append(node.id)
        elif isinstance(node, ast.Attribute) and node.attr in _CLIENT_PATH_NAMES:
            hits.append(node.attr)
        elif isinstance(node, ast.ImportFrom):
            hits.extend(a.name for a in node.names if a.name in _CLIENT_PATH_NAMES)
        elif isinstance(node, ast.Attribute) and node.attr == "dispatch":
            base = node.value
            base_name = getattr(base, "attr", None) or getattr(base, "id", None)
            if base_name == "registry":
                hits.append("registry.dispatch")
    return hits


class TestE6NoClientPathInTheSalonStream:
    _FILES = (
        Path(salon_handler.__file__),
        Path(salon_entry.__file__),
        Path(salon_handler.__file__).resolve().parents[1] / "handlers.py",
    )

    def test_census(self) -> None:
        sources = {p.name: p.read_text(encoding="utf-8") for p in self._FILES}
        assert "_serve_stranger" in sources["salon_handler.py"]  # положительно: файлы прочитаны
        assert "class SalonMaxHandler" in sources["handlers.py"]
        offenders = {name: _client_path_calls(src) for name, src in sources.items()}
        # handlers.py держит и клиентские хендлеры — стережётся только салонный класс.
        handlers_tree = ast.parse(sources["handlers.py"])
        salon_cls = next(
            n
            for n in ast.walk(handlers_tree)
            if isinstance(n, ast.ClassDef) and n.name == "SalonMaxHandler"
        )
        offenders["handlers.py"] = _client_path_calls(ast.unparse(salon_cls))
        assert offenders == {name: [] for name in offenders}, offenders

    def test_a_planted_call_is_caught(self) -> None:
        planted = "from apps.skills.welcome.skill import WelcomeSkill\nWelcomeSkill().handle(ctx)\n"
        assert _client_path_calls(planted) == ["WelcomeSkill", "WelcomeSkill"]
        assert _client_path_calls("registry.dispatch(ctx)\n") == ["registry.dispatch"]
        docstring_only = '"""WelcomeSkill упоминается только словами."""\n'
        assert _client_path_calls(docstring_only) == []


class TestE7SupportButton:
    def test_contact_from_settings_or_generic(self, tenant, sent, settings) -> None:
        _admin(tenant, linked=False)
        _handle(salon_handler.CB_SALON_SUPPORT, update_id=1)
        assert sent.call_args.kwargs["text"] == salon_handler.SUPPORT_FALLBACK_TEXT
        settings.AYLA_SUPPORT_CONTACT = "@ayla_support"
        _handle(salon_handler.CB_SALON_SUPPORT, update_id=2)
        assert sent.call_args.kwargs["text"] == "Поддержка Ayla: @ayla_support"

    def test_stranger_gets_support_too(self, sent, settings) -> None:
        settings.AYLA_SUPPORT_CONTACT = "@ayla_support"
        _handle(salon_handler.CB_SALON_SUPPORT)
        assert sent.call_args.kwargs["text"] == "Поддержка Ayla: @ayla_support"


class TestE8AnAdminCodeGrantsTheRoleButNotTheMenu:
    def test_after_the_code_the_state_is_not_linked(self, tenant, sent) -> None:
        _, code = issue_staff_invite(tenant=tenant, role=StaffInvite.Role.ADMIN)
        _handle(code)
        assert sent.call_count == 1
        text = sent.call_args.kwargs["text"]
        assert salon_handler.NOT_LINKED_TEXT in text
        assert re.search(r"администратор", text, re.I)  # роль выдана и названа
        assert _payloads(sent.call_args) == [
            salon_handler.CB_SALON_RECHECK,
            salon_handler.CB_SALON_SUPPORT,
        ]
        assert TenantStaff.all_tenants.filter(
            tenant=tenant, bot_user__channel_user_id=CHANNEL_USER_ID, deactivated_at__isnull=True
        ).exists()
