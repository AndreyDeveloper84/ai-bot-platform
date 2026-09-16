"""Карточки доступов и приглашений: показывают, не трогают, секрет не отдают.

Повод: моделей ``TenantStaff`` и ``StaffInvite`` в админке не было ни
одной карточки — на вопрос «кто в этом салоне админ» оператор отвечал
запросом в базу. Карточки заведены на ЧТЕНИЕ: выдача и отзыв доступа —
операции доменного слоя со своими проверками и аудитом, и admin-форма,
пишущая в эти таблицы напрямую, воспроизвела бы их логику мимо них.

Три предмета, и каждый со своим доказательством.

**A. Запрет записи — свойство таблицы, а не роли читателя.** Проверяется
на СУПЕРПОЛЬЗОВАТЕЛЕ: если бы запрет держался на роли, именно он бы его
и обошёл, а карточка осталась бы «зелёной» для всех остальных.

**B. ``code_hash`` не уезжает в HTML.** Дайджест кода приглашения —
учётные данные. Идиома взята у ``test_admin_secret_hiding.py``: смотреть
на отрисованный ответ, а не на объявление полей, потому что Django по
умолчанию показывает все редактируемые поля, и защита «я не перечислил
его в fields» держится ровно до первой правки.

**C. Состояние названо словами, и различает три случая.** «Не
использовано» и «срок истёк» — разные состояния; слить их значит спрятать
тот самый случай, ради которого оператор пришёл: приглашение, которое
человек уже не сможет принять.
"""

from __future__ import annotations

import secrets
from datetime import timedelta

import pytest
from django.contrib.admin.sites import site
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.identity.models import BotUser
from apps.tenancy.models import StaffInvite, Tenant, TenantStaff

pytestmark = pytest.mark.django_db

#: Значение фиктивное и выглядит фиктивным нарочно, но по форме — то же,
#: что настоящий дайджест: иначе поиск подстроки в HTML проверял бы не тот
#: формат. ``pragma`` — detect-secrets ловит имя, а не значение.
CODE_HASH = "ci-fake-not-a-real-invite-digest-0000"  # pragma: allowlist secret


@pytest.fixture
def tenant() -> Tenant:
    return Tenant.objects.create(slug="ops-staff", name="Салон операций")


@pytest.fixture
def person(tenant: Tenant) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="ops-staff-1",
        display_name="Анна",
    )


@pytest.fixture
def superuser_client() -> Client:
    password = secrets.token_urlsafe(24)
    get_user_model().objects.create_superuser(username="ops.operator", email="", password=password)
    client = Client()
    assert client.login(username="ops.operator", password=password)
    return client


@pytest.fixture
def staff_row(tenant: Tenant, person: BotUser) -> TenantStaff:
    return TenantStaff.all_tenants.create(
        tenant=tenant, bot_user=person, role=TenantStaff.Role.ADMIN
    )


@pytest.fixture
def invite_row(tenant: Tenant) -> StaffInvite:
    return StaffInvite.all_tenants.create(
        tenant=tenant,
        role=StaffInvite.Role.ADMIN,
        code_hash=CODE_HASH,
        expires_at=timezone.now() + timedelta(days=7),
    )


class TestTheCardsDoNotWrite:
    """A. Запрет — свойство таблицы. Проверяем на суперпользователе."""

    @pytest.mark.parametrize("model", [TenantStaff, StaffInvite])
    def test_no_add_change_or_delete_even_for_a_superuser(self, model, rf) -> None:
        request = rf.get("/admin/")
        request.user = get_user_model()(is_superuser=True, is_staff=True)

        model_admin = site._registry[model]
        assert model_admin.has_add_permission(request) is False
        assert model_admin.has_change_permission(request) is False
        assert model_admin.has_delete_permission(request) is False

    @pytest.mark.parametrize("model", [TenantStaff, StaffInvite])
    def test_the_card_is_registered_at_all(self, model) -> None:
        """ПРИСУТСТВИЕ впереди запретов: карточка есть.

        Без этого узла три ``False`` выше зеленели бы и на отсутствующей
        регистрации — точнее, падали бы по ключу, но падение читалось бы
        как поломка теста, а не как отсутствие предмета.
        """
        assert model in site._registry


class TestTheInviteDigestNeverReachesTheHtml:
    """B. Секрет не отдаётся ни списком, ни карточкой."""

    def test_changelist_html_does_not_carry_the_digest(
        self, superuser_client: Client, invite_row: StaffInvite
    ) -> None:
        url = reverse("admin:tenancy_staffinvite_changelist")
        body = superuser_client.get(url).content.decode("utf-8")

        # ПРИСУТСТВИЕ: строка отрисована — иначе отсутствие дайджеста
        # означало бы лишь пустой список.
        assert "Салон операций" in body
        assert CODE_HASH not in body

    def test_change_form_html_does_not_carry_the_digest(
        self, superuser_client: Client, invite_row: StaffInvite
    ) -> None:
        url = reverse("admin:tenancy_staffinvite_change", args=[invite_row.pk])
        body = superuser_client.get(url).content.decode("utf-8")

        assert "Салон операций" in body
        assert CODE_HASH not in body


class TestStateIsNamedNotGuessed:
    """C. Три состояния приглашения и два состояния доступа — словами."""

    def test_access_state_says_active_when_not_revoked(self, staff_row: TenantStaff) -> None:
        model_admin = site._registry[TenantStaff]
        assert model_admin.access_state(staff_row) == "Действует"

    def test_access_state_says_revoked_with_the_date(self, staff_row: TenantStaff) -> None:
        staff_row.deactivated_at = timezone.now()
        assert "Отозван" in site._registry[TenantStaff].access_state(staff_row)

    def test_invite_waiting_is_not_confused_with_expired(self, invite_row: StaffInvite) -> None:
        model_admin = site._registry[StaffInvite]
        assert model_admin.invite_state(invite_row) == "Ждёт принятия"

    def test_expired_invite_is_named_expired_not_waiting(self, invite_row: StaffInvite) -> None:
        """Тот самый случай, ради которого колонка заведена."""
        invite_row.expires_at = timezone.now() - timedelta(days=1)
        state = site._registry[StaffInvite].invite_state(invite_row)
        assert "Срок истёк" in state
        assert state != "Ждёт принятия"

    def test_used_invite_stays_used_after_expiry(self, invite_row: StaffInvite) -> None:
        """Порядок проверок — не косметика.

        Использованное приглашение остаётся использованным и после
        истечения срока; обратный порядок назвал бы его «просроченным» и
        стёр бы факт, что человек им воспользовался.
        """
        invite_row.used_at = timezone.now() - timedelta(days=2)
        invite_row.expires_at = timezone.now() - timedelta(days=1)
        assert "Использовано" in site._registry[StaffInvite].invite_state(invite_row)
