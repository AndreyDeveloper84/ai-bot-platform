"""DRF-2984 B2b — архив и возврат мастера из Mini App салона доходят до каталога.

Узлы идут через настоящие двери (``execute_deactivation``, ``reactivate_master``)
со стабом клиента каталога: предмет — что дверь сообщает каталогу и чьим
субъектом, а не форма HTTP-запроса.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest

from apps.admin_api.services.master_deactivation import execute_deactivation, reactivate_master
from apps.admin_api.tests.conftest import make_master
from apps.catalog.models import CatalogMaster

pytestmark = pytest.mark.django_db


class _Catalog:
    def __init__(self, exc: Exception | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.exc = exc

    def set_specialist_archived(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        if self.exc is not None:
            raise self.exc
        return {"archived": kwargs["archived"]}


@pytest.fixture
def catalog(monkeypatch) -> _Catalog:
    stub = _Catalog()
    monkeypatch.setattr(
        "apps.integrations.ayla.booking_client.get_ayla_booking_client", lambda: stub
    )
    return stub


@pytest.fixture
def sync_on(settings) -> None:
    settings.CATALOG_ARCHIVE_SYNC_ENABLED = True


def _deactivate(master: CatalogMaster, actor) -> None:
    execute_deactivation(
        master,
        plan=[],
        reason="ушла из салона",
        notify_reassigned_masters=False,
        custom_template=None,
        actor=actor,
        actor_role="owner",
    )


def test_s1_deactivation_tells_the_catalog_under_the_owner(
    sync_on, catalog, master, owner_bot_user, django_capture_on_commit_callbacks
):
    with django_capture_on_commit_callbacks(execute=True):
        _deactivate(master, owner_bot_user)

    assert catalog.calls == [
        {
            "specialist_id": str(master.catalog_specialist_id),
            "external_user_id": f"bot:{owner_bot_user.channel}:{owner_bot_user.channel_user_id}",
            "archived": True,
        }
    ]


def test_s2_reactivation_tells_it_back(
    sync_on, catalog, tenant, owner_bot_user, django_capture_on_commit_callbacks
):
    gone = make_master(tenant, name="Вернулась", is_active=False, external_id=81)
    CatalogMaster.all_tenants.filter(pk=gone.pk).update(archived_at="2026-10-01T00:00:00Z")
    gone.refresh_from_db()

    with django_capture_on_commit_callbacks(execute=True):
        reactivate_master(gone, notify_master=False, actor=owner_bot_user, actor_role="owner")

    assert [c["archived"] for c in catalog.calls] == [False]


def test_s3_switch_off_keeps_the_catalog_unaware(
    catalog, master, owner_bot_user, django_capture_on_commit_callbacks
):
    """Тумблер выключен (умолчание) — поведение до B2b: каталог об архиве не знает."""

    with django_capture_on_commit_callbacks(execute=True):
        _deactivate(master, owner_bot_user)

    master.refresh_from_db()
    assert master.archived_at is not None  # архив в боте случился
    assert catalog.calls == []  # empty-assert-ok: присутствие архива проверено строкой выше


def test_s4_a_catalog_failure_does_not_undo_the_salon_decision(
    sync_on, monkeypatch, master, owner_bot_user, django_capture_on_commit_callbacks, caplog
):
    failing = _Catalog(exc=RuntimeError("catalog down"))
    monkeypatch.setattr(
        "apps.integrations.ayla.booking_client.get_ayla_booking_client", lambda: failing
    )

    with (
        caplog.at_level(logging.WARNING, logger="apps.catalog.services.archive_sync"),
        django_capture_on_commit_callbacks(execute=True),
    ):
        _deactivate(master, owner_bot_user)

    master.refresh_from_db()
    assert master.archived_at is not None
    assert len(failing.calls) == 1
    assert any("catalog.archive_sync.failed" in r.getMessage() for r in caplog.records)


def test_s5_nothing_is_sent_when_the_decision_rolls_back(
    sync_on, catalog, master, owner_bot_user, django_capture_on_commit_callbacks
):
    """Откат точки сохранения отбрасывает её on_commit — каталогу сообщать нечего."""

    from django.db import transaction

    with django_capture_on_commit_callbacks(execute=True) as callbacks:
        with pytest.raises(RuntimeError), transaction.atomic():
            _deactivate(master, owner_bot_user)
            raise RuntimeError("откат")

    assert callbacks == []  # empty-assert-ok: s1 доказывает, что без отката колбэк есть

    assert catalog.calls == []  # empty-assert-ok: вызов ждёт коммита, коммита не было
