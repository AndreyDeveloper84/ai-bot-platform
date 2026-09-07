"""DRF-1553 — кнопка верификации на экране подключения салона.

Решение владельца (``docs/OPEN_DECISIONS.md`` §51.1): экран подключения
не называет следующий шаг, а даёт его кнопкой — тем же сервисом, что
зовёт действие ``verify_masters`` в админке каталога. Уровень доступа
один: суперпользователь (§27), кнопка ничьих прав не расширяет.

Что здесь доказывается:

* свежеподключённый салон — экран называет причину подслучаем («ждут
  верификации») И даёт кнопку;
* причина от архива или ``is_active=False`` — кнопки нет: верификация
  такого мастера бронируемым не сделает, и предлагать нечего;
* нажатие кнопки верифицирует ТЕХ ЖЕ мастеров, что действие в админке
  каталога, и пишет ТО ЖЕ в журнал. Паритет доказывается по факту —
  сравнением состояния строк и полей ``LogEntry`` двух экранов, а не
  тем, что «зовётся тот же код»;
* парная положительная стража (DRF-1411): после нажатия салон виден, и
  бронируемых мастеров ровно столько, сколько верифицировали;
* автоверификации нет: подключение и повторная оценка оставляют мастера
  ``pending`` (DRF-1496 этой задачей не ослабляется).
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from django.contrib.admin.models import LogEntry
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.audit.models import AuditLog
from apps.catalog.models import CatalogMaster, CatalogService
from apps.tenancy.onboarding import (
    REASON_MASTERS_AWAIT_VERIFICATION,
    REASON_NO_BOOKABLE_MASTERS,
    BackendProbe,
    ConnectResult,
    assess_salon,
)
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

_PENDING = CatalogMaster.InviteStatus.PENDING
_ACCEPTED = CatalogMaster.InviteStatus.ACCEPTED

CONNECT_URL = "admin:tenancy_tenant_connect"
CATALOG_CHANGELIST_URL = "admin:catalog_catalogmaster_changelist"
VERIFY_FIELD = "verify_masters_of"


@pytest.fixture
def owner_client(django_user_model) -> Client:
    user = django_user_model.objects.create_superuser(
        username="drf1553-vladelets",
        email="vladelets1553@example.com",
        password="x",  # pragma: allowlist secret
    )
    client = Client()
    client.force_login(user)
    return client


def _salon(slug: str = "sorok-okon") -> Tenant:
    """Салон, у которого невидимость может быть только про мастеров.

    Город задан, синхронизация проходила, активная услуга есть — иначе
    экран назвал бы другую причину, и тест проверял бы не то.
    """
    tenant = Tenant.objects.create(slug=slug, name="Сорок окон", city="Москва")
    tenant.last_catalog_sync_ok_at = timezone.now()
    tenant.save(update_fields=["last_catalog_sync_ok_at"])
    CatalogService.all_tenants.create(
        tenant=tenant,
        external_id=None,
        external_updated_at=timezone.now(),
        slug=f"strizhka-{slug}",
        name="Стрижка",
        is_active=True,
    )
    return tenant


def _master(tenant: Tenant, name: str, **kwargs) -> CatalogMaster:
    defaults = {
        "tenant": tenant,
        "external_id": None,
        "external_updated_at": timezone.now(),
        "name": name,
        "is_active": True,
        "invite_status": _PENDING,
        # Форма боевой синхронизированной строки: канонический ключ есть
        # (DRF-1540), приглашения никто не слал (DRF-1496).
        "ayla_user_id": uuid.uuid4(),
    }
    defaults.update(kwargs)
    return CatalogMaster.all_tenants.create(**defaults)


def _render_connect_result(client: Client, tenant: Tenant, monkeypatch) -> str:
    """Отрисовать экран в том состоянии, в котором его видит оператор.

    Кнопка живёт на странице ИТОГА подключения, а сам итог экран
    получает от :func:`connect_salon` — который ходит в Ayla по сети.
    Поэтому подменяется ровно он: салон и его мастера настоящие,
    оценка настоящая, поддельна только выборка из Ayla. Так проверяется
    разметка экрана, а не работа HTTP-клиента (у него свои тесты).
    """
    result = ConnectResult(
        tenant=tenant,
        probe=BackendProbe(services=1, specialists=9),
        sync_error=None,
        sync_skipped=False,
        assessment=assess_salon(tenant),
    )
    monkeypatch.setattr("apps.tenancy.admin.connect_salon", lambda **kwargs: result)
    response = client.post(
        reverse(CONNECT_URL),
        {
            "slug": tenant.slug,
            "name": tenant.name,
            "tenant_id": str(tenant.pk),
            "city": tenant.city,
        },
        follow=True,
    )
    assert response.status_code == 200
    return response.content.decode()


def _press_verify(client: Client, tenant: Tenant) -> str:
    """Нажать кнопку — тем же POST, что отправляет форма из разметки."""
    response = client.post(reverse(CONNECT_URL), {VERIFY_FIELD: str(tenant.pk)}, follow=True)
    assert response.status_code == 200
    return response.content.decode()


class TestScreenNamesTheSubcaseAndOffersTheButton:
    def test_freshly_connected_salon_names_reason_and_offers_button(
        self, owner_client: Client, monkeypatch
    ) -> None:
        """Мастера приехали синхронизацией → причина подслучаем + кнопка."""
        tenant = _salon()
        for i in range(9):
            _master(tenant, f"Мастер {i}")

        assessed = assess_salon(tenant)
        assert assessed.masters_total == 9
        assert assessed.masters_awaiting_verification == 9
        assert assessed.bookable_masters == 0
        assert REASON_MASTERS_AWAIT_VERIFICATION in assessed.reasons
        assert assessed.can_verify_masters is True

        # «0 из 9», а не «нет бронируемых»: одно число не говорит ничего,
        # расхождение говорит всё.
        page = _render_connect_result(owner_client, tenant, monkeypatch)
        assert "из 9" in page
        assert "ждут верификации" in page
        assert "Верифицировать 9 мастеров" in page
        assert "После верификации салон появится в поиске." in page

    def test_archived_and_inactive_masters_get_no_button(
        self, owner_client: Client, monkeypatch
    ) -> None:
        """Архив и ``is_active=False`` — причина прежняя, предлагать нечего."""
        tenant = _salon(slug="tri-topolya")
        _master(tenant, "В архиве", archived_at=timezone.now())
        _master(tenant, "Неактивная", is_active=False)

        assessed = assess_salon(tenant)
        # Присутствие на тех же данных: мастера в салоне есть, и оценка
        # их видит — значит «ноль ожидающих» ниже это их состояние, а не
        # пустая выборка.
        assert assessed.masters_total == 2
        assert assessed.masters_awaiting_verification == 0
        assert REASON_NO_BOOKABLE_MASTERS in assessed.reasons
        assert REASON_MASTERS_AWAIT_VERIFICATION not in assessed.reasons
        assert assessed.can_verify_masters is False

        page = _render_connect_result(owner_client, tenant, monkeypatch)
        # Пара к отрицанию: экран отрисован и про мастеров говорит —
        # значит отсутствие кнопки это решение, а не пустая страница.
        assert "Бронируемых мастеров" in page
        assert "Верифицировать" not in page

    def test_button_appears_only_for_the_verifiable_ones(
        self, owner_client: Client, monkeypatch
    ) -> None:
        """Подпись кнопки считает ожидающих, а не всех непринятых."""
        tenant = _salon(slug="smeshannyy")
        _master(tenant, "Ждёт")
        _master(tenant, "В архиве", archived_at=timezone.now())

        assessed = assess_salon(tenant)
        assert assessed.masters_total == 2
        assert assessed.masters_awaiting_verification == 1

        page = _render_connect_result(owner_client, tenant, monkeypatch)
        assert "Верифицировать 1 мастеров" in page


class TestButtonDoesWhatTheCatalogActionDoes:
    def test_same_masters_same_journal_as_catalog_admin_action(self, owner_client: Client) -> None:
        """Паритет по факту: состояние строк и строки журнала совпадают.

        Два салона с одинаковым набором мастеров: один проходит через
        действие админки каталога, другой — через кнопку на экране
        подключения. Сравниваются исходы, а не вызовы.
        """
        by_action = _salon(slug="cherez-deystvie")
        by_button = _salon(slug="cherez-knopku")
        shapes: dict[str, dict[str, Any]] = {
            "ждёт": {},
            "уже принята": {"invite_status": _ACCEPTED},
            "в архиве": {"archived_at": timezone.now()},
            "неактивная": {"is_active": False},
        }
        action_masters = {
            label: _master(by_action, f"Действие {label}", **kw) for label, kw in shapes.items()
        }
        button_masters = {
            label: _master(by_button, f"Кнопка {label}", **kw) for label, kw in shapes.items()
        }

        # Экран каталога верифицирует ровно то, что предложил бы экран
        # подключения: выборкой оператора здесь являются те же строки.
        selected = [
            str(action_masters[label].pk)
            for label in ("ждёт", "уже принята")
            # архив и снятая активность экраном подключения не
            # предлагаются — см. AWAITING_VERIFICATION
        ]
        action_response = owner_client.post(
            reverse(CATALOG_CHANGELIST_URL),
            {"action": "verify_masters", "_selected_action": selected},
            follow=True,
        )
        assert action_response.status_code == 200

        _press_verify(owner_client, by_button)

        # 1. Состояние: по каждой форме строки оба экрана пришли к одному.
        for label in shapes:
            action_row = CatalogMaster.all_tenants.get(pk=action_masters[label].pk)
            button_row = CatalogMaster.all_tenants.get(pk=button_masters[label].pk)
            assert action_row.invite_status == button_row.invite_status, label

        # 2. Журнал: те же поля у той же формы строки. Оба утверждения
        # ниже читают ОДНО имя ``journal`` — присутствие и отсутствие на
        # одних и тех же данных, присутствие впереди (DRF-1411).
        journal = {label: _journal_of(row) for label, row in button_masters.items()}
        assert journal["ждёт"], "кнопка на экране подключения не оставила следа"
        assert journal["ждёт"] == _journal_of(action_masters["ждёт"])

        # 3. Та, которой экран подключения не предлагал, им и не тронута —
        # и след ей не писался.
        assert not journal["в архиве"]

        # 4. Развёрнутый журнал — то, что реально читает оператор.
        # ``LogEntry`` разворачивается в ``AuditLog``
        # (``apps.adminconsole.journal``), и разойтись строки могут уже
        # ЗДЕСЬ: ``write_audit`` берёт тенанта из ``current_tenant()``, и
        # верификация под ``tenant_scope`` подписала бы строку салоном
        # там, где действие админки оставляет NULL. Измеряется, а не
        # предполагается.
        audit = {label: _audit_of(row) for label, row in button_masters.items()}
        assert audit["ждёт"], "кнопка не оставила развёрнутой строки журнала"
        assert audit["ждёт"] == _audit_of(action_masters["ждёт"])

    def test_after_the_button_salon_is_visible_with_exactly_that_many(
        self, owner_client: Client
    ) -> None:
        """Парная положительная стража DRF-1411 — числом, а не словом."""
        tenant = _salon(slug="posle-knopki")
        for i in range(9):
            _master(tenant, f"Мастер {i}")
        # Отрицательная половина на тех же данных, впереди.
        before = assess_salon(tenant)
        assert before.masters_awaiting_verification == 9
        assert before.bookable_masters == 0
        assert before.is_visible is False

        owner_client.post(reverse(CONNECT_URL), {VERIFY_FIELD: str(tenant.pk)}, follow=True)

        after = assess_salon(tenant)
        # Ровно столько, сколько верифицировали, — и салон виден.
        assert after.bookable_masters == before.masters_awaiting_verification
        assert after.masters_awaiting_verification == 0
        assert after.reasons == ()
        assert after.is_visible is True

    def test_verification_stays_manual(self, owner_client: Client) -> None:
        """Автоверификации нет: оценка и повторное чтение не меняют строк.

        Граница §51, подтверждённая §51.1: DRF-1496 не ослабляется.
        """
        tenant = _salon(slug="bez-avtoverifikacii")
        master = _master(tenant, "Ждёт")

        # Присутствие: оценка видит мастера и называет причину — значит
        # «остался pending» ниже это её невмешательство, а не пустота.
        assessed = assess_salon(tenant)
        assert assessed.masters_awaiting_verification == 1
        assess_salon(tenant)
        owner_client.get(reverse(CONNECT_URL))

        master.refresh_from_db()
        assert master.invite_status == _PENDING


class TestAccessLevelIsUnchanged:
    def test_button_is_superuser_only(self, django_user_model) -> None:
        """Тот же уровень доступа, что у подключения (§27, §51.1)."""
        tenant = _salon(slug="prava")
        master = _master(tenant, "Ждёт")

        staff = django_user_model.objects.create_user(
            username="drf1553-smotryashchiy", is_staff=True, is_superuser=False
        )
        # Право менять мастеров у роли есть — и всё равно экран
        # подключения ей закрыт: кнопка живёт за разрешением экрана, а
        # не за своим собственным.
        staff.user_permissions.add(_change_master_permission())
        client = Client()
        client.force_login(staff)

        denied = client.post(reverse(CONNECT_URL), {VERIFY_FIELD: str(tenant.pk)})
        assert denied.status_code == 403
        master.refresh_from_db()
        assert master.invite_status == _PENDING

        # Парная положительная: суперпользователю кнопка работает.
        root = django_user_model.objects.create_superuser(username="drf1553-root")
        client.force_login(root)
        allowed = client.post(reverse(CONNECT_URL), {VERIFY_FIELD: str(tenant.pk)}, follow=True)
        assert allowed.status_code == 200
        master.refresh_from_db()
        assert master.invite_status == _ACCEPTED


def _journal_of(master: CatalogMaster) -> list[tuple[int, int, str]]:
    """Сравнимая форма следа: автор, тип действия, текст.

    Без ``object_id`` и времени — они у двух салонов разные по
    определению, и сравнивать их значило бы требовать не паритета, а
    совпадения.
    """
    return [
        (entry.user_id, entry.action_flag, entry.get_change_message())
        for entry in LogEntry.objects.filter(object_id=str(master.pk)).order_by("action_time")
    ]


def _audit_of(master: CatalogMaster) -> list[tuple[object, str, str, str]]:
    """Развёрнутый след: тенант, действие, цель, текст.

    Тенант в сравнении участвует намеренно — именно он расходился, пока
    верификация шла под ``tenant_scope``.
    """
    return [
        (row.tenant_id, row.action, row.target, row.payload.get("change_message", ""))
        for row in AuditLog.all_tenants.filter(payload__object_id=str(master.pk)).order_by(
            "created_at"
        )
    ]


def _change_master_permission():
    from django.contrib.auth.models import Permission

    return Permission.objects.get(
        content_type__app_label="catalog", codename="change_catalogmaster"
    )
