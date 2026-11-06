"""DRF-1943 (хвост) — упавшая запись с голосовым не держит ссылку на запись.

Решение владельца: «голос не храним, только текст». Ссылку на голосовое MAX
отдаёт сутки без авторизации. Пока обработка идёт, она лежит в записи потока
Redis — это нужно, чтобы скачать запись. Если обработчик упал, запись раньше
оставалась в PEL целиком, вместе со ссылкой, до разборщика (по умолчанию
выключен) или до обрезки через ``INGRESS_RAW_RETENTION_HOURS``. Теперь такая
запись сразу уходит в ``<stream>:dlq`` копией без ссылки, а любая копия в DLQ
— и разборщика тоже — пишется без ссылки.
"""

from __future__ import annotations

import json

import pytest

from apps.ingress import streams
from apps.ingress.redaction import stream_data_without_voice
from apps.workers import consumer, reaper
from apps.workers.base import TenantAwareTask
from apps.workers.registry import emptied_registry_for_tests, register
from apps.workers.tests.test_consumer import _FakeStreamRedis

pytestmark = pytest.mark.django_db

STREAM = "ingress:max"
PEL = (STREAM, "consumers")
DLQ = f"{STREAM}:dlq"
SIGNATURE = "SIG1943voice"
TOKEN = "TOK1943voice"

AUDIO = {
    "type": "audio",
    "payload": {
        "id": 7,
        "url": f"https://a.oneme.ru/v?signatureToken={SIGNATURE}",
        "token": TOKEN,
    },
}
VIDEO = {
    "type": "video",
    "payload": {"url": f"https://a.oneme.ru/c?sig={SIGNATURE}", "token": TOKEN},
}
FILE_NO_NAME = {"type": "file", "payload": {"url": f"https://a.oneme.ru/f?sig={SIGNATURE}"}}
PDF = {"type": "file", "filename": "счёт.pdf", "payload": {"url": "https://a.oneme.ru/f?doc=1"}}


def _body(attachments: list[dict], *, sender: int = 42) -> dict:
    return {
        "update_type": "message_created",
        "timestamp": 1,
        "message": {
            "sender": {"user_id": sender},
            "recipient": {"chat_id": 1, "chat_type": "dialog"},
            "body": {"mid": "m-1943", "seq": 1, "text": "", "attachments": attachments},
        },
    }


def _forwarded_voice() -> dict:
    body = _body([])
    body["message"]["link"] = {"type": "forward", "message": {"attachments": [AUDIO]}}
    return body


@pytest.fixture
def fake_redis(monkeypatch):
    fake = _FakeStreamRedis()
    monkeypatch.setattr(streams, "_client", lambda: fake)
    return fake


@pytest.fixture(autouse=True)
def _clean_registry():
    with emptied_registry_for_tests():
        yield


@pytest.fixture
def failing_handler():
    @register(STREAM)
    class _Failing(TenantAwareTask):
        requires_tenant = False

        def handle(self, payload):
            raise RuntimeError("boom")

    return _Failing


def _dlq(fake) -> list[dict[str, str]]:
    return [fields for _, fields in fake.streams.get(DLQ, [])]


def _no_link(fields: dict[str, str]) -> bool:
    blob = json.dumps(fields, ensure_ascii=False)
    return SIGNATURE not in blob and TOKEN not in blob


