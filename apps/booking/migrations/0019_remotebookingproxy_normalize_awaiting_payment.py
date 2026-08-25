"""Heal mirror rows carrying the raw wire status ``awaiting_payment`` (DRF-1145).

Found by the DRF-1034 diagnostics on 16.08 (VERIFIED on the pilot):
``RemoteBookingProxy`` held ``awaiting_payment``, a value the model's
``Status`` enum never declared — the enum carries ``pending_payment``.
Django does not validate ``choices`` at the database level, so the value
landed silently. Every read that filters by the enum then misses the row;
``DRF-1085`` and ``miniapp_api`` already filter by raw strings to work
around exactly this.

### Why a data migration and not code

The writers were audited on current ``dev`` before this migration was
written: both produce enum members now. The eventbus consumer maps the
wire value at ingest (``awaiting_payment`` → ``Status.PENDING_PAYMENT``,
``apps/eventbus/consumers/booking.py``), and the dialog path's upsert
writes ``Status.CONFIRMED``. Nothing to fix in the writers — what
remains is the stored lie, and only a data step reaches it.

### The mapping is the consumer's own

``awaiting_payment`` → ``pending_payment`` is not invented here: it is
the mapping the ingest consumer already applies to every new event.
This step simply extends that decision backwards to the rows written
before the mapping existed.

The reverse is a no-op: rows healed by this step are indistinguishable
from genuinely-created ``pending_payment`` ones, and un-applying the
migration must not resurrect a value the enum never owned.
"""

from django.db import migrations

#: The wire value Ayla emits, mapped to the enum member at ingest.
WIRE_VALUE = "awaiting_payment"
ENUM_VALUE = "pending_payment"


def normalize_awaiting_payment_rows(apps, schema_editor) -> None:
    """Rewrite stored ``awaiting_payment`` to the enum's ``pending_payment``.

    Unconditional filtered ``UPDATE`` over one table. The historical
    model is used (``apps.get_model``) so the step runs against the
    schema as of this migration — and its plain manager is unscoped by
    tenant, which matters: ``TenantScopedManager`` would silently filter
    the heal to the ambient tenant and leave other tenants' rows sick.
    """

    RemoteBookingProxy = apps.get_model("booking", "RemoteBookingProxy")
    RemoteBookingProxy.objects.filter(status=WIRE_VALUE).update(status=ENUM_VALUE)


class Migration(migrations.Migration):
    dependencies = [
        ("booking", "0018_remotebookingproxy_announcement_claims"),
    ]

    operations = [
        migrations.RunPython(
            normalize_awaiting_payment_rows,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
