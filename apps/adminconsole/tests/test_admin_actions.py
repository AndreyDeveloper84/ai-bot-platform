"""Ни одно admin-действие не достаётся тому, кому правка не положена (DRF-1495).

Django пропускает admin-действие любому, кто открыл экран, если у
действия не объявлен ``permissions=``:
``ModelAdmin._filter_actions_by_permissions`` явно добавляет в список всё,
у чего нет ``allowed_permissions``.

Из-за этого «смотрящий» — роль, у которой нет ни одного права на
запись, — мог перезапускать dead-letter события шины и ставить в очередь
платный реиндекс базы знаний. Оба действия теперь объявляют свои
``permissions=`` — со своим предикатом, потому что ``has_change_permission``
у обоих экранов безусловно False и штатное ``permissions=("change",)``
убило бы действие для всех, включая владельца.

Тест ниже сторожит не эти два действия, а правило: любое действие на
любом зарегистрированном экране обязано объявить права. Новое действие
без ``permissions=`` красит сборку.
"""

from __future__ import annotations

import secrets

import pytest
from django.contrib import admin
from django.test import RequestFactory

from apps.adminconsole.accounts import PASSWORD_ENV_VAR, grant_admin_account
from apps.adminconsole.roles import is_visible_to_roles


def _all_registered_actions() -> list[tuple[str, str, object]]:
    """``(экран, имя действия, callable)`` для всего, что зарегистрировано."""
    found = []
    for model, model_admin in admin.site._registry.items():  # noqa: SLF001
        # Экран, которого роли не видят, действиями роли не угрожает:
        # его действия достаются только суперпользователю. Так закрыт
        # django_celery_beat — чужой пакет, чьи действия мы не правим
        # (см. ROLE_DENIED_APP_LABELS).
        if not is_visible_to_roles(model):
            continue
        meta = model._meta  # noqa: SLF001
        label = f"{meta.app_label}.{meta.model_name}"
        for name in model_admin.actions or ():
            callable_ = getattr(model_admin, name, None) if isinstance(name, str) else name
            if callable_ is None:
                continue
            found.append((label, getattr(callable_, "__name__", str(name)), callable_))
    return found


def test_every_admin_action_declares_its_permissions() -> None:
    """Действие без ``permissions=`` доступно и тому, кто только смотрит."""
    actions = _all_registered_actions()

    # Присутствие: действия вообще есть. Пустой список сделал бы
    # проверку ниже утверждением ни о чём.
    assert actions, "на зарегистрированных экранах не нашлось ни одного действия"

    undeclared = sorted(
        f"{label}.{name}"
        for label, name, callable_ in actions
        if not getattr(callable_, "allowed_permissions", None)
    )
    assert undeclared == [], (
        "у этих действий нет permissions= — Django отдаст их любому, кто "
        f"открыл экран, включая роль «смотрящий»: {undeclared}"
    )


@pytest.mark.django_db
def test_viewer_gets_no_actions_where_the_editor_does(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Отрицательная половина и парная положительная на одном экране."""
    from apps.kb.admin import KbDocumentAdmin
    from apps.kb.models import KbDocument

    model_admin = KbDocumentAdmin(KbDocument, admin.site)
    factory = RequestFactory()

    def _account(username: str, role: str) -> object:
        password = secrets.token_urlsafe(24)
        monkeypatch.setenv(PASSWORD_ENV_VAR, password)
        user, _ = grant_admin_account(username=username, role=role)
        monkeypatch.delenv(PASSWORD_ENV_VAR, raising=False)
        return user

    def _actions_for(user: object) -> list[str]:
        request = factory.get("/admin/kb/kbdocument/")
        request.user = user  # type: ignore[assignment]
        return sorted(model_admin.get_actions(request))

    editor = _account("act.editor", "editor")
    viewer = _account("act.viewer", "viewer")

    # Присутствие: обе роли действительно попадают на этот экран. Без
    # этого «смотрящему действие не досталось» было бы правдой и о том,
    # кого просто не пустили, — то есть ни о чём.
    assert editor.has_perm("kb.view_kbdocument")  # type: ignore[attr-defined]
    assert viewer.has_perm("kb.view_kbdocument")  # type: ignore[attr-defined]

    # Положительная половина: правящему действие доступно — значит оно
    # существует и доходит до фильтра прав.
    assert "force_reindex_selected_tenants" in _actions_for(editor)

    # Отрицательная половина: смотрящему то же действие не досталось.
    assert "force_reindex_selected_tenants" not in _actions_for(viewer)
