"""Отказ подтверждения говорит экрану, жив ли ещё талон (DRF-2373).

Предмет — тело отказа ``POST /assistant/confirm``. Соседний
``test_assistant_api.py`` держит саму петлю подтверждения; здесь только то,
что появилось: ``details.retriable`` и карточки отказа.

### Что было сломано — и это НЕ «отказ приходит голым»

Лист начинался с формулировки «в ответе нет поля для кнопок». На проводе это
верно, но на экране сломано было **хуже**. ``AylaChat.tsx`` снимал карточку
подтверждения только в ветви успеха, поэтому при отказе она **оставалась со
всеми кнопками** — включая «Подтвердить».

Для устаревшего, нечитаемого или чужого талона это ловушка: его аргументы
лежат **внутри подписи**, второй нажим пошлёт ровно то же самое и получит
ровно тот же отказ. Человеку был виден единственный обречённый выход. Это не
отсутствие выхода, а нарисованный выход, которого нет; молчаливая кнопка
была бы честнее.

Различить эти два рода отказа можно было только по ``slug``, а экран его не
читал. Поэтому поле — не «место под кнопку», а **ответ на вопрос «жив ли
талон»**.

### Почему узлы по проводу

Правка может «работать» в функции и не доезжать наружу — так и было в
DRF-2362. Здесь проверяется **тело HTTP-ответа**, а не возвращаемое значение
``execute``: между ними стоит вид, и потерять поле он может молча.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone as dt_timezone
from typing import Any

import pytest
from django.core import signing
from django.test import Client
from django.urls import reverse

from apps.catalog.models import CatalogMaster
from apps.identity.models import BotUser
from apps.master_api.services.assistant_actions import (
    ACTION_BLOCK_TIME,
    RETRIABLE_ACTION_SLUGS,
    is_retriable,
)
from apps.master_api.tests.conftest import init_data_header

pytestmark = pytest.mark.django_db

CONFIRM_URL = reverse("master_api:assistant_confirm")


def _confirm(client: Client, token: str, *, user_id: str = "12345"):  # noqa: ANN202
    return client.post(
        CONFIRM_URL,
        data={"token": token},
        content_type="application/json",
        HTTP_AUTHORIZATION=init_data_header(user_id),
    )


def _future(*, days: int, hour: int) -> datetime:
    return (datetime.now(tz=dt_timezone.utc) + timedelta(days=days)).replace(
        hour=hour, minute=0, second=0, microsecond=0
    )


def _token_for(master: CatalogMaster, *, salt_ok: bool = True) -> str:
    """Талон, выписанный тем же подписывателем, что и у продакшена."""
    from apps.master_api.services import assistant_actions as mod

    payload = json.dumps(
        {
            "action": ACTION_BLOCK_TIME,
            "master_id": str(master.id),
            "tenant_id": str(master.tenant_id),
            "args": {
                "start": _future(days=5, hour=9).isoformat(),
                "end": _future(days=5, hour=18).isoformat(),
                "reason_class": "vacation",
                "reason_text": "поездка",
            },
        }
    )
    if salt_ok:
        return mod._signer().sign(payload)  # noqa: SLF001 — тот же ключ, что у вида
    return signing.TimestampSigner(salt="not-the-right-salt").sign(payload)


class TestСловарьЖивучестиИЕгоУмолчание:
    """Умолчание — «повторять нечем», и это направление выбрано нарочно."""

    def test_исполнение_отказало_талон_цел(self) -> None:
        assert is_retriable("action_rejected") is True

    @pytest.mark.parametrize("slug", ["action_expired", "action_invalid", "action_not_yours"])
    def test_мёртвый_талон_повторять_нечем(self, slug: str) -> None:
        assert is_retriable(slug) is False

    def test_неизвестный_слаг_считается_окончательным(self) -> None:
        """Ошибиться можно в обе стороны, и цены у ошибок разные.

        Назвали временный отказ окончательным — человек спросит заново.
        Назвали окончательный временным — вернули кнопку, которая не может
        сработать. Поэтому неизвестное — окончательное.
        """
        assert is_retriable("совершенно новый слаг") is False
        assert is_retriable("") is False

    def test_живых_случаев_ровно_один_и_он_назван(self) -> None:
        """Стража на сам набор: разрастись молча он не должен."""
        assert RETRIABLE_ACTION_SLUGS == frozenset({"action_rejected"})


class TestМёртвыйТалонНазванМёртвым:
    def test_нечитаемый_талон_отвечает_retriable_false(
        self, client: Client, bot_user: BotUser, accepted_master: CatalogMaster
    ) -> None:
        resp = _confirm(client, "это-вообще-не-талон")

        assert resp.status_code == 400, resp.content
        body = resp.json()
        assert body["error"] == "action_invalid"
        assert body["details"]["retriable"] is False

    def test_подпись_чужим_ключом_тоже_мёртвая(
        self, client: Client, bot_user: BotUser, accepted_master: CatalogMaster
    ) -> None:
        resp = _confirm(client, _token_for(accepted_master, salt_ok=False))

        assert resp.status_code == 400, resp.content
        assert resp.json()["details"]["retriable"] is False

    def test_талон_чужого_мастера_мёртв_и_отвечает_403(
        self, client: Client, bot_user: BotUser, accepted_master: CatalogMaster
    ) -> None:
        """Код остался прежним — поле добавлено, а не подменило собой статус."""
        stranger = CatalogMaster.all_tenants.create(
            tenant=accepted_master.tenant,
            name="Чужой мастер",
            external_id=None,
            external_updated_at=_future(days=0, hour=0),
        )

        resp = _confirm(client, _token_for(stranger))

        assert resp.status_code == 403, resp.content
        assert resp.json()["error"] == "action_not_yours"
        assert resp.json()["details"]["retriable"] is False


class TestПолеДоезжаетПоПроводу:
    """Не возвращаемое значение ``execute``, а тело HTTP-ответа."""

    def test_отказ_несёт_и_причину_и_живучесть_и_карточки(
        self, client: Client, bot_user: BotUser, accepted_master: CatalogMaster
    ) -> None:
        resp = _confirm(client, "мусор")

        body = resp.json()
        # Положительная стража: прежние поля на месте. Без неё «поле
        # появилось» зеленело бы и на ответе, потерявшем текст отказа.
        assert body["detail"]
        assert body["error"]
        assert set(body["details"]) == {"retriable", "cards"}

    def test_карточки_отказа_больше_не_теряются(
        self,
        client: Client,
        bot_user: BotUser,
        accepted_master: CatalogMaster,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Потеря, а не новое поле: ``ask`` их отдаёт, ``confirm`` ронял.

        ``ActionError.cards`` заполняется и объявлен в докстринге класса, а
        ``services/assistant.py`` их пересылает. Путь подтверждения молча
        выбрасывал — и карточка, приложенная к отказу, не доезжала никуда.
        """
        # Вид импортирует ``execute`` внутри функции, поэтому подменять
        # надо у модуля-источника: имени ``execute`` в пространстве вида
        # просто нет, и подмена «по месту вызова» молча ничего не сделала бы.
        from apps.master_api.services import assistant_actions as mod

        card = {"kind": "slot_taken", "range": "14:00–15:00"}

        def boom(token: str, *, master: Any, actor: Any) -> Any:
            raise mod.ActionError("это время занято", slug="action_rejected", cards=[card])

        monkeypatch.setattr(mod, "execute", boom)

        resp = _confirm(client, "любой")

        assert resp.json()["details"]["cards"] == [card]


