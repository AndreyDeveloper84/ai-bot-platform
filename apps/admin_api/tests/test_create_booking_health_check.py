"""Медицинская передача на салонной поверхности — контракт §98 / §100.

Шесть утверждений владельца, все проверяемые, ни одно не про тон:

1. ``HEALTH_CHECK_REQUIRED``      — исход маршрутизирует, а не ломает;
2. ``HEALTH_CHECK_UNKNOWN``       — то же наружу, но отдельно внутри;
3. ``HEALTH_CHECK_NOT_APPLICABLE`` — отказ БЕЗ обещания консультации;
4. в ответе нет идентификатора записи;
5. человеку не показывается технический текст;
6. счётчик ``UNKNOWN`` отделим от ``REQUIRED``.

Почему это тесты, а не ревью: §98 прямо называет два последних
требования «проверяемыми утверждениями, а не тоном» и говорит, что без
контрактного теста они переживут одну правку вёрстки. Здесь они не
переживут.

До DRF-1614 весь этот путь отвечал ``outcome="failed"`` и **HTTP 502** —
осознанное медицинское решение показывалось администратору как поломка
сервера, то есть как повод звонить в поддержку про исправную систему.
"""

from __future__ import annotations

import itertools
import json
import uuid
from datetime import datetime, timezone

import pytest
from django.test import Client
from django.urls import reverse

from apps.admin_api.tests.conftest import init_data_header, make_master
from apps.catalog.models import CatalogService
from apps.integrations.ayla.health_check import (
    HANDOFF_TEXT,
    HEALTH_CHECK_NOT_APPLICABLE,
    HEALTH_CHECK_REQUIRED,
    HEALTH_CHECK_UNKNOWN,
    NOT_APPLICABLE_TEXT,
)
from apps.integrations.ayla.salon_client import SalonHealthCheckHandoff
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db


def _url() -> str:
    return reverse("admin_api:create_booking")


#: Сквозной счётчик: `external_id` уникален в пределах салона, а тест
#: счётчика вызывает `_refuse` дважды подряд. Без этого падал бы не
#: предмет, а оснастка — и падение выглядело бы дефектом контракта.
_SEQ = itertools.count(4242)


def _service(tenant: Tenant, n: int) -> CatalogService:
    return CatalogService.all_tenants.create(
        tenant=tenant,
        external_id=n,
        external_updated_at=datetime.now(tz=timezone.utc),
        slug=f"manicure-{n}",
        name="Маникюр",
        duration_min=60,
        is_active=True,
        ayla_service_id=uuid.uuid4(),
    )


class _RefusingSalon:
    """Ayla, отвечающая 422 с медицинским кодом."""

    def __init__(self, code: str) -> None:
        self.code = code

    def create_appointment(self, **kwargs):
        raise SalonHealthCheckHandoff("screening required upstream", code=self.code)


@pytest.fixture
def refuse_with(monkeypatch):
    def _install(code: str) -> None:
        monkeypatch.setattr(
            "apps.integrations.ayla.salon_client.get_salon_client",
            lambda: _RefusingSalon(code),
        )

    return _install


def _post(client: Client, master, service) -> object:
    return client.post(
        _url(),
        data=json.dumps(
            {
                "master_id": str(master.id),
                "service_id": str(service.id),
                "start_at": "2026-08-21T15:00:00+03:00",
                "client_name": "Мария",
                "client_phone": "+79990000000",
            }
        ),
        content_type="application/json",
        HTTP_AUTHORIZATION=init_data_header("5001"),
    )


def _refuse(client, tenant, refuse_with, code: str):
    refuse_with(code)
    n = next(_SEQ)
    return _post(client, make_master(tenant, name="Анна", external_id=n), _service(tenant, n))


ALL_THREE = [HEALTH_CHECK_REQUIRED, HEALTH_CHECK_UNKNOWN, HEALTH_CHECK_NOT_APPLICABLE]


@pytest.mark.parametrize("code", ALL_THREE)
def test_the_refusal_is_not_a_server_failure(
    client: Client, tenant: Tenant, owner_bot_user, refuse_with, code: str
) -> None:
    """Ни один из трёх кодов не выглядит поломкой (§98 п.1–3).

    Проверяются обе половины утверждения. `outcome != "failed"` — это то,
    что читает экран; `status != 502` — то, что читает всё остальное, и
    именно 502 отправлял администратора звонить в поддержку.
    """
    resp = _refuse(client, tenant, refuse_with, code)

    body = resp.json()
    assert resp.status_code == 422, f"{code}: статус должен зеркалить отказ Ayla"
    assert body["outcome"] == "blocked", f"{code}: получено {body['outcome']!r}"
    assert body["outcome"] != "failed"
    assert resp.status_code != 502


