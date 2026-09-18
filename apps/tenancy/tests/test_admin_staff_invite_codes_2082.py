"""Админка бота: выдать / отозвать код приглашения (DRF-2082, PR-2).

Условия листа, по узлу:

* **гейт тем же правом**: без `platform_operations` действия «Отозвать код» нет
  и view «Выдать код» — 403; с правом — есть и 200 (пара);
* **выдать код — тем же путём, что `issue_staff_invite`**: код показан
  оператору **один раз** на странице результата, в БД только хеш, повторный
  просмотр невозможен по построению (страница результата — ответ на POST,
  GET по тому же адресу кода не несёт); `LogEntry.change_message` и аудит —
  без кода и хеша; роль мастера без мастера — отказ формы, строки нет;
* **отозвать код** — строка `revoked_at`, аудит `surface=django_admin`; ввод
  кода после этого — отказ по имени (сервисный узел); использованный — отказ
  по имени и без изменений.

Стражи #1802 (`code_hash` нигде) — остаются, прогоняются рядом.
"""

from __future__ import annotations

import re
import secrets
from datetime import datetime, timedelta, timezone as dt_timezone
from typing import Any

import pytest
from django.contrib import admin
from django.contrib.admin.models import LogEntry
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.messages import get_messages
from django.test import Client, RequestFactory
from django.urls import reverse
from django.utils import timezone

from apps.audit.models import AuditLog
from apps.catalog.models import CatalogMaster
from apps.events.vocabulary import STAFF_INVITE_ISSUED, STAFF_INVITE_REVOKED
from apps.identity.services.staff_invites import CODE_ALPHABET, CODE_PREFIX, normalize_code
from apps.tenancy.admin import StaffInviteAdmin
from apps.tenancy.models import StaffInvite, Tenant

pytestmark = pytest.mark.django_db

CHANGELIST = "admin:tenancy_staffinvite_changelist"
ISSUE_URL = "admin:tenancy_staffinvite_issue"
ACTION_REVOKE = "revoke_staff_invite"
CODE_RE = re.compile(rf"{CODE_PREFIX}-[{CODE_ALPHABET}]{{4}}")


def _user(username: str, *, ops: bool):  # noqa: ANN202
    password = secrets.token_urlsafe(24)
    user = get_user_model().objects.create_user(username=username, password=password, is_staff=True)
    if ops:
        user.user_permissions.add(
            Permission.objects.get(
                codename="platform_operations", content_type__app_label="tenancy"
            )
        )
    return user, password


def _client(username: str, *, ops: bool) -> tuple[Client, Any]:
    user, password = _user(username, ops=ops)
    client = Client()
    assert client.login(username=username, password=password)
    return client, user


@pytest.fixture
def salon() -> Tenant:
    return Tenant.objects.create(slug="codes-admin-a", name="Салон кодов")


def _invite(salon: Tenant, **extra: Any) -> StaffInvite:
    return StaffInvite.all_tenants.create(
        tenant=salon,
        role=StaffInvite.Role.ADMIN,
        code_hash=f"ci-fake-{secrets.token_hex(8)}",  # pragma: allowlist secret
        expires_at=timezone.now() + timedelta(days=7),
        **extra,
    )


def _actions_for(user) -> set[str]:  # noqa: ANN001
    request = RequestFactory().get(reverse(CHANGELIST))
    request.user = user
    model_admin = admin.site._registry[StaffInvite]  # noqa: SLF001
    assert isinstance(model_admin, StaffInviteAdmin)
    return set(model_admin.get_actions(request))


def _messages(response) -> list[tuple[int, str]]:  # noqa: ANN001
    return [(m.level, str(m.message)) for m in get_messages(response.wsgi_request)]


# ─── право ─────────────────────────────────────────────────────────────────


def test_a1_without_platform_operations_no_revoke_action_and_issue_view_is_403(salon) -> None:
    client, user = _client("codes-no-ops", ops=False)
    with_ops, _ = _user("codes-with-ops-pair", ops=True)
    assert ACTION_REVOKE in _actions_for(with_ops)  # присутствие впереди отсутствия
    # empty-assert-ok: пара строкой выше и узел a2
    assert ACTION_REVOKE not in _actions_for(user)
    assert client.get(reverse(ISSUE_URL)).status_code == 403


def test_a2_with_platform_operations_action_and_issue_view_are_there(salon) -> None:
    client, user = _client("codes-ops", ops=True)
    assert ACTION_REVOKE in _actions_for(user)
    assert client.get(reverse(ISSUE_URL)).status_code == 200


# ─── выдать код ────────────────────────────────────────────────────────────


