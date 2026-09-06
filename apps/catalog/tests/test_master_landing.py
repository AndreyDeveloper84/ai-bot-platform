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
from typing import get_args
from uuid import uuid4

import pytest
from django.http import HttpRequest, JsonResponse
from django.test import RequestFactory

from apps.admin_api.services.master_deactivation import _find_fallback_masters
from apps.admin_api.services.staff_roster import build_staff_roster
from apps.booking.models import BookingRequest
from apps.catalog.master_state import (
    ACCEPTED,
    RoleState,
    SaleBlock,
    is_available,
    is_landed,
    master_state,
    sale_block,
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
        # Форма синхронизированной строки: ``upsert_specialists`` кладёт
        # сюда ``dto.user_id``. Умолчание, а не ``None``, потому что
        # именно эта форма стоит на боевом контуре и именно она обязана
        # продолжать продаваться после DRF-1540. Строки без ключа
        # заводятся явным ``ayla_user_id=None`` там, где это и есть
        # предмет теста.
        "ayla_user_id": uuid4(),
    }
    defaults.update(kwargs)
    return CatalogMaster.all_tenants.create(
        tenant=tenant,
        external_id=_NEXT_EXTERNAL_ID[0],
        external_updated_at=datetime.now(tz=timezone.utc),
        **defaults,
    )


