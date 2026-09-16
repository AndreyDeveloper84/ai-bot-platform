"""``resolve_or_create_bot_user(timezone=...)`` — ставит при создании, и только.

Параметр появился, чтобы свести «найти-или-создать человека» к одному пути:
ленивая регистрация Mini App ставила тенантский пояс своим `get_or_create`, и
это делало одну операцию двумя с разными правилами.

Отдельный файл, а не класс в `test_resolver.py`, по одной причине: там семь
классов про поведение, которое было ДО, и дописывать в них восьмой значило бы
смешать «что резолвер обещал всегда» с «что он умеет с сегодняшнего дня».
Форма утверждений взята оттуда же — `tenant_scope` + `STRICT_TENANT_SCOPE`.

Пояс здесь везде НЕ Europe/Moscow: читатели пишут `bot_user.timezone or
"Europe/Moscow"`, поэтому на московском «поставили» и «не поставили» дают один
наблюдаемый ответ, и тест прошёл бы на пустом поле.
"""

from __future__ import annotations

import pytest

from apps.identity.models import BotUser
from apps.identity.services import resolve_or_create_bot_user
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

TZ = "Asia/Yekaterinburg"
OTHER_TZ = "Europe/Kaliningrad"


@pytest.fixture
def tenant_a() -> Tenant:
    return Tenant.objects.create(slug="tz-a", name="A")


class TestResolverTimezoneOnCreate:
    def test_stamped_when_passed(self, tenant_a, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        with tenant_scope(tenant_a):
            user = resolve_or_create_bot_user(channel="max", channel_user_id="tz-100", timezone=TZ)
        user.refresh_from_db()
        assert user.timezone == TZ

    def test_blank_when_not_passed(self, tenant_a, settings):
        """Семь прочих вызывающих ничего не передают — их поведение то же."""

        settings.STRICT_TENANT_SCOPE = "strict"
        with tenant_scope(tenant_a):
            user = resolve_or_create_bot_user(channel="max", channel_user_id="tz-200")
        user.refresh_from_db()
        assert user.timezone == ""


class TestResolverTimezoneIsNotEnrichment:
    def test_never_fills_a_blank_on_an_existing_row(self, tenant_a, settings):
        """В отличие от display_name/phone/chat_id, пустой пояс НЕ дозаполняется.

        Пустое — объявленное состояние («Пусто означает „не задано“»), и
        читатели дают ему Europe/Moscow. Дозаполнить его задним числом значит
        сдвинуть человеку день, ничего у него не спросив.
        """

        settings.STRICT_TENANT_SCOPE = "strict"
        with tenant_scope(tenant_a):
            resolve_or_create_bot_user(channel="max", channel_user_id="tz-300")
            user = resolve_or_create_bot_user(channel="max", channel_user_id="tz-300", timezone=TZ)
        user.refresh_from_db()
        assert user.timezone == ""

    def test_never_overwrites_a_different_zone(self, tenant_a, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        with tenant_scope(tenant_a):
            resolve_or_create_bot_user(channel="max", channel_user_id="tz-400", timezone=TZ)
            user = resolve_or_create_bot_user(
                channel="max", channel_user_id="tz-400", timezone=OTHER_TZ
            )
        user.refresh_from_db()
        assert user.timezone == TZ


class TestResolverTimezoneLeavesTheRestAlone:
    def test_the_other_three_fields_still_enrich(self, tenant_a, settings):
        """Положительный контроль: обогащение не сломано добавлением параметра.

        Без него «пояс не дозаполняется» было бы неотличимо от «обогащение
        перестало работать вообще».
        """

        settings.STRICT_TENANT_SCOPE = "strict"
        with tenant_scope(tenant_a):
            resolve_or_create_bot_user(channel="max", channel_user_id="tz-500")
            user = resolve_or_create_bot_user(
                channel="max",
                channel_user_id="tz-500",
                display_name="Анна",
                phone="+79991112233",
                chat_id="chat-9",
                timezone=TZ,
            )
        user.refresh_from_db()
        assert user.display_name == "Анна"
        assert user.phone == "+79991112233"
        assert user.chat_id == "chat-9"
        assert user.timezone == ""

    def test_one_row_per_person_not_two(self, tenant_a, settings):
        """Параметр не участвует в поиске — иначе разный пояс дал бы вторую строку."""

        settings.STRICT_TENANT_SCOPE = "strict"
        with tenant_scope(tenant_a):
            resolve_or_create_bot_user(channel="max", channel_user_id="tz-600", timezone=TZ)
            resolve_or_create_bot_user(channel="max", channel_user_id="tz-600", timezone=OTHER_TZ)
        assert BotUser.all_tenants.filter(tenant=tenant_a, channel_user_id="tz-600").count() == 1
