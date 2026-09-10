"""Медицинская передача в диалоговом канале — контракт §98 / §100.

Третий из трёх путей. Дефект здесь был тоньше, чем на двух других:
передача происходила — человека действительно уводили к оператору, — но
под чужим именем. `reason="yclients_api_error"`, событие
`booking.confirm_failed`, текст «Не получилось оформить запись».

Это врёт дважды. Человеку — про поломку, которой не было. И нам: журнал,
записавший медицинское решение как сбой интеграции, соврёт через месяц,
когда кто-нибудь станет считать, почему люди уходят к оператору.

Шесть утверждений владельца те же, что на двух других поверхностях, но
проверяются в терминах этой: текст берётся из `BookingToolResult.text`,
счётчик — из аудита `booking.tool_invoked`, а «запись не создана» — из
отсутствия подтверждения и брони.
"""

from __future__ import annotations

from typing import Any

import pytest

from apps.audit.models import AuditLog
from apps.identity.models import BotUser
from apps.integrations.ayla.booking_client import BookingBadRequestError
from apps.integrations.ayla.health_check import (
    HANDOFF_TEXT,
    HEALTH_CHECK_NOT_APPLICABLE,
    HEALTH_CHECK_REQUIRED,
    HEALTH_CHECK_UNKNOWN,
    NOT_APPLICABLE_TEXT,
)
from apps.skills.booking.tests.test_ayla_write_lifecycle import FakeAyla, _adapter
from apps.skills.booking.tools import execute_confirm
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

ALL_THREE = [HEALTH_CHECK_REQUIRED, HEALTH_CHECK_UNKNOWN, HEALTH_CHECK_NOT_APPLICABLE]


@pytest.fixture(autouse=True)
def _flag_on(settings):
    settings.BOOKING_VIA_AYLA_REST = True


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(slug="health-gate", name="Health Gate")


@pytest.fixture
def bot_user(tenant: Tenant) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="bu-health",
        chat_id="bu-health",
        phone="79991234567",
        client_name="Anna",
    )


class _RefusingAyla(FakeAyla):
    """Ayla, отвечающая 422 с медицинским кодом и явным флагом."""

    def __init__(self, code: str, *, handoff: bool | None) -> None:
        super().__init__()
        self._code = code
        self._handoff = handoff

    def create_appointment(self, **kwargs: Any):
        raise BookingBadRequestError(
            f"http_422_{self._code.lower()}",
            status_code=422,
            code=self._code,
            handoff=self._handoff,
        )


def _confirm(tenant: Tenant, bot_user: BotUser, code: str, *, handoff: bool | None = None):
    payload: dict[str, Any] = {
        "master_id": "7c9e0000-0000-0000-0000-000000000011",
        "service_id": "1a2b3c4d-0000-0000-0000-000000000010",
        "slot_datetime": "2026-07-01T16:00:00+03:00",
        "client_phone": "79991234567",
        "client_name": "Anna",
        "master_name": "Ольга",
        "service_name": "Массаж",
    }
    with tenant_scope(tenant):
        return execute_confirm(
            client=_adapter(_RefusingAyla(code, handoff=handoff)),
            payload=payload,
            tenant=tenant,
            bot_user=bot_user,
        )


def _outcomes() -> list[str]:
    """Значения `outcome` из аудита вызовов инструмента.

    ``all_tenants``, а не ``objects``: тенант-скоупный менеджер вне
    запроса вернёт пусто, и тест позеленел бы на нуле строк — то есть
    доказал бы ровно обратное тому, что утверждает.
    """
    return [
        row.payload.get("outcome", "")
        for row in AuditLog.all_tenants.filter(action="booking.tool_invoked")
    ]


@pytest.mark.parametrize("code", ALL_THREE)
def test_the_handoff_is_not_named_a_failure(tenant: Tenant, bot_user: BotUser, code: str) -> None:
    """Передача перестаёт называться отказом шины (§98 п.1–3).

    Проверяется имя, а не факт: передача была и раньше. Именно имя
    `yclients_api_error` превращало медицинское решение в сбой
    интеграции — для человека в словах, для нас в журнале.
    """
    result = _confirm(tenant, bot_user, code)

    assert result.error == "health_check_handoff", f"{code}: получено {result.error!r}"
    assert result.error != "yclients_api_error"
    assert "health_check" in "|".join(_outcomes()), f"{code}: журнал не назвал исход"