@pytest.mark.parametrize("code", ALL_THREE)
def test_nothing_claims_a_booking_was_created(
    client: Client, tenant: Tenant, owner_bot_user, refuse_with, code: str
) -> None:
    """«Не обещать, что запись создана» = нет идентификатора (§98 п.4).

    Положительная стража идёт первой: сначала убеждаемся, что ключ
    `appointment_id` на этой поверхности вообще существует и в успешном
    ответе несёт значение, иначе «его нет» было бы верно и при опечатке
    в имени ключа.
    """
    from apps.admin_api import views_booking_create as module

    committed = module._outcome("committed", "ok", 201, appointment_id="A-1")
    assert b"appointment_id" in committed.content, (
        "ключ идентификатора исчез с поверхности — тест ниже проверял бы пустоту"
    )

    body = _refuse(client, tenant, refuse_with, code).json()

    assert "appointment_id" not in body, f"{code}: поверхность вернула идентификатор записи"
    assert body["outcome"] != "committed"


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (HEALTH_CHECK_REQUIRED, HANDOFF_TEXT),
        (HEALTH_CHECK_UNKNOWN, HANDOFF_TEXT),
        (HEALTH_CHECK_NOT_APPLICABLE, NOT_APPLICABLE_TEXT),
    ],
)
def test_the_person_reads_the_owner_sentence_and_no_technical_text(
    client: Client, tenant: Tenant, owner_bot_user, refuse_with, code: str, expected: str
) -> None:
    """Человек видит фразу владельца — и ничего из машинного (§98 п.5).

    Слово `HEALTH_CHECK_...` в тексте означало бы, что причина утекла в
    прозу; наличие `detail` от Ayla («screening required upstream») —
    что поверхность пересказала человеку внутреннюю диагностику.
    """
    body = _refuse(client, tenant, refuse_with, code).json()

    assert body["detail"] == expected
    assert "HEALTH_CHECK" not in body["detail"]
    assert "upstream" not in body["detail"]
    assert "422" not in body["detail"]


def test_not_applicable_promises_no_specialist() -> None:
    """Отказ, которому некого назначить, ничего не обещает (§100.A).

    Утверждение построено на смысле, а не на совпадении строк: слово
    «специалист» — единственная часть фразы передачи, которая является
    обещанием, и именно её на этом пути быть не должно.
    """
    assert "специалист" in HANDOFF_TEXT.lower(), (
        "фраза передачи перестала обещать специалиста — проверка ниже потеряла предмет"
    )
    assert "специалист" not in NOT_APPLICABLE_TEXT.lower()
    assert NOT_APPLICABLE_TEXT != HANDOFF_TEXT


def test_unknown_is_countable_apart_from_required(
    client: Client, tenant: Tenant, owner_bot_user, refuse_with, caplog
) -> None:
    """Счётчик UNKNOWN отделим от REQUIRED (§98 п.6).

    Содержательная причина, а не аккуратность: очередь разметки услуг
    приоритизируется числом UNKNOWN. Слитый счётчик оставляет её без
    критерия, и обнаружится это по кривому приоритету через недели, без
    видимой причины.

    Проверяется именно РАЗЛИЧИМОСТЬ: два отказа подряд обязаны оставить
    в журнале две разные строки. Совпадение строк означало бы, что
    посчитать UNKNOWN нечем.
    """
    import logging

    def _codes_logged(code: str) -> list[str]:
        caplog.clear()
        with caplog.at_level(logging.INFO, logger="apps.admin_api.views_booking_create"):
            _refuse(client, tenant, refuse_with, code)
        return [r.getMessage() for r in caplog.records if "health_check_handoff" in r.getMessage()]

    required = _codes_logged(HEALTH_CHECK_REQUIRED)
    unknown = _codes_logged(HEALTH_CHECK_UNKNOWN)

    assert required, "передача не оставила следа в журнале — считать нечего"
    assert unknown, "передача не оставила следа в журнале — считать нечего"
    assert any(HEALTH_CHECK_UNKNOWN in m for m in unknown)
    assert not any(HEALTH_CHECK_UNKNOWN in m for m in required), (
        "REQUIRED пишется в журнал как UNKNOWN — счётчик разметки будет завышен"
    )
