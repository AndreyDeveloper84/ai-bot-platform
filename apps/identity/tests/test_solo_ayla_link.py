"""Подставной ключ не попадает в строку ни одним путём (§122).

Требование сформулировано так, что проверяться должно ПОДМЕНОЙ:
заставить источник вернуть ``is_proxy: true`` и убедиться, что ключ **не
записан**, а состояние осталось `SETUP_PENDING`. Не «проверка
сработала» — после подмены предмета нет.

Почему это не край, а обычный случай: ``resolve_external_user`` заводит
прокси **лениво, на первом же обращении**. Ответ «вот ключ,
is_proxy=true» получит бот для любого человека, чья MAX-идентичность ещё
не связана с настоящим аккаунтом, — то есть для каждого соло-мастера до
подтверждения владения.
"""

from __future__ import annotations

import uuid

import pytest

from apps.identity.services.solo_ayla_link import (
    SoloLinkRefused,
    link_solo_provider_to_ayla,
)
from apps.identity.services.solo_onboarding import (
    BOOTSTRAP_TENANT_SLUG,
    SoloSetupState,
    create_solo_provider,
)
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db


@pytest.fixture
def bootstrap_tenant(db):
    return Tenant.objects.create(
        slug=BOOTSTRAP_TENANT_SLUG,
        name="Ayla Solo — Registration",
        is_active=True,
    )


@pytest.fixture
def result(bootstrap_tenant):
    return create_solo_provider(channel="max", channel_user_id="solo-link-1", display_name="Ольга")


class TestAProxyKeyIsNeverWritten:
    def test_a_proxy_answer_is_refused(self, result):
        with pytest.raises(SoloLinkRefused) as exc:
            link_solo_provider_to_ayla(result.master, ayla_user_id=uuid.uuid4(), is_proxy=True)

        assert exc.value.reason == SoloLinkRefused.PROXY

    def test_after_the_refusal_the_row_is_untouched(self, result):
        """ПОСЛЕ ПОДМЕНЫ ПРЕДМЕТА НЕТ — не «отказ случился».

        Отказ, оставивший ключ записанным, был бы хуже отсутствия
        отказа: строка выглядела бы связанной, а совпадения по этому
        ключу не будет никогда.
        """
        link_solo_provider_to_ayla_refused = uuid.uuid4()
        with pytest.raises(SoloLinkRefused):
            link_solo_provider_to_ayla(
                result.master,
                ayla_user_id=link_solo_provider_to_ayla_refused,
                is_proxy=True,
            )

        result.master.refresh_from_db()
        assert result.master.ayla_user_id is None

    def test_the_state_stays_setup_pending_after_a_proxy_answer(self, result):
        """Второе требование §122, проверенное на том же пути.

        Регистрация не завершается как «готово», пока ключа нет. Держит
        это `setup_state` без сеттера — тест проверяет, что НОВЫЙ путь
        не научился его обходить.
        """
        with pytest.raises(SoloLinkRefused):
            link_solo_provider_to_ayla(result.master, ayla_user_id=uuid.uuid4(), is_proxy=True)

        result.master.refresh_from_db()
        assert result.setup_state is SoloSetupState.SETUP_PENDING
        assert result.blocked_by == "ayla_unlinked"

    def test_a_missing_key_is_refused_with_its_own_name(self, result):
        """Пустой ключ и подставной — разные события.

        Слитые в одно имя, они не сказали бы оператору, ждать ли
        подтверждения владения или чинить вызов.
        """
        with pytest.raises(SoloLinkRefused) as exc:
            link_solo_provider_to_ayla(result.master, ayla_user_id=None, is_proxy=False)

        assert exc.value.reason == SoloLinkRefused.MISSING


