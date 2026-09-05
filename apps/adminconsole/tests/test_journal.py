"""Правка через админку оставляет след с автором (DRF-1495).

Без этого админка над живыми данными пилота — источник расхождений,
которые потом нечем объяснить.
"""

from __future__ import annotations

import secrets

import pytest
from django.test import Client

from apps.adminconsole.accounts import PASSWORD_ENV_VAR, grant_admin_account
from apps.audit.models import AuditLog
from apps.experiments.models import Experiment
from apps.tenancy.models import Tenant


@pytest.fixture
def tenant(db) -> Tenant:  # noqa: ANN001
    return Tenant.objects.create(slug="drf1495-journal", name="DRF-1495 journal")


@pytest.fixture
def experiment(tenant: Tenant) -> Experiment:
    return Experiment.objects.create(
        tenant=tenant,
        name="journal-subject",
        hypothesis="Правка этого объекта обязана попасть в журнал.",
        status=Experiment.Status.DRAFT,
        primary_kpi="handoff_rate",
        variants=[{"name": "control", "weight": 100}],
    )


def _editor_client(monkeypatch: pytest.MonkeyPatch, username: str) -> Client:
    password = secrets.token_urlsafe(24)
    monkeypatch.setenv(PASSWORD_ENV_VAR, password)
    grant_admin_account(username=username, role="editor")
    monkeypatch.delenv(PASSWORD_ENV_VAR, raising=False)
    client = Client()
    assert client.login(username=username, password=password)
    return client


@pytest.mark.django_db
def test_admin_change_lands_in_the_journal_with_its_author(
    monkeypatch: pytest.MonkeyPatch,
    experiment: Experiment,
) -> None:
    client = _editor_client(monkeypatch, "j.editor")
    url = f"/admin/experiments/experiment/{experiment.pk}/change/"

    response = client.post(
        url,
        data={
            "tenant": str(experiment.tenant_id),
            "name": experiment.name,
            "hypothesis": experiment.hypothesis,
            "status": Experiment.Status.RUNNING,
            "primary_kpi": experiment.primary_kpi,
            "guardrails": "[]",
            "variants": '[{"name": "control", "weight": 100}]',
            "started_at_0": "",
            "started_at_1": "",
            "ended_at_0": "",
            "ended_at_1": "",
        },
    )

    # Присутствие: правка действительно прошла. Без неё «в журнале есть
    # строка» проверялось бы на пустоте.
    assert response.status_code == 302
    experiment.refresh_from_db()
    assert experiment.status == Experiment.Status.RUNNING

    rows = list(AuditLog.all_tenants.filter(action="admin.object.updated"))
    assert len(rows) == 1, "правка через админку не оставила ровно одной строки журнала"

    row = rows[0]
    assert row.target == "experiments.experiment"
    assert str(row.target_id) == str(experiment.pk)
    # Автор — по имени (читается сразу) и по id (связывается с записью).
    assert row.payload["actor_username"] == "j.editor"
    assert row.payload["actor_pk"] is not None
    assert row.payload["source"] == "django_admin"
    # Django перечисляет имена изменённых полей — по ним видно, что трогали.
    assert "change_message" in row.payload


@pytest.mark.django_db
def test_journal_names_the_field_that_changed_and_never_its_value(
    monkeypatch: pytest.MonkeyPatch,
    experiment: Experiment,
) -> None:
    """Журнал говорит «меняли hypothesis», а не что там теперь написано."""
    client = _editor_client(monkeypatch, "s.editor")
    secret_looking_text = f"tok-{secrets.token_urlsafe(16)}"

    response = client.post(
        f"/admin/experiments/experiment/{experiment.pk}/change/",
        data={
            "tenant": str(experiment.tenant_id),
            "name": experiment.name,
            "hypothesis": secret_looking_text,
            "status": experiment.status,
            "primary_kpi": experiment.primary_kpi,
            "guardrails": "[]",
            "variants": '[{"name": "control", "weight": 100}]',
            "started_at_0": "",
            "started_at_1": "",
            "ended_at_0": "",
            "ended_at_1": "",
        },
    )
    assert response.status_code == 302
    experiment.refresh_from_db()
    assert experiment.hypothesis == secret_looking_text  # присутствие: значение записалось

    row = AuditLog.all_tenants.get(action="admin.object.updated")
    serialised = str(row.payload)
    # Django собирает change_message из verbose_name и переводит его —
    # сравниваем без регистра, имя поля от этого не меняется.
    assert "hypothesis" in serialised.lower(), "журнал не назвал изменённое поле"
    assert secret_looking_text not in serialised, "журнал утащил значение поля"