class TestFailedVoiceEntryLeavesThePel:
    @pytest.mark.parametrize(
        "body",
        [_body([AUDIO]), _body([VIDEO]), _body([FILE_NO_NAME]), _forwarded_voice()],
        ids=["audio", "video", "file-without-name", "forwarded-voice"],
    )
    def test_moved_to_the_dlq_without_the_link(self, fake_redis, failing_handler, body):
        # Положительная пара в том же прогоне: текстовая запись падает так же
        # и остаётся в PEL — как было до правки.
        streams.enqueue("max", body)
        text_id = streams.enqueue("max", _body([]) | {"text": "привет"})

        processed = consumer.consume_once(streams=[STREAM])

        assert processed == 0
        assert list(fake_redis.pel[PEL]) == [text_id]
        assert [entry for entry, _ in fake_redis.xrange(STREAM)] == [text_id]
        copies = _dlq(fake_redis)
        assert len(copies) == 1
        copy = copies[0]
        assert copy["__reaper.voice_redacted"] == "1"
        assert copy["__reaper.from"] == STREAM
        assert copy["__reaper.classification"] == "tenant_required_missing"
        assert '"redacted": "voice"' in copy["data"]
        assert _no_link(copy)

    def test_the_copy_still_names_the_sender_for_forget_all(self, fake_redis, failing_handler):
        streams.enqueue("max", _body([AUDIO], sender=777))
        consumer.consume_once(streams=[STREAM])

        copies = _dlq(fake_redis)
        assert len(copies) == 1
        assert streams._sender_of(copies[0]) == "777"

    def test_a_document_is_not_voice_and_stays_in_the_pel(self, fake_redis, failing_handler):
        entry = streams.enqueue("max", _body([PDF]))
        consumer.consume_once(streams=[STREAM])

        assert list(fake_redis.pel[PEL]) == [entry]
        assert DLQ not in fake_redis.streams

    def test_the_batch_goes_on_and_the_entry_stays_if_the_dlq_write_fails(
        self, fake_redis, failing_handler, monkeypatch
    ):
        voice = streams.enqueue("max", _body([AUDIO]))
        text = streams.enqueue("max", _body([]))
        real_xadd = fake_redis.xadd

        def xadd(stream, fields, **kwargs):
            if stream == DLQ:
                raise ConnectionError("redis down")
            return real_xadd(stream, fields, **kwargs)

        monkeypatch.setattr(fake_redis, "xadd", xadd)
        consumer.consume_once(streams=[STREAM])

        # Обе записи прочитаны и обе остались — ни одна не потеряна.
        assert sorted(fake_redis.pel[PEL]) == sorted([voice, text])

    def test_xdel_runs_even_if_xack_fails(self, fake_redis, failing_handler, monkeypatch):
        entry = streams.enqueue("max", _body([AUDIO]))

        def xack(*_args, **_kwargs):
            raise ConnectionError("redis down")

        monkeypatch.setattr(fake_redis, "xack", xack)
        consumer.consume_once(streams=[STREAM])

        assert len(_dlq(fake_redis)) == 1
        # Запись осталась в PEL (XACK не прошёл), но тела со ссылкой в
        # потоке нет: приватность держит именно XDEL.
        assert entry in fake_redis.pel[PEL]
        assert entry not in [e for e, _ in fake_redis.xrange(STREAM)]


class TestReaperCopy:
    def _pending(self, fake, fields: dict[str, str]) -> str:
        fake.xgroup_create(STREAM, "consumers", "$", True)
        entry = fake.xadd(STREAM, fields)
        fake.pel.setdefault(PEL, {})[entry] = fields
        return entry

    def test_the_reaper_writes_the_dlq_copy_without_the_link(self, fake_redis):
        self._pending(fake_redis, {"data": json.dumps(_body([AUDIO])), "resolved_tenant_id": "t-1"})

        assert reaper.reap_pel_once(STREAM, idle_ms=0, batch_size=10) == 1

        copies = _dlq(fake_redis)
        assert len(copies) == 1
        assert copies[0]["__reaper.classification"] == "handler_failure"
        assert copies[0]["__reaper.voice_redacted"] == "1"
        assert _no_link(copies[0])

    def test_a_copy_without_data_gets_no_data(self, fake_redis):
        self._pending(fake_redis, {"canary": "1", "resolved_tenant_id": ""})

        assert reaper.reap_pel_once(STREAM, idle_ms=0, batch_size=10) == 1

        copies = _dlq(fake_redis)
        assert len(copies) == 1
        assert copies[0]["canary"] == "1"
        assert "data" not in copies[0]
        assert "__reaper.voice_redacted" not in copies[0]


class TestStreamData:
    def test_voice_is_cut_and_said_so(self):
        data, cut = stream_data_without_voice(json.dumps(_body([AUDIO])))
        assert cut is True
        assert SIGNATURE not in data
        assert json.loads(data)["message"]["body"]["attachments"] == [
            {"type": "audio", "redacted": "voice"}
        ]

    def test_an_already_cut_body_is_not_voice_again(self):
        once, _ = stream_data_without_voice(json.dumps(_body([AUDIO])))
        twice, cut = stream_data_without_voice(once)
        assert cut is False
        assert twice == once

    @pytest.mark.parametrize("data", [json.dumps(_body([PDF])), "{}", "not json", "", None])
    def test_anything_else_is_returned_as_is(self, data):
        assert stream_data_without_voice(data) == (data, False)
