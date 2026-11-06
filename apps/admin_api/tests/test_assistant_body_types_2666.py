"""DRF-2666 — словарь вместо вопроса не попадает в нить ассистента и к модели.

``text`` двери уходит в ``_remember(thread, role="user", content=text)`` —
в историю, которую человек видит, — и в запрос к модели.
"""

from __future__ import annotations

import json

import pytest
from django.urls import reverse

from apps.admin_api.tests.test_admin_assistant_2119 import FakeResult, llm  # noqa: F401
from apps.conversations.models import StaffAssistantMessage

from .conftest import init_data_header

pytestmark = pytest.mark.django_db

DICT = {"a": 1}


def _ask(client, text):
    return client.post(
        reverse("admin_api:assistant_ask"),
        data=json.dumps({"text": text}),
        content_type="application/json",
        HTTP_AUTHORIZATION=init_data_header("5001"),
    )


def test_a_dict_question_is_refused_and_nothing_is_remembered(
    client,
    owner_bot_user,
    llm,  # noqa: F811
):
    resp = _ask(client, DICT)

    assert resp.status_code == 400
    assert resp.json()["error"] == "bad_request"
    remembered = StaffAssistantMessage.all_tenants.count()
    assert remembered == 0  # empty-assert-ok: пара ниже — вопрос хранится


def test_a_string_question_is_remembered_verbatim(
    client,
    owner_bot_user,
    llm,  # noqa: F811
):
    llm["script"].append(FakeResult(text="Пока тихо."))

    resp = _ask(client, "кто сегодня у Ольги?")

    assert resp.status_code == 200, resp.content
    assert StaffAssistantMessage.all_tenants.filter(
        role="user", content="кто сегодня у Ольги?"
    ).exists()
