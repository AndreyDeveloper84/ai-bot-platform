"""DRF-1496 — админка мастеров: список, верификация, архив.

Что здесь проверяется, по требованиям задачи:

* смена состояния приглашения меняет бронируемость ровно так, как
  говорит ``_MasterManager.bookable()`` — и умолчание для новых
  записей теперь ``pending``, а не ложное ``accepted``;
* архивация без причины не проходит — вообще, а не с пустой строкой;
* парная положительная стража: активный принявший мастер бронируется
  как раньше (DRF-1411 — каждому отрицанию пара на тех же данных);
* верификация и архив оставляют след с автором — ``LogEntry``, который
  ``apps.adminconsole.journal`` разворачивает в ``AuditLog``.
"""

from __future__ import annotations

import pytest
from django.contrib.admin.models import LogEntry
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.audit.models import AuditLog
from apps.catalog.models import CatalogMaster
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant

CHANGELIST_URL = "admin:catalog_catalogmaster_changelist"

_PENDING = CatalogMaster.InviteStatus.PENDING
_ACCEPTED = CatalogMaster.InviteStatus.ACCEPTED


@pytest.fixture
def salon(db) -> Tenant:  # noqa: ANN001
    return Tenant.objects.create(slug="drf1496-salon", name="Салон DRF-1496")


@pytest.fixture
def owner_client(db) -> Client:  # noqa: ANN001
    user = get_user_model().objects.create_superuser(
        username="drf1496-owner",
        email="owner1496@example.com",
        password="x",  # pragma: allowlist secret
    )
    client = Client()
    client.force_login(user)
    return client


def _master(salon: Tenant, name: str, **kwargs) -> CatalogMaster:
    defaults = {
        "tenant": salon,
        "external_id": None,
        "external_updated_at": timezone.now(),
        "name": name,
        "is_active": True,
    }
    defaults.update(kwargs)
    return CatalogMaster.all_tenants.create(**defaults)


def _is_bookable(master: CatalogMaster) -> bool:
    """Предикат продажи — тем же кодом, что читает витрина."""
    with tenant_scope(master.tenant):
        return CatalogMaster.objects.bookable().filter(pk=master.pk).exists()


def _run_action(client: Client, action: str, master: CatalogMaster, **extra):
    return client.post(
        reverse(CHANGELIST_URL),
        {"action": action, "_selected_action": [str(master.pk)], **extra},
        follow=True,
    )


# --- умолчание и бронируемость -------------------------------------------


@pytest.mark.django_db
def test_new_master_defaults_to_pending_and_is_not_bookable(salon: Tenant) -> None:
    """Мастер без явного состояния рождается ``pending`` и не продаётся."""
    master = _master(salon, "Без Состояния")

    assert master.invite_status == _PENDING

    # Парная положительная стража на тех же данных: тот же запрос к
    # ``bookable()`` умеет находить принятого мастера — значит пустой
    # ответ для pending-мастера не «сломанный запрос», а честное «нет».
    accepted = _master(salon, "Принятая", invite_status=_ACCEPTED)
    assert _is_bookable(accepted)
    assert not _is_bookable(master)


@pytest.mark.django_db
def test_active_accepted_master_stays_bookable_as_before(salon: Tenant) -> None:
    """Парная положительная проверка из задачи: как раньше, так и сейчас."""
    master = _master(salon, "Активная Принятая", invite_status=_ACCEPTED)

    assert _is_bookable(master)

    # И отрицательная половина на той же строке: стоит убрать любое из
    # условий — бронируемость честно пропадает.
    master.invite_status = _PENDING
    master.save(update_fields=["invite_status"])
    assert not _is_bookable(master)


# --- верификация ----------------------------------------------------------


@pytest.mark.django_db
def test_verify_action_changes_bookability_as_bookable_says(
    salon: Tenant, owner_client: Client
) -> None:
    """Смена состояния действием меняет бронируемость ровно по ``bookable()``."""
    master = _master(salon, "Ждёт Проверки", invite_status=_PENDING)
    # Присутствие: тот же предикат находит принятого мастера — значит
    # «не нашёл» ниже это состояние строки, а не сломанный запрос.
    control = _master(salon, "Контрольная", invite_status=_ACCEPTED)
    assert _is_bookable(control)
    assert not _is_bookable(master)  # исходное отрицание, пара ниже

    response = _run_action(owner_client, "verify_masters", master)

    assert response.status_code == 200
    master.refresh_from_db()
    assert master.invite_status == _ACCEPTED
    # Положительная пара: та же строка, тот же предикат — теперь продаётся.
    assert _is_bookable(master)


@pytest.mark.django_db
def test_verify_action_is_journaled_with_author(salon: Tenant, owner_client: Client) -> None:
    """Верификация пишет след: LogEntry и развёрнутый AuditLog с автором."""
    master = _master(salon, "Журналируемая", invite_status=_PENDING)

    _run_action(owner_client, "verify_masters", master)

    entry = LogEntry.objects.filter(object_id=str(master.pk)).order_by("-action_time").first()
    # Присутствие: след действия существует и подписан автором.
    assert entry is not None
    assert entry.user.username == "drf1496-owner"
    assert "Верификация вручную" in entry.get_change_message()

    audit = (
        AuditLog.all_tenants.filter(action="admin.object.updated").order_by("-created_at").first()
    )
    assert audit is not None
    assert audit.payload["actor_username"] == "drf1496-owner"
    assert audit.payload["object_id"] == str(master.pk)


