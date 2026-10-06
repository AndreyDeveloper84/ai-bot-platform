"""DRF-1943 — «голос не храним»: ссылка на запись голосового не ложится в журнал.

Вебхук MAX с голосовым несёт во вложении ``payload{id, url, token}``; ``url``
отдаёт запись 24 часа без авторизации. Журнал держит тело 72 часа и показывает
его в админке — значит, тело обязано лечь без этой ссылки, а воркер обязан
получить её как раньше (тело для обработки едет потоком, не журналом).
"""

from __future__ import annotations

import copy
import json
import uuid
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.test import Client
from django.utils import timezone

from apps.audit.models import AuditLog
from apps.channels.max.audio import extract_first_audio
from apps.events.models import Event
from apps.ingress import redaction
from apps.ingress.models import WebhookJournal
from apps.ingress.redaction import REDACTED_VOICE, REDACTION_FAILED, without_voice_links
from apps.ingress.retention import erase_person_rows
from apps.ingress.services import record_webhook

_WEBHOOK_SECRET = "drf1943-secret"  # pragma: allowlist secret — test-only literal

#: Подпись ссылки и ручка вложения — то, чего в журнале быть не должно.
SIG = "SIG-MARKER-1943"
TOKEN = "TOKEN-MARKER-1943"
VOICE = {
    "type": "audio",
    "payload": {"id": 7, "url": f"https://a.oneme.ru/v.ogg?sig={SIG}", "token": TOKEN},
}


def _body(*, attachments: list, text: str = "", user_id: int = 19430001, link=None) -> dict:
    message: dict = {
        "sender": {"user_id": user_id, "name": "Ирина"},
        "recipient": {"chat_id": 8899, "chat_type": "dialog"},
        "body": {"mid": f"mid-{uuid.uuid4().hex[:8]}", "seq": 1, "text": text},
    }
    message["body"]["attachments"] = attachments
    if link is not None:
        message["link"] = link
    return {"update_type": "message_created", "timestamp": 1731320000000, "message": message}


def _dump(value) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


class TestWithoutVoiceLinks:
    def test_the_voice_attachment_is_replaced_whole(self) -> None:
        out = without_voice_links(_body(attachments=[copy.deepcopy(VOICE)]))
        assert out["message"]["body"]["attachments"] == [REDACTED_VOICE]
        assert SIG not in _dump(out) and TOKEN not in _dump(out)

    def test_the_original_body_is_not_touched(self) -> None:
        # Тот же объект уходит в поток воркеру — ссылка в нём обязана остаться.
        body = _body(attachments=[copy.deepcopy(VOICE)])
        before = copy.deepcopy(body)
        without_voice_links(body)
        assert body == before
        assert extract_first_audio(body["message"]["body"]["attachments"]) is not None

    def test_a_forwarded_voice_is_covered_too(self) -> None:
        link = {"type": "forward", "message": {"mid": "m-0", "attachments": [copy.deepcopy(VOICE)]}}
        out = without_voice_links(_body(attachments=[], link=link))
        assert out["message"]["link"]["message"]["attachments"] == [REDACTED_VOICE]
        assert out["message"]["link"]["type"] == "forward"

    @pytest.mark.parametrize(
        "attachment",
        [
            # Ссылка рядом с payload, payload не словарь, расшифровка по соседству.
            {"type": "audio", "url": f"https://a.oneme.ru/v.ogg?sig={SIG}", "payload": {}},
            {"type": "audio", "payload": f"https://a.oneme.ru/v.ogg?sig={SIG}"},
            {"type": "audio", "payload": {"url": f"https://x/?sig={SIG}"}, "transcription": SIG},
        ],
    )
    def test_no_shape_of_an_audio_attachment_keeps_its_link(self, attachment: dict) -> None:
        out = without_voice_links(_body(attachments=[attachment]))
        assert out["message"]["body"]["attachments"] == [REDACTED_VOICE]
        assert SIG not in _dump(out)

    def test_other_attachments_and_text_stay_as_they_came(self) -> None:
        image = {"type": "image", "payload": {"url": "https://i.oneme.ru/i?r=abc", "token": "p"}}
        body = _body(attachments=[image, copy.deepcopy(VOICE)], text="смотри")
        out = without_voice_links(body)
        assert out["message"]["body"]["attachments"] == [image, REDACTED_VOICE]
        assert out["message"]["body"]["text"] == "смотри"
        assert out["message"]["sender"] == body["message"]["sender"]

    def test_a_body_without_voice_is_an_equal_copy(self) -> None:
        body = _body(attachments=[], text="Привет")
        out = without_voice_links(body)
        assert out == body
        assert out is not body

    def test_the_redacted_body_gives_the_extractor_nothing(self) -> None:
        # Контракт с читателем ссылки: что вырезано, то скачать нечем.
        out = without_voice_links(_body(attachments=[copy.deepcopy(VOICE)]))
        assert extract_first_audio([VOICE]) is not None
        assert extract_first_audio(out["message"]["body"]["attachments"]) is None