class TestВременныйОтказОстаётсяВременным:
    def test_исполнение_отказало_а_талон_цел(
        self,
        client: Client,
        bot_user: BotUser,
        accepted_master: CatalogMaster,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Второй узел пары — без него мы «починили» бы окончательный отказ
        и незаметно сломали временный, где повтор осмыслен."""
        from apps.master_api.services import assistant_actions as mod

        def boom(token: str, *, master: Any, actor: Any) -> Any:
            raise mod.ActionError("не удалось создать запись", slug="action_rejected")

        monkeypatch.setattr(mod, "execute", boom)

        resp = _confirm(client, "любой")

        assert resp.status_code == 400, resp.content
        assert resp.json()["details"]["retriable"] is True


class TestОтказыБезТалонаНеПритворяютсяЖивыми:
    def test_пустое_тело_не_обещает_повтора(
        self, client: Client, bot_user: BotUser, accepted_master: CatalogMaster
    ) -> None:
        """``bad_request`` — не предложение, и живучести у него нет.

        Отсутствие поля экран читает как «мёртв» (умолчание там такое же),
        поэтому здесь важно лишь то, что ``retriable: true`` не появляется.
        """
        resp = client.post(
            CONFIRM_URL,
            data={},
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("12345"),
        )

        assert resp.status_code == 400
        assert (resp.json().get("details") or {}).get("retriable") is not True


@pytest.fixture
def client() -> Client:
    return Client()
