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

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.catalog.admin import CatalogMasterAdmin
from apps.catalog.models import CatalogMaster
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

    def test_the_unavailable_item_is_not_called_unconfigured(
        self, superuser_client: Client, master: CatalogMaster
    ) -> None:
        """Та самая ловушка: «возможности нет» — не «не настроено».

        Слить их значит отправить оператора чинить то, что не чинится:
        мест работы мастеру некуда сохранить, и он ничего не мог сделать.
        """
        body = _card(superuser_client, master)

        # ПРИСУТСТВИЕ: блок на месте и пункт в нём есть.
        assert "Места работы" in body
        assert "возможности ещё нет" in body


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
