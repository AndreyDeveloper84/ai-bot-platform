"""Лог ленивого создания BotUser не несёт персональных данных (DRF-2006).

При первом верифицированном входе в Mini App ``_lazy_register_bot_user``
заводит ``BotUser`` и пишет строку ``miniapp_api.auth.lazy_register``. До этого
листа строка несла MAX id человека (``channel_user_id``), его имя и фамилию
(``display``) и slug тенанта (у соло-кабинета он привязан к человеку).

Ни один существующий сторож этого не ловит:

* ``tools/lint/pii_guard.py`` проверяет файлы коммита, а не то, что пишется в
  лог во время работы, и имена по замыслу не проверяет;
* ``apps.observability.pii_filter.PIIRedactingFilter`` маскирует при записи
  только телефоны (+7/8 и 10 цифр), e-mail и карты: имя не маскируется, MAX id
  без префикса +7/8 под шаблон телефона не попадает.

Здесь закрепляется: строка создания остаётся (след нужен для расследования),
но в ней нет ни MAX id, ни имени, ни slug; повторный вход второй строки не
пишет.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time as time_module
from urllib.parse import urlencode

import pytest

from apps.identity.models import BotUser
from apps.miniapp_api.auth import verify_init_data
from apps.miniapp_api.views import _lazy_register_bot_user
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

BOT_TOKEN = "test-bot-token-2006"  # pragma: allowlist secret
MAX_USER_ID = 20060001
FIRST = "Проба2006"
LAST = "Фамилия2006"
TENANT_SLUG = "lazy-log-2006"
EVENT = "miniapp_api.auth.lazy_register"


def _sign(params: dict[str, str]) -> str:
    data_check_string = "\n".join(f"{k}={params[k]}" for k in sorted(params))
    secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    digest = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode({**params, "hash": digest})


def _verified():
    raw = _sign(
        {
            "user": json.dumps({"id": MAX_USER_ID, "first_name": FIRST, "last_name": LAST}),
            "auth_date": str(int(time_module.time())),
        }
    )
    return verify_init_data(raw, bot_token=BOT_TOKEN)


@pytest.fixture
def tenant() -> Tenant:
    return Tenant.objects.create(slug=TENANT_SLUG, name="Lazy Log 2006", timezone="Europe/Moscow")


def _lazy_records(caplog) -> list[logging.LogRecord]:
    return [r for r in caplog.records if EVENT in r.getMessage()]


def test_lazy_register_record_carries_no_personal_data(tenant, caplog):
    with caplog.at_level(logging.INFO):
        _lazy_register_bot_user(tenant, _verified())

    (record,) = _lazy_records(caplog)
    rendered = f"{record.getMessage()} {record.args!r}"
    for personal in (str(MAX_USER_ID), FIRST, LAST, TENANT_SLUG):
        assert personal not in rendered, f"в строке создания BotUser осталось: {personal!r}"


def test_lazy_register_record_is_written_once_on_creation(tenant, caplog):
    """Положительная стража: след создания не пропал — ровно одна строка на новую запись."""
    with caplog.at_level(logging.INFO):
        bot_user = _lazy_register_bot_user(tenant, _verified())

    assert BotUser.all_tenants.filter(pk=bot_user.pk).exists()
    assert len(_lazy_records(caplog)) == 1


def test_repeat_contact_writes_no_second_record(tenant, caplog):
    first = _lazy_register_bot_user(tenant, _verified())
    # Обработчик caplog висит на весь тест: строка первого (создающего) вызова
    # уже в caplog.records. Без очистки проверка ниже видела бы её, а не повтор.
    caplog.clear()

    with caplog.at_level(logging.INFO):
        again = _lazy_register_bot_user(tenant, _verified())

    assert again.pk == first.pk
    assert BotUser.all_tenants.filter(tenant=tenant, channel="max", channel_user_id=str(MAX_USER_ID)).count() == 1
    assert _lazy_records(caplog) == []
