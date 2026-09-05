"""Пять ворот отвечают одним предикатом — DRF-1506.

Определений «мастер приземлился» было пять, и каждое игнорировало свой
столбец. Этот файл держит их вместе: каждое из пяти мест вызывается
здесь по-настоящему — менеджер каталога, декоратор мастер-приложения,
резолвер ролей, ростер владелицы и подбор замены при деактивации, — и
на одних и тех же данных они обязаны сходиться.

Проверка парная во всех смыслах. Приземлившаяся мастер проходит все
пятеро ворот; неприземлившаяся (та форма, которую пишет путь
приглашения) не проходит ни одних. Одного отрицания мало: тест, который
умеет только сказать «не пустили», зеленеет и на сломанной фикстуре, за
чем и следит ``negative_assert_guard`` (DRF-1411).

Третий случай назван отдельно и защищён отдельно — синхронизированная
мастер: продаётся, но в мастер-приложение не входит, потому что входить
ей нечем. На боевом контуре 05.09.2026 таких девять из девяти, и именно
их сняло бы с продажи требование ``linked_bot_user`` в ``bookable()``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from django.http import HttpRequest, JsonResponse
from django.test import RequestFactory

from apps.admin_api.services.master_deactivation import _find_fallback_masters
from apps.admin_api.services.staff_roster import build_staff_roster
from apps.booking.models import BookingRequest
from apps.catalog.master_state import (
    ACCEPTED,
    is_available,
    is_landed,
    master_state,
)
from apps.catalog.models import CatalogMaster, CatalogService, MasterService
from apps.identity.models import BotUser
from apps.identity.services.role_resolver import resolve_role
from apps.master_api.auth import require_master_init_data
from apps.master_api.tests.conftest import BOT_TOKEN, init_data_header
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db


MAX_USER_ID = "770001"


@pytest.fixture(autouse=True)
def _bot_token(settings) -> None:
    settings.MAX_BOT_TOKEN = BOT_TOKEN


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(
        slug="landing-test",
        name="Студия Приземления",
        timezone="Europe/Moscow",
    )


@pytest.fixture
def bot_user(tenant: Tenant) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id=MAX_USER_ID,
        display_name="Анна",
        chat_id=MAX_USER_ID,
    )


@pytest.fixture
def service(tenant: Tenant) -> CatalogService:
    return CatalogService.all_tenants.create(
        tenant=tenant,
        external_id=1,
        external_updated_at=datetime.now(tz=timezone.utc),
        slug="manicure",
        name="Маникюр",
        duration_min=60,
        is_active=True,
    )


_NEXT_EXTERNAL_ID = [100]


def _make_master(tenant: Tenant, **kwargs) -> CatalogMaster:
    _NEXT_EXTERNAL_ID[0] += 1
    defaults = {
        "name": "Анна Петрова",
        "is_active": True,
        "invite_status": CatalogMaster.InviteStatus.ACCEPTED,
        "archived_at": None,
        "linked_bot_user": None,
    }
    defaults.update(kwargs)
    return CatalogMaster.all_tenants.create(
        tenant=tenant,
        external_id=_NEXT_EXTERNAL_ID[0],
        external_updated_at=datetime.now(tz=timezone.utc),
        **defaults,
    )


# --- пять ворот, вызываемые по-настоящему -------------------------------


def _gate_bookable(tenant: Tenant, master: CatalogMaster) -> bool:
    """Ворота 1 — ``_MasterManager.bookable()``: продаётся ли клиенту."""

    with tenant_scope(tenant):
        return CatalogMaster.objects.bookable().filter(pk=master.pk).exists()


def _gate_master_api(master: CatalogMaster) -> bool:
    """Ворота 2 — ``require_master_init_data``: пускает ли в приложение."""

    @require_master_init_data
    def view(request: HttpRequest) -> JsonResponse:
        return JsonResponse({"ok": True})

    resp = view(RequestFactory().get("/", HTTP_AUTHORIZATION=init_data_header(MAX_USER_ID)))
    return resp.status_code == 200


def _gate_role_resolver(bot_user: BotUser) -> bool:
    """Ворота 3 — ``resolve_role``: считает ли платформа её мастером."""

    return resolve_role(bot_user).is_master


def _gate_roster(tenant: Tenant, master: CatalogMaster) -> str:
    """Ворота 4 — ростер владелицы: каким словом он её называет."""

    people, _total, _truncated = build_staff_roster(tenant)
    for person in people:
        if person.master_id == master.id:
            for grant in person.roles:
                if grant.role == "master":
                    return grant.state
    return "absent"


def _gate_fallback(
    tenant: Tenant,
    candidate: CatalogMaster,
    service: CatalogService,
) -> bool:
    """Ворота 5 — подбор замены при деактивации: можно ли передать ей запись."""

    leaving = _make_master(tenant, name="Уходящая")
    # ``get_or_create``, потому что тест ниже проходит эти ворота дважды на
    # одном и том же мастере — до снятия ``is_active`` и после. Пара
    # (master, service) уникальна, и второй ``create`` уронил бы тест
    # IntegrityError раньше, чем он успел бы что-нибудь проверить.
    MasterService.all_tenants.get_or_create(tenant=tenant, master=candidate, service=service)
    MasterService.all_tenants.get_or_create(tenant=tenant, master=leaving, service=service)
    booking = BookingRequest.all_tenants.create(
        tenant=tenant,
        service=service,
        master=leaving,
        service_name=service.name,
        master_name=leaving.name,
        client_name="Клиентка",
        client_phone="+79991234567",
        visit_at=datetime.now(tz=timezone.utc) + timedelta(days=3),
        duration_min=60,
        status=BookingRequest.Status.CONFIRMED,
    )
    found = _find_fallback_masters(booking, deactivating_master_id=leaving.id)
    # ``FallbackCandidate.master_id`` — строка, а не UUID (шлётся на клиент).
    return any(c.master_id == str(candidate.id) for c in found)


# --- парная проверка на всех пяти воротах --------------------------------


class TestLandedPassesEveryGateAndNotLandedPassesNone:
    """Одна фраза, проверенная пятью способами.

    Оба утверждения стоят в одном теле теста нарочно. «Не пустили» без
    «пустили» на тех же данных — это не проверка: сломанная фикстура
    даёт ровно ту же зелень.
    """

    def test_five_gates_agree(
        self,
        tenant: Tenant,
        bot_user: BotUser,
        service: CatalogService,
    ) -> None:
        landed = _make_master(
            tenant,
            name="Приземлившаяся",
            linked_bot_user=bot_user,
            invite_status=CatalogMaster.InviteStatus.ACCEPTED,
            is_active=True,
        )
        # save() штампует accepted_at сам — приземление без даты
        # приземления невозможно по построению.
        landed.refresh_from_db()
        assert landed.accepted_at is not None

        assert is_landed(landed) is True
        assert _gate_bookable(tenant, landed) is True
        assert _gate_master_api(landed) is True
        assert _gate_role_resolver(bot_user) is True
        assert _gate_roster(tenant, landed) == "active"
        assert _gate_fallback(tenant, landed, service) is True

    def test_five_gates_reject_the_shape_the_invite_path_writes(
        self,
        tenant: Tenant,
        bot_user: BotUser,
        service: CatalogService,
    ) -> None:
        """Форма из ``views_invite``: PENDING, ``is_active=False``, без бота.

        Положительная стража — приземлившаяся мастер в том же салоне на
        тех же воротах. Без неё «не пустили» ничего не доказывает.
        """

        landed = _make_master(
            tenant,
            name="Приземлившаяся",
            linked_bot_user=bot_user,
            is_active=True,
        )
        invited = _make_master(
            tenant,
            name="Приглашённая",
            invite_status=CatalogMaster.InviteStatus.PENDING,
            is_active=False,
            invited_at=datetime.now(tz=timezone.utc) - timedelta(days=1),
        )

        # Положительная половина: ворота работают и пускают.
        assert _gate_bookable(tenant, landed) is True
        assert _gate_master_api(landed) is True
        assert _gate_role_resolver(bot_user) is True
        assert _gate_roster(tenant, landed) == "active"
        assert _gate_fallback(tenant, landed, service) is True

        # Отрицательная половина: те же ворота, тот же салон.
        assert is_landed(invited) is False
        assert _gate_bookable(tenant, invited) is False
        assert _gate_roster(tenant, invited) == "pending"
        assert _gate_fallback(tenant, invited, service) is False
        # У приглашённой нет BotUser — в мастер-приложение и к резолверу
        # она приходит не своими воротами, а никакими: связи нет.
        assert invited.linked_bot_user_id is None


class TestTheSplitThatProducedDRF1080:
    """Мастер, у которой есть бот, но снят ``is_active``.

    Это та самая форма, на которой определения разъезжались: резолвер
    ролей не спрашивал ``is_active`` и называл её мастером, а декоратор
    мастер-приложения спрашивал только его и отвечал 403 на каждой
    ручке. Человек был мастером для платформы и никем для приложения.

    Теперь оба спрашивают ``is_landed``, и разъехаться им нечем.
    """

    def test_all_five_gates_say_no_and_all_five_say_yes_when_she_is_active(
        self,
        tenant: Tenant,
        bot_user: BotUser,
        service: CatalogService,
    ) -> None:
        master = _make_master(
            tenant,
            name="Деактивированная",
            linked_bot_user=bot_user,
            invite_status=CatalogMaster.InviteStatus.ACCEPTED,
            is_active=True,
        )

        # Положительная половина — та же строка, пока она активна.
        assert _gate_bookable(tenant, master) is True
        assert _gate_master_api(master) is True
        assert _gate_role_resolver(bot_user) is True
        assert _gate_roster(tenant, master) == "active"
        assert _gate_fallback(tenant, master, service) is True

        master.is_active = False
        master.save(update_fields=["is_active"])

        # Отрицательная половина — все пятеро, а не двое из пяти.
        assert is_landed(master) is False
        assert _gate_bookable(tenant, master) is False
        assert _gate_master_api(master) is False
        assert _gate_role_resolver(bot_user) is False
        assert _gate_roster(tenant, master) == "revoked"
        assert _gate_fallback(tenant, master, service) is False


class TestTheRosterStopsCallingAnInvitedMasterRevoked:
    """Разрыв 2 — порядок проверок в ростере.

    ``_master_state`` спрашивал ``not is_active`` раньше
    ``invite_status``, а путь приглашения пишет ``is_active=False``
    вместе с ``PENDING``. Владелице салона мастера, которую она позвала
    минуту назад, показывали как «доступ отозван».
    """

    def test_invited_is_pending_and_accepted_is_active(self) -> None:
        now = datetime.now(tz=timezone.utc)

        # Отрицание: ровно та тройка столбцов, что пишет views_invite.
        assert master_state(archived_at=None, is_active=False, invite_status="pending") == "pending"
        # Положительная стража на той же функции.
        assert master_state(archived_at=None, is_active=True, invite_status=ACCEPTED) == "active"
        # Архив по-прежнему сильнее всего: она ушла, а не ждёт.
        assert master_state(archived_at=now, is_active=False, invite_status="pending") == "revoked"
        # Принята, но деактивирована — «отозван» здесь честное слово.
        assert master_state(archived_at=None, is_active=False, invite_status=ACCEPTED) == "revoked"


class TestTheNineSyncedMastersStayOnSale:
    """Граница, нарушение которой обрушило бы пилот.

    Замер боевого контура 05.09.2026: девять активных мастеров, у всех
    ``linked_bot_user IS NULL`` — они приехали синхронизацией, а она
    платформенных полей не трогает. Требование бот-аккаунта в
    ``bookable()`` сняло бы с продажи всех девятерых разом.
    """

    def test_bookable_count_does_not_drop_for_the_pilot_shape(self, tenant: Tenant) -> None:
        for i in range(9):
            _make_master(
                tenant,
                name=f"Синхронизированная {i}",
                linked_bot_user=None,
                is_active=True,
                invite_status=CatalogMaster.InviteStatus.ACCEPTED,
            )

        with tenant_scope(tenant):
            bookable = CatalogMaster.objects.bookable().count()

        assert bookable == 9
        # И ровно то, что делает эту цифру нетривиальной: ни одна из
        # девяти не приземлилась. Продаётся ≠ приземлилась, и разводить
        # эти два вопроса — вся причина, по которой определений два.
        rows = list(CatalogMaster.all_tenants.filter(tenant=tenant))
        assert len(rows) == 9
        assert all(is_available(m) for m in rows)
        assert not any(is_landed(m) for m in rows)

    def test_an_archived_master_is_the_only_row_the_tightening_takes_off_sale(
        self, tenant: Tenant
    ) -> None:
        """Что именно добавил ``archived_at`` в ``bookable()``.

        Раньше заархивированная мастер, у которой ``is_active`` не
        успели снять, оставалась на продаже. Пара строк: одна такая и
        одна здоровая — иначе «ноль бронируемых» прошло бы за успех.
        """

        healthy = _make_master(tenant, name="Здоровая")
        archived = _make_master(
            tenant,
            name="Заархивированная",
            is_active=True,
            archived_at=datetime.now(tz=timezone.utc),
        )

        assert _gate_bookable(tenant, healthy) is True
        assert _gate_bookable(tenant, archived) is False


class TestAcceptedAtIsTheModelsJobNotTheCallers:
    """``accepted_at`` штампует ``save()``, и обойти его нечем."""

    def test_stamped_on_landing_even_with_a_narrow_update_fields(
        self, tenant: Tenant, bot_user: BotUser
    ) -> None:
        master = _make_master(
            tenant,
            invite_status=CatalogMaster.InviteStatus.PENDING,
            is_active=False,
        )
        assert master.accepted_at is None  # положительная стража ниже

        master.linked_bot_user = bot_user
        master.invite_status = CatalogMaster.InviteStatus.ACCEPTED
        master.is_active = True
        # Узкий update_fields — ровно то, что пишет onboarding_accept.
        # Без дополнения списка в save() штамп не доехал бы до базы.
        master.save(update_fields=["linked_bot_user", "invite_status", "is_active"])

        master.refresh_from_db()
        assert master.accepted_at is not None

    def test_not_re_stamped_and_not_cleared(self, tenant: Tenant, bot_user: BotUser) -> None:
        master = _make_master(tenant, linked_bot_user=bot_user)
        master.refresh_from_db()
        first = master.accepted_at
        assert first is not None

        master.name = "Другое имя"
        master.save()
        master.refresh_from_db()
        assert master.accepted_at == first

        # Приглашение отозвали — дата остаётся: она отвечает «когда
        # приняла», а «принята ли сейчас» отвечает invite_status.
        master.invite_status = CatalogMaster.InviteStatus.CANCELLED
        master.save(update_fields=["invite_status"])
        master.refresh_from_db()
        assert master.accepted_at == first
        assert is_landed(master) is False

    def test_a_synced_master_gets_no_stamp(self, tenant: Tenant) -> None:
        synced = _make_master(tenant, linked_bot_user=None)
        synced.refresh_from_db()

        assert synced.accepted_at is None
        # Положительная стража на той же фабрике: связанная строка штамп
        # получает, так что NULL выше — про отсутствие связи, а не про
        # сломанный save().
        assert is_available(synced) is True


class TestTheLiteralCannotDriftFromTheEnum:
    def test_accepted_literal_matches_the_enum(self) -> None:
        assert ACCEPTED == CatalogMaster.InviteStatus.ACCEPTED
