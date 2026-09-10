"""Админская поверхность салона: мёртвая дверь, настоящая причина, шаг верификации.

Повод — боевой: 09.09.2026 в 05:57 владелец завёл через админку салон
``testovuy-salin``, который никогда не свяжется с Ayla. Замер того же
дня: ``last_catalog_sync_at = None``, такого UUID в Ayla нет.
Синхронизация не пыталась ни разу и не попытается — потому что строка
родилась со случайным первичным ключом, а первичный ключ потом не
поменять.

Три среза, каждый со своим доказательством.

**A. Второй двери нет.** Рядом со здоровым экраном «Подключить салон»
стояла штатная форма Django «Add Tenant». Принять Ayla-UUID она
физически не может: ``Tenant.id`` объявлен ``editable=False``, значит
в форму поле не попадает и ключ подставляется случайный. Дверь ведёт
только в мёртвый салон, поэтому её нет ни в списке, ни за адресом, ни
зелёным плюсом рядом с каждым выбором салона в чужих админках.

**B. Причина названа настоящая.** «Синхронизация ни разу не проходила»
читается как «запустите синхронизацию». Для мёртвого UUID правда
другая: она вернёт ноль ВСЕГДА. Отдельный код причины в закрытом
перечне — и колонка связи в списке, где мёртвый салон сегодня
неотличим от живых.

**C. Шаг верификации виден всегда.** Кнопка жила только на экране
подключения, то есть ровно один раз — сразу после нажатия. У уже
подключённого салона её не было вовсе.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from django.contrib.admin.sites import site
from django.contrib.admin.widgets import RelatedFieldWidgetWrapper
from django.test import Client, RequestFactory
from django.urls import reverse
from django.utils import timezone

from apps.catalog.models import CatalogMaster, CatalogService
from apps.scheduling.models import WorkingHours
from apps.tenancy.models import Tenant
from apps.tenancy.onboarding import REASON_LABELS, REASON_NEVER_SYNCED, assess_salon

# ``REASON_ID_NOT_IN_AYLA`` ввозится внутри тестов среза B, а не здесь.
# Модульный ввоз имени, которого на сегодняшнем коде нет, обрушил бы
# СБОР всего файла — и срезы A и C покраснели бы ошибкой ввоза вместо
# своих собственных утверждений. Красный обязан называть свой предмет.

pytestmark = pytest.mark.django_db

_PENDING = CatalogMaster.InviteStatus.PENDING
_ACCEPTED = CatalogMaster.InviteStatus.ACCEPTED

CHANGELIST_URL = "admin:tenancy_tenant_changelist"
ADD_URL = "admin:tenancy_tenant_add"
CHANGE_URL = "admin:tenancy_tenant_change"
VERIFY_FIELD = "verify_masters_of"


# ---------------------------------------------------------------------------
# Общая утварь
# ---------------------------------------------------------------------------


class _FakeAyla:
    """Ответ Ayla по идентификатору — ровно то, что читает ``probe_backend``.

    Контекстный менеджер, потому что таков контракт настоящего клиента
    (``with http_client as http``). Ничего не изображает сверх двух
    выборок: услуги и мастера — их и спрашивает проверка.
    """

    def __init__(self, *, services: int = 0, specialists: int = 0, boom: bool = False) -> None:
        self._services = services
        self._specialists = specialists
        self._boom = boom
        self.asked: list[str] = []

    def __enter__(self) -> _FakeAyla:
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def fetch_salon_services(self, *, tenant_id: str) -> list[dict[str, Any]]:
        self.asked.append(tenant_id)
        if self._boom:
            raise RuntimeError("Ayla недоступна")
        return [{"id": i} for i in range(self._services)]

    def fetch_specialists(self, *, tenant_id: str) -> list[dict[str, Any]]:
        if self._boom:
            raise RuntimeError("Ayla недоступна")
        return [{"id": i} for i in range(self._specialists)]


@pytest.fixture
def owner_client(django_user_model) -> Client:
    user = django_user_model.objects.create_superuser(
        username="vladelets-kontura",
        email="vladelets@example.com",
        password="x",  # pragma: allowlist secret
    )
    client = Client()
    client.force_login(user)
    return client


def _never_synced_salon(slug: str = "testovuy-salin") -> Tenant:
    """Форма боевого мёртвого салона: строка есть, синхронизации не было.

    Город задан намеренно — иначе причиной была бы его нехватка, и тест
    проверял бы не то.
    """
    return Tenant.objects.create(slug=slug, name="Тестовый салон", city="Пенза")


def _synced_salon(slug: str = "zhivoy") -> Tenant:
    tenant = Tenant.objects.create(slug=slug, name="Живой салон", city="Пенза")
    tenant.last_catalog_sync_at = timezone.now()
    tenant.last_catalog_sync_ok_at = timezone.now()
    tenant.save(update_fields=["last_catalog_sync_at", "last_catalog_sync_ok_at"])
    CatalogService.all_tenants.create(
        tenant=tenant,
        external_id=None,
        external_updated_at=timezone.now(),
        slug=f"strizhka-{slug}",
        name="Стрижка",
        is_active=True,
    )
    return tenant


def _master(tenant: Tenant, name: str, **kwargs: Any) -> CatalogMaster:
    defaults: dict[str, Any] = {
        "tenant": tenant,
        "external_id": None,
        "external_updated_at": timezone.now(),
        "name": name,
        "is_active": True,
        "invite_status": _PENDING,
        "ayla_user_id": uuid.uuid4(),
    }
    defaults.update(kwargs)
    return CatalogMaster.all_tenants.create(**defaults)


# ---------------------------------------------------------------------------
# Срез A — второй двери нет
# ---------------------------------------------------------------------------


class TestSecondDoorIsGone:
    def test_changelist_offers_only_the_working_door(self, owner_client: Client) -> None:
        """В списке салонов остаётся один вход — «Подключить салон»."""
        _never_synced_salon()
        page = owner_client.get(reverse(CHANGELIST_URL)).content.decode()

        # Присутствие впереди отрицания и на тех же данных: страница
        # отрисована и здоровую дверь показывает — значит отсутствие
        # второй ниже это решение, а не пустой ответ.
        assert "Подключить салон" in page
        assert 'href="connect/"' in page
        assert reverse(ADD_URL) not in page

    def test_add_form_refuses_and_creates_nothing(self, owner_client: Client) -> None:
        """Адрес формы «Add Tenant» больше не создаёт строку даже вручную."""
        _never_synced_salon(slug="uzhe-est")
        before = Tenant.all_objects.count()
        # Присутствие: строка в таблице есть, значит «столько же» ниже —
        # измеренное равенство, а не пустая база. Точного числа тут не
        # назвать: системный тенант `global_kb` приезжает миграцией.
        assert Tenant.all_objects.filter(slug="uzhe-est").exists()

        response = owner_client.post(
            reverse(ADD_URL),
            {"slug": "vtoraya-dver", "name": "Через вторую дверь", "city": "Пенза"},
        )

        assert response.status_code == 403
        assert Tenant.all_objects.count() == before
        assert not Tenant.all_objects.filter(slug="vtoraya-dver").exists()

    def test_no_green_plus_beside_tenant_selects(self, django_user_model) -> None:
        """Зелёный плюс рядом с выбором салона вёл в ту же мёртвую форму.

        Он рисуется ровно тогда, когда админка салона отвечает «добавлять
        можно». Таких мест в контуре тринадцать — по одному на каждую
        чужую форму с правимым выбором салона, — и закрываются они тем же
        одним переключателем, что кнопка в списке.
        """
        root = django_user_model.objects.create_superuser(username="root-plus")
        request = RequestFactory().get("/")
        request.user = root

        model_admin = site._registry[WorkingHours]  # noqa: SLF001 — admin registry API
        widget = model_admin.get_form(request)().fields["tenant"].widget

        # Присутствие: поле выбора салона на форме есть и обёрнуто тем
        # самым виджетом, который и решает про плюс. Отрицание ниже
        # относится к нему, а не к отсутствующему полю.
        assert isinstance(widget, RelatedFieldWidgetWrapper)
        assert widget.can_add_related is False


# ---------------------------------------------------------------------------
# Срез B — названа настоящая причина
# ---------------------------------------------------------------------------


class TestDeadIdentifierIsNamed:
    def test_code_belongs_to_the_closed_list(self) -> None:
        """Перечень закрыт: причина без подписи показана быть не может."""
        from apps.tenancy.onboarding import REASON_ID_NOT_IN_AYLA

        assert REASON_ID_NOT_IN_AYLA in REASON_LABELS
        assert REASON_LABELS[REASON_ID_NOT_IN_AYLA].strip()

    def test_unknown_uuid_is_named_instead_of_never_synced(self) -> None:
        """По мёртвому UUID Ayla молчит — и причина это называет.

        Пара на тех же данных: живому идентификатору достаётся прежнее
        «ни разу не проходила», мёртвому — новое «не найден в Ayla».
        Разница между двумя ответами и есть предмет среза.
        """
        from apps.tenancy.onboarding import REASON_ID_NOT_IN_AYLA

        alive = _never_synced_salon(slug="est-v-ayla")
        dead = _never_synced_salon(slug="net-v-ayla")

        found = _FakeAyla(services=5, specialists=9)
        alive_seen = assess_salon(alive, http_client=found)
        # Присутствие: проверка действительно ходила и спрашивала про
        # ЭТОТ салон — значит нули у мёртвого ниже это ответ Ayla, а не
        # пропущенный вызов.
        assert found.asked == [str(alive.id)]
        assert REASON_NEVER_SYNCED in alive_seen.reasons
        assert REASON_ID_NOT_IN_AYLA not in alive_seen.reasons

        dead_seen = assess_salon(dead, http_client=_FakeAyla(services=0, specialists=0))
        assert REASON_ID_NOT_IN_AYLA in dead_seen.reasons
        assert REASON_NEVER_SYNCED not in dead_seen.reasons

    def test_without_a_probe_the_source_is_not_accused(self) -> None:
        """Ни клиента, ни ответа — прежняя причина, а не обвинение Ayla.

        Замера нет: сказать «идентификатора нет в Ayla» здесь значило бы
        назвать состояние источника, которого никто не измерял.
        """
        from apps.tenancy.onboarding import REASON_ID_NOT_IN_AYLA

        tenant = _never_synced_salon(slug="bez-zamera")

        no_client = assess_salon(tenant)
        assert REASON_NEVER_SYNCED in no_client.reasons
        assert REASON_ID_NOT_IN_AYLA not in no_client.reasons

        broken = assess_salon(tenant, http_client=_FakeAyla(boom=True))
        assert REASON_NEVER_SYNCED in broken.reasons
        assert REASON_ID_NOT_IN_AYLA not in broken.reasons

    def test_card_prints_the_real_reason(self, owner_client: Client, monkeypatch) -> None:
        """Карточка салона печатает подпись новой причины, а не старую."""
        from apps.tenancy.onboarding import REASON_ID_NOT_IN_AYLA

        dead = _never_synced_salon(slug="karto4ka-mertvogo")
        monkeypatch.setattr(
            "apps.tenancy.admin.TenantAdmin._ayla_probe_client",
            lambda self: _FakeAyla(services=0, specialists=0),
        )

        page = owner_client.get(reverse(CHANGE_URL, args=[dead.id])).content.decode()

        # Присутствие: карточка отрисована и про видимость говорит.
        assert "Клиентам не виден" in page
        assert REASON_LABELS[REASON_ID_NOT_IN_AYLA] in page
        assert REASON_LABELS[REASON_NEVER_SYNCED] not in page

    def test_list_separates_linked_from_dead(self, owner_client: Client) -> None:
        """В списке мёртвый салон отличим от живого — колонкой связи.

        Три формы, а не две: «связан», «пробовал и не смог» и «не
        пробовал ни разу». Последняя и есть боевой мёртвый салон:
        синхронизация к нему не подходила вовсе.
        """
        _synced_salon(slug="svyazan")
        tried = _never_synced_salon(slug="probovala")
        tried.last_catalog_sync_at = timezone.now()
        tried.save(update_fields=["last_catalog_sync_at"])
        _never_synced_salon(slug="ne-probovala")

        page = owner_client.get(reverse(CHANGELIST_URL)).content.decode()

        # Присутствие: все три строки в списке есть — значит различия
        # ниже это разные ответы колонки, а не отфильтрованный список.
        for slug in ("svyazan", "probovala", "ne-probovala"):
            assert slug in page
        assert "Связь с Ayla" in page
        assert "связан" in page
        assert "синхронизация ни разу не подходила" in page
        assert "синхронизация шла, но ни разу не удалась" in page


# ---------------------------------------------------------------------------
# Срез C — шаг верификации виден всегда
# ---------------------------------------------------------------------------


class TestVerifyStepLivesOnTheCard:
    def test_card_of_a_connected_salon_offers_the_button(self, owner_client: Client) -> None:
        """У уже подключённого салона кнопка есть — на его карточке."""
        tenant = _synced_salon(slug="podklyuchen-davno")
        for i in range(9):
            _master(tenant, f"Мастер {i}")

        assessed = assess_salon(tenant)
        # Присутствие: салону действительно есть что верифицировать.
        assert assessed.masters_awaiting_verification == 9
        assert assessed.can_verify_masters is True

        page = owner_client.get(reverse(CHANGE_URL, args=[tenant.id])).content.decode()
        assert "Верифицировать 9 мастеров" in page
        assert VERIFY_FIELD in page

    def test_button_on_the_card_verifies(self, owner_client: Client) -> None:
        """Нажатие с карточки меняет состояние — числом, а не словом."""
        tenant = _synced_salon(slug="knopka-s-kartochki")
        for i in range(9):
            _master(tenant, f"Мастер {i}")
        before = assess_salon(tenant)
        assert before.masters_awaiting_verification == 9
        assert before.bookable_masters == 0
        assert before.is_visible is False

        response = owner_client.post(
            reverse(CHANGE_URL, args=[tenant.id]),
            {VERIFY_FIELD: str(tenant.id)},
            follow=True,
        )
        assert response.status_code == 200

        after = assess_salon(tenant)
        assert after.bookable_masters == before.masters_awaiting_verification
        assert after.masters_awaiting_verification == 0
        assert after.is_visible is True

    def test_card_does_not_promise_what_verification_cannot_do(self, owner_client: Client) -> None:
        """Архив и ``is_active=False`` — кнопки нет: предлагать нечего."""
        tenant = _synced_salon(slug="nechego-predlagat")
        _master(tenant, "В архиве", archived_at=timezone.now())
        _master(tenant, "Неактивная", is_active=False)

        assessed = assess_salon(tenant)
        assert assessed.masters_total == 2
        assert assessed.masters_awaiting_verification == 0

        page = owner_client.get(reverse(CHANGE_URL, args=[tenant.id])).content.decode()
        # Присутствие: карточка отрисована и про мастеров говорит —
        # значит отсутствие кнопки это решение, а не пустая страница.
        assert "Бронируемых мастеров" in page
        assert "Верифицировать" not in page

    def test_card_button_is_superuser_only(self, django_user_model) -> None:
        """Уровень доступа тот же, что у экрана подключения (§27, §51.1)."""
        from django.contrib.auth.models import Permission

        tenant = _synced_salon(slug="prava-na-kartochke")
        master = _master(tenant, "Ждёт")

        staff = django_user_model.objects.create_user(
            username="smotryashchiy-kartochki", is_staff=True, is_superuser=False
        )
        staff.user_permissions.add(
            Permission.objects.get(content_type__app_label="tenancy", codename="change_tenant"),
            Permission.objects.get(content_type__app_label="tenancy", codename="view_tenant"),
            Permission.objects.get(
                content_type__app_label="catalog", codename="change_catalogmaster"
            ),
        )
        client = Client()
        client.force_login(staff)

        seen = client.get(reverse(CHANGE_URL, args=[tenant.id]))
        assert seen.status_code == 200
        page = seen.content.decode()
        # Присутствие: карточка роли открыта и про мастеров говорит —
        # значит отсутствие кнопки ниже это решение, а не пустой ответ.
        # Кода 200 для этого мало: пустое тело — тоже 200.
        assert "Бронируемых мастеров" in page
        assert "Верифицировать" not in page

        denied = client.post(reverse(CHANGE_URL, args=[tenant.id]), {VERIFY_FIELD: str(tenant.id)})
        assert denied.status_code == 403
        master.refresh_from_db()
        assert master.invite_status == _PENDING

        # Парная положительная: владельцу кнопка работает.
        root = django_user_model.objects.create_superuser(username="root-kartochki")
        client.force_login(root)
        allowed = client.post(
            reverse(CHANGE_URL, args=[tenant.id]), {VERIFY_FIELD: str(tenant.id)}, follow=True
        )
        assert allowed.status_code == 200
        master.refresh_from_db()
        assert master.invite_status == _ACCEPTED
