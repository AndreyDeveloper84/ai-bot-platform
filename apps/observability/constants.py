"""Shared observability constants — single source of truth.

Sprint 8 code review (P1-2): the agreement thresholds were duplicated
across the Telegram digest (apps/observability/tasks.py) and the
dashboard view (apps/observability/views.py). Two copies of the same
0.95 / 0.85 boundary meant changing the Sprint 8 exit-gate bar (or
the corresponding alert palette) required edits in two places, with
the bug window being the time between them.

This module is the canonical home. Both consumers import from here.
"""

from __future__ import annotations

# Sprint 8 exit-gate floor for production strict-scope flip (F1 / DRF-730).
# ShadowDeltaSnapshot.intent_agreement ≥ this value = green / no action.
AGREEMENT_THRESHOLD_GREEN: float = 0.95

# Degraded band. Between AMBER and GREEN = ⚠ band in the digest +
# yellow badge in the dashboard. Operator should investigate but no
# automated rollback yet.
AGREEMENT_THRESHOLD_AMBER: float = 0.85

# Below AMBER = 🚨 / red band. Operator pages; runbooks/rollback-procedure.md
# Cycle 1 trigger.
