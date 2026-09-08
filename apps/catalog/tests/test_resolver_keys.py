"""Перевод ключей Ayla в ключи зеркала — T6 (DRF-1567), решение владельца §81.

Предмет набора — не «перевод работает», а **что происходит с тем, кого
перевести не удалось**. Именно там живёт дефект, ради которого задача
и заведена: наивный транзит отбросил бы непереводимых, полка вернула бы
пустой подбор, а состояние называлось бы `OK` — молча пустая полка под
именем «всё хорошо».

Сегодня это не видно: подтверждённых связей ноль, полка пуста и так.
Вылезло бы через недели, после разметки каталога, с готовым ложным
объяснением «наверное, опять разметка».
"""

from __future__ import annotations

import uuid

import pytest
from django.utils import timezone

from apps.catalog.models import CatalogMaster
from apps.catalog.resolver_keys import translate_provider_keys
from apps.tenancy.models import Tenant


@pytest.fixture
def tenant(db) -> Tenant:  # noqa: ANN001
    return Tenant.objects.create(slug="keys-tenant", name="Keys tenant")


def _master(tenant: Tenant, *, external_id: int, ayla_user_id=None, **overrides) -> CatalogMaster:
    """Строка зеркала. По умолчанию — продаваемая.

    `invite_status=accepted` и `is_active=True` стоят потому, что
    предикат `AVAILABLE` требует именно их; тест про непродаваемого
    портит РОВНО одно поле, и это видно в его теле.
    """
    fields = dict(
        tenant=tenant,
        external_updated_at=timezone.now(),
        external_id=external_id,
        name=f"Мастер {external_id}",
        specialization="Парикмахер",
        is_active=True,
        invite_status=CatalogMaster.InviteStatus.ACCEPTED,
        ayla_user_id=ayla_user_id or uuid.uuid4(),
    )
    fields.update(overrides)
    return CatalogMaster.objects.create(**fields)


def _decision(*ayla_ids: str) -> dict:
    """Форма решения — та, в которой ключи приезжают сегодня."""
    return {
        "data": {
            "ordered": [
                {
                    "candidate": {"kind": "PROVIDER", "id": ayla_id},
                    "rank": i + 1,
                    "tier": 1,
                    "reason_codes": ["MATCH_SERVICE_EXACT"],
                }
                for i, ayla_id in enumerate(ayla_ids)
            ],
        },
    }


def _ids(payload: dict) -> list[str]:
    return [row["candidate"]["id"] for row in payload["data"]["ordered"]]


# ---------------------------------------------------------------------------
# Перевод
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_sellable_master_gets_the_mirror_key(tenant):
    """Обычная работа: ключ Ayla заменён ключом зеркала."""
    ayla_id = uuid.uuid4()
    master = _master(tenant, external_id=900001, ayla_user_id=ayla_id)

    out, census = translate_provider_keys(_decision(str(ayla_id)))

    assert _ids(out) == [str(master.id)]
    assert (census.translated, census.untranslated) == (1, 0)


@pytest.mark.django_db
def test_unknown_key_travels_as_is_and_is_counted_apart(tenant):
    """Зеркало не знает такого человека — скорее всего отстало.

    Кандидат едет дальше **как есть**: отбросить его здесь значило бы
    лишить полку возможности заметить потерю. Полка умеет назвать это
    состояние сама (`UNRENDERABLE_CANDIDATES`), и второй классификатор
    нам не нужен.
    """
    stranger = str(uuid.uuid4())

    out, census = translate_provider_keys(_decision(stranger))

    assert _ids(out) == [stranger], "кандидат обязан доехать, а не исчезнуть"
    assert (census.no_mirror_row, census.not_for_sale) == (1, 0)