def _state(
    *,
    archived_at: datetime | None = None,
    is_active: bool = True,
    invite_status: str = ACCEPTED,
    ayla_user_id: object = None,
) -> str:
    """``master_state`` на голых столбцах, без похода в базу.

    Гейт принимает строку целиком, а строкой годится и словарь — тот же
    путь, которым его зовёт ростер через ``.values()``. Столбцы названы
    по одному, чтобы тест ломался, когда гейт начнёт спрашивать пятый:
    молчаливо подставленное умолчание — это ровно тот отказ, который
    DRF-1540 убирает.
    """

    return master_state(
        {
            "archived_at": archived_at,
            "is_active": is_active,
            "invite_status": invite_status,
            "ayla_user_id": ayla_user_id,
        }
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
        linked = uuid4()

        # Отрицание: ровно та четвёрка столбцов, что пишет views_invite.
        assert _state(is_active=False, invite_status="pending", ayla_user_id=None) == "pending"
        # Положительная стража на той же функции.
        assert _state(is_active=True, invite_status=ACCEPTED, ayla_user_id=linked) == "active"
        # Архив по-прежнему сильнее всего: она ушла, а не ждёт.
        assert (
            _state(
                archived_at=now,
                is_active=False,
                invite_status="pending",
                ayla_user_id=None,
            )
            == "revoked"
        )
        # Принята, но деактивирована — «отозван» здесь честное слово.
        assert _state(is_active=False, invite_status=ACCEPTED, ayla_user_id=linked) == "revoked"


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

    def test_the_tightening_takes_off_only_the_rows_with_no_ayla_link(self, tenant: Tenant) -> None:
        """Замер DRF-1540, зашитый в тест: дельта на форме пилота — ноль.

        Синхронизация заполняет ``ayla_user_id`` (``upserter`` кладёт
        ``dto.user_id``), поэтому строки, которые продаются сегодня,
        продаются и после ужесточения. Снимается ровно инвайт-форма —
        та, до которой уведомления и так не доходили.

        Обе половины на одних данных: счётчик, который умеет только
        падать, зеленел бы и на пустом тенанте.
        """

        for i in range(9):
            _make_master(tenant, name=f"Синхронизированная {i}", ayla_user_id=uuid4())

        with tenant_scope(tenant):
            before = CatalogMaster.objects.bookable().count()
        assert before == 9

        # Инвайт-форма: приглашение принято, ключа нет. Одиннадцатой
        # строкой она бы встала на витрину — и не получила бы ни одного
        # уведомления о записи.
        invited = _make_master(tenant, name="Из приглашения", ayla_user_id=None)

        with tenant_scope(tenant):
            after = CatalogMaster.objects.bookable().count()
            total = CatalogMaster.objects.filter(tenant=tenant).count()

        assert after == 9
        assert total == 10
        assert _gate_roster(tenant, invited) == "ayla_unlinked"

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


# --- DRF-1540 — связь с Ayla как условие продажи -------------------------


class TestAMasterWithNoAylaLinkIsNotSoldAndTheOwnerIsToldWhy:
    """Решение владельца 06.09.2026: молчаливый отказ меняется на видимый.

    Мастер из приглашения и та же мастер из синхронизации живут двумя
    строками, склеить их сегодня не по чему. Инвайт-строка продавалась
    клиентам, а уведомление о записи уходило по мосту ``master_user_id``
    на другую — человек не узнавал о записи вообще. Всё выглядело
    работающим, и это худшая форма отказа.

    Владелец выбрал закрыться: непроданный мастер лучше проданного, до
    которого не доходят уведомления. Настоящая починка — канонический
    идентификатор в приглашении (DRF-1541, контракт Ayla).
    """

    def test_the_storefront_closes_and_the_cabinet_stays_open(
        self,
        tenant: Tenant,
        bot_user: BotUser,
        service: CatalogService,
    ) -> None:
        """Граница проведена ровно там, где её провёл владелец.

        Обе половины в одном теле: «не продаётся» без «входит в кабинет»
        на тех же данных прошло бы и на строке, сломанной чем угодно
        другим.
        """

        unlinked = _make_master(
            tenant,
            name="Несвязанная",
            linked_bot_user=bot_user,
            invite_status=CatalogMaster.InviteStatus.ACCEPTED,
            is_active=True,
            ayla_user_id=None,
        )
        unlinked.refresh_from_db()
        assert unlinked.accepted_at is not None  # приземление состоялось

        # Витрина закрыта — трое ворот, которые отвечают на вопрос о продаже.
        assert is_available(unlinked) is False
        assert _gate_bookable(tenant, unlinked) is False
        assert _gate_fallback(tenant, unlinked, service) is False

        # Кабинет открыт — та же строка, вопрос о личности, а не о продаже.
        # Это граница DRF-1521 («не продаётся, но в кабинет входит»), и
        # ужесточение продажи не имело права её перейти.
        assert is_landed(unlinked) is True
        assert _gate_master_api(unlinked) is True
        assert _gate_role_resolver(bot_user) is True

        # Владелица салона видит причину, а не пустоту и не «активна».
        assert _gate_roster(tenant, unlinked) == "ayla_unlinked"

    def test_the_same_row_sells_again_the_moment_the_link_appears(
        self,
        tenant: Tenant,
        bot_user: BotUser,
        service: CatalogService,
    ) -> None:
        """Парная положительная стража (DRF-1411) на тех же данных.

        Отличается ровно один столбец. Без этой половины «не продаётся»
        зеленело бы и на фикстуре, сломанной чем угодно другим.
        """

        master = _make_master(
            tenant,
            name="Она же, но связанная",
            linked_bot_user=bot_user,
            invite_status=CatalogMaster.InviteStatus.ACCEPTED,
            is_active=True,
            ayla_user_id=None,
        )
        assert _gate_bookable(tenant, master) is False
        assert _gate_roster(tenant, master) == "ayla_unlinked"

        master.ayla_user_id = uuid4()
        master.save(update_fields=["ayla_user_id"])

        assert is_available(master) is True
        assert _gate_bookable(tenant, master) is True
        assert _gate_fallback(tenant, master, service) is True
        assert _gate_roster(tenant, master) == "active"

    def test_two_refusals_never_share_a_word(
        self,
        tenant: Tenant,
        bot_user: BotUser,
    ) -> None:
        """Почему гейт возвращает причину, а не «нет».

        «Приглашение не принято» — владелица идёт к мастеру. «Не удалось
        связать с Ayla» — владелица идёт к нам. Один текст на две
        причины отправил бы её чинить не там, и это не про формулировку,
        а про то, кто чинит.
        """

        invited = _make_master(
            tenant,
            name="Приглашённая",
            invite_status=CatalogMaster.InviteStatus.PENDING,
            is_active=False,
            ayla_user_id=None,
        )
        unlinked = _make_master(
            tenant,
            name="Несвязанная",
            linked_bot_user=bot_user,
            ayla_user_id=None,
        )
        revoked = _make_master(
            tenant,
            name="Отозванная",
            is_active=False,
            ayla_user_id=None,
        )

        states = {
            "invited": _gate_roster(tenant, invited),
            "unlinked": _gate_roster(tenant, unlinked),
            "revoked": _gate_roster(tenant, revoked),
        }

        assert states == {
            "invited": "pending",
            "unlinked": "ayla_unlinked",
            "revoked": "revoked",
        }
        # И то же самое как утверждение о механизме, а не о трёх строках:
        # три разные причины дали три разных слова.
        assert len(set(states.values())) == 3

    def test_a_pending_invite_outranks_the_missing_link(self) -> None:
        """Какую из двух одновременных причин показать.

        У только что приглашённой мастера пусты оба поля. «Не удалось
        связать профиль с Ayla» здесь было бы верно и бесполезно: она
        ещё не пришла, связывать пока нечего, а владелице надо повторить
        приглашение. Поэтому ``ayla_unlinked`` — последняя ветка.
        """

        assert _state(is_active=False, invite_status="pending", ayla_user_id=None) == "pending"
        # Положительная стража на той же паре столбцов: приняла — и
        # причина сменилась на настоящую.
        assert _state(is_active=True, invite_status=ACCEPTED, ayla_user_id=None) == "ayla_unlinked"

    def test_a_proxy_id_is_not_what_this_column_asks_for(self, tenant: Tenant) -> None:
        """Граница, которую задача запрещает переходить.

        ``resolve_identity`` умеет лениво завести в Ayla прокси-профиль,
        и соблазн положить его сюда велик: поле заполнится, гейт
        пропустит. Пропустит — и уведомление уйдёт на id, по которому
        совпадения не будет никогда, то есть вернётся ровно тот
        молчаливый отказ, который здесь закрывается.

        Тест не умеет отличить прокси-UUID от канонического — их и не
        отличить по значению. Он фиксирует то, что проверяемо: гейт
        спрашивает СТОЛБЕЦ, а не «какой-нибудь идентификатор», и любая
        будущая правка, решившая заполнять его чем попало, обязана
        объясниться здесь, а не в тишине.
        """

        master = _make_master(tenant, name="С ключом", ayla_user_id=uuid4())
        assert sale_block(master) is None

        master.ayla_user_id = None
        master.save(update_fields=["ayla_user_id"])
        assert sale_block(master) == "ayla_unlinked"


class TestTheReasonVocabularyCannotBeExtendedByHalves:
    """Страховка для DRF-1521, которая придёт в этот же гейт.

    Она добавит четвёртое условие готовности профиля и своё значение
    причины. Добавить его в один словарь и забыть про второй — самая
    дешёвая из возможных ошибок, и её ловит равенство ниже: ростер
    владелицы не имеет права знать меньше причин, чем витрина.
    """

    def test_role_state_is_active_plus_every_sale_block(self) -> None:
        assert set(get_args(RoleState)) == {"active"} | set(get_args(SaleBlock))
        # Положительная стража: словари не пусты, и равенство выше не
        # выполнилось по вырожденности.
        assert "ayla_unlinked" in get_args(SaleBlock)
        assert len(get_args(SaleBlock)) >= 3
