"""Снимок дневника не делит с питанием ни автомат защиты, ни квоту (DRF-2629).

Прокси снимка записи (DRF-2455) кормил общий автомат ``nutrition_client``:
серия таймаутов на снимках открывала автомат клиента, и вместе со снимком
отказывали дневник, распознавание, сводка и коуч. Квоты на человека у прокси
не было. Тот же род, что DRF-2618 (фото мастера против записи); оба приёма
повторены:

* автомат — по НАЗНАЧЕНИЮ (``BreakerPurpose``), обязательным параметром без
  умолчания, и там, где успех/отказ засчитываются ПОСЛЕ вызова (общие
  разборщики ответа), назначение приходит параметром — развести только вход
  значило бы развести наполовину;
* квота на человека до похода в каталог, число выведено (``views_diary_days``).

Узлы — парами, которые обязаны различаться:

* b1 — таймауты снимков открывают автомат снимков, а дневник на ТОМ ЖЕ
  клиенте в тот же момент отвечает строками дней;
* b2 — наоборот: таймауты дневника открывают автомат питания, снимок идёт;
* b3 — при открытом автомате снимков каталог за снимком не спрошен, дневник
  работает; имена автоматов разные;
* b4 — перепись по построению: в клиенте нет голого ``self._circuit``,
  ``_breaker`` и назначение разборщиков — без умолчания;
* q1 — человек выбрал квоту снимков: 121-й — 429 с ``Retry-After``, и в тот
  же момент дневник у него открывается; другой человек — не 429; сверх
  квоты каталог не спрошен.
"""

from __future__ import annotations

import ast
import asyncio
from collections.abc import Generator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from django.core.cache import cache
from django.urls import reverse

from apps.integrations.ayla import DiaryDayRow, DiaryDaysResponse, get_nutrition_client
from apps.integrations.ayla import nutrition_client as nc
from apps.integrations.ayla.nutrition_client import BreakerPurpose, reset_nutrition_client
from apps.miniapp_api import views_diary_days
from apps.miniapp_api.tests.test_diary_days_2099 import (  # переиспользуем стенд
    _auth,
    _bot_token,  # noqa: F401 — autouse
    _nutrition_on,  # noqa: F401 — autouse
    bot_user,  # noqa: F401 — фикстура
    consent,  # noqa: F401 — фикстура
    tenant,  # noqa: F401 — фикстура
)

EXT = "bot:max:2629"
LOG_ID = "11111111-2222-3333-4444-555555555555"
JPEG = b"\xff\xd8\xff\xe0" + b"pixels" * 400  # больше MIN_PHOTO_RESPONSE_BYTES
#: Форма провода ``internal/diary/days/`` — из ``test_nutrition_client_diary_days_2099``.
WIRE_DAYS = {
    "timezone": "Europe/Moscow",
    "from": "2026-09-14",
    "to": "2026-09-20",
    "days": [
        {"date": "2026-09-15", "meals_count": 2, "kcal": 500.0, "has_entries": True},
    ],
}


# ─── клиент: настоящий NutritionClient, провод — MockTransport ───────────────