@pytest.mark.django_db
def test_account_grant_and_revoke_are_journalled(monkeypatch: pytest.MonkeyPatch) -> None:
    from apps.adminconsole.accounts import revoke_admin_account

    monkeypatch.setenv(PASSWORD_ENV_VAR, secrets.token_urlsafe(24))
    grant_admin_account(username="l.editor", role="editor", actor_username="owner")
    monkeypatch.delenv(PASSWORD_ENV_VAR, raising=False)

    granted = AuditLog.all_tenants.get(action="admin.account.granted")
    assert granted.payload["username"] == "l.editor"
    assert granted.payload["role"] == "editor"
    assert granted.payload["actor_username"] == "owner"
    # Пароля в журнале нет — записан только факт, что он задан.
    assert granted.payload["password_set"] is True
    assert [key for key in granted.payload if "password" in key.lower()] == ["password_set"]

    revoke_admin_account(username="l.editor", actor_username="owner")

    revoked = AuditLog.all_tenants.get(action="admin.account.revoked")
    assert revoked.payload["username"] == "l.editor"
    assert revoked.payload["groups_removed"] == ["ayla-editor"]


@pytest.mark.django_db
def test_bulk_delete_lands_in_the_journal_row_per_object(
    monkeypatch: pytest.MonkeyPatch,
    tenant: Tenant,
) -> None:
    """Самая разрушительная операция — и та, на которой журнал молчал.

    Django пишет ``LogEntry`` через ``LogEntryManager.log_actions``, и на
    одном объекте это ``instance.save()``, а на нескольких —
    ``bulk_create``, который не шлёт ``post_save``. Первая версия задачи
    подписывалась на сигнал и теряла ровно групповое удаление: чем больше
    строк выделили, тем тише был журнал.
    """
    victims = [
        Experiment.objects.create(
            tenant=tenant,
            name=f"bulk-victim-{index}",
            hypothesis="Удаляется пачкой.",
            status=Experiment.Status.DRAFT,
            primary_kpi="handoff_rate",
            variants=[{"name": "control", "weight": 100}],
        )
        for index in range(3)
    ]
    client = _editor_client(monkeypatch, "b.editor")

    response = client.post(
        "/admin/experiments/experiment/",
        data={
            "action": "delete_selected",
            "_selected_action": [str(item.pk) for item in victims],
            "post": "yes",
        },
        follow=True,
    )

    # Присутствие: удаление действительно произошло. Без него «строк в
    # журнале три» проверялось бы на операции, которой не было.
    assert response.status_code == 200
    assert Experiment.all_tenants.filter(name__startswith="bulk-victim-").count() == 0

    rows = list(AuditLog.all_tenants.filter(action="admin.object.deleted"))
    assert len(rows) == len(victims), (
        f"групповое удаление {len(victims)} строк оставило в журнале {len(rows)}"
    )
    assert {row.payload["actor_username"] for row in rows} == {"b.editor"}
    assert {row.payload["object_id"] for row in rows} == {str(item.pk) for item in victims}


@pytest.mark.django_db
def test_single_delete_lands_exactly_once(
    monkeypatch: pytest.MonkeyPatch,
    experiment: Experiment,
) -> None:
    """Путь на одном объекте другой (``instance.save()``) — и не двоится."""
    client = _editor_client(monkeypatch, "d.editor")

    response = client.post(
        f"/admin/experiments/experiment/{experiment.pk}/delete/",
        data={"post": "yes"},
        follow=True,
    )

    assert response.status_code == 200
    assert not Experiment.all_tenants.filter(pk=experiment.pk).exists()

    rows = list(AuditLog.all_tenants.filter(action="admin.object.deleted"))
    assert len(rows) == 1, f"одно удаление оставило {len(rows)} строк журнала"
    assert rows[0].payload["actor_username"] == "d.editor"