@pytest.mark.django_db
def test_revoke_action_removes_bookability(salon: Tenant, owner_client: Client) -> None:
    """Отзыв приглашения снимает с продажи — и не трогает уже отменённых."""
    master = _master(salon, "Отзываемая", invite_status=_ACCEPTED)
    assert _is_bookable(master)  # присутствие перед отрицанием

    _run_action(owner_client, "revoke_invite_masters", master)

    master.refresh_from_db()
    assert master.invite_status == CatalogMaster.InviteStatus.CANCELLED
    assert not _is_bookable(master)


# --- архив ----------------------------------------------------------------


@pytest.mark.django_db
def test_archive_without_reason_does_not_archive(salon: Tenant, owner_client: Client) -> None:
    """Архивация без причины не проходит — строка остаётся живой."""
    master = _master(salon, "Без Причины", invite_status=_ACCEPTED)
    assert _is_bookable(master)  # присутствие: мастер продаётся до действия

    response = _run_action(owner_client, "archive_masters", master, apply="1", archive_reason="  ")

    master.refresh_from_db()
    assert response.status_code == 200
    assert master.archived_at is None
    assert _is_bookable(master)


@pytest.mark.django_db
def test_archive_with_reason_archives_and_journals(salon: Tenant, owner_client: Client) -> None:
    """Положительная пара: с причиной архив проходит и оставляет след."""
    master = _master(salon, "Уходит В Архив", invite_status=_ACCEPTED)
    assert _is_bookable(master)  # продаётся до — отрицание ниже честно

    response = _run_action(
        owner_client,
        "archive_masters",
        master,
        apply="1",
        archive_reason="ушла из салона 05.09",
    )

    assert response.status_code == 200
    master.refresh_from_db()
    assert master.archived_at is not None
    assert master.archive_reason == "ушла из салона 05.09"
    assert not _is_bookable(master)

    entry = LogEntry.objects.filter(object_id=str(master.pk)).order_by("-action_time").first()
    assert entry is not None
    assert entry.user.username == "drf1496-owner"
    assert "ушла из салона 05.09" in entry.get_change_message()


@pytest.mark.django_db
def test_unarchive_restores_previous_bookability(salon: Tenant, owner_client: Client) -> None:
    """Извлечение из архива возвращает принятого мастера на витрину."""
    master = _master(salon, "Вернувшаяся", invite_status=_ACCEPTED)
    # Присутствие на тех же данных: до архивации мастер продаётся.
    assert _is_bookable(master)
    master.archived_at = timezone.now()
    master.archive_reason = "временно"
    master.save(update_fields=["archived_at", "archive_reason"])
    # Отрицание: та же строка в архиве с продажи снята.
    assert not _is_bookable(master)

    _run_action(owner_client, "unarchive_masters", master)

    master.refresh_from_db()
    assert master.archived_at is None
    assert master.archive_reason == ""
    # Положительная пара: та же строка снова продаётся.
    assert _is_bookable(master)


# --- карточка: чего в ней нет ---------------------------------------------


@pytest.mark.django_db
def test_change_form_shows_no_invite_token_and_no_editable_inputs(
    salon: Tenant, owner_client: Client
) -> None:
    """``invite_token`` не показывается; полей для свободной правки нет."""
    master = _master(salon, "Карточная", invite_status=_ACCEPTED)

    response = owner_client.get(reverse("admin:catalog_catalogmaster_change", args=[master.pk]))

    assert response.status_code == 200
    content = response.content.decode()
    # Присутствие: карточка вообще отрисовалась и показывает мастера.
    assert "Карточная" in content
    assert "invite_status" in content
    # Отрицания поверх показанного: одноразового токена и полей ввода нет.
    assert "invite_token" not in content
    assert 'name="name"' not in content
    assert 'name="archive_reason"' not in content


@pytest.mark.django_db
def test_changelist_explains_why_master_is_not_bookable(
    salon: Tenant, owner_client: Client
) -> None:
    """В списке видно, почему мастер не бронируется, а не только факт."""
    _master(salon, "Ждущая", invite_status=_PENDING)
    _master(salon, "Архивная", invite_status=_ACCEPTED, archived_at=timezone.now())
    _master(salon, "Бронируемая", invite_status=_ACCEPTED)

    response = owner_client.get(reverse(CHANGELIST_URL))

    assert response.status_code == 200
    content = response.content.decode()
    # Присутствие: все трое в списке.
    assert "Ждущая" in content
    assert "Архивная" in content
    assert "Бронируемая" in content
    # Причины рядом с фактом.
    assert "приглашение: Pending invite" in content
    assert "в архиве" in content
