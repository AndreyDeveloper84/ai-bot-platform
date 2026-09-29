"""DRF-2220 — a raw webhook body does not live in Redis without a term.

``ingress:<channel>`` carries the webhook as MAX sent it: the message text,
the sender's name, a shared contact. Before this leaf the consumer only
XACK'd, so every body stayed for good (on the stand: from 03.09 on), and
«забудь всё» never looked there. The consumer half — a processed entry
leaves the stream — is pinned in ``apps/workers/tests/test_consumer.py``;
this module holds the rest: the retention trim, the per-person purge, the
one-off cleanup command and the export's statement of the term.
"""

from __future__ import annotations

import io
import json
from datetime import datetime, timedelta, timezone

from django.core.management import call_command
import pytest

from apps.ingress import streams
from apps.workers import registry

NOW = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)
STREAM = "ingress:max_global"
DLQ = f"{STREAM}{streams.DLQ_SUFFIX}"
_SECRET = "мой номер +79161234567"


def _ms(moment: datetime) -> int:
    return int(moment.timestamp() * 1000)


def _body(user_id: str) -> dict[str, str]:
    return {
        "data": json.dumps(
            {
                "update_type": "message_created",
                "message": {
                    "sender": {"user_id": user_id},
                    "recipient": {"chat_id": f"chat-{user_id}"},
                    "body": {"text": _SECRET},
                },
            },
            ensure_ascii=False,
        ),
        "trace_id": "",
        "resolved_tenant_id": "",
    }


class _Streams:
    """XADD-by-id, XRANGE, XDEL, XTRIM MINID, XPENDING — ids are «ms-seq»."""

    def __init__(self) -> None:
        self.streams: dict[str, list[tuple[str, dict[str, str]]]] = {}
        self.pending: dict[str, set[str]] = {}

    def put(self, stream: str, moment: datetime, fields: dict[str, str], *, pending=False) -> str:
        seq = len(self.streams.get(stream, [])) + 1
        entry_id = f"{_ms(moment)}-{seq}"
        self.streams.setdefault(stream, []).append((entry_id, dict(fields)))
        if pending:
            self.pending.setdefault(stream, set()).add(entry_id)
        return entry_id

    def ids(self, stream: str) -> list[str]:
        return [entry_id for entry_id, _ in self.streams.get(stream, [])]

    @staticmethod
    def _key(entry_id: str) -> tuple[int, int]:
        ms, _, seq = entry_id.partition("-")
        return int(ms), int(seq or 0)

    def xrange(self, stream, min="-", max="+", count=None):  # noqa: A002
        def ok(entry_id: str) -> bool:
            key = self._key(entry_id)
            if min not in ("-",):
                excl = min.startswith("(")
                lo = min.lstrip("(")
                lo_key = self._key(lo)
                if key < lo_key or (excl and key == lo_key):
                    return False
            if max not in ("+",):
                hi_ms, _, hi_seq = max.partition("-")
                hi_key = (int(hi_ms), int(hi_seq) if hi_seq else 2**63)
                if key > hi_key:
                    return False
            return True

        out = [(i, dict(f)) for i, f in self.streams.get(stream, []) if ok(i)]
        return out[:count] if count else out

    def xdel(self, stream, *entry_ids):
        before = len(self.streams.get(stream, []))
        self.streams[stream] = [e for e in self.streams.get(stream, []) if e[0] not in entry_ids]
        self.pending.get(stream, set()).difference_update(entry_ids)
        return before - len(self.streams[stream])

    def xtrim(self, stream, minid=None, approximate=True, **_kw):
        assert approximate is False, "the term is exact, not «about 72 hours»"
        floor = int(minid)
        before = len(self.streams.get(stream, []))
        self.streams[stream] = [
            e for e in self.streams.get(stream, []) if self._key(e[0])[0] >= floor
        ]
        return before - len(self.streams[stream])

    def xpending_range(self, stream, groupname, min="-", max="+", count=10):  # noqa: A002
        rows = sorted(self.pending.get(stream, set()), key=self._key)
        return [{"message_id": i} for i in rows][:count]


@pytest.fixture
def fake(monkeypatch, settings) -> _Streams:
    settings.INGRESS_RAW_RETENTION_HOURS = 72
    client = _Streams()
    monkeypatch.setattr(streams, "_client", lambda: client)
    monkeypatch.setattr(registry, "registered_streams", lambda: [STREAM])
    monkeypatch.setattr("django.utils.timezone.now", lambda: NOW)
    return client


class TestTheRetentionTrim:
    def test_what_is_past_the_term_goes_from_the_stream_and_its_dlq(self, fake):
        old = fake.put(STREAM, NOW - timedelta(hours=73), _body("1"), pending=True)
        fresh = fake.put(STREAM, NOW - timedelta(hours=71), _body("1"), pending=True)
        old_dlq = fake.put(DLQ, NOW - timedelta(hours=100), _body("2"))
        fresh_dlq = fake.put(DLQ, NOW - timedelta(hours=1), _body("2"))
        assert fake.ids(STREAM) == [old, fresh]
        assert fake.ids(DLQ) == [old_dlq, fresh_dlq]

        trimmed = streams.trim_expired()

        assert trimmed == {STREAM: 1, DLQ: 1}
        assert fake.ids(STREAM) == [fresh]
        assert fake.ids(DLQ) == [fresh_dlq]

    def test_the_term_is_the_setting_not_a_constant(self, fake, settings):
        settings.INGRESS_RAW_RETENTION_HOURS = 1
        kept = fake.put(STREAM, NOW - timedelta(minutes=30), _body("1"))
        fake.put(STREAM, NOW - timedelta(hours=2), _body("1"))

        streams.trim_expired()

        assert fake.ids(STREAM) == [kept]


