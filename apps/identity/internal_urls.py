"""Внутренние ручки бота для каталога — ``/api/v1/internal/privacy/``.

Сегодня одна: ``account-deletion/`` — бот-половина удаления аккаунта по
просьбе исполнителя каталога (§7 D3, DRF-1725). Подпись — HMAC ingest'а.
"""

from __future__ import annotations

from django.urls import path

from apps.identity.account_deletion_views import InternalAccountDeletionView

app_name = "identity_internal"

urlpatterns = [
    path("account-deletion/", InternalAccountDeletionView.as_view(), name="account_deletion"),
]
