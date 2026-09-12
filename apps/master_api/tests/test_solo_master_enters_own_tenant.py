"""Соло-мастер открывает кабинет из салонного бота (DRF-1755, срез 2 DRF-1705).

До этого среза — не открывал. Замер `docs/SOLO_PATH_INPUTS_MEASUREMENT_DRF1705.md`
§1: у соло-мастера ДВЕ строки ``BotUser`` — **A** в тенанте салона (её
создаёт первое же сообщение салонному боту, ``salon_handler.py:520-525``,
роль ``customer``) и **B** в его собственном тенанте (``create_solo_provider``,
``solo_onboarding.py:462-469``); карточка ``CatalogMaster.linked_bot_user = B``.
Резолвер брал строку **в тенанте подписавшего бота** → A, и
``require_master_init_data`` искал карточку по A → ``401 not_a_master``.
Запасной ход резолвера до этого места не доходил: строка в тенанте бота
ЕСТЬ, просто не та.

Правило среза 2: тенант — **от человека**. Среди всех строк личности
«рабочие» — те, у чьего тенанта есть активная ``TenantStaff`` или живая
``CatalogMaster`` на этой строке. Одна рабочая → она, чей бы токен ни
подписал initData. Ноль рабочих → как раньше (клиент салона остаётся
клиентом салона). Две и больше → срез 5 (DRF-1766); до него —
детерминированно первая по ``(tenant.created_at, pk)`` с ``WARNING``.

Каждый тест ниже называет, что он красный ДО правки и почему: это
targeted proof, а не описание желаемого.
"""

from __future__ import annotations

import json
import time as time_module
from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.catalog.models import CatalogMaster
from apps.channels.bot_registry import BotEntry
from apps.identity.models import BotUser
from apps.identity.services.bot_user_resolver import resolve_bot_user
from apps.master_api.auth import _resolve_bot_user
from apps.master_api.tests.conftest import _sign, init_data_header
from apps.tenancy.models import Tenant, TenantStaff

pytestmark = pytest.mark.django_db

CHANNEL_USER_ID = "77001122"


class _Verified:
    def __init__(self, user_id: str, bot_slug: str = "") -> None:
        self.user_id = user_id
        self.bot_slug = bot_slug


@pytest.fixture
def salon() -> Tenant:
    tenant, _ = Tenant.all_objects.get_or_create(
        slug="formula-tela", defaults={"name": "Формула тела"}
    )
    return tenant


@pytest.fixture
def solo() -> Tenant:
    return Tenant.all_objects.create(slug="solo-max-0badc0de", name="Кабинет соло")


def _master(tenant: Tenant, row: BotUser, *, name: str = "Соло Мастер") -> CatalogMaster:
    return CatalogMaster.all_tenants.create(
        tenant=tenant,
        name=name,
        external_id=None,
        external_updated_at=timezone.now(),
        invite_status=CatalogMaster.InviteStatus.ACCEPTED,
        is_active=True,
        linked_bot_user=row,
    )


def _touch(row: BotUser, when) -> None:
    # `last_seen` is auto_now — only update() states the fact (DRF-1653).
    BotUser.all_tenants.filter(pk=row.pk).update(last_seen=when)


@pytest.fixture
def solo_pair(salon: Tenant, solo: Tenant) -> tuple[BotUser, BotUser]:
    """Ровно та форма, которую строит соло-путь: A — салон/customer, B — соло/owner+карточка.

    A свежее B нарочно: человек только что написал салонному боту и открыл
    Mini App, а кабинет заводил вчера. Любое правило «по свежести» выбирает
    A; правило «по тенанту подписи» — тоже A. Тест красный при обоих.
    """

    now = timezone.now()
    a = BotUser.all_tenants.create(tenant=salon, channel="max", channel_user_id=CHANNEL_USER_ID)
    b = BotUser.all_tenants.create(tenant=solo, channel="max", channel_user_id=CHANNEL_USER_ID)
    _touch(b, now - timedelta(days=1))
    _touch(a, now)
    TenantStaff.all_tenants.create(
        tenant=solo, bot_user=b, role=TenantStaff.Role.OWNER, created_by=b
    )
    _master(solo, b)
    return a, b


