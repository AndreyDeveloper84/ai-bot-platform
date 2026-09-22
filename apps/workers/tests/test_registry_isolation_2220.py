"""DRF-2220 — a test that empties the handler registry must put it back.

The registry is process-global and filled once, at app ready. Fixtures in
four worker test files used to clear it on the way in and on the way out,
so every later test in the same process saw no handlers. The ingress purge
of «забудь всё» reads the registry to know which streams to scan; with it
empty it scanned none and reported nothing deleted. Reproduced on dev
b1ea3dd9 with one pair in one process: ``test_consumer.py::TestEmptyRegistry``
then ``test_forget_all_matrix.py::...[redis.ingress_stream]`` — red.
"""

from __future__ import annotations

from apps.ingress.streams import raw_streams
from apps.workers.registry import emptied_registry_for_tests, register, registered_streams


def test_the_registry_is_empty_inside_and_restored_after():
    production = registered_streams()
    assert production, "the channel handlers are registered at app ready"

    with emptied_registry_for_tests():
        assert registered_streams() == []  # empty-assert-ok: production presence asserted above

        @register("ingress:probe")
        class _Probe:  # noqa: N801 — throwaway handler
            pass

        assert registered_streams() == ["ingress:probe"]

    assert registered_streams() == production


def test_the_purge_sees_every_production_stream():
    """What «забудь всё» scans is what the channels registered, DLQs included."""

    streams = raw_streams()
    for stream in registered_streams():
        assert stream in streams
        assert f"{stream}:dlq" in streams


class TestAnEmptyRegistryIsNotAClean:
    """A process without the channel handlers must not report the streams as checked.

    The purge takes its stream list from the registry. Empty, it would scan
    nothing and answer «0 deleted» — a clean bill for work it never did.
    """

    def test_the_purge_refuses_rather_than_scans_nothing(self):
        from datetime import datetime, timezone

        import pytest

        from apps.ingress.streams import NoIngressStreams, purge_person_entries

        with emptied_registry_for_tests():
            with pytest.raises(NoIngressStreams):
                purge_person_entries(["someone"], through=datetime.now(timezone.utc))

    def test_the_erasure_says_not_checked_and_logs_why(self, db, caplog):
        import logging
        import uuid

        from django.utils import timezone as dj_timezone

        from apps.conversations.erasure import anonymize_dialogue
        from apps.conversations.models import ArchivedMessage
        from apps.identity.models import BotUser
        from apps.tenancy.models import Tenant

        tenant = Tenant.objects.create(slug=f"reg-{uuid.uuid4().hex[:8]}", name="Registry 2220")
        person = BotUser.all_tenants.create(
            tenant=tenant, channel="max", channel_user_id="u-reg-2220", chat_id="u-reg-2220"
        )

        with emptied_registry_for_tests(), caplog.at_level(logging.ERROR):
            result = anonymize_dialogue(
                [person.id], through=dj_timezone.now(), reason=ArchivedMessage.Reason.FORGET_ALL
            )

        assert result.raw_streams_checked is False
        assert "no ingress streams registered" in caplog.text