class TestThePersonalPurge:
    def test_only_this_persons_entries_up_to_the_request_go(self, fake):
        asked_at = NOW - timedelta(minutes=10)
        mine = fake.put(STREAM, asked_at - timedelta(minutes=5), _body("me"), pending=True)
        mine_dlq = fake.put(DLQ, asked_at - timedelta(hours=2), _body("me"))
        after_asking = fake.put(STREAM, asked_at + timedelta(minutes=1), _body("me"))
        theirs = fake.put(STREAM, asked_at - timedelta(minutes=4), _body("someone-else"))
        assert fake.ids(STREAM) == [mine, after_asking, theirs]
        assert fake.ids(DLQ) == [mine_dlq]

        result = streams.purge_person_entries(["me"], through=asked_at)

        assert result == streams.RawPurgeResult(deleted=2, unattributed=0)
        # A turn sent after asking is the person's own again and may still be
        # in flight to the consumer — it stays; so does the stranger's.
        assert fake.ids(STREAM) == [after_asking, theirs]
        assert fake.ids(DLQ) == []  # empty-assert-ok: mine_dlq asserted present above

    def test_a_body_that_does_not_parse_is_counted_and_kept(self, fake):
        asked_at = NOW
        garbled = fake.put(STREAM, asked_at - timedelta(minutes=1), {"data": "{not json"})
        unknown_type = fake.put(
            STREAM,
            asked_at - timedelta(minutes=1),
            {"data": json.dumps({"update_type": "user_added", "user": {"user_id": "me"}})},
        )

        result = streams.purge_person_entries(["me"], through=asked_at)

        # Fail-closed towards privacy means «not silently»: the count is in
        # the result (and the log); the entries leave by the retention term.
        assert result == streams.RawPurgeResult(deleted=0, unattributed=2)
        assert fake.ids(STREAM) == [garbled, unknown_type]

    def test_nobody_to_look_for_touches_nothing(self, fake):
        kept = fake.put(STREAM, NOW - timedelta(minutes=1), _body(""))

        assert streams.purge_person_entries(["", None], through=NOW) == streams.RawPurgeResult()
        assert fake.ids(STREAM) == [kept]


class TestTheCleanupCommand:
    def _seed(self, fake):
        processed = fake.put(STREAM, NOW - timedelta(hours=5), _body("1"))
        pending_fresh = fake.put(STREAM, NOW - timedelta(hours=5), _body("2"), pending=True)
        pending_old = fake.put(STREAM, NOW - timedelta(hours=80), _body("3"), pending=True)
        dlq_old = fake.put(DLQ, NOW - timedelta(hours=90), _body("4"))
        dlq_fresh = fake.put(DLQ, NOW - timedelta(hours=1), _body("5"))
        return processed, pending_fresh, pending_old, dlq_old, dlq_fresh

    def test_the_default_is_a_dry_run_that_prints_no_body(self, fake):
        processed, pending_fresh, pending_old, dlq_old, dlq_fresh = self._seed(fake)
        out = io.StringIO()

        call_command("purge_ingress_streams", stdout=out)

        text = out.getvalue()
        assert "DRY RUN" in text
        assert f"{STREAM}: total=3 pending=2 processed=1 expired=1 to_delete=2" in text
        assert f"{DLQ}: total=2 pending=0 processed=2 expired=1 to_delete=2" in text
        assert _SECRET not in text
        assert fake.ids(STREAM) == [processed, pending_fresh, pending_old]
        assert fake.ids(DLQ) == [dlq_old, dlq_fresh]

    def test_apply_removes_processed_and_expired_and_keeps_fresh_pending(self, fake):
        _processed, pending_fresh, _pending_old, _dlq_old, _dlq_fresh = self._seed(fake)
        out = io.StringIO()

        call_command("purge_ingress_streams", "--apply", stdout=out)

        assert "deleted=4 of to_delete=4" in out.getvalue()
        assert fake.ids(STREAM) == [pending_fresh]
        # The DLQ has no consumer group: every copy there is already out of
        # the pipeline, so «processed» covers all of it.
        assert fake.ids(DLQ) == []  # empty-assert-ok: two DLQ entries seeded and counted above


class TestTheExportStatesTheTerm:
    def test_the_declared_term_is_the_configured_one(self, settings):
        from django.conf import settings as live

        from apps.identity.export_coverage import NON_REGISTRY_STORES

        prose = NON_REGISTRY_STORES["redis.ingress_stream"]
        hours = live.INGRESS_RAW_RETENTION_HOURS
        assert prose.count(f"{hours} часов") + prose.count(f"{hours} часа") >= 2, prose

    def test_the_export_does_not_promise_what_the_reaper_default_does_not_do(self):
        from apps.identity.export_coverage import NON_REGISTRY_STORES

        prose = NON_REGISTRY_STORES["redis.ingress_stream"]
        # The reaper is off by default, so the DLQ move is conditional and
        # a failed entry stays in the stream until the term.
        assert "если включён разбор зависших" in prose
        assert "Обработанное сообщение удаляется из очереди сразу после обработки" in prose
