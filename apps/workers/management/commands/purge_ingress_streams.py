"""One-off cleanup of raw webhook bodies already in the ingress streams (DRF-2220).

Before DRF-2220 the consumer only XACK'd: every processed entry stayed in its
``ingress:<channel>`` stream for good, with the message text, the sender's
name and any shared contact. The code no longer leaves them behind; this
command removes what it left behind before.

### What it removes

Per stream (every registered ``ingress:*`` stream and its ``:dlq``):

* **processed** — entries no longer pending in the consumer group, i.e.
  XACK'd and never deleted. Nothing reads them again.
* **expired** — entries older than ``INGRESS_RAW_RETENTION_HOURS``, pending
  or not. The same rule the hourly trim applies from now on.

Pending entries inside the window are kept: they are the reaper's and the
operator's to triage.

### Usage

::

    python manage.py purge_ingress_streams            # dry run: counts only
    python manage.py purge_ingress_streams --apply    # delete

The dry run is the default because this deletes data. It prints counts and id
ranges, never an entry body. ``--apply`` on the stand or in production only on
the owner's word.
"""

from __future__ import annotations

from typing import Any, cast

from django.core.management.base import BaseCommand

from apps.ingress import streams as ingress_streams
from apps.ingress.streams import DEFAULT_GROUP_NAME, DLQ_SUFFIX

#: XRANGE / XPENDING page and XDEL batch size.
_PAGE = 500


def _all_ids(client: Any, stream: str) -> list[str]:
    ids: list[str] = []
    low = "-"
    while True:
        page = client.xrange(stream, min=low, max="+", count=_PAGE)
        ids.extend(entry_id for entry_id, _fields in page)
        if len(page) < _PAGE:
            return ids
        low = f"({page[-1][0]}"


def _pending_ids(client: Any, stream: str) -> set[str]:
    """Ids still pending in the consumer group. A DLQ stream has no group."""

    if stream.endswith(DLQ_SUFFIX):
        return set()
    pending: set[str] = set()
    low = "-"
    while True:
        try:
            page = client.xpending_range(stream, DEFAULT_GROUP_NAME, min=low, max="+", count=_PAGE)
        except Exception as exc:  # noqa: BLE001 — NOGROUP: nothing is pending
            if "NOGROUP" in str(exc):
                return pending
            raise
        pending.update(str(row["message_id"]) for row in page)
        if len(page) < _PAGE:
            return pending
        low = f"({page[-1]['message_id']}"


def _older_than(entry_id: str, cutoff: str) -> bool:
    return int(entry_id.split("-", 1)[0]) < int(cutoff)


class Command(BaseCommand):
    help = (
        "Remove raw webhook bodies left in the ingress streams: processed entries "
        "and entries past INGRESS_RAW_RETENTION_HOURS. Dry run unless --apply."
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Delete. Without it the command only counts (the default).",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        apply = bool(options["apply"])
        client = ingress_streams._client()
        cutoff = ingress_streams.retention_cutoff_id()
        self.stdout.write(
            f"{'APPLY' if apply else 'DRY RUN'} — retention cutoff id {cutoff} "
            f"(INGRESS_RAW_RETENTION_HOURS); counts and id ranges only, no bodies."
        )
        total_doomed = 0
        total_deleted = 0
        for stream in ingress_streams.raw_streams():
            ids = _all_ids(client, stream)
            pending = _pending_ids(client, stream)
            processed = [i for i in ids if i not in pending]
            expired = [i for i in ids if _older_than(i, cutoff)]
            doomed = sorted(
                set(processed) | set(expired), key=lambda i: tuple(map(int, i.split("-")))
            )
            span = f"{doomed[0]} … {doomed[-1]}" if doomed else "—"
            self.stdout.write(
                f"{stream}: total={len(ids)} pending={len(pending)} "
                f"processed={len(processed)} expired={len(expired)} "
                f"to_delete={len(doomed)} range={span}"
            )
            total_doomed += len(doomed)
            if apply:
                for start in range(0, len(doomed), _PAGE):
                    batch = doomed[start : start + _PAGE]
                    total_deleted += int(cast(int, client.xdel(stream, *batch)))
        if apply:
            self.stdout.write(f"deleted={total_deleted} of to_delete={total_doomed}")
        else:
            self.stdout.write(f"to_delete={total_doomed} — nothing deleted; rerun with --apply")
