"""Envelope §2, payload §3, PII §6 validators.

Three independent passes returning a list of error strings each — empty
list means valid. They never raise; the caller (emit) decides what to
do with the errors.

Distinct severity:
  - envelope/payload errors    → warn-and-still-insert (telemetry must
                                  never drop; matches apps.events.emit
                                  contract)
  - PII §6 violation           → REJECT. PII in event payload is a
                                  legal-grade issue; we refuse to write
                                  the row.
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Any

from apps.eventbus.envelope import Envelope
from apps.eventbus.ulid import is_valid as is_valid_ulid
from apps.eventbus.vocabulary import is_canonical, required_keys

_VALID_ACTOR_TYPES = {"ai", "human", "system", "external"}
_VALID_ACTOR_ROLES = {"owner", "admin", "master", "customer", "ai_assistant"}

# event-taxonomy.md §6 forbidden field names (case-insensitive substring).
# A `data` or `metadata` key matching one of these patterns triggers REJECT.
_PII_KEY_PATTERNS = (
    "phone",
    "email",
    "raw_text",
    "raw_message",
    "client_name",
    "customer_name",
    "full_name",
    "first_name",
    "last_name",
    "credit_card",
    "card_number",
    "cvv",
)

# Heuristics for PII values. We only check string leaves; nested dicts /
# lists are walked.
#
# Phone regex requires the ``+`` international prefix. Without the
# prefix, raw digit strings collide with ISO timestamps, UUIDs, IDs.
# Phones without ``+`` rely on the key-name check (``_PII_KEY_PATTERNS``)
# — payloads name their phone fields ``customer_phone`` etc., which
# the key check catches.
_PHONE_RE = re.compile(r"\+\d[\d\s\-\(\)]{7,}")
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w.-]+\.\w{2,}")


def validate_envelope(env: Envelope) -> list[str]:
    """Check §2 envelope shape. Returns error strings; empty = OK."""

    errors: list[str] = []

    if not is_valid_ulid(env.event_id):
        errors.append(f"event_id must be a 26-char ULID, got {env.event_id!r}")

    if not is_canonical(env.event_name):
        errors.append(f"unknown event_name: {env.event_name!r}")

    if not _is_semver(env.event_version):
        errors.append(f"event_version must be SemVer, got {env.event_version!r}")

    if env.occurred_at.tzinfo is None:
        errors.append("occurred_at must be timezone-aware (UTC)")

    if env.actor.type not in _VALID_ACTOR_TYPES:
        errors.append(
            f"actor.type must be one of {sorted(_VALID_ACTOR_TYPES)}, got {env.actor.type!r}"
        )

    if env.actor.role is not None and env.actor.role not in _VALID_ACTOR_ROLES:
        errors.append(
            f"actor.role must be one of {sorted(_VALID_ACTOR_ROLES)} or None, got {env.actor.role!r}"
        )

    if env.tenant_id is not None and not isinstance(env.tenant_id, uuid.UUID):
        errors.append(f"tenant_id must be UUID or None, got {type(env.tenant_id).__name__}")

    if not isinstance(env.data, dict):
        errors.append(f"data must be a dict, got {type(env.data).__name__}")
    else:
        try:
            json.dumps(env.data)
        except (TypeError, ValueError) as exc:
            errors.append(f"data not JSON-serialisable: {exc}")

    return errors


def validate_payload(event_name: str, data: dict[str, Any]) -> list[str]:
    """Check §3 catalog required keys for ``event_name``.

    Unknown event_name → empty list (envelope validator already
    surfaces that). Known event with missing required key → error.
    """

    required = required_keys(event_name)
    if not required:
        return []
    missing = required - set(data.keys())
    if missing:
        return [f"missing required keys for {event_name!r}: {sorted(missing)}"]
    return []


def lint_pii(payload: dict[str, Any]) -> list[str]:
    """§6 PII forbidden-field lint. Walks nested dicts/lists.

    Returns list of violations; empty = clean.
    """

    violations: list[str] = []
    _walk_pii(payload, path="data", violations=violations)
    return violations


def _walk_pii(node: Any, *, path: str, violations: list[str]) -> None:
    if isinstance(node, dict):
        for k, v in node.items():
            key_lower = str(k).lower()
            for pat in _PII_KEY_PATTERNS:
                if pat in key_lower:
                    violations.append(f"PII §6: forbidden key {path}.{k!r} (matches {pat!r})")
                    break  # one report per key
            _walk_pii(v, path=f"{path}.{k}", violations=violations)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            _walk_pii(v, path=f"{path}[{i}]", violations=violations)
    elif isinstance(node, str):
        if _PHONE_RE.search(node):
            violations.append(f"PII §6: phone-shaped string at {path!r}")
        if _EMAIL_RE.search(node):
            violations.append(f"PII §6: email-shaped string at {path!r}")


def _is_semver(v: str) -> bool:
    # MAJOR.MINOR or MAJOR.MINOR.PATCH — taxonomy.md §7
    parts = v.split(".")
    if len(parts) not in (2, 3):
        return False
    return all(p.isdigit() for p in parts)
