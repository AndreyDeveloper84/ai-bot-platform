"""Заявку на изменение расписания в админке нельзя разрешить руками (DRF-1607).

Разрешение заявки — решение владелицы салона, и оно приходит из
переписки с ботом (Q-M6): её нажатие меняет строку и применяет диф к
расписанию одной транзакцией. Правка ``status`` через форму админки
проходит мимо этого пути молча — ни события, ни следа, который оставил
бы диалог.

Обещание read-only стояло в докстринге ``apps/scheduling/admin.py`` и
держалось только текстом: ``readonly_fields`` содержал два поля из
двадцати. Проверяемый инвариант не живёт в комментарии — здесь сторож.

Три уровня, от слабого к сильному:

* :func:`test_every_column_of_the_request_is_frozen` — счётчик по
  ``_meta``, а не построчный перечень: сумма отдельных «это поле не
  правится» не равна «править нечего»;
* :func:`test_the_card_shows_the_decision_but_offers_no_way_to_type_it`
  — карточка действительно открывается и действительно про ЭТУ заявку,
  и в ней нет ни одного элемента ввода решения;
* :func:`test_the_form_offers_no_field_to_type_a_decision_into` —
  самый сильный: ``ModelAdmin.get_form`` не принимает от оператора ни
  одного поля. До правки принимала четырнадцать, включая ``status``.
"""

from __future__ import annotations

import uuid

import pytest
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.test import Client, RequestFactory
from django.urls import reverse
from django.utils import timezone

from apps.catalog.models import CatalogMaster
from apps.scheduling.models import ScheduleChangeRequest, WorkingHours
from apps.tenancy.models import Tenant

#: Поля, которыми заявка РЕШАЕТСЯ. Перечень нужен не вместо счётчика
#: ниже, а рядом с ним: счётчик ловит «поле разморозили», а этот список
#: называет те четыре, ради которых задача существует.
_DECISION_FIELDS = ("status", "resolution_note", "resolved_at", "resolved_by")


@pytest.fixture
def salon(db) -> Tenant:  # noqa: ANN001
    return Tenant.objects.create(slug="scr-salon", name="Салон «Заявка»", city="Пенза")


@pytest.fixture
def owner(db) -> Client:  # noqa: ANN001
    get_user_model().objects.create_superuser(
        username="scr-owner",
        email="scr@example.com",
        password="x",  # pragma: allowlist secret
    )
    client = Client()
    client.force_login(get_user_model().objects.get(username="scr-owner"))
    return client


@pytest.fixture
def request_row(salon: Tenant) -> ScheduleChangeRequest:
    master = CatalogMaster.all_tenants.create(
        tenant=salon,
        external_id=None,
        external_updated_at=timezone.now(),
        name="Ольга",
        is_active=True,
        ayla_user_id=uuid.uuid4(),
        invite_status=CatalogMaster.InviteStatus.PENDING,
    )
    return ScheduleChangeRequest.all_tenants.create(
        tenant=salon,
        master=master,
        requested_change={"type": "working_hours_change", "day_of_week": 0},
        reason="утренняя йога",
        status=ScheduleChangeRequest.Status.PENDING,
    )


def _model_admin():  # noqa: ANN202
    return admin.site._registry[ScheduleChangeRequest]  # noqa: SLF001


def test_every_column_of_the_request_is_frozen() -> None:
    """Гейту нужен счётчик: «правится ноль полей», а не «это и это нет».

    ``readonly_fields`` собирается из ``_meta.fields``, поэтому проверка
    считает разницу множеств. Построчный перечень зеленел бы и тогда,
    когда у модели появилась НОВАЯ правимая колонка.
    """
    model_admin = _model_admin()
    all_fields = {f.name for f in ScheduleChangeRequest._meta.fields}
    frozen = set(model_admin.readonly_fields)

    # Положительная стража к отрицанию ниже: пустое множество полей
    # прошло бы сравнение с пустым остатком (ноль обязан иметь стражу).
    assert len(all_fields) > 10, "модель внезапно опустела — проверка меряет не то"

    assert not all_fields - frozen, f"эти поля правятся руками: {sorted(all_fields - frozen)}"
    for name in _DECISION_FIELDS:
        assert name in frozen, f"{name} — поле решения, оно обязано быть только на чтение"


