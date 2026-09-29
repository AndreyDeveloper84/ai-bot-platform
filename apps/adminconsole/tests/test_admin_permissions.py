"""Смотрящий не правит, правящий правит — на одних и тех же данных (DRF-1495).

Пара обязательна целиком. «Смотрящему нельзя» само по себе доказывает
только то, что кто-то получил 403, — 403 одинаково выдаётся и когда
права настроены верно, и когда форма не собралась, и когда объекта нет.
Поэтому обе половины ходят по **одному и тому же** объекту, одним и тем
же телом запроса, и меняют одно и то же поле.
"""

from __future__ import annotations

import secrets

import pytest
from django.test import Client

from apps.adminconsole.accounts import PASSWORD_ENV_VAR, grant_admin_account
from apps.experiments.models import Experiment
from apps.tenancy.models import Tenant

CHANGE_URL = "/admin/experiments/experiment/{pk}/change/"
HANDOFF_QUEUE_URL = "/admin/handoff/admintask/"


@pytest.fixture
def tenant(db) -> Tenant:  # noqa: ANN001
    return Tenant.objects.create(slug="drf1495-perm", name="DRF-1495 perms")


@pytest.fixture
def experiment(tenant: Tenant) -> Experiment:
    return Experiment.objects.create(
        tenant=tenant,
        name="router-threshold-v1",
        hypothesis="Порог роутера выше даёт меньше ложных передач оператору.",
        status=Experiment.Status.DRAFT,
        primary_kpi="handoff_rate",
        variants=[{"name": "control", "weight": 50}, {"name": "v2", "weight": 50}],
    )


def _login_as(monkeypatch: pytest.MonkeyPatch, username: str, role: str) -> Client:
    """Завести запись через настоящий механизм выдачи и войти под ней."""
    password = secrets.token_urlsafe(24)
    monkeypatch.setenv(PASSWORD_ENV_VAR, password)
    grant_admin_account(username=username, role=role)
    monkeypatch.delenv(PASSWORD_ENV_VAR, raising=False)

    client = Client()
    assert client.login(username=username, password=password), (
        f"{username!r} не смог войти — дальнейшие проверки прав были бы о пустом месте"
    )
    return client


def _change_payload(experiment: Experiment, *, primary_kpi: str) -> dict[str, str]:
    """Тело формы изменения эксперимента. Одно и то же для обеих половин."""
    return {
        "tenant": str(experiment.tenant_id),
        "name": experiment.name,
        "hypothesis": experiment.hypothesis,
        "status": experiment.status,
        "primary_kpi": primary_kpi,
        "guardrails": "[]",
        "variants": '[{"name": "control", "weight": 50}, {"name": "v2", "weight": 50}]',
        "started_at_0": "",
        "started_at_1": "",
        "ended_at_0": "",
        "ended_at_1": "",
    }


@pytest.mark.django_db
def test_viewer_can_read_the_handoff_queue(monkeypatch: pytest.MonkeyPatch) -> None:
    """Смотрящий смотрит. Иначе 403 ниже означал бы «просто не пускают»."""
    client = _login_as(monkeypatch, "i.viewer", "viewer")

    response = client.get(HANDOFF_QUEUE_URL)

    assert response.status_code == 200


@pytest.mark.django_db
def test_viewer_cannot_change_and_editor_can_change_the_same_object(
    monkeypatch: pytest.MonkeyPatch,
    experiment: Experiment,
) -> None:
    """Отрицательная и парная положительная — на одном объекте, одним телом."""
    before = experiment.primary_kpi
    payload = _change_payload(experiment, primary_kpi="booking_conversion_rate")
    url = CHANGE_URL.format(pk=experiment.pk)

    # --- присутствие: объект есть и правка была бы видна ---
    assert before == "handoff_rate"
    assert payload["primary_kpi"] != before

    # --- отрицательная половина: смотрящий ---
    viewer_client = _login_as(monkeypatch, "n.viewer", "viewer")
    # Читать объект смотрящий может — значит 403 ниже про правку, а не про доступ.
    assert viewer_client.get(url).status_code == 200

    viewer_response = viewer_client.post(url, data=payload, follow=False)

    assert viewer_response.status_code == 403
    experiment.refresh_from_db()
    assert experiment.primary_kpi == before, "смотрящий изменил данные"

    # --- парная положительная половина: правящий, тот же объект, то же тело ---
    editor_client = _login_as(monkeypatch, "n.editor", "editor")

    editor_response = editor_client.post(url, data=payload, follow=False)

    assert editor_response.status_code == 302, (
        "правящий не сохранил — форма не собралась, и тогда 403 выше "
        f"ничего не доказывает. Ответ: {editor_response.status_code}"
    )
    experiment.refresh_from_db()
    assert experiment.primary_kpi == "booking_conversion_rate"


@pytest.mark.django_db
def test_neither_role_reaches_the_accounts_screen(monkeypatch: pytest.MonkeyPatch) -> None:
    """Правящий не должен уметь выдать себе права."""
    for username, role in (("a.viewer", "viewer"), ("a.editor", "editor")):
        client = _login_as(monkeypatch, username, role)

        # Присутствие: этот аккаунт вообще ходит по админке.
        assert client.get("/admin/").status_code == 200

        assert client.get("/admin/auth/user/").status_code == 403
        assert client.get("/admin/auth/group/").status_code == 403


@pytest.mark.django_db
def test_revoked_account_stops_working_immediately(monkeypatch: pytest.MonkeyPatch) -> None:
    """Отзыв гасит и вход, и уже открытую сессию."""
    from apps.adminconsole.accounts import revoke_admin_account

    client = _login_as(monkeypatch, "r.editor", "editor")
    # Присутствие: до отзыва сессия рабочая.
    assert client.get("/admin/").status_code == 200

    revoke_admin_account(username="r.editor")

    # Django редиректит неаутентифицированного на форму входа.
    assert client.get("/admin/").status_code == 302
