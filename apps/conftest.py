"""Fixtures shared by every app's tests — opt-in, never autouse.

Lives here rather than in ``tests/conftest.py`` because that one scopes to
``tests/`` only; ``apps/**/tests`` need a common ancestor of their own.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

import pytest


@dataclass
class CatalogAdminLinkStub:
    """What the catalog would answer to ``ensure_catalog_salon_admin`` (DRF-2085).

    ``calls`` — every (tenant slug, person pk, actor_label) the core asked
    for; tests that grant ``admin`` and want to assert the catalog half was
    asked read it, tests that merely need the grant to go through ignore it.
    ``refuse_with`` — set a reason to make the next call refuse by name.
    """

    calls: list[tuple[str, str, str]] = field(default_factory=list)
    refuse_with: str | None = None
    created: bool = True


@pytest.fixture
def catalog_admin_link_stub(monkeypatch) -> CatalogAdminLinkStub:
    """Replace the catalog half of ``role=admin`` with a canned answer.

    Opt in per file with ``pytestmark = pytest.mark.usefixtures(
    "catalog_admin_link_stub")`` — the seam is named at the top of the file,
    so a reader knows the catalog was NOT part of what that file proves. The
    real client is exercised in ``apps/identity/tests/test_salon_admin_link_2085.py``.
    """
    from apps.identity.services import salon_admin_link

    stub = CatalogAdminLinkStub()

    def _fake(*, tenant, bot_user, actor_label, http_client=None):
        stub.calls.append((tenant.slug, str(bot_user.pk), actor_label))
        if stub.refuse_with is not None:
            raise salon_admin_link.CatalogAdminLinkRefused(
                stub.refuse_with,
                correlation_id="stub-corr",
                tenant=tenant,
                bot_user=bot_user,
                actor_label=actor_label,
            )
        return salon_admin_link.CatalogAdminLinkOutcome(
            ayla_user_id=uuid.uuid4(),
            relationship_id=uuid.uuid4(),
            created=stub.created,
            correlation_id="stub-corr",
            idempotency_key=salon_admin_link.idempotency_key_for(tenant.id, str(bot_user.pk)),
        )

    monkeypatch.setattr(salon_admin_link, "ensure_catalog_salon_admin", _fake)
    return stub
