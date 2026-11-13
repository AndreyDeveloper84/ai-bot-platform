"""Соло-мастер видит в салонном боте мастерскую витрину (§69, вариант «в»).

Решение владельца 08.09.2026 (``docs/OPEN_DECISIONS.md`` §69): соло-мастер —
и мастер, и владелица своего кабинета; он видит **мастерское меню**, а
владельческое открывается **одной кнопкой «Салон»**. Меню по старшей роли
владелец отклонил прямо.

Дефект (ручной проход на стенде 13.11.2026, DRF-1503): сразу после
регистрации (ветка READY → ``_send_menu``) соло-мастер получил «Салон «Студия
…».» и меню владельца — «Сегодня», «Заявки от мастеров», «Записать клиента»,
«Кабинет салона»: признака «соло» у роли нет, и владелица в правах побеждала
мастера.

Права не меняются: соло остаётся владелицей. «Сегодня» владелицы — по-прежнему
день салона с «Визит состоялся?» — живёт под «Салоном»; свой день мастера —
отдельная кнопка «Мой день» (``CB_MY_DAY``).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from django.utils import timezone

from apps.catalog.models import CatalogMaster
from apps.channels.bot_registry import BotEntry
from apps.channels.max import salon_greeting, salon_handler, staff_menu
from apps.conversations.staff_assistant import is_entry_reply
from apps.identity.models import BotUser
from apps.identity.services.role_resolver import resolve_role
from apps.identity.services.solo_identity_link import open_link
from apps.identity.services.solo_onboarding import create_solo_provider
from apps.identity.services.solo_view import is_solo_master
from apps.orchestrator.ui.keyboards import parse_callback
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant, TenantStaff

pytestmark = pytest.mark.django_db

ENTRY = BotEntry(
    slug="salon",
    webhook_secret="wh-salon",  # pragma: allowlist secret
    api_token="token-salon",  # pragma: allowlist secret
    tenant_slug="",
    stream="max_salon",
    web_app="ayla_salon_bot",
)

SOLO_SHOWCASE = ["📅 Мой день", "🏢 Салон", "🏠 Мой кабинет"]
OWNER_FIRST = ["📅 Сегодня", staff_menu.LABEL_REQUESTS, "✍️ Записать клиента"]


@pytest.fixture
def solo():
    made = create_solo_provider(
        channel="max", channel_user_id="690001", display_name="Ольга Петрова"
    )
    open_link(made.master, bot_user=made.bot_user, tenant=made.tenant)
    return made


@pytest.fixture
def owner_master_not_solo():
    """Владелица-мастер, чей салон заведён НЕ соло-регистрацией.

    Роли те же, что у соло, человек тоже один — но связи соло-кабинета нет, и
    витрина остаётся владельческой (бот, в отличие от Mini App, требует связь).
    """
    tenant = Tenant.objects.create(
        slug="owner-salon", name="Салон владелицы", timezone="Europe/Moscow"
    )
    row = BotUser.all_tenants.create(tenant=tenant, channel="max", channel_user_id="690002")
    TenantStaff.all_tenants.create(
        tenant=tenant, bot_user=row, role=TenantStaff.Role.OWNER, created_by=row
    )
    CatalogMaster.all_tenants.create(
        tenant=tenant,
        name="Ирина",
        external_id=None,
        external_updated_at=timezone.now(),
        invite_status=CatalogMaster.InviteStatus.ACCEPTED,
        is_active=True,
        linked_bot_user=row,
    )
    return SimpleNamespace(bot_user=row, tenant=tenant)


@pytest.fixture
def said(monkeypatch):
    out: list[dict] = []
    monkeypatch.setattr(
        salon_handler,
        "_reply",
        lambda event, text, attachments=None: out.append(
            {"text": text, "attachments": attachments}
        ),
    )
    return out


def _labels(buttons) -> list[str]:
    return [b["label"] for b in buttons]


def _keyboard_labels(attachments) -> list[str]:
    rows = attachments[0]["payload"]["buttons"]
    # Клавиатура MAX: подпись кнопки — в ``text``.
    return [button["text"] for row in rows for button in row]


def _tap(text: str):
    return SimpleNamespace(
        text=text, chat_id="555", channel="max", channel_user_id="690001", raw={}
    )


def _press(made, action: str) -> None:
    """Кнопка — как в боте: внутри области салона (``_serve`` входит в неё)."""
    with tenant_scope(made.tenant):
        salon_handler._handle_button(
            _tap(action), resolve_role(made.bot_user), made.bot_user, made.tenant, ENTRY
        )


class TestTheSoloShowcase:
    def test_right_after_registration_the_menu_is_the_masters(self, solo, said) -> None:
        # Путь со стенда: READY регистрации шлёт ровно ``_send_menu``.
        role_ctx = resolve_role(solo.bot_user)
        # Присутствие: соло правда владелица И мастер — та развилка, которую решает §69.
        assert role_ctx.is_owner and role_ctx.is_master
        assert is_solo_master(role_ctx) is True

        salon_handler._send_menu(_tap("cb:solo:confirm"), role_ctx, solo.tenant, ENTRY)

        (reply,) = said
        assert reply["text"] == f"Салон «{solo.tenant.name}». Ваш день и кабинет мастера."
        labels = _keyboard_labels(reply["attachments"])
        assert labels == SOLO_SHOWCASE
        assert staff_menu.LABEL_REQUESTS not in labels
        # Заголовок соло — «ответ на вход»: в историю ассистента он не попадёт.
        assert is_entry_reply(reply["text"]) is True

    def test_every_showcase_callback_is_a_valid_callback(self, solo) -> None:
        buttons = staff_menu.menu_buttons(resolve_role(solo.bot_user), ENTRY)
        assert _labels(buttons) == SOLO_SHOWCASE
        callbacks = [b["callback"] for b in buttons if "web_app" not in b]
        assert callbacks == [staff_menu.CB_MY_DAY, staff_menu.CB_OWNER_MENU]
        for callback in callbacks:
            assert parse_callback(callback) is not None, callback

    def test_the_salon_button_opens_the_owners_menu_without_itself(self, solo, said) -> None:
        _press(solo, staff_menu.CB_OWNER_MENU)

        (reply,) = said
        assert reply["text"] == f"Салон «{solo.tenant.name}»."
        labels = _keyboard_labels(reply["attachments"])
        assert labels[:3] == OWNER_FIRST
        assert "🏢 Салон" not in labels  # петли «Салон → Салон» нет

    def test_my_day_is_the_masters_day_and_today_stays_the_salons(
        self, solo, said, monkeypatch
    ) -> None:
        from apps.channels.max import staff_actions

        monkeypatch.setattr(staff_actions, "master_day", lambda master: f"ДЕНЬ МАСТЕРА {master.id}")
        monkeypatch.setattr(staff_actions, "master_visit_rows", lambda master: [])
        monkeypatch.setattr(staff_actions, "salon_day", lambda tenant: "ДЕНЬ САЛОНА")

        _press(solo, staff_menu.CB_MY_DAY)
        assert said[-1]["text"] == f"ДЕНЬ МАСТЕРА {solo.master.id}"

        # «Сегодня» владелицы под «Салоном» — по-прежнему день салона.
        _press(solo, staff_menu.CB_DAY)
        assert said[-1]["text"] == "ДЕНЬ САЛОНА"

    def test_the_greeting_is_the_masters_and_does_not_burn_the_first_one(
        self, solo, said, monkeypatch
    ) -> None:
        monkeypatch.setattr(
            salon_greeting,
            "gather",
            lambda tenant, role_ctx, now=None: salon_greeting.GreetingData(),
        )
        role_ctx = resolve_role(solo.bot_user)

        built = salon_greeting.build_greeting(
            bot_user=solo.bot_user, role_ctx=role_ctx, tenant=solo.tenant, entry=ENTRY
        )
        assert built is not None
        text, buttons = built
        # Присутствие: это мастерская строка роли с именем салона.
        assert salon_greeting.MASTER_ROLE_LINE.format(salon=solo.tenant.name) in text
        assert "владел" not in text.lower()
        callbacks = [b.get("callback") for b in buttons[:2]]
        assert callbacks == [staff_menu.CB_MY_DAY, staff_menu.CB_OWNER_MENU]

        with tenant_scope(solo.tenant):
            salon_handler._greet_or_menu(
                _tap("/start"), role_ctx, solo.tenant, solo.bot_user, ENTRY
            )
        assert said, "приветствие отправлено"
        solo.bot_user.refresh_from_db()
        # Первое владельческое приветствие соло не показано — и не «сожжено».
        assert solo.bot_user.welcomed_at is None

    def test_a_broken_read_falls_back_to_the_owners_menu(self, solo, monkeypatch) -> None:
        from apps.identity.models import SoloIdentityLink

        def boom(*a, **kw):
            raise RuntimeError("db is gone")

        monkeypatch.setattr(SoloIdentityLink.objects, "filter", boom)
        role_ctx = resolve_role(solo.bot_user)
        assert is_solo_master(role_ctx) is False
        assert _labels(staff_menu.menu_buttons(role_ctx, ENTRY))[:3] == OWNER_FIRST


class TestEveryoneElseKeepsTheirMenu:
    def test_the_owner_master_not_registered_as_solo(self, owner_master_not_solo) -> None:
        role_ctx = resolve_role(owner_master_not_solo.bot_user)
        assert role_ctx.is_owner and role_ctx.is_master  # присутствие: та же пара ролей
        assert is_solo_master(role_ctx) is False
        assert _labels(staff_menu.menu_buttons(role_ctx, ENTRY))[:3] == OWNER_FIRST

    def test_a_solo_with_a_second_person_is_a_salon_and_gets_the_first_greeting(
        self, solo, monkeypatch
    ) -> None:
        CatalogMaster.all_tenants.create(
            tenant=solo.tenant,
            name="Второй мастер",
            external_id=None,
            external_updated_at=solo.master.external_updated_at,
            invite_status=CatalogMaster.InviteStatus.PENDING,
            is_active=False,
        )
        role_ctx = resolve_role(solo.bot_user)
        assert is_solo_master(role_ctx) is False
        assert staff_menu.LABEL_REQUESTS in _labels(staff_menu.menu_buttons(role_ctx, ENTRY))
        # Впервые на поверхности владелицы — первое владельческое приветствие.
        built = salon_greeting.build_greeting(
            bot_user=solo.bot_user, role_ctx=role_ctx, tenant=solo.tenant, entry=ENTRY
        )
        assert built is not None
        _text, buttons = built
        assert buttons == salon_greeting.first_buttons(ENTRY)

    def test_my_day_without_a_card_is_the_menu(self, said) -> None:
        tenant = Tenant.objects.create(slug="desk-salon", name="Салон", timezone="Europe/Moscow")
        row = BotUser.all_tenants.create(tenant=tenant, channel="max", channel_user_id="690003")
        desk = SimpleNamespace(
            is_owner=False, is_admin=False, is_receptionist=True, is_master=False
        )
        with tenant_scope(tenant):
            salon_handler._handle_button(_tap(staff_menu.CB_MY_DAY), desk, row, tenant, ENTRY)
        (reply,) = said
        # Присутствие: ответ — меню ресепшна, а не «карточка не найдена».
        assert reply["text"] == f"Салон «{tenant.name}»."
        assert staff_menu.LABEL_DAY_ADMIN in _keyboard_labels(reply["attachments"])
