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
