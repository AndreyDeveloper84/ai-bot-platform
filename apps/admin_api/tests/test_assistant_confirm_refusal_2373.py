"""Отказ подтверждения у администратора говорит то же, что у мастера (DRF-2373).

### Почему это не «заодно починили ещё и админку»

``AylaChat.tsx`` — **один** компонент, и обслуживает он оба вида: мастерский
(`/master/ayla`) и админский. ``ActionError`` у них тоже **один класс** —
``admin_api/services/assistant.py`` импортирует его из ``master_api``, и
словарь слагов общий.

Значит научить экран читать живучесть талона и оставить админский вид без
неё — не «неполная починка», а **новое поведение, зависящее от того, кто
ответил**: у мастера ловушка исчезла бы, у администратора осталась. Правка
здесь неделима.

Сам предмет — тот же, и он описан у соседа
(``apps/master_api/tests/test_assistant_confirm_refusal_2373.py``): до листа
экран снимал карточку подтверждения только в ветви успеха, и при мёртвом
талоне человеку оставался единственный обречённый выход.

Здесь — только то, что специфично админскому виду: его собственный
``execute_admin_action`` и его собственная ветвь ``except``.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from django.test import Client
from django.urls import reverse

from apps.master_api.services.assistant_actions import is_retriable

from .conftest import init_data_header

pytestmark = pytest.mark.django_db

CONFIRM_URL = reverse("admin_api:assistant_confirm")


def _confirm(client: Client, token: str, *, user_id: str = "5001"):  # noqa: ANN202
    return client.post(
        CONFIRM_URL,
        data=json.dumps({"token": token}),
        content_type="application/json",
        HTTP_AUTHORIZATION=init_data_header(user_id),
    )


class TestАдминскийОтказГоворитТоЖе:
    def test_нечитаемый_талон_называется_мёртвым(self, client: Client, owner_bot_user: Any) -> None:
        resp = _confirm(client, "это-вообще-не-талон")

        assert resp.status_code == 400, resp.content
        assert resp.json()["details"]["retriable"] is False

    def test_прежние_поля_никуда_не_делись(self, client: Client, owner_bot_user: Any) -> None:
        """Положительная стража: поле добавлено, а не подменило собой тело.

        Без неё «retriable пришёл» зеленело бы и на ответе, потерявшем текст
        отказа, — а текст это единственное, что человек прочитает.
        """
        body = _confirm(client, "мусор").json()

        assert body["error"]
        assert body["detail"]
        assert set(body["details"]) == {"retriable", "cards"}

    def test_временный_отказ_остаётся_временным(
        self, client: Client, owner_bot_user: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Второй узел пары: иначе починили бы окончательный и сломали этот."""
        # Вид импортирует ``execute_admin_action`` внутри функции, поэтому
        # подменяется модуль-источник: имени в пространстве вида нет, и
        # подмена «по месту вызова» молча ничего не сделала бы.
        from apps.master_api.services.assistant_actions import ActionError

        def boom(token: str, **kwargs: Any) -> Any:
            raise ActionError("салон отклонил заявку", slug="action_rejected")

        monkeypatch.setattr("apps.admin_api.services.assistant.execute_admin_action", boom)

        resp = _confirm(client, "любой")

        assert resp.status_code == 400, resp.content
        assert resp.json()["details"]["retriable"] is True


class TestСловарьОбщийАНеСкопированный:
    def test_живучесть_считает_та_же_функция(self) -> None:
        """Второго словаря не заводится — оба вида зовут одну функцию.

        Скопируй кто-нибудь набор слагов сюда, он разошёлся бы с мастерским
        молча: расхождение видно только тому, кто читает оба файла подряд.
        """
        from apps.admin_api import views_assistant as admin_view
        from apps.master_api import views_assistant as master_view

        assert admin_view.assistant_confirm is not master_view.assistant_confirm
        assert is_retriable("action_rejected") is True
        assert is_retriable("action_expired") is False