class TestARealKeyIsWritten:
    """Положительная стража: дверь закрывает НЕ всё.

    Без неё все тесты отказа зеленели бы и на функции, которая
    отказывает всегда, — то есть на связывании, невозможном в принципе,
    вместо связывания, запрещённого для прокси.
    """

    def test_a_real_answer_lands_in_the_row(self, result):
        key = uuid.uuid4()

        written = link_solo_provider_to_ayla(result.master, ayla_user_id=key, is_proxy=False)

        assert written is True
        result.master.refresh_from_db()
        assert result.master.ayla_user_id == key

    def test_and_the_provider_becomes_ready(self, result):
        """Выход из `SETUP_PENDING` существует — это и есть условие §122.

        Владелец разрешил состояние при условии, что оно кончается.
        Тест закрепляет конец, а не только начало.
        """
        link_solo_provider_to_ayla(result.master, ayla_user_id=uuid.uuid4(), is_proxy=False)
        result.master.refresh_from_db()

        assert result.blocked_by is None
        assert result.setup_state is SoloSetupState.READY

    def test_a_string_key_is_accepted_too(self, result):
        """Ответ приезжает по HTTP, то есть строкой.

        Отказать строке значило бы отказать боевому вызывающему и
        принимать только тестового.
        """
        key = uuid.uuid4()

        link_solo_provider_to_ayla(result.master, ayla_user_id=str(key), is_proxy=False)

        result.master.refresh_from_db()
        assert result.master.ayla_user_id == key


class TestRepeatsAndCollisionsAreDifferentEvents:
    def test_the_same_key_twice_is_harmless(self, result):
        """Повтор — нормальный сценарий, а не инцидент.

        Связывание пойдёт по пути с повторами: оператор, ретрай, второй
        заход человека. Отказ на идентичном повторе превратил бы
        обычный случай в тревогу.
        """
        key = uuid.uuid4()
        first = link_solo_provider_to_ayla(result.master, ayla_user_id=key, is_proxy=False)
        second = link_solo_provider_to_ayla(result.master, ayla_user_id=key, is_proxy=False)

        assert first is True
        assert second is False
        result.master.refresh_from_db()
        assert result.master.ayla_user_id == key

    def test_a_different_key_is_refused_not_overwritten(self, result):
        """Столкновение двух личностей не разрешается перезаписью.

        Перезапись выбрала бы одну из них не глядя — и сделала бы это
        молча, потому что снаружи строка осталась бы «связанной».
        """
        first_key = uuid.uuid4()
        link_solo_provider_to_ayla(result.master, ayla_user_id=first_key, is_proxy=False)

        with pytest.raises(SoloLinkRefused) as exc:
            link_solo_provider_to_ayla(result.master, ayla_user_id=uuid.uuid4(), is_proxy=False)

        assert exc.value.reason == SoloLinkRefused.CONFLICT
        result.master.refresh_from_db()
        assert result.master.ayla_user_id == first_key


class TestTheWritersOfTheKeyAreCounted:
    def test_the_census_of_catalogmaster_key_writers(self):
        """Кто пишет `CatalogMaster.ayla_user_id` — пересчётом, а не памятью.

        Этот тест уже сработал на своём авторе. Я писал его, считая, что
        писатель ОДИН (синхронизация), — потому что грепал в одной папке.
        Тест назвал пять мест, из которых два пишут **другую** колонку
        (`BotUser.ayla_user_id`, у неё своя семантика: для клиента прокси
        и есть его субъект), а один оказался живым вторым писателем
        нужной: путь принятия приглашения.

        Отсюда узкая область поиска ниже: колонка `CatalogMaster`, а не
        любое поле с этим именем. Одинаковое имя на двух моделях — самая
        дорогая часть этой истории.
        """
        import pathlib
        import re

        root = pathlib.Path(__file__).resolve().parents[3]
        writers = set()
        for path in root.joinpath("apps").rglob("*.py"):
            posix = path.as_posix()
            if "/tests/" in posix or "/migrations/" in posix:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            if re.search(r"\bmaster\.ayla_user_id\s*=(?!=)", text) or re.search(
                r'"ayla_user_id":\s*dto\.', text
            ):
                writers.add(path.relative_to(root).as_posix())

        assert writers == {
            # Синхронизация каталога — ключ приезжает из выгрузки Ayla.
            "apps/catalog/services/upserter.py",
            # Принятие приглашения — blank-fill из `bot_user.ayla_user_id`.
            "apps/master_api/views.py",
            # Соло-путь: Ayla о таком мастере не знает и не узнает.
            "apps/identity/services/solo_ayla_link.py",
        }, writers
