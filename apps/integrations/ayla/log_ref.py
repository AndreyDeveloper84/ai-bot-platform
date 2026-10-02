"""A marker for a person in a log line — instead of the person's channel id (DRF-2187).

Ayla clients know the person only as ``external_user_id`` —
``bot:{channel}:{channel_user_id}`` (:mod:`apps.integrations.ayla.user_proxy`).
That string IS the person's id in the channel, and the rule for logs is «only a
pseudonymous key, never the channel id» (DRF-2009,
``apps/observability/tests/test_logs_carry_no_channel_identity.py``). The
clients have no internal key in hand, so their refusal lines printed the raw
id: 27 logging calls across four clients on 02.10.

:func:`external_user_log_ref` gives those lines what they were using the id
for — telling one person's failures from another's — without the id:

* **keyed.** A plain hash of ``bot:max:<digits>`` can be reversed by walking
  the digits. The marker is an HMAC under a key derived from
  ``SECRET_KEY``, so the log alone does not give the id back;
* **stable** for one key: the same person gets the same marker on every
  line and in every client, so lines still correlate. A rotated
  ``SECRET_KEY`` starts a new series — markers are for reading a log, not
  for joining tables;
* **short** — 12 hex characters: enough to tell people apart in a log, not
  an identifier anything else keys on.

No Django model is imported here on purpose: the clients are imported early
and from many places.
"""

from __future__ import annotations

import hashlib
import hmac

from django.conf import settings

_PURPOSE = b"ayla.external_user.log_ref.v1"
_ABSENT = "-"


def external_user_log_ref(external_user_id: str | None) -> str:
    """12 hex characters standing for ``external_user_id`` in a log line.

    ``"-"`` when there is no id to stand for — a line about nobody must not
    look like a line about somebody.
    """
    if not external_user_id:
        return _ABSENT
    key = hmac.new(str(settings.SECRET_KEY).encode("utf-8"), _PURPOSE, hashlib.sha256).digest()
    return hmac.new(key, str(external_user_id).encode("utf-8"), hashlib.sha256).hexdigest()[:12]