@pytest.mark.django_db
def test_known_but_unsellable_master_is_counted_separately(tenant):
    """Строка есть, продавать нельзя — это ДРУГАЯ болезнь, чем «не знаем».

    Ненулевое значение означает, что пул Ayla и зеркало разошлись в том,
    кто продаётся: Ayla рекомендует того, кого мы продать не можем.
    Сложи мы это с «зеркало отстало», два разных лечения получили бы
    одно число, и чинить пошли бы наугад.

    Портится РОВНО одно поле — архивация, — поэтому видно, что дело
    не в связке ключей.
    """
    ayla_id = uuid.uuid4()
    _master(tenant, external_id=900002, ayla_user_id=ayla_id, archived_at=timezone.now())

    out, census = translate_provider_keys(_decision(str(ayla_id)))

    assert _ids(out) == [str(ayla_id)], "непродаваемый доезжает с ключом Ayla"
    assert (census.not_for_sale, census.no_mirror_row) == (1, 0)


@pytest.mark.django_db
def test_nothing_is_ever_dropped(tenant):
    """Главная гарантия: множество не усыхает ни при каком исходе.

    Три кандидата на входе — три на выходе, в том же порядке. Порядок
    пришёл готовым и пересобрать его не из чего (§4.3), а усечённое
    множество отняло бы у полки право заметить потерю.
    """
    sellable = uuid.uuid4()
    unsellable = uuid.uuid4()
    stranger = uuid.uuid4()
    mirror = _master(tenant, external_id=900003, ayla_user_id=sellable)
    _master(tenant, external_id=900004, ayla_user_id=unsellable, is_active=False)

    out, census = translate_provider_keys(
        _decision(str(sellable), str(unsellable), str(stranger)),
    )

    assert _ids(out) == [str(mirror.id), str(unsellable), str(stranger)]
    assert (census.translated, census.not_for_sale, census.no_mirror_row) == (1, 1, 1)


@pytest.mark.django_db
def test_a_kind_we_do_not_translate_is_left_alone(tenant):
    """`SERVICE` мы переводить не умеем — и не делаем вид, что перевели.

    Вид сверяется, а не подразумевается (контракт §5, K1). Молча
    пропустив чужой вид как «наш», мы вернули бы ту самую подмену
    предмета, из-за которой задача и появилась.
    """
    service_id = str(uuid.uuid4())
    payload = _decision(service_id)
    payload["data"]["ordered"][0]["candidate"]["kind"] = "SERVICE"

    out, census = translate_provider_keys(payload)

    assert _ids(out) == [service_id]
    assert (census.translated, census.untranslated) == (0, 0)


@pytest.mark.django_db
def test_the_input_is_not_mutated(tenant):
    """Оригинал остаётся оригиналом.

    Вызывающий, который положит в лог исходный ответ рядом с переводом,
    не должен обнаружить, что «исходный» уже переведён — иначе разбор
    инцидента пойдёт по подделанному следу.
    """
    ayla_id = uuid.uuid4()
    _master(tenant, external_id=900005, ayla_user_id=ayla_id)
    payload = _decision(str(ayla_id))

    translate_provider_keys(payload)

    assert _ids(payload) == [str(ayla_id)]


@pytest.mark.django_db
def test_shelf_shape_is_translated_too(tenant):
    """Обход не знает формы ответа — и это намеренно.

    Ссылка на кандидата живёт в разных обёртках: в решении внутри
    `ordered[]`, у домашнего экрана внутри полки, а полок несколько.
    Форма менялась дважды за сутки; модуль, знающий обёртки поимённо,
    ломался бы каждый раз.
    """
    ayla_id = uuid.uuid4()
    master = _master(tenant, external_id=900006, ayla_user_id=ayla_id)
    payload = {
        "data": {
            "layer_1_your_places": {"items": [], "reason_codes": []},
            "layer_2_ayla_picks": {
                "items": [
                    {
                        "candidate": {"kind": "PROVIDER", "id": str(ayla_id)},
                        "rank": 1,
                        "tier": 1,
                        "reason_codes": [],
                        "evidence": [],
                    }
                ],
                "reason_codes": [],
            },
        },
    }

    out, census = translate_provider_keys(payload)

    row = out["data"]["layer_2_ayla_picks"]["items"][0]
    assert row["candidate"]["id"] == str(master.id)
    assert census.translated == 1