@pytest.fixture
def salon_bot(settings) -> None:
    settings.MAX_BOT_REGISTRY = (
        BotEntry(
            slug="salon",
            webhook_secret="wh-salon",  # pragma: allowlist secret
            api_token="tok-salon",  # pragma: allowlist secret
            tenant_slug="formula-tela",
            stream="max_salon",
        ),
    )
    settings.MAX_BOT_TENANT_SLUG = "formula-tela"


class TestTheWorkingRowWins:
    def test_solo_master_resolves_to_the_solo_row_from_the_salon_bot(self, salon_bot, solo_pair):
        """Красный до правки: резолвер отдавал A (тенант подписавшего бота)."""

        a, b = solo_pair

        resolved = _resolve_bot_user(_Verified(CHANNEL_USER_ID, bot_slug="salon"))

        assert resolved == b, f"got tenant={resolved.tenant.slug!r}, want {b.tenant.slug!r}"

    def test_the_same_answer_without_a_bot_slug(self, salon_bot, solo_pair):
        """Легаси-подпись одним токеном (``bot_slug=""``) → шаг 2, MAX_BOT_TENANT_SLUG → тоже A. Красный."""

        _a, b = solo_pair

        assert _resolve_bot_user(_Verified(CHANNEL_USER_ID)) == b

    def test_master_endpoint_answers_200_not_401(self, client, salon_bot, solo_pair):
        """Живой симптом Phase 0 (сценарий 8): кабинет отвечал ``401 not_a_master``. Красный до правки."""

        # initData подписан легаси MAX_BOT_TOKEN (conftest): bot_slug="" → шаг 2.
        resp = client.get(
            reverse("master_api:dashboard"),
            HTTP_AUTHORIZATION=init_data_header(CHANNEL_USER_ID),
        )

        assert resp.status_code == 200, resp.content

    def test_a_deactivated_staff_row_and_an_archived_card_are_not_working(
        self, salon_bot, salon, solo
    ):
        """Отрицательный контроль на само слово «рабочая».

        Снятый owner и карточка в архиве — не рабочая строка: человек
        остаётся тем, кем его видит тенант подписи. Зелёный и до правки
        (правило совпадает со старым), оставлен как граница нового.
        """

        now = timezone.now()
        a = BotUser.all_tenants.create(tenant=salon, channel="max", channel_user_id=CHANNEL_USER_ID)
        b = BotUser.all_tenants.create(tenant=solo, channel="max", channel_user_id=CHANNEL_USER_ID)
        TenantStaff.all_tenants.create(
            tenant=solo, bot_user=b, role=TenantStaff.Role.OWNER, created_by=b, deactivated_at=now
        )
        card = _master(solo, b)
        CatalogMaster.all_tenants.filter(pk=card.pk).update(archived_at=now)

        assert _resolve_bot_user(_Verified(CHANNEL_USER_ID, bot_slug="salon")) == a


class TestZeroWorkingRowsKeepsTodaysRule:
    def test_a_plain_customer_stays_in_the_signing_bots_tenant(self, salon_bot, salon, solo):
        """Отрицательный контроль: клиент салона с второй строкой где-то ещё — салон, как сегодня."""

        a = BotUser.all_tenants.create(tenant=salon, channel="max", channel_user_id=CHANNEL_USER_ID)
        BotUser.all_tenants.create(tenant=solo, channel="max", channel_user_id=CHANNEL_USER_ID)

        assert _resolve_bot_user(_Verified(CHANNEL_USER_ID, bot_slug="salon")) == a

    def test_unknown_user_is_still_none(self, salon_bot, solo_pair):
        assert resolve_bot_user(_Verified("no-such-person")) is None


