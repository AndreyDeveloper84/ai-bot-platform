"""Готовность мастера в карточке: попунктно, без совокупного «готов».

Повод — ловушка, названная замером: пункт ``location`` отвечает
``unavailable`` / ``capability_not_built`` ВСЕГДА (возможности нет в
продукте). Значит совокупное ``ready`` истинно ИЗ-ЗА отсутствующей
возможности, и одно слово «готов» на экране оператора врало бы
**устойчиво**, а не изредка.

Отсюда три предмета.

**A. Блок отрисовывается вообще.** Узел существует потому, что поле
собирает ``build_readiness`` и склеивает разметку: пропущенный импорт или
несуществующее имя дают ``NameError`` только в момент отрисовки — ни
``ast.parse``, ни ``manage check`` его не видят. Проверено на себе: оба
``format_html`` в этом файле отсутствовали, и поймал их запуск, а не
чтение.

**B. Словарь состояний полон.** Ключи ``_READINESS_BADGES`` обязаны
покрывать ``ItemState`` целиком, иначе оператор увидит машинное имя
вместо слова. Образец — ``_BOOKABLE_NOTES`` у соседней колонки, чья
полнота держится тем же способом.

**C. Совокупного «готов» на экране нет.** Присутствие проверяется первым:
утверждение об отсутствии зелено и на пустой странице.
"""

from __future__ import annotations

import secrets
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.catalog.admin import CatalogMasterAdmin
from apps.catalog.models import CatalogMaster, CatalogService, MasterService
from apps.master_api.services.onboarding_readiness import ItemState
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db


@pytest.fixture
def salon() -> Tenant:
    return Tenant.objects.create(slug="ready-salon", name="Салон готовности")


@pytest.fixture
def master(salon: Tenant) -> CatalogMaster:
    # Состав взят у соседей по каталогу, а не подобран по одной ошибке за
    # прогон: модель требует ровно три поля (``tenant``,
    # ``external_updated_at``, ``name``), и четыре существующих теста
    # заводят мастера одинаково. ``external_id`` — ЧИСЛО: строка «ready-1»
    # роняла фикстуру до карточки, и красное было моим, а не предмета.
    return CatalogMaster.all_tenants.create(
        tenant=salon,
        external_id=90001,
        external_updated_at=timezone.now(),
        name="Анна",
    )


@pytest.fixture
def superuser_client() -> Client:
    password = secrets.token_urlsafe(24)
    get_user_model().objects.create_superuser(username="ops.readiness", email="", password=password)
    client = Client()
    assert client.login(username="ops.readiness", password=password)
    return client


def _card(client: Client, master: CatalogMaster) -> str:
    url = reverse("admin:catalog_catalogmaster_change", args=[master.pk])
    return client.get(url).content.decode("utf-8")


class TestTheBlockRenders:
    def test_the_card_opens_and_shows_the_readiness_section(
        self, superuser_client: Client, master: CatalogMaster
    ) -> None:
        """Отрисовка — единственный способ поймать несуществующее имя."""
        body = _card(superuser_client, master)

        assert "Готовность к работе" in body
        # Пункты названы словами оператора, а не ключами проекции.
        for label in ("Услуги", "Места работы", "Расписание", "Профиль"):
            assert label in body, f"пункт не назван: {label}"

    def test_the_unasked_item_is_not_called_unconfigured(
        self, superuser_client: Client, master: CatalogMaster
    ) -> None:
        """Та самая ловушка: «не спрошено» — не «не настроено».

        Слить их значит отправить оператора чинить то, чего он не видел:
        место работы каталог отдаёт только самому мастеру (DRF-2370), у
        оператора субъекта нет. До правки узел держался на тексте описания
        секции («возможности ещё нет»), а не на бейдже пункта, и был зелен
        ни о чём.
        """
        body = _card(superuser_client, master)

        # ПРИСУТСТВИЕ: блок на месте, пункт в нём есть и назван «не спрошено».
        assert "Места работы: " in body
        location_badge = body.split("Места работы: ", 1)[1].split("</li>", 1)[0]
        assert "не удалось спросить (no_subject)" in location_badge
        # ОТСУТСТВИЕ: «не настроено» у этого пункта нет.
        assert "не настроено" not in location_badge

    def test_a_solo_masters_services_are_not_asked_rather_than_unconfigured(
        self, superuser_client: Client, master: CatalogMaster
    ) -> None:
        """Живой прогон 09.11.2026: у готового соло-мастера карточка говорила «не настроено».

        Зеркало цен соло не видит (они на предложении каталога), а выбор
        услуг каталог отдаёт только самому мастеру — у оператора субъекта нет.
        Ответ тот же, что у места работы: «не удалось спросить (no_subject)».
        """
        service = CatalogService.all_tenants.create(
            tenant=master.tenant,
            external_id=90101,
            external_updated_at=timezone.now(),
            slug="solo-card-service",
            name="Массаж спины",
            is_active=True,
        )
        MasterService.all_tenants.create(tenant=master.tenant, master=master, service=service)

        with patch(
            "apps.master_api.services.onboarding_readiness.workspace_kind", return_value="solo"
        ):
            body = _card(superuser_client, master)

        # ПРИСУТСТВИЕ впереди: пункт на месте, и его бейдж — «не спрошено».
        assert "Услуги: " in body
        services_badge = body.split("Услуги: ", 1)[1].split("</li>", 1)[0]
        assert "не удалось спросить (no_subject)" in services_badge
        # ОТСУТСТВИЕ: прежнего «не настроено» у этого пункта нет. Смотрим на
        # бейдж пункта, а не на всю страницу: у соседних пунктов это слово
        # законно.
        assert "не настроено" not in services_badge


class TestTheVocabularyIsComplete:
    def test_every_item_state_has_a_word(self) -> None:
        """Забытый ключ печатал бы оператору машинное имя."""
        declared = set(ItemState.__args__)  # type: ignore[attr-defined]

        # ПРИСУТСТВИЕ: словарь непуст — иначе разность множеств была бы
        # пустой по той же причине и ничего не сказала бы.
        assert CatalogMasterAdmin._READINESS_BADGES
        assert declared - set(CatalogMasterAdmin._READINESS_BADGES) == set()

    def test_every_identity_state_has_a_word(self) -> None:
        """Состояния связи — из ``identity_facts``: три плюс отказ."""
        expected = {"linked", "unlinked", "pending", "rejected"}
        assert CatalogMasterAdmin._IDENTITY_BADGES
        assert expected - set(CatalogMasterAdmin._IDENTITY_BADGES) == set()


class TestNoAggregateVerdict:
    def test_the_card_never_says_simply_ready(
        self, superuser_client: Client, master: CatalogMaster
    ) -> None:
        """Совокупное «готов» истинно из-за несуществующей возможности."""
        body = _card(superuser_client, master)

        # ПРИСУТСТВИЕ впереди: блок отрисован.
        assert "Готовность к работе" in body
        # ОТСУТСТВИЕ: одного слова-вердикта на экране нет.
        assert "Готов к работе" not in body