def test_the_screen_neither_adds_nor_deletes() -> None:
    """Заявку заводит мастер, а стирать след решения нельзя вовсе."""
    model_admin = _model_admin()
    assert model_admin.has_add_permission(None) is False
    assert model_admin.has_delete_permission(None) is False


def test_the_form_was_not_made_more_inviting() -> None:
    """Запрет не тащит за собой удобство: ``fieldsets`` по-прежнему нет.

    Понятный раздел «Решение» с выпадающим списком состояний приглашает
    нажать там, где нажимать нельзя. Сначала запрет — удобство отдельным
    решением. Это же держит ``tests/test_admin_humanize.py``.
    """
    assert getattr(_model_admin(), "fieldsets", None) is None


@pytest.mark.django_db
def test_the_card_shows_the_decision_but_offers_no_way_to_type_it(
    owner: Client, request_row: ScheduleChangeRequest, settings
) -> None:  # noqa: ANN001
    """Состояние ВИДНО, но не правимо — как у карточки визита (DRF-1498).

    ``STRICT_TENANT_SCOPE`` выставлен в ``audit`` — режим прода и
    стейджа (``config/settings/base.py``): у админского запроса нет
    тенанта в контексте, и это свойство админки вообще, а не предмет
    этой задачи (та же оговорка, что в
    ``tests/test_admin_humanize.py::test_every_touched_admin_screen_renders``).
    """
    settings.STRICT_TENANT_SCOPE = "audit"
    url = reverse("admin:scheduling_schedulechangerequest_change", args=[request_row.pk])
    response = owner.get(url)

    assert response.status_code == 200
    body = response.content.decode()

    # Положительная пара к отрицаниям: карточка нарисована и про ЭТУ
    # заявку. Без неё «поля нет» значило бы всего лишь «страницы нет».
    assert "утренняя йога" in body
    assert "Состояние заявки" in body, "состояние обязано быть ВИДНО — просто не правимо"

    for name in _DECISION_FIELDS:
        assert f'name="{name}"' not in body, f"в карточке есть поле ввода {name}"


@pytest.mark.django_db
def test_the_form_offers_no_field_to_type_a_decision_into(
    request_row: ScheduleChangeRequest,
) -> None:
    """То, что форма ВООБЩЕ принимает от оператора, — пустой набор.

    Проверяется не разметка, а ``ModelAdmin.get_form``: именно её
    ``base_fields`` решают, чьё значение из POST будет записано. До
    правки там лежали ``status``, ``resolution_note``, ``resolved_at`` и
    ещё одиннадцать полей.

    POST-ом это доказать не вышло, и причина названа, а не обойдена: у
    админского запроса нет тенанта в контексте, поэтому ``master``
    приходит с пустым queryset и форма краснеет «выберите корректный
    вариант» ЕЩЁ ДО того, как дело дойдёт до ``status``. Тест на POST
    зеленел бы и на сломанном коде — то есть доказывал бы чужое
    свойство. Отдельная находка про пустые перечни в админке уже
    записана в ``tests/test_admin_humanize.py``.

    Положительная стража — рабочие часы: у их формы поля есть. Без неё
    «полей нет» могло бы означать «``get_form`` вернула пустое всем».
    """
    factory = RequestFactory()
    http_request = factory.get("/")
    http_request.user = get_user_model()(is_superuser=True, is_staff=True)

    frozen_form = _model_admin().get_form(http_request, obj=request_row)
    living_form = admin.site._registry[WorkingHours].get_form(http_request, obj=None)  # noqa: SLF001

    assert living_form.base_fields, "форма рабочих часов пуста — стража меряет не то"
    assert not frozen_form.base_fields, "форма заявки принимает поля: " + ", ".join(
        sorted(frozen_form.base_fields)
    )
