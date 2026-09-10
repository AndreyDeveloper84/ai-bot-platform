"""Регистрация соло-мастера не может отчитаться о готовности, которой нет.

§122 (10.09.2026), два требования владельца:

* «регистрация **не должна ложно завершаться как „готово"**»;
* «соло-мастер не должен оставаться в состоянии, где он зарегистрирован,
  но невидим клиентам **навсегда**» — допустим `SETUP_PENDING`, но не
  вечная невидимость.

Второе требование — про дверь и выход из неё, и его закрывает срез,
которого ещё нет (встречное заведение в Ayla). **Первое — про тип
результата, и оно закрывается здесь, до появления двери.**

### Где была тишина

До этой правки у `create_solo_provider` было два исхода: `created=True`
(посеял) и `created=False` (уже было). Ни один не отвечал на вопрос, ради
которого человек регистрируется, — **увидят ли меня клиенты**. Успех и
полууспех в контракте были неотличимы, а неотличимы здесь значит: честный
вызывающий, проверивший `created`, отчитается «готово» о человеке, которого
не видно.

### Почему свойство, а не поле

Поле можно не заполнить, а незаполненное поле снаружи неотличимо от
отсутствующего. Тогда сторож ловил бы наличие атрибута — а нужно ловить
**невозможность соврать**: чтобы результат сказал `READY`, вызывающему
придётся изменить строку каталога, то есть сделать человека продаваемым
на самом деле.
"""

from __future__ import annotations

import pytest

from apps.catalog.models import CatalogMaster
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
def identity():
    return {"channel": "max", "channel_user_id": "solo-setup-1", "display_name": "Ольга"}


class TestAFreshRegistrationIsNotReady:
    def test_a_seeded_provider_reports_setup_pending(
        self,
        bootstrap_tenant,
        identity,
    ):
        """Посеяли — но не связали, и результат это говорит.

        Это состояние КАЖДОЙ сегодняшней регистрации: канонический ключ
        приезжает только выгрузкой из Ayla, а Ayla про соло-мастера,
        заведённого в боте, не знает. Раньше тот же исход назывался
        `created=True` и читался как «готово».
        """
        result = create_solo_provider(**identity)

        assert result.created is True
        assert result.setup_state is SoloSetupState.SETUP_PENDING
        assert result.is_ready is False

    def test_the_reason_is_named_not_merely_absent(
        self,
        bootstrap_tenant,
        identity,
    ):
        """«Не готов» без причины отправляет человека искать дверь.

        `ayla_unlinked` говорит, чего именно ждать, и — что важнее — чья
        это работа: связывание наше, а не его.
        """
        result = create_solo_provider(**identity)

        assert result.blocked_by == "ayla_unlinked"

    def test_created_alone_says_nothing_about_readiness(
        self,
        bootstrap_tenant,
        identity,
    ):
        """`created` и готовность — РАЗНЫЕ вопросы, и это закреплено.

        Тест существует, чтобы правка, сделавшая `created` синонимом
        готовности, краснела. Сегодня они совпасть не могут по другой
        причине — но совпадение по случаю не есть контракт.
        """
        first = create_solo_provider(**identity)
        second = create_solo_provider(**identity)

        assert first.created is True
        assert second.created is False
        # Разные `created`, одна готовность: она про строку, а не про то,
        # кто её создал.
        assert first.setup_state is second.setup_state


class TestALinkedProviderIsReady:
    """Положительная стража. Без неё всё выше зеленело бы и на коде,
    который отвечает `SETUP_PENDING` всегда, — то есть на состоянии,
    из которого нет выхода, вместо состояния с выходом.

    Это ровно то, что §122 запрещает вторым требованием, и поймать это
    можно только проверкой обратного направления.
    """

    def test_a_master_with_the_canonical_key_reads_ready(
        self,
        bootstrap_tenant,
        identity,
    ):
        import uuid

        result = create_solo_provider(**identity)
        assert result.is_ready is False

        # Ровно то, что сделает встречное заведение в Ayla, когда оно
        # появится: проставит канонический ключ. Больше ничего.
        CatalogMaster.all_tenants.filter(pk=result.master.pk).update(
            ayla_user_id=uuid.uuid4(),
        )
        result.master.refresh_from_db()

        assert result.blocked_by is None
        assert result.setup_state is SoloSetupState.READY
        assert result.is_ready is True


class TestReadinessIsNotSomethingTheCallerCanClaim:
    def test_there_is_no_way_to_set_the_state(
        self,
        bootstrap_tenant,
        identity,
    ):
        """Невозможность соврать, а не наличие поля.

        Результат заморожен, а готовность — свойство без сеттера. У
        вызывающего нет способа объявить человека готовым, не сделав его
        таковым: единственный путь к `READY` лежит через строку каталога.
        """
        result = create_solo_provider(**identity)

        with pytest.raises(AttributeError):
            result.setup_state = SoloSetupState.READY  # type: ignore[misc]
        with pytest.raises(Exception):
            # frozen dataclass: присвоение любому полю запрещено
            result.created = False  # type: ignore[misc]

        assert result.is_ready is False

    def test_the_state_follows_the_row_not_the_result_object(
        self,
        bootstrap_tenant,
        identity,
    ):
        """Готовность пересчитывается по строке при каждом чтении.

        Закреплено намеренно: закешируй мы её в момент создания, ответ
        «не готов» пережил бы связывание и человек остался бы невидимым
        для нас, будучи видимым для клиентов.
        """
        import uuid

        result = create_solo_provider(**identity)
        assert result.setup_state is SoloSetupState.SETUP_PENDING

        result.master.ayla_user_id = uuid.uuid4()

        assert result.setup_state is SoloSetupState.READY


class TestTheStateSpeaksTheSameLanguageAsTheStorefront:
    def test_the_reason_comes_from_the_single_definition(
        self,
        bootstrap_tenant,
        identity,
    ):
        """Причина берётся у `sale_block`, а не у своей копии условий.

        DRF-1506 свёл пять поверхностей к одному определению. Своя копия
        разъехалась бы с витриной молча: человек читался бы готовым здесь
        и непродаваемым там, и спор двух экранов о том, продаётся ли он,
        разрешить было бы нечем.
        """
        from apps.catalog.master_state import sale_block

        result = create_solo_provider(**identity)

        assert result.blocked_by == sale_block(result.master)

    def test_a_new_gate_condition_reaches_this_state_for_free(
        self,
        bootstrap_tenant,
        identity,
    ):
        """Следующее условие продаваемости доедет сюда само.

        Проверяется не будущим, а сегодняшним: архивная строка — другая
        причина того же гейта, и `setup_state` о ней узнаёт, ничего не
        зная про архив.
        """
        from django.utils import timezone

        result = create_solo_provider(**identity)
        CatalogMaster.all_tenants.filter(pk=result.master.pk).update(
            archived_at=timezone.now(),
        )
        result.master.refresh_from_db()

        assert result.blocked_by == "revoked"
        assert result.setup_state is SoloSetupState.SETUP_PENDING
