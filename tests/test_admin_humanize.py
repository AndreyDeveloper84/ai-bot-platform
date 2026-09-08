"""Админка говорит по-человечески — и не врёт про отсутствие.

Это targeted proof задачи «сделать названия полей и стили человеческими».
Каждый тест здесь краснеет на состоянии `origin/dev` до правки и зеленеет
после — предмет у каждого назван в докстринге.

Отдельно и намеренно проверяется то, чего делать БЫЛО НЕЛЬЗЯ:

* карточка визита по-прежнему не отдаёт правимое поле статуса
  (:func:`test_booking_change_form_still_refuses_editable_status`) —
  красивая группировка полей не должна была превратиться в приглашение
  закрыть визит мимо машины состояний;
* бейдж состояния обязан печатать И человеческую подпись, И машинное имя
  (:func:`test_badges_show_both_human_label_and_machine_code`): без кода
  оператор нем — в задачах и логах состояние зовут строкой ``pending``;
* отсутствие значения называется словами, а не подставляется нулём или
  прочерком (:func:`test_absence_is_named_not_substituted`,
  OPEN_DECISIONS §65).

Самый ценный тест здесь — :func:`test_every_touched_admin_screen_renders`.
Группировка полей в ``fieldsets`` ломается не на импорте и не на
``manage.py check``, а на рендере: поле, которое нельзя показать, роняет
страницу FieldError'ом уже у оператора. Поэтому экраны действительно
открываются.
"""

from __future__ import annotations

import uuid
from datetime import time, timedelta

import pytest
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.booking.models import BookingReminder, BookingRequest
from apps.catalog.models import CatalogMaster, CatalogService, MasterService
from apps.catalog.provenance import MasterServiceSource, master_service_write
from apps.identity.models import BotUser
from apps.scheduling.models import (
    ScheduleChangeRequest,
    ScheduleException,
    SlotConfig,
    TimeBlock,
    WorkingHours,
)
from apps.tenancy.models import Tenant


@pytest.fixture
def salon(db) -> Tenant:  # noqa: ANN001
    return Tenant.objects.create(slug="humanize-salon", name="Салон «Человеческий»", city="Пенза")


@pytest.fixture
def owner(db) -> Client:  # noqa: ANN001
    user = get_user_model().objects.create_superuser(
        username="humanize-owner",
        email="humanize@example.com",
        password="x",  # pragma: allowlist secret
    )
    client = Client()
    client.force_login(user)
    return client


@pytest.fixture
def master(salon: Tenant) -> CatalogMaster:
    return CatalogMaster.all_tenants.create(
        tenant=salon,
        external_id=None,
        external_updated_at=timezone.now(),
        name="Ольга",
        is_active=True,
        ayla_user_id=uuid.uuid4(),
        invite_status=CatalogMaster.InviteStatus.PENDING,
    )


@pytest.fixture
def service(salon: Tenant) -> CatalogService:
    return CatalogService.all_tenants.create(
        tenant=salon,
        external_id=None,
        external_updated_at=timezone.now(),
        slug="strizhka",
        name="Стрижка",
        is_active=True,
    )


@pytest.fixture
def client_account(salon: Tenant) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=salon, channel="max", channel_user_id="humanize-1", chat_id="humanize-1"
    )


@pytest.fixture
def booking(salon: Tenant) -> BookingRequest:
    return BookingRequest.all_tenants.create(
        tenant=salon,
        service_name="Стрижка",
        client_name="Марина",
        client_phone="+70000000000",
        visit_at=timezone.now(),
        status=BookingRequest.Status.CONFIRMED,
        source="bot",
    )


# ──────────────────────────────────────────────────────────────────────
# Подписи
# ──────────────────────────────────────────────────────────────────────


def test_site_header_names_the_contour() -> None:
    """До правки шапка была дефолтной «Django administration».

    ``site_header`` не задавался нигде в репозитории, и оператор не
    понимал, в чей контур он вошёл.
    """
    assert admin.site.site_header == "Ayla — админка контура"
    assert admin.site.site_title == "Ayla"
    assert "Салоны" in admin.site.index_title


def test_app_sections_are_russian_and_ordered(owner: Client) -> None:
    """До правки индекс перечислял «Booking», «Catalog», «Scheduling».

    Проверяется и порядок: нужные оператору разделы идут раньше
    служебных, а не по алфавиту английских имён.
    """
    # Импорт внутри теста намеренно: модуль ``theme`` — часть этой самой
    # правки, и на состоянии до неё его нет. Импорт на уровне файла
    # превратил бы «до» в ошибку сбора вместо честного падения проверок.
    from apps.adminconsole.theme import APP_ORDER

    response = owner.get(reverse("admin:index"))
    assert response.status_code == 200
    app_list = response.context["app_list"]
    labels = [app["app_label"] for app in app_list]
    names = {app["app_label"]: app["name"] for app in app_list}

    assert names["tenancy"] == "Салоны"
    assert names["catalog"] == "Каталог салона"
    assert names["booking"] == "Записи клиентов"
    assert names["scheduling"] == "Расписание мастеров"

    # Порядок: салоны раньше каталога, каталог раньше записей.
    assert labels.index("tenancy") < labels.index("catalog") < labels.index("booking")

    # Служебные внутренности не зовут нажимать и стоят после рабочих.
    for service_label in ("eventbus", "replay", "promptreg", "persona", "ingress"):
        if service_label not in names:
            continue
        assert names[service_label].startswith("Служебное")
        assert labels.index(service_label) > labels.index("booking")

    # Ни одно приложение из явного порядка не потерялось по дороге.
    for known in APP_ORDER:
        if known in labels:
            assert labels.index(known) < len(labels)