@pytest.mark.parametrize("code", ALL_THREE)
def test_nothing_claims_a_booking_was_created(tenant: Tenant, bot_user: BotUser, code: str) -> None:
    """«Не обещать, что запись создана» (§98 п.4).

    Положительная стража впереди: на успешном пути этот же вызов
    возвращает подтверждение с `ok=True`, иначе «подтверждения нет»
    было бы верно и для сломанной оснастки.
    """
    from apps.integrations.ayla.booking_client import AylaBookingRecord
    from apps.skills.booking.tests.test_ayla_write_lifecycle import _appt_raw

    ok_fake = FakeAyla()
    ok_fake.create_response = AylaBookingRecord(
        appointment_id="3f1c2e9a-4b7d-4c2a-9e1f-8a2b6c0d1e34",
        raw=_appt_raw(start="2026-07-01T16:00:00+03:00", end="2026-07-01T17:00:00+03:00"),
    )
    with tenant_scope(tenant):
        good = execute_confirm(
            client=_adapter(ok_fake),
            payload={
                "master_id": "7c9e0000-0000-0000-0000-000000000011",
                "service_id": "1a2b3c4d-0000-0000-0000-000000000010",
                "slot_datetime": "2026-07-01T16:00:00+03:00",
                "client_phone": "79991234567",
                "client_name": "Anna",
                "master_name": "Ольга",
                "service_name": "Массаж",
            },
            tenant=tenant,
            bot_user=bot_user,
        )
    assert good.confirmation is not None and good.confirmation.ok, (
        "успешный путь перестал подтверждать запись — проверка ниже потеряла предмет"
    )

    result = _confirm(tenant, bot_user, code)

    assert result.confirmation is None or not result.confirmation.ok
    # Стража присутствия на тех же данных: инструмент вернул человеку
    # непустую фразу, значит отрицания ниже сказаны про текст, а не про
    # его отсутствие.
    assert result.text, "исход не дал человеку ни слова — проверять нечего"
    assert "Вы записаны" not in result.text
    assert "записан" not in result.text.lower()


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (HEALTH_CHECK_REQUIRED, HANDOFF_TEXT),
        (HEALTH_CHECK_UNKNOWN, HANDOFF_TEXT),
        (HEALTH_CHECK_NOT_APPLICABLE, NOT_APPLICABLE_TEXT),
    ],
)
def test_the_person_reads_the_owner_sentence(
    tenant: Tenant, bot_user: BotUser, code: str, expected: str
) -> None:
    """Одна спокойная фраза на всех трёх поверхностях (§98 п.5).

    Прежний текст канала — «Не получилось оформить запись» — здесь
    запрещён прямо: он описывает поломку, которой не было.
    """
    text = _confirm(tenant, bot_user, code).text or ""

    assert text == expected
    assert "HEALTH_CHECK" not in text
    assert "http_422" not in text
    assert "Не получилось" not in text


def test_the_explicit_flag_wins_over_the_code(tenant: Tenant, bot_user: BotUser) -> None:
    """Обещание берётся из `error.details.handoff`, а не выводится.

    Каталог объявил поле именно затем, чтобы поверхность не разбирала
    строку кода. Проверка ставит флаг ПРОТИВ кода: если бы решение
    по-прежнему выводилось из имени, фраза осталась бы прежней.
    """
    against = _confirm(tenant, bot_user, HEALTH_CHECK_REQUIRED, handoff=False)
    assert against.text == NOT_APPLICABLE_TEXT, "флаг проигнорирован — причина всё ещё выводится"

    absent = _confirm(tenant, bot_user, HEALTH_CHECK_REQUIRED, handoff=None)
    assert absent.text == HANDOFF_TEXT, "без поля должно работать прежнее умолчание по коду"


def test_unknown_is_countable_apart_from_required(tenant: Tenant, bot_user: BotUser) -> None:
    """Счётчик UNKNOWN отделим от REQUIRED (§98 п.6).

    Очередь разметки услуг приоритизируется числом UNKNOWN. Слитый
    счётчик оставляет её без критерия, и обнаружится это по кривому
    приоритету через недели, без видимой причины.
    """
    _confirm(tenant, bot_user, HEALTH_CHECK_REQUIRED)
    _confirm(tenant, bot_user, HEALTH_CHECK_UNKNOWN)

    # Множество, а не срез по длине: порядок строк аудита ничем не
    # гарантирован, и тест, опирающийся на него, краснел бы по причине,
    # не имеющей отношения к предмету.
    outcomes = {o for o in _outcomes() if "health_check" in o}

    assert outcomes, "передача не оставила следа в аудите — считать нечего"
    assert len(outcomes) == 2, f"два разных отказа дали {len(outcomes)} имя(ён): {outcomes}"
    assert any("unknown" in o for o in outcomes), (
        "UNKNOWN неотличим — очередь разметки без критерия"
    )
    assert any(o.endswith("required") for o in outcomes)