@pytest.mark.django_db
class TestRecordWebhook:
    def test_the_journal_row_holds_no_link(self) -> None:
        body = _body(attachments=[copy.deepcopy(VOICE)])
        row, created = record_webhook(
            channel="max", external_event_id=f"e-{uuid.uuid4().hex}", raw_payload=body
        )
        assert created is True
        row.refresh_from_db()
        assert row.raw_payload["message"]["body"]["attachments"] == [REDACTED_VOICE]
        assert row.raw_payload["message"]["sender"]["user_id"] == 19430001
        stored = _dump(row.raw_payload)
        assert SIG not in stored and TOKEN not in stored
        # Событие и аудит приёма тело не несут вовсе.
        side = _dump(list(Event._base_manager.values())) + _dump(
            list(AuditLog._base_manager.values())
        )
        assert "ingress.webhook_received" in side
        assert SIG not in side and TOKEN not in side
        # А тот, кто звал, держит тело со ссылкой — ему ставить его в поток.
        assert extract_first_audio(body["message"]["body"]["attachments"]) is not None

    def test_a_text_webhook_is_stored_as_it_came(self) -> None:
        body = _body(attachments=[], text="Привет")
        row, _ = record_webhook(
            channel="max", external_event_id=f"e-{uuid.uuid4().hex}", raw_payload=body
        )
        row.refresh_from_db()
        assert row.raw_payload == body

    def test_a_failed_redaction_never_stores_the_raw_body(self, monkeypatch) -> None:
        def boom(payload):
            raise RuntimeError("no")

        monkeypatch.setattr(redaction, "without_voice_links", boom)
        row, created = record_webhook(
            channel="max",
            external_event_id=f"e-{uuid.uuid4().hex}",
            raw_payload=_body(attachments=[copy.deepcopy(VOICE)]),
        )
        assert created is True  # приём вебхука сбой вырезания не стоит
        row.refresh_from_db()
        assert row.raw_payload == REDACTION_FAILED

    def test_forget_all_still_finds_the_voice_row_by_its_sender(self) -> None:
        row, _ = record_webhook(
            channel="max",
            external_event_id=f"e-{uuid.uuid4().hex}",
            raw_payload=_body(attachments=[copy.deepcopy(VOICE)], user_id=19430002),
        )
        erased = erase_person_rows(["19430002"], through=timezone.now() + timedelta(minutes=1))
        assert erased == 1
        row.refresh_from_db()
        assert row.raw_payload == {}


@pytest.mark.django_db
def test_the_worker_still_gets_the_link_the_journal_does_not(settings) -> None:
    """Вид: в журнал — без ссылки, в поток — со ссылкой (воркеру её скачивать)."""
    settings.MAX_WEBHOOK_SECRET = _WEBHOOK_SECRET
    body = _body(attachments=[copy.deepcopy(VOICE)])
    body["update_id"] = f"upd-{uuid.uuid4().hex[:8]}"
    with patch("apps.ingress.views.enqueue") as enqueue:
        response = Client().post(
            "/api/v1/ingress/max/",
            data=json.dumps(body),
            content_type="application/json",
            HTTP_X_MAX_BOT_API_SECRET=_WEBHOOK_SECRET,
        )
    assert response.status_code == 200, response.content
    assert enqueue.call_count == 1
    queued = enqueue.call_args.kwargs["payload"]
    assert extract_first_audio(queued["message"]["body"]["attachments"]) is not None
    assert SIG in _dump(queued)

    (row,) = WebhookJournal.objects.all()
    assert row.raw_payload["message"]["body"]["attachments"] == [REDACTED_VOICE]
    assert SIG not in _dump(row.raw_payload)