@pytest.mark.parametrize(
    ("model", "field", "expected"),
    [
        (CatalogMaster, "invite_status", "Состояние приглашения"),
        (CatalogMaster, "name", "Имя мастера"),
        (CatalogService, "is_active", "Продаётся"),
        (Tenant, "city", "Город"),
        (Tenant, "shadow_mode", "Теневой режим: бот не пишет клиенту"),
        (BookingRequest, "visit_at", "Время визита"),
        (BookingRequest, "client_name", "Имя клиента"),
        (WorkingHours, "lunch_start", "Начало перерыва"),
        (MasterService, "source", "Откуда взялась связь"),
    ],
)
def test_field_labels_are_human(model, field, expected) -> None:  # noqa: ANN001
    """До правки Django выводил подписи из имён схемы: «invite status»,
    «shadow mode», «visit at». Теперь у полей есть свои имена."""
    assert model._meta.get_field(field).verbose_name == expected


def test_model_names_are_russian() -> None:
    """До правки разделы назывались «Catalog: masters», «Tenants»."""
    assert str(CatalogMaster._meta.verbose_name_plural) == "Мастера"
    assert str(Tenant._meta.verbose_name_plural) == "Салоны"
    assert str(BookingRequest._meta.verbose_name_plural) == "Записи клиентов"
    assert str(MasterService._meta.verbose_name) == "Связь мастера с услугой"


# ──────────────────────────────────────────────────────────────────────
# Отсутствие и бейджи
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "model",
    [CatalogMaster, CatalogService, Tenant, BookingRequest, BookingReminder, WorkingHours],
)
def test_absence_is_named_not_substituted(model) -> None:  # noqa: ANN001
    """Отсутствие значения нельзя подменять значением (§65).

    Django по умолчанию печатает «-», и в колонке чисел это читается как
    ноль. Экран обязан сказать словами, что данных нет.
    """
    model_admin = admin.site._registry[model]  # noqa: SLF001 — реестр админки
    assert model_admin.empty_value_display == "нет данных"
    assert model_admin.empty_value_display not in {"-", "—", "0", ""}


def test_badges_show_both_human_label_and_machine_code(
    owner: Client, master: CatalogMaster
) -> None:
    """Человеческой подписи мало: без кода оператор не может спросить.

    До правки в колонке стояло голое ``pending``. Стало — «Приглашение
    не принято» И ``pending`` рядом; проверяются ОБА, потому что потеря
    любого из двух это отдельный дефект.
    """
    response = owner.get(reverse("admin:catalog_catalogmaster_changelist"))
    assert response.status_code == 200
    body = response.content.decode()

    assert "Приглашение не принято" in body, "пропала человеческая подпись"
    assert "ayla-badge__code" in body, "пропал слой машинного имени"
    assert ">pending<" in body, "пропало машинное имя состояния"


def test_badge_stylesheet_is_wired_into_the_page(owner: Client, master: CatalogMaster) -> None:
    """Стили подключены штатным ``class Media``, а не инлайном."""
    response = owner.get(reverse("admin:catalog_catalogmaster_changelist"))
    assert "adminconsole/ayla-admin.css" in response.content.decode()


# ──────────────────────────────────────────────────────────────────────
# Запреты, которые правка не имела права ослабить
# ──────────────────────────────────────────────────────────────────────


def test_booking_change_form_still_refuses_editable_status(
    owner: Client, booking: BookingRequest
) -> None:
    """Группировка полей не должна была открыть правку статуса визита.

    Владелец запретил закрывать визиты и менять статусы через админку —
    это обход машины состояний. Красивый выпадающий список статусов
    здесь был бы ХУЖЕ прежнего невнятного экрана: он приглашает нажать.
    """
    url = reverse("admin:booking_bookingrequest_change", args=[booking.pk])
    body = owner.get(url).content.decode()

    # Положительная пара к трём отрицаниям ниже (DRF-1411): карточка
    # действительно нарисована и действительно про ЭТУ запись. Без этой
    # строки «поля нет» означало бы всего лишь «страницы нет».
    assert booking.client_name in body
    assert "Состояние записи" in body, "поле статуса обязано быть ВИДНО — просто не правимо"

    # Отрицательные: ни одного элемента ввода, которым состояние визита
    # можно было бы переставить руками.
    assert 'name="status"' not in body
    assert 'name="completed_at"' not in body
    assert 'name="completed_by"' not in body


