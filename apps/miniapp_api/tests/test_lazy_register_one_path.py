"""Ленивая регистрация Mini App идёт ЧЕРЕЗ резолвер — один путь к человеку.

До этой правки «найти-или-создать человека» было двумя операциями с разными
правилами: `resolve_or_create_bot_user` заполняет пустое поле на существующей
строке и никогда не перезаписывает, а локальный `get_or_create` в этой ручке не
обогащал ничего. Одна операция, два ответа — то, что запрещено прямо.

Три утверждения, и каждое отвечает на свой вопрос:

* путь один — ручка зовёт резолвер, а не собственный `get_or_create`;
* поведение не потеряно — тенантский пояс по-прежнему ставится при создании;
* скоуп не протёк — `require_init_data` в скоуп не входит (#1019 / EPIC #1014),
  и точечный `tenant_scope` вокруг одного вызова закрывается ДО тела вью.

Третье проверяется отдельно, потому что оно про цену решения, а не про его
результат: сторож `test_require_init_data_scope_move.py` мерит тело вью, и
если бы мой скоуп протёк, он бы покраснел — но покраснел бы ПОЗЖЕ и в чужом
файле. Утверждение на своём месте дешевле разбирать.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time as time_module
from urllib.parse import urlencode

import pytest
from django.http import JsonResponse
from django.test import RequestFactory

from apps.identity.models import BotUser
from apps.miniapp_api.views import require_init_data
from apps.tenancy.context import current_tenant
from apps.tenancy.models import Tenant

BOT_TOKEN = "lazy-register-token"
BOT_TENANT_SLUG = "lazy-register-test"
#: Не Europe/Moscow намеренно: читатели пишут `bot_user.timezone or
#: "Europe/Moscow"`, поэтому на московском поясе «поставили» и «не поставили»
#: дают один и тот же наблюдаемый ответ, и тест прошёл бы при пустом поле.
TENANT_TZ = "Asia/Yekaterinburg"


def _sign(params: dict[str, str], *, token: str = BOT_TOKEN) -> str:
    data_check_string = "\n".join(f"{k}={params[k]}" for k in sorted(params))
    secret_key = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    digest = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode({**params, "hash": digest}, doseq=False)


def _init_data_header(user_id: str = "77001") -> str:
    params = {
        "user": json.dumps({"id": int(user_id), "first_name": "Мария"}),
        "auth_date": str(int(time_module.time())),
    }
    return f"MaxInitData {_sign(params)}"


@pytest.fixture(autouse=True)
def _bind_bot(settings) -> None:
    settings.MAX_BOT_TOKEN = BOT_TOKEN
    settings.MAX_BOT_TENANT_SLUG = BOT_TENANT_SLUG
    settings.STRICT_TENANT_SCOPE = "strict"


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(
        slug=BOT_TENANT_SLUG, name="Lazy Register Salon", timezone=TENANT_TZ
    )


def _call_through_decorator(user_id: str = "77001") -> dict:
    seen: dict = {}

    @require_init_data
    def stub(request):
        seen["tenant_in_view"] = current_tenant()
        seen["bot_user"] = request.bot_user
        return JsonResponse({"ok": True})

    req = RequestFactory().get("/x", HTTP_AUTHORIZATION=_init_data_header(user_id))
    seen["response"] = stub(req)
    return seen


# --- путь один ---------------------------------------------------------------


def test_lazy_register_goes_through_the_resolver(tenant, monkeypatch) -> None:
    """Ручка зовёт общий резолвер, а не свой `get_or_create`."""

    calls: list[dict] = []
    import apps.miniapp_api.views as views

    real = views.resolve_or_create_bot_user

    def spy(**kwargs):
        calls.append(kwargs)
        return real(**kwargs)

    monkeypatch.setattr(views, "resolve_or_create_bot_user", spy)
    seen = _call_through_decorator()

    assert seen["response"].status_code == 200, getattr(seen["response"], "content", b"")
    assert len(calls) == 1, calls
    assert calls[0]["channel"] == "max"
    assert calls[0]["timezone"] == TENANT_TZ


# --- поведение не потеряно ---------------------------------------------------


def test_tenant_timezone_still_stamped_on_creation(tenant) -> None:
    seen = _call_through_decorator()
    bot_user = seen["bot_user"]
    bot_user.refresh_from_db()

    assert bot_user.tenant_id == tenant.id
    assert bot_user.timezone == TENANT_TZ


def test_existing_row_keeps_its_own_timezone(tenant) -> None:
    """Обогащение пояса НЕ происходит: пустое — объявленное состояние.

    Строка, заведённая любым из прочих путей, приходит с `timezone=""`, и
    читатели трактуют это как Europe/Moscow. Проставить ей пояс салона на
    первом же заходе в Mini App значило бы молча сдвинуть человеку день.
    """

    existing = BotUser.all_tenants.create(
        tenant=tenant, channel="max", channel_user_id="77001", display_name="Была"
    )
    assert existing.timezone == ""

    seen = _call_through_decorator()
    existing.refresh_from_db()

    assert seen["bot_user"].id == existing.id
    assert existing.timezone == ""


# --- скоуп не протёк ---------------------------------------------------------


def test_the_pointwise_scope_closes_before_the_view(tenant) -> None:
    """(S1): вход в скоуп локален. Тело вью по-прежнему видит None (#1019)."""

    seen = _call_through_decorator()

    assert seen["tenant_in_view"] is None
    assert seen["bot_user"].tenant_id == tenant.id


def test_scope_closes_on_the_already_existing_path_too(tenant) -> None:
    """Вторая ветка того же решения: без создания скоуп тоже не остаётся."""

    BotUser.all_tenants.create(tenant=tenant, channel="max", channel_user_id="77002")
    seen = _call_through_decorator("77002")

    assert seen["tenant_in_view"] is None
