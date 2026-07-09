"""Memory consent gate (M-B3 / #1100), aligned to ADR-0011 §11.

The Memory Foundation stores user facts as `MemoryEntry` rows in
`apps/identity` (owned by the bot per Option B — see
`docs/adr/ADR-0011-user-personal-context-privacy.md`). Consent is modelled
**per sensitivity zone**, and ADR-0011 §11 is the canonical basis:

- 🟢 **GREEN** — service-contract basis. `MemoryEntry.consent_at` is **NULL**;
  no per-entry consent record is needed. The write basis is the general
  **PERSONAL_DATA** (152-ФЗ) consent the user grants at the marketplace welcome
  flow (#1046). This module's :func:`can_store_green_memory` is the check the
  concierge (M-B2 #1099) runs before writing a green fact.
- 🟡 **YELLOW** / 🔴 **RED** — consent is a per-entry `MemoryEntry.consent_at`
  timestamp (set when the user states/confirms the fact), enforced by the
  writer (`apps/identity/services/memory_writer.py`) + DB CHECK 2, NOT by a
  ConsentRecord row. They are **not actively collected in the pilot** (the
  writer fail-closes them under minor-protection until #597). Nothing to gate
  here — this module deliberately does NOT introduce ConsentRecord.memory_*
  types; that would diverge from the ADR-0011 consent model.

### Global path

Memory runs on the GLOBAL (sentinel-tenant) bot where ``current_tenant()`` is
None, so the gate reads consent via
:func:`apps.consent.services.has_global_consent` (anchored on ``bot_user``),
never the tenant-scoped :func:`has_consent` (which raises when unscoped).

### TODO — consent wording pending legal review (#947)

The GREEN basis rides the PERSONAL_DATA welcome consent, which per ADR-0011 §4.1
also covers *behavioural inference* of low-sensitivity facts. Wording that
*informs* the user of that inference is owned by legal (#947) and NOT finalised
here — this module only wires the machinery.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from apps.consent.models import ConsentRecord
from apps.consent.services import has_global_consent

if TYPE_CHECKING:
    from apps.identity.models import BotUser


def can_store_green_memory(bot_user: "BotUser") -> bool:
    """True iff ``bot_user`` may store a 🟢 green-zone memory fact.

    Green's 152-ФЗ basis is the general PERSONAL_DATA welcome consent (#1046),
    per ADR-0011 §11 — there is no separate per-field opt-in. Read on the
    tenant-less global path (memory runs there).
    """

    return has_global_consent(bot_user, ConsentRecord.ConsentType.PERSONAL_DATA.value)