def test_schedule_change_request_form_was_not_made_more_inviting() -> None:
    """Находка, а не работа: у заявок статус правится руками и сегодня.

    Докстринг модуля обещал read-only, код обещания не держит. Правка
    оформления НЕ добавляет этому экрану ``fieldsets``: понятный раздел
    «Решение» звал бы нажать там, где решение обязано приходить из
    переписки с владельцем. Тест держит это решение.
    """
    model_admin = admin.site._registry[ScheduleChangeRequest]  # noqa: SLF001
    assert getattr(model_admin, "fieldsets", None) is None
    assert model_admin.readonly_fields == ("requested_change", "created_at")


# ──────────────────────────────────────────────────────────────────────
# Экраны действительно открываются
# ──────────────────────────────────────────────────────────────────────


@pytest.fixture
def one_row_of_everything(salon, master, service, booking, client_account):  # noqa: ANN001
    """По строке в каждую тронутую таблицу — чтобы карточки было что открыть."""
    with master_service_write(MasterServiceSource.DEV_SEED, reason="test fixture"):
        edge = MasterService.all_tenants.create(tenant=salon, master=master, service=service)
    return {
        "tenancy.tenant": salon.pk,
        "catalog.catalogmaster": master.pk,
        "catalog.catalogservice": service.pk,
        "catalog.masterservice": edge.pk,
        "booking.bookingrequest": booking.pk,
        "scheduling.workinghours": WorkingHours.all_tenants.create(
            tenant=salon,
            master=master,
            day_of_week=0,
            is_working=True,
            start_time=time(10, 0),
            end_time=time(19, 0),
        ).pk,
        "scheduling.scheduleexception": ScheduleException.all_tenants.create(
            tenant=salon,
            master=master,
            date=timezone.now().date(),
            type=ScheduleException.Type.VACATION,
        ).pk,
        "scheduling.timeblock": TimeBlock.all_tenants.create(
            tenant=salon,
            master=master,
            start_at=timezone.now(),
            end_at=timezone.now() + timedelta(hours=1),
            reason="перерыв",
        ).pk,
        "scheduling.slotconfig": SlotConfig.all_tenants.create(tenant=salon).pk,
        "booking.bookingreminder": BookingReminder.all_tenants.create(
            tenant=salon,
            bot_user=client_account,
            yclients_record_id="rec-1",
            chat_id="chat-1",
            visit_at=timezone.now(),
            kind=BookingReminder.Kind.DAY_BEFORE,
            status=BookingReminder.Status.PENDING,
            scheduled_at=timezone.now(),
        ).pk,
    }


def test_every_touched_admin_screen_renders(owner: Client, one_row_of_everything, settings) -> None:  # noqa: ANN001
    """Главная страховка правки: список И карточка открываются.

    Сгруппированные ``fieldsets`` не проверяются ни импортом, ни
    ``manage.py check`` — поле, которое нельзя показать, роняет страницу
    ``FieldError``'ом уже у оператора. Поэтому экраны открываются
    по-настоящему, а ответ обязан быть 200.

    ``STRICT_TENANT_SCOPE`` возвращён в ``audit`` — режим прода и
    стейджа (``config/settings/base.py``), тогда как корневая
    ``tests/conftest.py`` держит для всей сюиты ``strict``. Это не
    послабление ради зелени, а условие, при котором тест меряет СВОЙ
    предмет:

    под ``strict`` пять экранов падают ``CrossTenantError`` ещё ДО
    рендера полей, потому что у админского запроса нет тенанта в
    контексте, а ``list_filter``/``raw_id_fields`` по FK на мастера
    ходят через ``objects``. Проверено на ЧИСТОМ ``origin/dev``
    (8c276fa) отдельным зондом: тот же перечень из пяти адресов —
    карточка связи услуги, СПИСОК записей клиентов, карточки рабочих
    часов, исключения и блокировки. Дефект существует до этой правки и
    ею не внесён; в проде (``audit``) он не роняет страницу, а тихо
    отдаёт ПУСТОЙ перечень мастеров в фильтре и в выборе — это отдельная
    находка, переданная главному окну, и чинится она не здесь.
    """
    settings.STRICT_TENANT_SCOPE = "audit"
    broken: list[str] = []
    for dotted, pk in one_row_of_everything.items():
        app_label, model_name = dotted.split(".")
        for url in (
            reverse(f"admin:{app_label}_{model_name}_changelist"),
            reverse(f"admin:{app_label}_{model_name}_change", args=[pk]),
        ):
            try:
                status = owner.get(url).status_code
            except Exception as exc:  # noqa: BLE001 — нам нужен ПЕРЕЧЕНЬ, а не первый
                broken.append(f"{url}: {type(exc).__name__}: {exc}")
                continue
            if status != 200:
                broken.append(f"{url}: HTTP {status}")
    assert not broken, "экраны админки не открылись: " + "; ".join(broken)