class TestTwoWorkingRowsAskThePerson:
    def test_two_working_rows_are_a_question_not_a_silent_pick_2026_09_12(
        self, salon_bot, salon, solo
    ):
        """Эталон ПЕРЕВЁРНУТ 12.09.2026 (DRF-1766, срез 5).

        Раньше (заглушка среза 2 «до DRF-1766»): две рабочие строки → старейший
        тенант детерминированно + WARNING several_working_tenants. Решение
        владельца — выбор, не страж: резолвер поднимает SalonChoiceRequired
        с обоими тенантами, а поверхности спрашивают человека. Что осталось
        от прежнего эталона — порядок кандидатов по возрасту тенанта, не по
        ``last_seen`` (DRF-1653): одни и те же данные — один и тот же список.
        """
        from apps.identity.services.bot_user_resolver import SalonChoiceRequired

        a = BotUser.all_tenants.create(tenant=salon, channel="max", channel_user_id=CHANNEL_USER_ID)
        b = BotUser.all_tenants.create(tenant=solo, channel="max", channel_user_id=CHANNEL_USER_ID)
        TenantStaff.all_tenants.create(
            tenant=salon, bot_user=a, role=TenantStaff.Role.ADMIN, created_by=a
        )
        TenantStaff.all_tenants.create(
            tenant=solo, bot_user=b, role=TenantStaff.Role.OWNER, created_by=b
        )
        _touch(a, timezone.now() - timedelta(days=3))
        _touch(b, timezone.now())
        older_first = sorted((salon, solo), key=lambda t: (t.created_at, str(t.pk)))

        with pytest.raises(SalonChoiceRequired) as exc:
            _resolve_bot_user(_Verified(CHANNEL_USER_ID, bot_slug="salon"))

        assert [t.slug for t in exc.value.tenants] == [t.slug for t in older_first]
        # And the choice, once made, is honoured — either way round.
        assert (
            _resolve_bot_user(_Verified(CHANNEL_USER_ID, bot_slug="salon"), chosen_slug=solo.slug)
            == b
        )
        assert (
            _resolve_bot_user(_Verified(CHANNEL_USER_ID, bot_slug="salon"), chosen_slug=salon.slug)
            == a
        )


class TestMeAnswersForTheWorkingTenantOnTheSalonSurface:
    """``/api/v1/me`` решает, какой экран смонтирует Mini App (``is_solo_provider``).

    Его декоратор — ``require_init_data`` клиентской поверхности, он берёт
    строку тенанта ``MAX_BOT_TENANT_SLUG`` и резолвером не пользуется. Без
    этого теста срез 2 открыл бы ручки ``master_api`` и оставил экран
    клиентским: ``/me`` отдавал бы салон, ``role=customer``,
    ``is_solo_provider=False``.
    """

    def test_me_from_the_salon_bot_names_the_solo_tenant(
        self, client, salon_bot, solo_pair, settings
    ):
        """Красный до правки: ``tenant.slug == "formula-tela"``, ``is_solo_provider is False``."""

        # initData подписан токеном САЛОННОГО бота — поверхность мастеров.
        params = {
            "user": json.dumps({"id": int(CHANNEL_USER_ID), "first_name": "Соло"}),
            "auth_date": str(int(time_module.time())),
        }
        header = f"MaxInitData {_sign(params, token='tok-salon')}"

        resp = client.get(reverse("identity:me"), HTTP_AUTHORIZATION=header)

        assert resp.status_code == 200, resp.content
        body = resp.json()
        assert body["tenant"]["slug"] == "solo-max-0badc0de", body
        assert body["is_solo_provider"] is True, body

    def test_me_from_the_client_bot_stays_the_salon_customer(self, client, solo_pair, settings):
        """Отрицательный контроль: клиентский бот спрашивает «кто ты как клиент» — салон, как сегодня.

        Иначе соло-мастер, записывающийся к кому-то как клиент, увидел бы
        свой кабинет вместо каталога салона.
        """

        settings.MAX_BOT_REGISTRY = (
            BotEntry(
                slug="client",
                webhook_secret="wh-client",  # pragma: allowlist secret
                api_token="tok-client",  # pragma: allowlist secret
                tenant_slug="formula-tela",
                stream="max",
            ),
        )
        settings.MAX_BOT_TENANT_SLUG = "formula-tela"
        # initData подписан токеном этого бота.
        params = {
            "user": json.dumps({"id": int(CHANNEL_USER_ID), "first_name": "Соло"}),
            "auth_date": str(int(time_module.time())),
        }
        header = f"MaxInitData {_sign(params, token='tok-client')}"

        resp = client.get(reverse("identity:me"), HTTP_AUTHORIZATION=header)

        assert resp.status_code == 200, resp.content
        assert resp.json()["tenant"]["slug"] == "formula-tela", resp.json()
