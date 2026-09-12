"""Ayla identity resolution — the single deterministic path (DRF-1035).

Before this module, ``BotUser.ayla_user_id`` had **no writer in
production**. The blank-fill machinery existed in
:mod:`apps.identity.services.resolver` and was covered by tests, but both
MAX handlers and the Telegram handler call the resolvers without the
argument, so the field stayed ``NULL`` for everyone who was not
provisioned by hand. Booking, persistent memory, global consent reads,
152-ФЗ export/delete and all five inbound eventbus consumers key off that
field — so a ``NULL`` there is not «one broken feature», it is «this
person does not exist as a subject» for six subsystems at once.

:func:`ensure_ayla_link` is the missing writer, and the ONLY place the bot
resolves identity. Per the DRF-1035 owner ruling (J-O3,
*identity-on-first-dependent-action*) it is called **before the first
action that genuinely needs a persistent Ayla subject** — never on «hello».

### Who calls this (and who deliberately does not)

Callers (P0):

* booking create — :func:`apps.skills.booking.provider.get_booking_provider`
* persistent memory WRITE —
  :func:`apps.orchestrator.memory.personal_context.record_explicit_green_facts`
* personal-data export + delete —
  :func:`apps.identity.services.privacy._resolve_person_link`

Deliberate non-callers:

* **Memory reads / surfacing / memory commands.** Memory is keyed on
  ``ayla_user_id``; while it is ``NULL`` nothing can ever have been
  written, so there is provably nothing to read. Resolving in order to
  read an empty set would mint a permanent identity for «привет» — the
  exact over-reach J-O3 rejects. Worse for commands: creating a permanent
  identity *in response to «забудь всё»* is backwards.
* **Consent grant / revoke.** ``ConsentRecord`` hangs off the ``bot_user``
  FK, so ownership is fully determined by local identity —
  ``record_global_consent`` and the live revoke path
  (``withdraw_personal_data_for_bot_users``) need no UUID. This was
  already settled by the DRF-956 / T-05 ruling; see
  ``apps/identity/services/privacy.py`` step 3.

### Tenant semantics: fan-out, and why it is provably correct

The resolved id is written to **every** ``BotUser`` row sharing the
``(channel, channel_user_id)`` pair, across tenants. Ayla's client subject
is global, which is not an assumption but a structural fact: the external
id ``bot:{channel}:{channel_user_id}`` carries no tenant, and Ayla resolves
it with ``get_or_create(username=<that string>)`` against a globally UNIQUE
column. Both of a person's rows therefore map to one and the same Ayla
user — a tenant-scoped ``client_id`` is impossible by construction. Ayla's
own model says the same in words: «a customer is multi-provider by
default … there is no "primary tenant" for customers»
(``users.TenantUserRelationship``).

Writing only the requesting row would recreate a defect
``apps/identity/services/privacy.py`` already has to work around: the
global path stamps the sentinel shell while the Mini App reads the
``MAX_BOT_TENANT_SLUG`` shell, so «a linked person looks unlinked».

### Invariants

* **Never overwrites — with exactly one exception (DRF-1790, 12.09.2026):
  proxy → real.** A stored key whose sort is the catalog's isolated proxy
  (``ayla_user_id_is_proxy=True``) is replaced by a REAL key
  (``is_proxy=False``) the catalog answers for the same external id, and
  the replacement is published as ``identity.rebound`` (old, new, trigger)
  and written to the audit log. Every other disagreement — real → anything
  different, proxy → a different proxy, unknown sort → a different key —
  is surfaced (``identity.ayla_link.conflict``), never silently reconciled:
  ``privacy._resolve_person_link`` treats 2+ distinct ids as a fail-closed
  conflict, so this module must not manufacture one.

  Why the exception exists: the catalog binds proxy → real ONCE (operator
  linking §6, later OTP), and from then on answers with the real key. Until
  this day the bot kept the proxy key forever — so after B-2.1/B-2.2 every
  subject-bound call it made carried a URL subject that no longer matched
  its own header (403 on export, personal context, the profile card), and
  ``user.profile.updated`` for the real key found no row at all. A linked
  person went dark. The transition is one-way in the catalog, so accepting
  it here is not a guess — it is following the authority for this id.
* **Never raises.** Every failure degrades to ``None`` and the caller
  keeps its pre-existing unlinked behaviour.
* **Idempotent for a real key.** A stored key of KNOWN real sort
  short-circuits before any network call. A stored proxy key (or one of
  unknown sort) does NOT: each dependent action asks the catalog again,
  until the answer is real — one HTTP call per dependent action for an
  unlinked person, which J-O3 permits (dependent action, never «hello»).
  A row of unknown sort whose key the catalog confirms learns its sort in
  place, so the re-ask converges for legacy rows too.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import TYPE_CHECKING

from apps.events.services import emit
from apps.identity.models import BotUser

if TYPE_CHECKING:  # pragma: no cover
    pass

logger = logging.getLogger(__name__)


def _as_uuid(value: object) -> uuid.UUID | None:
    """Coerce a stored/returned id to ``UUID``; ``None`` when unusable.

    SQLite stores UUIDField as text, Postgres as native uuid — the model
    layer normally hands back a ``UUID`` either way, but a defensive
    coercion keeps comparisons total instead of accidentally comparing
    ``str`` to ``UUID`` (which is silently always False).
    """
    if value is None or value == "":
        return None
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        return None


def _persist(
    bot_user: "BotUser", resolved: uuid.UUID, *, is_proxy: bool, trigger: str = "unknown"
) -> tuple[int, int]:
    """Blank-fill ``resolved`` across the person's shells, with its sort — and
    move a known-proxy key to a real one (DRF-1790).

    Returns ``(rows_updated, rows_conflicting)``. Rows that already hold
    ``resolved`` are neither updated nor counted as conflicts (a row of
    unknown sort learns its sort in the same pass). A row holding a
    different key is REBOUND when — and only when — its stored sort is the
    proxy and ``resolved`` is real; it is a conflict otherwise.

    DRF-1649 — ``is_proxy`` travels in the **same** ``save()`` as the key. A
    sort that could be written separately would add a fourth state, "key
    present, sort pending", and a consumer meeting it would have to guess
    exactly the thing this column exists to stop it guessing.
    """
    shells = BotUser.all_tenants.filter(
        channel=bot_user.channel,
        channel_user_id=bot_user.channel_user_id,
    )

    rows_updated = 0
    rows_conflicting = 0
    rows_rebound = 0
    for shell in shells:
        current = _as_uuid(shell.ayla_user_id)
        if current is None:
            shell.ayla_user_id = resolved
            shell.ayla_user_id_is_proxy = is_proxy
            # One save, both columns. See the docstring: two saves would make
            # "key present, sort pending" reachable.
            shell.save(update_fields=["ayla_user_id", "ayla_user_id_is_proxy"])
            rows_updated += 1
        elif current == resolved:
            if shell.ayla_user_id_is_proxy is None:
                # Legacy row (pre DRF-1649): the key is confirmed, the sort
                # was unknown — learn it, so the re-ask below converges.
                shell.ayla_user_id_is_proxy = is_proxy
                shell.save(update_fields=["ayla_user_id_is_proxy"])
        elif shell.ayla_user_id_is_proxy is True and not is_proxy:
            # DRF-1790 — the one permitted overwrite: the catalog bound this
            # person's proxy to a real account and now answers with the real
            # key. Old and new travel together in the event and the audit
            # row; the ids are keys, not personal values.
            old = str(current)
            shell.ayla_user_id = resolved
            shell.ayla_user_id_is_proxy = False
            shell.save(update_fields=["ayla_user_id", "ayla_user_id_is_proxy"])
            rows_rebound += 1
            _record_rebound(shell, old=old, new=str(resolved), trigger=trigger)
        else:
            # Never overwrite anything else. Two distinct ids on one person is
            # an identity inconsistency privacy._resolve_person_link
            # fail-closes on; our job is to make it visible, not to pick a
            # winner — and «unknown sort» is not «proxy» (see the module
            # docstring: guessing proxy would let a real identity be moved).
            rows_conflicting += 1

    # Keep the in-memory instance consistent with what we just wrote, so
    # the caller (and anything holding this object for the rest of the
    # turn) sees the link without a refetch.
    if _as_uuid(bot_user.ayla_user_id) is None or (
        bot_user.ayla_user_id_is_proxy is True and not is_proxy
    ):
        bot_user.ayla_user_id = resolved
        bot_user.ayla_user_id_is_proxy = is_proxy
    elif _as_uuid(bot_user.ayla_user_id) == resolved and bot_user.ayla_user_id_is_proxy is None:
        bot_user.ayla_user_id_is_proxy = is_proxy

    # Owner 11.09 §2 (S2-2): an identity link is the second criterion of the
    # matching rule, so the moment it is written the person's salon shells
    # stop being SHADOW / UNRESOLVED. The client-contour shell is LINKED by
    # construction and is stamped the same way for the same reason.
    if rows_updated or rows_rebound:
        from apps.identity.services.salon_customer import advance_to_linked

        advance_to_linked(bot_user.channel, bot_user.channel_user_id)

    return rows_updated + rows_rebound, rows_conflicting


def _record_rebound(shell: "BotUser", *, old: str, new: str, trigger: str) -> None:
    """Publish and audit one proxy → real move. Keys only — never personal values."""

    logger.info(
        "identity.rebound bot_user=%s tenant=%s trigger=%s — proxy key replaced by the "
        "real one the catalog now answers for this identity",
        shell.id,
        shell.tenant_id,
        trigger,
    )
    emit(
        "identity.rebound",
        distinct_id=str(shell.id),
        properties={
            "trigger": trigger,
            "channel": shell.channel,
            "old_ayla_user_id": old,
            "new_ayla_user_id": new,
        },
    )
    try:
        from apps.audit.services import write_audit

        write_audit(
            "identity.rebound",
            target="BotUser",
            target_id=shell.id,
            payload={
                "trigger": trigger,
                "channel": shell.channel,
                "old_ayla_user_id": old,
                "new_ayla_user_id": new,
            },
        )
    except Exception:  # noqa: BLE001 — the rebound itself is already written; audit is best-effort here
        logger.exception("identity.rebound.audit_failed bot_user=%s", shell.id)


def persist_resolved_identity(bot_user: "BotUser", identity, *, trigger: str) -> None:
    """Write an identity the caller ALREADY resolved, by the same rule as
    :func:`ensure_ayla_link` (blank-fill, proxy → real rebound, conflicts
    surfaced). For callers that hold a fresh catalog answer — the solo link
    attempt — so the person's rows follow the binding without a second HTTP
    call. Never raises.
    """

    try:
        rows_updated, rows_conflicting = _persist(
            bot_user, identity.ayla_user_id, is_proxy=identity.is_proxy, trigger=trigger
        )
    except Exception:  # noqa: BLE001 — persistence must not break the caller's flow
        logger.exception(
            "identity.ayla_link.persist_failed bot_user=%s trigger=%s", bot_user.id, trigger
        )
        return
    if rows_conflicting:
        logger.error(
            "identity.ayla_link.conflict bot_user=%s trigger=%s rows_conflicting=%d — "
            "existing link differs from resolved subject; NOT overwriting",
            bot_user.id,
            trigger,
            rows_conflicting,
        )


def ensure_ayla_link(bot_user: "BotUser", *, trigger: str = "unknown") -> uuid.UUID | None:
    """Return this person's canonical Ayla user id, resolving it if needed.

    Args:
      bot_user: the acting ``BotUser``.
      trigger: which capability asked (``booking`` / ``memory_write`` /
        ``personal_data``) — observability only, never affects behaviour.

    Returns the ``UUID`` on success, or ``None`` when Ayla could not be
    reached or answered unusably AND no key was stored yet (a stored key is
    returned as-is when the re-ask fails — DRF-1790). ``None`` means «stay
    unlinked for now»:
    every caller already has a correct unlinked path (booking raises
    ``ayla_client_id_missing`` → ``AdminTask`` → operator notification),
    and identity resolution must never abort a user's turn.
    """
    # Lazy import: apps.integrations.ayla imports Django settings at module
    # scope, and this module is imported from apps.identity.services, which
    # participates in app loading.
    from apps.integrations.ayla.identity_client import (
        IdentityResolveError,
        resolve_identity,
    )
    from apps.integrations.ayla.user_proxy import external_user_id_for

    existing = _as_uuid(getattr(bot_user, "ayla_user_id", None))
    # A cache hit only for a key of KNOWN real sort (DRF-1790). A proxy key —
    # or one whose sort is unknown — is asked again on every dependent
    # action: the catalog may have bound this person since, and a stale
    # proxy key makes every subject-bound call fail. See the module docstring.
    if existing is not None and getattr(bot_user, "ayla_user_id_is_proxy", None) is False:
        emit(
            "identity.ayla_link.cache_hit",
            distinct_id=str(bot_user.id),
            properties={"trigger": trigger, "channel": bot_user.channel},
        )
        return existing

    external_user_id = external_user_id_for(bot_user)
    emit(
        "identity.ayla_link.requested",
        distinct_id=str(bot_user.id),
        properties={"trigger": trigger, "channel": bot_user.channel},
    )

    started = time.monotonic()
    try:
        identity = resolve_identity(external_user_id)
    except IdentityResolveError as exc:
        # Degrade, never raise. `str(exc)` is a fixed reason string built
        # by the client (`network: ReadTimeout`, `server: HTTP 502`, …) —
        # no user content, no secrets.
        logger.warning(
            "identity.ayla_link.failed bot_user=%s trigger=%s reason=%s",
            bot_user.id,
            trigger,
            exc,
        )
        emit(
            "identity.ayla_link.failed",
            distinct_id=str(bot_user.id),
            properties={"trigger": trigger, "reason": str(exc)},
        )
        # DRF-1790: a re-ask that fails must not un-link a person. The
        # stored key (proxy, or of unknown sort) is stale at worst; «stay
        # unlinked for now» is for people who never had one.
        return existing
    except Exception:  # noqa: BLE001 — identity must never break a turn
        logger.exception(
            "identity.ayla_link.unexpected bot_user=%s trigger=%s", bot_user.id, trigger
        )
        emit(
            "identity.ayla_link.failed",
            distinct_id=str(bot_user.id),
            properties={"trigger": trigger, "reason": "unexpected"},
        )
        return existing

    latency_ms = int((time.monotonic() - started) * 1000)
    emit(
        "identity.ayla_link.resolved",
        distinct_id=str(bot_user.id),
        properties={
            "trigger": trigger,
            "is_proxy": identity.is_proxy,
            "latency_ms": latency_ms,
        },
    )

    try:
        rows_updated, rows_conflicting = _persist(
            bot_user, identity.ayla_user_id, is_proxy=identity.is_proxy, trigger=trigger
        )
    except Exception:  # noqa: BLE001 — a persistence failure must not lose the turn
        # The id itself is still valid and usable for THIS action; the
        # next call will simply resolve again. Returning it is strictly
        # better than failing the user's booking over a write error.
        logger.exception("identity.ayla_link.persist_failed bot_user=%s", bot_user.id)
        emit(
            "identity.ayla_link.failed",
            distinct_id=str(bot_user.id),
            properties={"trigger": trigger, "reason": "persist_failed"},
        )
        return identity.ayla_user_id

    emit(
        "identity.ayla_link.persisted",
        distinct_id=str(bot_user.id),
        properties={"trigger": trigger, "rows_updated": rows_updated},
    )

    if rows_conflicting:
        # Count only — never the ids themselves (same discipline as
        # privacy._resolve_person_link's conflict log).
        logger.error(
            "identity.ayla_link.conflict bot_user=%s rows_conflicting=%d — "
            "existing link differs from resolved subject; NOT overwriting",
            bot_user.id,
            rows_conflicting,
        )
        emit(
            "identity.ayla_link.conflict",
            distinct_id=str(bot_user.id),
            properties={"trigger": trigger, "rows_conflicting": rows_conflicting},
        )

    return identity.ayla_user_id