@pytest.fixture
def ayla(settings: Any, monkeypatch: pytest.MonkeyPatch) -> Generator[dict[str, Any], None, None]:
    settings.AYLA_BASE_URL = "https://ayla.test"
    settings.NUTRITION_SERVICE_TOKEN = "svc-token-2629"  # noqa: S105 — test sentinel
    state: dict[str, Any] = {"photo": "ok", "diary": "ok", "seen": []}
    real_async = httpx.AsyncClient

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        state["seen"].append(path)
        if path.endswith("/photo/"):
            if state["photo"] == "timeout":
                raise httpx.ReadTimeout("slow network", request=request)
            return httpx.Response(200, content=JPEG, headers={"Content-Type": "image/jpeg"})
        if path.endswith("/diary/days/"):
            if state["diary"] == "timeout":
                raise httpx.ReadTimeout("slow network", request=request)
            return httpx.Response(200, json={"data": WIRE_DAYS})
        return httpx.Response(404, json={})

    def factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_async(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", factory)
    reset_nutrition_client()
    yield state
    reset_nutrition_client()


def _photo() -> Any:
    return asyncio.run(get_nutrition_client().food_photo(external_user_id=EXT, log_id=LOG_ID))


def _diary() -> DiaryDaysResponse:
    return asyncio.run(get_nutrition_client().diary_days(external_user_id=EXT))


def _open(purpose: BreakerPurpose) -> bool:
    import time

    return get_nutrition_client()._breaker(purpose).is_open(now=time.monotonic())


class TestB1PhotoTimeoutsLeaveTheDiaryAlone:
    def test_same_client_same_moment(self, ayla: dict[str, Any]) -> None:
        ayla["photo"] = "timeout"
        for _ in range(nc.CIRCUIT_FAILURE_THRESHOLD):
            with pytest.raises(nc.NutritionUnavailableError):
                _photo()
        assert _open(BreakerPurpose.FOOD_PHOTO) is True
        assert _open(BreakerPurpose.NUTRITION) is False
        days = _diary()
        assert [d.date for d in days.days] == ["2026-09-15"]


class TestB2DiaryTimeoutsLeaveThePhotoAlone:
    def test_reverse(self, ayla: dict[str, Any]) -> None:
        ayla["diary"] = "timeout"
        for _ in range(nc.CIRCUIT_FAILURE_THRESHOLD):
            with pytest.raises(nc.NutritionUnavailableError):
                _diary()
        assert _open(BreakerPurpose.NUTRITION) is True
        assert _open(BreakerPurpose.FOOD_PHOTO) is False
        content, content_type = _photo()
        assert content == JPEG and content_type == "image/jpeg"


class TestB3OpenPhotoBreaker:
    def test_catalog_not_asked_for_photo_and_diary_works(self, ayla: dict[str, Any]) -> None:
        ayla["photo"] = "timeout"
        for _ in range(nc.CIRCUIT_FAILURE_THRESHOLD):
            with pytest.raises(nc.NutritionUnavailableError):
                _photo()
        asked = len(ayla["seen"])
        with pytest.raises(nc.NutritionUnavailableError, match="circuit_open"):
            _photo()
        assert len(ayla["seen"]) == asked  # за снимком каталог не спрошен
        assert _diary().timezone == "Europe/Moscow"

    def test_breakers_are_named_apart(self, ayla: dict[str, Any]) -> None:
        client = get_nutrition_client()
        assert client._breaker(BreakerPurpose.NUTRITION).name == "ayla.nutrition"
        assert client._breaker(BreakerPurpose.FOOD_PHOTO).name == "ayla.nutrition.food_photo"


class TestB4PurposeIsNamedEverywhere:
    """Новый метод не попадёт в чужой автомат молча: назначения без умолчания."""

    SRC = Path(nc.__file__).read_text(encoding="utf-8")

    def _class(self) -> ast.ClassDef:
        tree = ast.parse(self.SRC)
        return next(
            n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "NutritionClient"
        )

    def test_no_bare_circuit_attribute(self) -> None:
        bare = [
            n.lineno
            for n in ast.walk(self._class())
            if isinstance(n, ast.Attribute)
            and n.attr == "_circuit"
            and isinstance(n.value, ast.Name)
            and n.value.id == "self"
        ]
        assert bare == []
        # Положительный контроль: перепись видит обращения к автомату.
        accesses = self.SRC.count("self._breaker(")
        assert accesses >= 70, accesses

    def test_purpose_has_no_default_anywhere(self) -> None:
        with_purpose = []
        for fn in ast.walk(self._class()):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            args = fn.args
            positional = args.posonlyargs + args.args
            pos_defaults = (
                dict(
                    zip(
                        [a.arg for a in positional][-len(args.defaults) :],
                        args.defaults,
                        strict=False,
                    )
                )
                if args.defaults
                else {}
            )
            kw_defaults = {a.arg: d for a, d in zip(args.kwonlyargs, args.kw_defaults, strict=True)}
            for name in [a.arg for a in positional + args.kwonlyargs]:
                if name == "purpose":
                    with_purpose.append(fn.name)
                    assert pos_defaults.get(name) is None, fn.name
                    assert kw_defaults.get(name) is None, fn.name
        # _breaker + восемь разборщиков, считающих успех/отказ после вызова.
        assert "_breaker" in with_purpose
        assert len(with_purpose) >= 9, sorted(with_purpose)

    def test_food_photo_speaks_only_to_its_own_breaker(self) -> None:
        fn = next(
            f
            for f in self._class().body
            if isinstance(f, ast.AsyncFunctionDef) and f.name == "food_photo"
        )
        text = ast.get_source_segment(self.SRC, fn) or ""
        assert "BreakerPurpose.FOOD_PHOTO" in text
        assert "BreakerPurpose.NUTRITION" not in text


# ─── прокси: квота на человека ───────────────────────────────────────────────


@pytest.fixture
def _clean_cache():
    cache.clear()
    yield
    cache.clear()


def _catalog() -> tuple[Any, Any]:
    client = AsyncMock()
    client.food_photo = AsyncMock(return_value=(JPEG, "image/jpeg"))
    client.diary_days = AsyncMock(
        return_value=DiaryDaysResponse(
            timezone="Europe/Moscow",
            date_from="2026-09-14",
            date_to="2026-09-20",
            days=(DiaryDayRow(date="2026-09-15", meals_count=2, kcal=500.0, has_entries=True),),
        )
    )
    client.get_profile = AsyncMock(return_value=None)
    return patch("apps.integrations.ayla.get_nutrition_client", return_value=client), client


@pytest.mark.django_db
@pytest.mark.usefixtures("_clean_cache")
class TestQ1PerPersonQuota:
    def test_photos_limited_diary_open_other_person_free(
        self,
        client,
        bot_user,  # noqa: F811
        consent,  # noqa: F811
    ) -> None:
        limit = views_diary_days.FOOD_PHOTO_PER_PERSON_PER_MINUTE
        assert limit == 120  # решённое число — литералом, не только константой
        photo_url = reverse("miniapp_api:customer_food_photo", kwargs={"log_id": LOG_ID})
        days_url = reverse("miniapp_api:customer_diary_days")
        auth = _auth(bot_user.channel_user_id)
        patcher, catalog = _catalog()
        with patcher:
            codes = [
                client.get(photo_url, HTTP_AUTHORIZATION=auth).status_code for _ in range(limit)
            ]
            over = client.get(photo_url, HTTP_AUTHORIZATION=auth)
            diary = client.get(days_url, HTTP_AUTHORIZATION=auth)

        assert codes == [200] * limit
        assert over.status_code == 429
        assert over.json()["error"] == "photo_rate_limited"
        assert over["Retry-After"] == "60"
        # Сверх квоты каталог за снимком не спрошен.
        assert catalog.food_photo.await_count == limit
        # ...а дневник у того же человека в тот же момент открывается.
        assert diary.status_code == 200, diary.content
        assert diary.json()["days"][0]["date"] == "2026-09-15"

    def test_quota_is_per_person(self, client, bot_user, consent) -> None:  # noqa: F811
        """Пара: у A счётчик исчерпан — A получает 429, B в тот же момент — 200.

        Первый вариант узла заполнял чужой ключ и не краснел на подмене «один
        ключ на всех»: общий ключ чужого не трогает. Контроль «A — 429» и есть
        то, что общий ключ ломает.
        """
        from apps.identity.models import BotUser

        other = BotUser.all_tenants.create(
            tenant=bot_user.tenant, channel="max", channel_user_id="99002", display_name="Ольга"
        )
        photo_url = reverse("miniapp_api:customer_food_photo", kwargs={"log_id": LOG_ID})
        cache.set(f"miniapp.food_photo.quota:{bot_user.pk}", 10_000, timeout=60)
        patcher, _catalog_mock = _catalog()
        with patcher:
            exhausted = client.get(photo_url, HTTP_AUTHORIZATION=_auth(bot_user.channel_user_id))
            fresh = client.get(photo_url, HTTP_AUTHORIZATION=_auth(other.channel_user_id))
        assert exhausted.status_code == 429
        assert fresh.status_code == 200