def test_i1_code_is_shown_once_and_only_its_hash_is_stored(salon) -> None:
    client, user = _client("ops-i1", ops=True)

    response = client.post(
        reverse(ISSUE_URL),
        {"tenant": str(salon.pk), "role": "admin", "note": "для Ани", "apply": "1"},
    )

    assert response.status_code == 200
    body = response.content.decode("utf-8")
    codes = CODE_RE.findall(body)
    assert len(codes) == 1, codes  # ровно один раз на странице результата
    code = codes[0]
    invite = StaffInvite.all_tenants.get(tenant=salon)
    assert invite.role == "admin" and invite.note == "для Ани"
    assert invite.code_hash and normalize_code(code) not in invite.code_hash
    # empty-assert-ok: присутствие выше — тот же body несёт ровно один код
    assert invite.code_hash not in body  # хеш на страницу не уезжает
    # Повторный просмотр невозможен по построению: GET того же адреса кода не несёт.
    again = client.get(reverse(ISSUE_URL)).content.decode("utf-8")
    # empty-assert-ok: тот же шаблон нашёл 1 код на странице результата выше
    assert CODE_RE.findall(again) == []
    # Журнал и аудит — без кода и хеша.
    entry = LogEntry.objects.filter(user=user).order_by("-id").first()
    assert entry is not None
    assert (
        code not in entry.get_change_message()
        and invite.code_hash not in entry.get_change_message()
    )
    rows = list(AuditLog.all_tenants.filter(action=STAFF_INVITE_ISSUED, target_id=invite.pk))
    assert len(rows) == 1
    assert rows[0].payload["surface"] == "django_admin"
    assert rows[0].payload["actor_label"] == f"django_admin:user={user.pk}"
    assert code not in str(rows[0].payload) and invite.code_hash not in str(rows[0].payload)


def test_i2_master_role_without_a_master_is_refused_and_nothing_is_written(salon) -> None:
    client, _ = _client("ops-i2", ops=True)

    response = client.post(
        reverse(ISSUE_URL), {"tenant": str(salon.pk), "role": "master", "apply": "1"}, follow=True
    )

    assert not StaffInvite.all_tenants.filter(tenant=salon).exists()
    assert 40 in [level for level, _ in _messages(response)]


def test_i3_master_role_with_a_master_of_this_salon_issues_a_linked_code(salon) -> None:
    client, _ = _client("ops-i3", ops=True)
    master = CatalogMaster.all_tenants.create(
        tenant=salon,
        external_id=-77,
        external_updated_at=datetime(2026, 9, 1, tzinfo=dt_timezone.utc),
        name="Мастер",
    )

    response = client.post(
        reverse(ISSUE_URL),
        {"tenant": str(salon.pk), "role": "master", "catalog_master": str(master.pk), "apply": "1"},
    )

    assert response.status_code == 200
    invite = StaffInvite.all_tenants.get(tenant=salon)
    assert invite.role == "master" and invite.catalog_master_id == master.pk
    assert len(CODE_RE.findall(response.content.decode("utf-8"))) == 1


# ─── отозвать код ──────────────────────────────────────────────────────────


def test_v1_revoke_action_sets_revoked_at_and_journals(salon) -> None:
    client, user = _client("ops-v1", ops=True)
    invite = _invite(salon)

    response = client.post(
        reverse(CHANGELIST),
        {
            "action": ACTION_REVOKE,
            "_selected_action": [str(invite.pk)],
            "apply": "1",
            "reason": "не тому",
        },
        follow=True,
    )

    invite.refresh_from_db()
    assert invite.revoked_at is not None
    rows = list(AuditLog.all_tenants.filter(action=STAFF_INVITE_REVOKED, target_id=invite.pk))
    assert len(rows) == 1
    assert rows[0].payload["surface"] == "django_admin"
    assert rows[0].payload["actor_label"] == f"django_admin:user={user.pk}"
    assert 25 in {level for level, _ in _messages(response)}
    model_admin = admin.site._registry[StaffInvite]  # noqa: SLF001
    assert isinstance(model_admin, StaffInviteAdmin)
    assert model_admin.invite_state(invite).startswith("Отозвано")


def test_v2_used_code_is_not_revoked_and_says_why(salon) -> None:
    client, _ = _client("ops-v2", ops=True)
    invite = _invite(salon, used_at=timezone.now())

    response = client.post(
        reverse(CHANGELIST),
        {
            "action": ACTION_REVOKE,
            "_selected_action": [str(invite.pk)],
            "apply": "1",
            "reason": "x",
        },
        follow=True,
    )

    invite.refresh_from_db()
    assert invite.revoked_at is None
    errors = [text for level, text in _messages(response) if level == 40]
    assert len(errors) == 1
    assert StaffInviteAdmin.CODE_WORDS["invite_already_used"] in errors[0]


def test_v3_the_change_form_now_says_how_a_code_is_revoked(salon) -> None:
    """Описание карточки #1802 говорило «отозвать нечем» — с этим PR это неправда;
    докстринг, переживший дефект, — тот же класс ошибки, что и дефект."""
    client, _ = _client("ops-v3", ops=True)
    invite = _invite(salon)

    body = client.get(reverse("admin:tenancy_staffinvite_change", args=[invite.pk])).content.decode(
        "utf-8"
    )

    assert "Салон кодов" in body
    assert "Отозвать код" in body
    assert "нечем" not in body
