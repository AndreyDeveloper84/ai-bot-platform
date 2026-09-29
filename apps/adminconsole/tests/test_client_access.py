"""Пропуск и журнал доступа — правила без экрана (DRF-1514).

Экранные проверки живут в ``test_client_scope.py``. Здесь — сами
правила: что считается причиной, откуда берутся клиент и салон, сколько
живёт пропуск и что попадает в журнал.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.adminconsole.client_access import (
    ClientAccessError,
    DEFAULT_TTL_MINUTES,
    check_reason,
    client_label,
    grant_for_client,
    granted_client_ids,
    is_unrestricted,
    open_access,
    record_denial,
    ttl,
)
from apps.adminconsole.client_scope import (
    HIDDEN_FIELDS,
    QUEUE_SCREEN,
    SCOPED_SCREENS,
    install_client_data_scope,
)
from apps.adminconsole.models import (
    ClientDataAccessGrant,
    ClientDataAccessLog,
    validate_reason,
)
from apps.adminconsole.tests.conftest import make_client_thread

pytestmark = pytest.mark.django_db

GOOD_REASON = "разбираю жалобу на запись от 5 сентября"


@pytest.fixture
def actor(db):  # noqa: ANN001, ANN201
    return get_user_model().objects.create_user(
        username="i.smotryashiy",
        password="x",  # pragma: allowlist secret
        is_staff=True,
    )


# ── причина ───────────────────────────────────────────────────────────


def test_a_real_reason_passes() -> None:
    """Присутствие: проверка вообще умеет говорить «да»."""
    assert check_reason(f"  {GOOD_REASON}  ") == GOOD_REASON


@pytest.mark.parametrize("reason", ["", "   ", "\n\t", "надо", "тест"])
def test_a_reason_that_explains_nothing_is_refused(reason: str) -> None:
    with pytest.raises(ClientAccessError):
        check_reason(reason)


def test_the_model_field_refuses_the_same_reasons() -> None:
    """Правило одно, а проверок две: служба и поле модели."""
    validate_reason(GOOD_REASON)  # присутствие
    with pytest.raises(ValidationError):
        validate_reason("надо")


# ── откуда берутся клиент и салон ─────────────────────────────────────


def test_the_grant_takes_client_and_salon_from_the_appeal(actor, salon) -> None:  # noqa: ANN001
    bot_user, _, _, task = make_client_thread(
        salon, channel_user_id="c1", display_name="Аня", text="крашу волосы"
    )

    grant = open_access(actor=actor, admin_task=task, reason=GOOD_REASON)

    assert grant.client_id == bot_user.id
    assert grant.client_label == "Аня"
    assert grant.tenant_slug == salon.slug
    assert grant.admin_task_id == task.id
    assert grant.actor_username == "i.smotryashiy"
    assert grant.reason == GOOD_REASON


def test_an_appeal_is_required(actor) -> None:  # noqa: ANN001
    with pytest.raises(ClientAccessError):
        open_access(actor=actor, admin_task=None, reason=GOOD_REASON)


def test_the_client_label_never_carries_the_phone(salon) -> None:  # noqa: ANN001
    """DRF-1039: телефон не показываем — в том числе в подписи пропуска."""
    bot_user, _, _, _ = make_client_thread(
        salon,
        channel_user_id="c1",
        display_name="",
        text="крашу волосы",
        phone="+79995550101",
    )
    bot_user.client_name = ""
    bot_user.save(update_fields=["client_name"])

    label = client_label(bot_user)

    assert label == "c1", "подпись должна падать на id канала, а не на телефон"
    assert "+7999" not in label


# ── срок ──────────────────────────────────────────────────────────────


def test_the_grant_expires(actor, salon, settings) -> None:  # noqa: ANN001
    _, _, _, task = make_client_thread(
        salon, channel_user_id="c1", display_name="Аня", text="крашу волосы"
    )
    grant = open_access(actor=actor, admin_task=task, reason=GOOD_REASON)

    assert granted_client_ids(actor) == [grant.client_id]  # присутствие
    assert grant_for_client(actor, grant.client_id) is not None

    ClientDataAccessGrant.objects.update(expires_at=timezone.now() - timedelta(seconds=1))

    assert granted_client_ids(actor) == []
    assert grant_for_client(actor, grant.client_id) is None


@pytest.mark.parametrize("value", [None, "", "не число", 0, -5])
def test_a_broken_ttl_setting_is_not_a_reason_to_open_forever(settings, value) -> None:  # noqa: ANN001
    settings.ADMINCONSOLE_CLIENT_ACCESS_TTL_MINUTES = value

    assert ttl() == timedelta(minutes=DEFAULT_TTL_MINUTES)


def test_the_ttl_setting_is_honoured(settings) -> None:  # noqa: ANN001
    settings.ADMINCONSOLE_CLIENT_ACCESS_TTL_MINUTES = 15

    assert ttl() == timedelta(minutes=15)


# ── кому не писаны ────────────────────────────────────────────────────


def test_only_the_owner_walks_past(actor) -> None:  # noqa: ANN001
    owner = get_user_model().objects.create_superuser(
        username="vladelec",
        email="o@example.com",
        password="x",  # pragma: allowlist secret
    )

    assert is_unrestricted(owner)  # присутствие
    assert not is_unrestricted(actor)
    assert not is_unrestricted(None)


# ── журнал ────────────────────────────────────────────────────────────


def test_a_denial_is_journaled_without_a_grant(actor) -> None:  # noqa: ANN001
    record_denial(
        actor=actor,
        screen="conversations.message",
        detail="Пропуска нет.",
    )

    row = ClientDataAccessLog.objects.get()
    assert row.outcome == ClientDataAccessLog.Outcome.DENIED
    assert row.actor_username == "i.smotryashiy"
    assert row.actor_pk == str(actor.pk)
    assert row.client_id is None
    assert row.reason == ""
    assert row.detail == "Пропуска нет."


def test_the_journal_keeps_no_foreign_keys() -> None:
    """Журнал переживает отзыв учётки, удаление клиента и чистку очереди."""
    from django.db import models

    relations = [
        f.name for f in ClientDataAccessLog._meta.get_fields() if isinstance(f, models.ForeignKey)
    ]

    assert relations == [], f"журнал обзавёлся ссылками и теперь смертен: {relations}"


# ── установка политики ────────────────────────────────────────────────


def test_the_policy_landed_on_the_screens_it_names() -> None:
    """Промах мимо всех экранов выглядел бы как «всё разрешено»."""
    from django.contrib import admin

    registered = {
        f"{model._meta.app_label}.{model._meta.model_name}" for model in admin.site._registry
    }
    expected = (set(SCOPED_SCREENS) | {QUEUE_SCREEN} | set(HIDDEN_FIELDS)) & registered

    assert expected, "ни один из перечисленных экранов не зарегистрирован — список устарел"
    for label in expected:
        model_admin = next(
            ma
            for model, ma in admin.site._registry.items()
            if f"{model._meta.app_label}.{model._meta.model_name}" == label
        )
        if label in SCOPED_SCREENS:
            assert getattr(model_admin.get_queryset, "_ayla_client_scope", False), label
        if label == QUEUE_SCREEN:
            assert getattr(model_admin, "_ayla_client_queue_scope", False), label
        if label in HIDDEN_FIELDS:
            assert getattr(model_admin.get_fieldsets, "_ayla_client_hidden_fields", False), label


def test_installing_twice_wraps_nothing_twice() -> None:
    """``ready()`` вызывается повторно при перезагрузке реестра в тестах."""
    assert install_client_data_scope() == []
