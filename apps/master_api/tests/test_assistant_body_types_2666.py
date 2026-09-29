"""DRF-2666 — словарь вместо вопроса мастера не попадает в нить и к модели."""

from __future__ import annotations

import json

import pytest

from apps.conversations.models import StaffAssistantMessage
from apps.master_api.tests.conftest import init_data_header
from apps.master_api.tests.test_assistant_api import ASK_URL, FakeResult, llm  # noqa: F401

pytestmark = pytest.mark.django_db

DICT = {"a": 1}


def _ask(client, text):
    return client.post(
        ASK_URL,
        data=json.dumps({"text": text}),
        content_type="application/json",
        HTTP_AUTHORIZATION=init_data_header("12345"),
    )


def test_a_dict_question_is_refused_and_nothing_is_remembered(
    client,
    bot_user,
    accepted_master,
    llm,  # noqa: F811
):
    resp = _ask(client, DICT)

    assert resp.status_code == 400
    assert resp.json()["error"] == "bad_request"
    remembered = StaffAssistantMessage.all_tenants.count()
    assert remembered == 0  # empty-assert-ok: пара ниже — вопрос хранится


def test_a_string_question_is_remembered_verbatim(
    client,
    bot_user,
    accepted_master,
    llm,  # noqa: F811
):
    llm["script"].append(FakeResult(text="Завтра три записи."))

    resp = _ask(client, "что у меня завтра?")

    assert resp.status_code == 200, resp.content
    assert StaffAssistantMessage.all_tenants.filter(
        role="user", content="что у меня завтра?"
    ).exists()
