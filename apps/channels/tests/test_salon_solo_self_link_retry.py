"""Соло-мастер довязывается с каталогом сам — входом и «Повторить проверку».

§77 п.38 владельца (24.09.2026): «мне не надо участия человека в регистрации
мастеров». DRF-2450 закрыл первый ход. Здесь — его неудача: каталог не ответил
в момент «Создать мой профиль», кабинет создан, связь осталась ``PENDING``.

**Шов, на котором жил разрыв.** Узлы идут через настоящий вход
(``handle_salon_max_event``) с настоящим пре-чеком. Соседние файлы этого не
делают, и не случайно: ``test_solo_bind_at_provisioning_2450`` зовёт
``_register_solo_provider`` напрямую дважды, а ``test_salon_stranger_without_botuser``
подменяет ``salon_entry.unlinked_reason`` на «связан». Между ними и стоял
человек с кабинетом: он уже не незнакомец, до ветки повтора не доходит, и
«Повторить проверку» ему ничего не дозванивала.

Каталог подставлен (:class:`_FakeCatalog`) и держит те же три факта, что в
узлах DRF-2450: прокси появляется после представления личности, дверь сверяет
claim, до связи личность резолвится в прокси, после — в настоящую учётку.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import patch

import pytest
from django.core.cache import cache
from django.utils import timezone

from apps.catalog.models import CatalogMaster
from apps.channels.bot_registry import BotEntry
from apps.channels.max import salon_handler
from apps.channels.max.salon_handler import handle_salon_max_event
from apps.identity.models import BotUser, SoloIdentityLink
from apps.identity.services import solo_link_retry
from apps.identity.services import specialist_identity_link as door_client
from apps.identity.services.solo_identity_link import REJECTED_RECOVERY_TEXT, reject_by_operator
from apps.identity.services.specialist_identity_link import SpecialistIdentityLinkRefused
from apps.integrations.ayla import identity_client
from apps.integrations.ayla.identity_client import ResolvedIdentity
from apps.integrations.ayla.user_proxy import external_user_id_for
from apps.tenancy.models import Tenant, TenantStaff

pytestmark = pytest.mark.django_db

CHANNEL_USER_ID = "2450901"
CHAT_ID = "2450"
SPECIALIST_ID = uuid.UUID("24500000-0000-4000-8000-000000000901")
PROXY_ID = uuid.UUID("24500000-0000-4000-8000-0000000009aa")
REAL_ID = uuid.UUID("24500000-0000-4000-8000-0000000009bb")

SALON_BOT = BotEntry(
    slug="salon",
    webhook_secret="wh-salon",  # pragma: allowlist secret
    api_token="token-salon",  # pragma: allowlist secret
    tenant_slug="formula-tela",
    stream="max_salon",
    miniapp_url="https://app.example/staff",
)


class _Event:
    """Стоит на месте ``CanonicalEvent`` для прямого вызова регистрации."""

    def __init__(self, text: str = salon_handler.SOLO_CONFIRM_CALLBACK) -> None:
        self.text = text
        self.chat_id = CHAT_ID
        self.channel = "max"
        self.channel_user_id = CHANNEL_USER_ID
        self.raw: dict = {}


def _event() -> Any:
    """Стоит на месте ``CanonicalEvent``: регистрация читает только эти поля."""
    return _Event()


class _FakeCatalog:
    """Каталог в одном месте: провижининг, представление личности, дверь."""

    def __init__(self) -> None:
        self.claim: str | None = None
        self.seen: set[str] = set()
        self.bound: set[str] = set()
        self.calls: list[str] = []
        self.door_refusal: str | None = None

    def provision(self, link, *, tenant, bot_user, display_name, http_client=None):
        self.calls.append("provision")
        if link.catalog_provisioned_at is None:
            self.claim = external_user_id_for(bot_user)
            link.catalog_specialist_id = SPECIALIST_ID
            link.catalog_provisioned_at = timezone.now()
            link.save(update_fields=["catalog_specialist_id", "catalog_provisioned_at"])
            link.master.catalog_specialist_id = SPECIALIST_ID
            link.master.save(update_fields=["catalog_specialist_id"])
        return None

    def resolve(self, external_user_id, *, timeout_s=None):
        self.calls.append("resolve")
        self.seen.add(external_user_id)
        if external_user_id in self.bound:
            return ResolvedIdentity(ayla_user_id=REAL_ID, is_proxy=False)
        return ResolvedIdentity(ayla_user_id=PROXY_ID, is_proxy=True)

    def door(self, *, specialist_id, bot_user, actor_label, http_client=None):
        self.calls.append("door")
        external = external_user_id_for(bot_user)
        if self.door_refusal:
            raise SpecialistIdentityLinkRefused(self.door_refusal, correlation_id="corr-901")
        if external != self.claim:
            raise SpecialistIdentityLinkRefused("claim_mismatch", correlation_id="corr-901")
        if external not in self.seen:
            raise SpecialistIdentityLinkRefused("identity_unknown", correlation_id="corr-901")
        self.bound.add(external)

    def doors(self) -> int:
        return self.calls.count("door")


@pytest.fixture(autouse=True)
def _registry(settings):
    settings.MAX_BOT_REGISTRY = (SALON_BOT,)
    settings.AYLA_SUPPORT_CONTACT = ""
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def catalog(monkeypatch) -> _FakeCatalog:
    fake = _FakeCatalog()
    monkeypatch.setattr(
        "apps.identity.services.solo_catalog_provisioning.provision_catalog_workspace",
        fake.provision,
    )
    monkeypatch.setattr(identity_client, "resolve_identity", fake.resolve)
    monkeypatch.setattr(door_client, "bind_master_identity_in_catalog", fake.door)
    return fake


@pytest.fixture
def sent():
    with patch("apps.channels.max.outbound.send_message") as mock:
        yield mock


@pytest.fixture
def emitted(monkeypatch) -> list[tuple[str, dict]]:
    out: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        salon_handler, "emit", lambda name, payload=None, **kw: out.append((name, payload))
    )
    return out


def _payload(text: str, *, update_id: int) -> dict:
    return {
        "update_type": "message_created",
        "update_id": update_id,
        "timestamp": 1_700_000_000_000,
        "message": {
            "sender": {"user_id": int(CHANNEL_USER_ID), "name": "Ольга", "is_bot": False},
            "recipient": {"chat_id": int(CHAT_ID), "user_id": 999, "chat_type": "dialog"},
            "body": {"mid": f"mid-901-{update_id}", "seq": 1, "text": text, "attachments": []},
        },
    }


_update_ids = iter(range(1, 10_000))


def _handle(text: str) -> None:
    """Сообщение через настоящий вход салонного бота — с настоящим пре-чеком."""
    with patch(
        "apps.master_api.services.assistant.answer_master_question",
        side_effect=RuntimeError("no assistant in this test"),
    ):
        handle_salon_max_event(_payload(text, update_id=next(_update_ids)))


def _text(sent) -> str:
    return sent.call_args.kwargs["text"]


def _button_payloads(sent) -> list[str]:
    out: list[str] = []
    for att in sent.call_args.kwargs.get("attachments") or []:
        for row in (att.get("payload") or {}).get("buttons") or []:
            out.extend(b["payload"] for b in row if b.get("type") == "callback")
    return out


def _link() -> SoloIdentityLink:
    return SoloIdentityLink.objects.get(channel="max", channel_user_id=CHANNEL_USER_ID)


def _row() -> BotUser:
    return BotUser.all_tenants.get(channel="max", channel_user_id=CHANNEL_USER_ID)


def _registered_with_a_failed_link(catalog: _FakeCatalog, sent) -> None:
    """Кабинет создан, а каталог в этот момент связь не принял."""
    catalog.door_refusal = "transport_error"
    salon_handler._register_solo_provider(_event(), entry=SALON_BOT, display_name="Ольга")
    assert _link().status == SoloIdentityLink.Status.PENDING
    assert _text(sent) == salon_handler.SOLO_CREATED_PENDING
    sent.reset_mock()


# ─── пара: вход довязывает сам — против прежнего «перечитал и не тронул» ─────


class TestTheNextMessageCompletesTheLink:
    def test_start_after_a_failed_first_link_ends_linked_with_the_menu(
        self, catalog, sent, emitted
    ) -> None:
        """Красный до правки: связь оставалась PENDING, ответ — NOT_LINKED."""
        _registered_with_a_failed_link(catalog, sent)
        catalog.door_refusal = None

        _handle("/start")

        link = _link()
        assert link.status == SoloIdentityLink.Status.LINKED
        assert link.provenance == SoloIdentityLink.Provenance.CATALOG_RESOLVED
        assert link.operator_id is None  # человека в этом ходе не было
        link.master.refresh_from_db()
        assert link.master.ayla_user_id == REAL_ID
        row = _row()
        assert row.ayla_user_id == REAL_ID and row.ayla_user_id_is_proxy is False
        assert sent.call_count == 1
        assert _text(sent) not in (
            salon_handler.NOT_LINKED_TEXT,
            salon_handler.SOLO_NOT_LINKED_TEXT,
        )
        assert sent.call_args.kwargs.get("attachments"), "вход персонала — с кнопками"

    def test_the_recheck_button_really_rechecks(self, catalog, sent, emitted) -> None:
        """Красный до правки: кнопка перечитывала состояние и ничего не дозванивала."""
        _registered_with_a_failed_link(catalog, sent)
        catalog.door_refusal = None
        doors_before = catalog.doors()

        _handle(salon_handler.CB_SALON_RECHECK)

        assert catalog.doors() == doors_before + 1
        assert _link().status == SoloIdentityLink.Status.LINKED
        assert _text(sent) not in (
            salon_handler.NOT_LINKED_TEXT,
            salon_handler.SOLO_NOT_LINKED_TEXT,
        )

    def test_a_plain_line_heals_too(self, catalog, sent, emitted) -> None:
        _registered_with_a_failed_link(catalog, sent)
        catalog.door_refusal = None

        _handle("привет")

        assert _link().status == SoloIdentityLink.Status.LINKED
        text = _text(sent)
        assert _row().tenant.name in text, "меню персонала называет кабинет"
        assert salon_handler.SOLO_NOT_LINKED_TEXT not in text

    def test_the_retry_is_reported_by_name(self, catalog, sent, emitted) -> None:
        _registered_with_a_failed_link(catalog, sent)
        catalog.door_refusal = None

        _handle("/start")

        retried = [p for name, p in emitted if name == "channels.max.salon.solo_link_retried"]
        assert retried == [
            {
                "bot_user_id": str(_row().id),
                "linked": True,
                "explicit": True,
                "catalog_provisioning_refusal": None,
                "identity_link_refusal": None,
                "link_refusal": None,
            }
        ]

    def test_without_the_heal_the_same_entry_stays_not_linked(
        self, catalog, sent, emitted, monkeypatch
    ) -> None:
        """Подмена: дозвон выключен — исход прежний. Узлы выше держат именно его."""
        _registered_with_a_failed_link(catalog, sent)
        catalog.door_refusal = None
        monkeypatch.setattr(salon_handler, "_heal_solo_link", lambda event, verdict: False)

        _handle("/start")

        assert _link().status == SoloIdentityLink.Status.PENDING
        assert _text(sent) == salon_handler.SOLO_NOT_LINKED_TEXT


# ─── каталог всё ещё отказывает: правда словами, повтор без долбёжки ────────


class TestWhileTheCatalogStillRefuses:
    def test_the_person_is_told_about_their_own_state(self, catalog, sent, emitted) -> None:
        _registered_with_a_failed_link(catalog, sent)

        _handle("/start")

        assert _text(sent) == salon_handler.SOLO_NOT_LINKED_TEXT
        assert _button_payloads(sent) == [
            salon_handler.CB_SALON_RECHECK,
            salon_handler.CB_SALON_SUPPORT,
        ]
        link = _link()
        assert link.status == SoloIdentityLink.Status.PENDING
        assert link.last_attempt_refusal == "proxy_identity"
        retried = [p for name, p in emitted if name == "channels.max.salon.solo_link_retried"]
        assert retried[-1]["identity_link_refusal"] == "transport_error"
        assert retried[-1]["linked"] is False

    def test_plain_lines_do_not_hammer_the_catalog(self, catalog, sent, emitted) -> None:
        _registered_with_a_failed_link(catalog, sent)
        doors_before = catalog.doors()

        _handle("привет")
        _handle("ну что там")
        _handle("алло")

        assert catalog.doors() == doors_before + 1, "обычная реплика — раз в интервал"
        assert _text(sent) == salon_handler.SOLO_NOT_LINKED_TEXT

    def test_an_explicit_recheck_does_not_wait_for_the_plain_line_interval(
        self, catalog, sent, emitted
    ) -> None:
        _registered_with_a_failed_link(catalog, sent)
        doors_before = catalog.doors()

        _handle("привет")
        _handle(salon_handler.CB_SALON_RECHECK)

        assert catalog.doors() == doors_before + 2

    def test_a_double_tap_calls_the_catalog_once(self, catalog, sent, emitted) -> None:
        """Ревью: явная просьба без интервала — это синхронные походы в каталог на каждый тап."""
        _registered_with_a_failed_link(catalog, sent)
        doors_before = catalog.doors()

        _handle(salon_handler.CB_SALON_RECHECK)
        _handle(salon_handler.CB_SALON_RECHECK)

        assert catalog.doors() == doors_before + 1
        assert _text(sent) == salon_handler.SOLO_NOT_LINKED_TEXT, "второй тап — ответ, не тишина"

        cache.clear()  # короткий интервал прошёл
        _handle(salon_handler.CB_SALON_RECHECK)

        assert catalog.doors() == doors_before + 2

    def test_the_intervals_are_sixty_seconds_plain_and_ten_explicit(
        self, catalog, sent, emitted, monkeypatch
    ) -> None:
        """Числа интервалов закреплены: перепутанные или «навсегда» прошли бы узлы выше."""
        _registered_with_a_failed_link(catalog, sent)
        timeouts: dict[str, Any] = {}
        real_add = cache.add

        def _spy(key: str, value: Any, timeout: Any = None, **kwargs: Any) -> bool:
            if key.startswith("salon_solo_link_retry:"):
                timeouts[key.split(":")[1]] = timeout
            return real_add(key, value, timeout=timeout, **kwargs)

        monkeypatch.setattr(cache, "add", _spy)

        _handle("привет")
        _handle(salon_handler.CB_SALON_RECHECK)

        assert timeouts == {"plain": 60, "explicit": 10}

    def test_a_cache_outage_does_not_stop_the_retry_or_the_answer(
        self, catalog, sent, emitted, monkeypatch
    ) -> None:
        """Тормоз недоступен → дозвон идёт как без тормоза, человек получает ответ."""
        _registered_with_a_failed_link(catalog, sent)
        doors_before = catalog.doors()
        real_add = cache.add

        def _down(key: str, *args: Any, **kwargs: Any) -> bool:
            if key.startswith("salon_solo_link_retry:"):
                raise ConnectionError("cache is down")
            return real_add(key, *args, **kwargs)

        monkeypatch.setattr(cache, "add", _down)

        _handle("привет")
        _handle("алло")

        assert catalog.doors() == doors_before + 2
        assert _text(sent) == salon_handler.SOLO_NOT_LINKED_TEXT

    def test_a_switched_off_cabinet_is_not_retried(self, catalog, sent, emitted) -> None:
        """Ревью P1: пре-чек смотрит связь раньше активности — выключенный приходил с NOT_LINKED.

        Выключение — решение человека. Красный до правки: кабинет провижинился
        и связывался за его спиной.
        """
        _registered_with_a_failed_link(catalog, sent)
        Tenant.all_objects.filter(pk=_row().tenant_id).update(is_active=False)
        catalog.door_refusal = None
        calls_before = list(catalog.calls)

        _handle(salon_handler.CB_SALON_RECHECK)

        assert sent.call_count == 1, "человеку ответили"
        assert [c for c in catalog.calls[len(calls_before) :] if c != "resolve"] == []
        assert _link().status == SoloIdentityLink.Status.PENDING
        assert not [n for n, _ in emitted if n == "channels.max.salon.solo_link_retried"]

    def test_a_support_tap_gets_the_support_answer_not_a_retry(
        self, catalog, sent, emitted, settings
    ) -> None:
        """Ревью: удачный дозвон на тапе поддержки увёл бы человека в меню без контакта."""
        settings.AYLA_SUPPORT_CONTACT = "@ayla_support_901"
        _registered_with_a_failed_link(catalog, sent)
        catalog.door_refusal = None
        doors_before = catalog.doors()

        _handle(salon_handler.CB_SALON_SUPPORT)

        assert "@ayla_support_901" in _text(sent)
        assert catalog.doors() == doors_before
        assert _link().status == SoloIdentityLink.Status.PENDING

    def test_a_crashing_retry_still_answers(self, catalog, sent, emitted, monkeypatch) -> None:
        """Ошибка настройки или базы внутри дозвона — не молчание человеку."""
        _registered_with_a_failed_link(catalog, sent)

        def _boom(*args: Any, **kwargs: Any):
            raise RuntimeError("misconfigured")

        monkeypatch.setattr(solo_link_retry, "retry_solo_link", _boom)

        _handle("/start")

        assert _text(sent) == salon_handler.SOLO_NOT_LINKED_TEXT


# ─── отказ оператора: не дозванивается и не обещает проверку ────────────────


class TestAnOperatorsRejectionStands:
    def test_it_is_not_retried_and_is_answered_safely(self, catalog, sent, emitted) -> None:
        from django.contrib.auth import get_user_model

        _registered_with_a_failed_link(catalog, sent)
        operator = get_user_model().objects.create_user(username="op-901", password="x")  # noqa: S106
        reject_by_operator(_link(), operator=operator, reason="fraud_suspected")
        catalog.door_refusal = None
        doors_before = catalog.doors()

        _handle(salon_handler.CB_SALON_RECHECK)

        assert catalog.doors() == doors_before
        assert _link().status == SoloIdentityLink.Status.REJECTED
        assert _text(sent) == REJECTED_RECOVERY_TEXT
        assert _button_payloads(sent) == [salon_handler.CB_SALON_SUPPORT]

    def test_a_rejection_landing_during_the_retry_is_not_overwritten(
        self, catalog, sent, emitted
    ) -> None:
        """Ревью: исход писался целой строкой из экземпляра, прочитанного до сети.

        Оператор отклоняет, пока дозвон ходит в каталог; каталог при этом
        отвечает настоящим ключом. Красный до правки: связь становилась LINKED.
        """
        from django.contrib.auth import get_user_model

        _registered_with_a_failed_link(catalog, sent)
        operator = get_user_model().objects.create_user(username="op-902", password="x")  # noqa: S106
        catalog.door_refusal = None
        bind = catalog.door

        def _door_while_the_operator_rejects(**kwargs: Any) -> None:
            reject_by_operator(_link(), operator=operator, reason="fraud_suspected")
            bind(**kwargs)

        with patch.object(
            door_client, "bind_master_identity_in_catalog", _door_while_the_operator_rejects
        ):
            _handle(salon_handler.CB_SALON_RECHECK)

        link = _link()
        assert link.status == SoloIdentityLink.Status.REJECTED
        assert link.reject_reason == "fraud_suspected"
        assert link.provenance != SoloIdentityLink.Provenance.CATALOG_RESOLVED
        assert _text(sent) == REJECTED_RECOVERY_TEXT

    def test_a_rejection_landing_during_provisioning_stops_the_side_effects_too(
        self, catalog, sent, emitted, monkeypatch
    ) -> None:
        """Ревью P1: статус проверялся по экземпляру, прочитанному до провижининга.

        Провижининг — самый длинный вызов (до 30 с). Отказ, пришедший за это
        время, держал статус, но дверь всё равно привязывала личность, а
        карточка и строка получали настоящий ключ. Красный до правки: дверь
        звалась.
        """
        from django.contrib.auth import get_user_model

        _registered_with_a_failed_link(catalog, sent)
        operator = get_user_model().objects.create_user(username="op-903", password="x")  # noqa: S106
        catalog.door_refusal = None
        doors_before = catalog.doors()

        def _provision_while_the_operator_rejects(link, **kwargs: Any):
            reject_by_operator(_link(), operator=operator, reason="fraud_suspected")
            return catalog.provision(link, **kwargs)

        monkeypatch.setattr(
            "apps.identity.services.solo_catalog_provisioning.provision_catalog_workspace",
            _provision_while_the_operator_rejects,
        )

        _handle(salon_handler.CB_SALON_RECHECK)

        assert catalog.doors() == doors_before, "после отказа дверь каталога не зовётся"
        link = _link()
        assert link.status == SoloIdentityLink.Status.REJECTED
        link.master.refresh_from_db()
        assert link.master.ayla_user_id is None
        assert _row().ayla_user_id_is_proxy is not False, "ключ строки настоящим не стал"
        assert _text(sent) == REJECTED_RECOVERY_TEXT


# ─── ключ строки уже настоящий, а связь всё ещё ждёт ────────────────────────


class TestALinkPendingBehindAStaffVerdict:
    """Ревью: пре-чек владельца смотрит на ключ ``BotUser``, а не на связь.

    Дверь каталога привязала личность, а следующий за ней резолв не ответил
    (или оператор привязал в каталоге и не нажал «проверить» в боте). На
    следующем сообщении пре-чек сам переводит ключ строки в настоящий и
    отвечает ``STAFF`` — а карточка мастера без ключа и связь ``PENDING``.
    Меню есть, продажи нет, и дозвон, стоявший только на ``NOT_LINKED``, сюда
    не заходил бы никогда.
    """

    @staticmethod
    def _bound_in_the_catalog_but_not_recorded(catalog: _FakeCatalog, sent) -> None:
        _registered_with_a_failed_link(catalog, sent)
        catalog.door_refusal = None
        catalog.bound.add(external_user_id_for(_row()))

    def test_the_next_message_finishes_the_link(self, catalog, sent, emitted) -> None:
        self._bound_in_the_catalog_but_not_recorded(catalog, sent)

        _handle("привет")

        link = _link()
        assert link.status == SoloIdentityLink.Status.LINKED
        link.master.refresh_from_db()
        assert link.master.ayla_user_id == REAL_ID
        assert _row().tenant.name in _text(sent), "человек при этом в меню персонала"

    @pytest.mark.parametrize("door_refusal", ["identity_not_proxy", "identity_already_bound"])
    def test_a_door_that_refuses_an_already_bound_identity_does_not_stop_the_link(
        self, catalog, sent, emitted, door_refusal
    ) -> None:
        """Ревью P1: подставная дверь не умела отказать уже привязанной личности.

        Настоящая — отказывает (``specialist_identity_link.HINTS``). Связь при
        этом всё равно завершается: её решает ответ каталога о ключе, а отказ
        двери уходит в событие именем. Узел держит это поведение — правка
        «остановиться на отказе двери» вернула бы зависание из докстринга класса.
        """
        self._bound_in_the_catalog_but_not_recorded(catalog, sent)
        catalog.door_refusal = door_refusal

        _handle("привет")

        assert _link().status == SoloIdentityLink.Status.LINKED
        retried = [p for name, p in emitted if name == "channels.max.salon.solo_link_retried"]
        assert retried[-1]["identity_link_refusal"] == door_refusal
        assert retried[-1]["linked"] is True

    def test_without_the_heal_the_menu_hides_a_pending_link(
        self, catalog, sent, emitted, monkeypatch
    ) -> None:
        """Подмена: дозвон выключен — состояние из докстринга класса настоящее."""
        self._bound_in_the_catalog_but_not_recorded(catalog, sent)
        monkeypatch.setattr(salon_handler, "_heal_solo_link", lambda event, verdict: False)

        _handle("привет")

        row = _row()
        assert row.ayla_user_id == REAL_ID and row.ayla_user_id_is_proxy is False
        assert row.tenant.name in _text(sent)
        link = _link()
        assert link.status == SoloIdentityLink.Status.PENDING
        link.master.refresh_from_db()
        assert link.master.ayla_user_id is None


# ─── контроль: салонный персонал не задет ───────────────────────────────────


class TestSalonStaffAreUntouched:
    def test_an_unlinked_salon_admin_gets_the_salon_text_and_no_catalog_calls(
        self, catalog, sent, emitted
    ) -> None:
        tenant, _ = Tenant.all_objects.get_or_create(
            slug="formula-tela", defaults={"name": "Формула тела"}
        )
        user = BotUser.all_tenants.create(
            tenant=tenant, channel="max", channel_user_id=CHANNEL_USER_ID, chat_id=CHAT_ID
        )
        TenantStaff.all_tenants.create(tenant=tenant, bot_user=user, role=TenantStaff.Role.ADMIN)

        _handle(salon_handler.CB_SALON_RECHECK)

        assert _text(sent) == salon_handler.NOT_LINKED_TEXT
        assert "door" not in catalog.calls and "provision" not in catalog.calls
        assert not [n for n, _ in emitted if n == "channels.max.salon.solo_link_retried"]

    def test_linked_staff_without_the_owner_role_cost_no_link_lookup(
        self, catalog, sent, emitted, monkeypatch
    ) -> None:
        """Заход под ``STAFF`` читает базу только у владельца — соло им является всегда."""
        tenant, _ = Tenant.all_objects.get_or_create(
            slug="formula-tela", defaults={"name": "Формула тела"}
        )
        user = BotUser.all_tenants.create(
            tenant=tenant,
            channel="max",
            channel_user_id=CHANNEL_USER_ID,
            chat_id=CHAT_ID,
            ayla_user_id=REAL_ID,
            ayla_user_id_is_proxy=False,
        )
        TenantStaff.all_tenants.create(tenant=tenant, bot_user=user, role=TenantStaff.Role.ADMIN)
        lookups: list[Any] = []
        monkeypatch.setattr(
            solo_link_retry, "solo_workspace_link", lambda bot_user: lookups.append(bot_user)
        )

        _handle("привет")

        assert tenant.name in _text(sent), "администратор салона — в своём меню"
        assert lookups == []

    def test_a_salon_master_card_is_not_a_solo_workspace(self, catalog) -> None:
        tenant, _ = Tenant.all_objects.get_or_create(
            slug="formula-tela", defaults={"name": "Формула тела"}
        )
        user = BotUser.all_tenants.create(
            tenant=tenant, channel="max", channel_user_id=CHANNEL_USER_ID, chat_id=CHAT_ID
        )
        CatalogMaster.all_tenants.create(
            tenant=tenant,
            external_id=-901,
            external_updated_at=timezone.now(),
            name="Анна",
            linked_bot_user=user,
            invite_status=CatalogMaster.InviteStatus.ACCEPTED,
            is_active=True,
        )

        assert solo_link_retry.solo_workspace_link(user) is None


# ─── дорожка повтора сама по себе ───────────────────────────────────────────


class TestTheRetryLane:
    def test_a_linked_workspace_is_not_bound_again(self, catalog, sent, emitted) -> None:
        salon_handler._register_solo_provider(_event(), entry=SALON_BOT, display_name="Ольга")
        assert _link().status == SoloIdentityLink.Status.LINKED
        doors_before = catalog.doors()
        found = solo_link_retry.solo_workspace_link(_row())
        assert found is not None and not found.is_pending

        retry = solo_link_retry.retry_solo_link(
            found.link, master=found.master, tenant=_row().tenant, bot_user=_row()
        )

        assert retry.attempted is False and retry.linked is True
        assert catalog.doors() == doors_before

    def test_an_unprovisioned_workspace_does_not_reach_the_door(
        self, catalog, sent, emitted, monkeypatch
    ) -> None:
        _registered_with_a_failed_link(catalog, sent)
        monkeypatch.setattr(
            "apps.identity.services.solo_catalog_provisioning.provision_catalog_workspace",
            lambda link, **kw: "token_missing",
        )
        doors_before = catalog.doors()
        found = solo_link_retry.solo_workspace_link(_row())
        assert found is not None

        retry = solo_link_retry.retry_solo_link(
            found.link, master=found.master, tenant=_row().tenant, bot_user=_row()
        )

        assert retry.attempted is False and retry.catalog_refusal == "token_missing"
        assert catalog.doors() == doors_before
