"""DRF-1581 — кнопка принудительной пересинхронизации каталога в админке.

Что здесь проверяется, по требованиям задачи:

* действие ставит **одно задание на салон**, сколько бы строк ни выбрано
  (дедуп по тенанту, образец — K9 в ``apps/kb/admin.py``);
* веера по всем салонам нет: задания уходят только за выбранные тенанты —
  анонимный лимит Ayla (30/мин, находка окна DRF-1595) веер бы не пережил;
* результат виден оператору: message с slug'ами и тем, куда смотреть.
  Молчаливая кнопка хуже отсутствующей — человек решит, что нажал, и уйдёт;
* право на действие — отдельный предикат (образец DRF-1495): «смотрящий»
  кнопку не получает;
* задача ``sync_catalog_for_tenant`` идемпотентна замком сервиса:
  повторный запуск, пока первый держит замок, возвращает ``skipped`` —
  и это видно в результате, а не тонет молча.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from django.contrib import admin, messages
from django.utils import timezone

from apps.catalog.admin import CatalogServiceAdmin
from apps.catalog.models import CatalogService
from apps.catalog.services.sync import MirrorCounts, SyncResult
from apps.catalog.tasks import sync_catalog_for_tenant
from apps.tenancy.models import Tenant


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(slug="resync-a", name="Resync A")


@pytest.fixture
def tenant_b(db) -> Tenant:
    return Tenant.objects.create(slug="resync-b", name="Resync B")


@pytest.fixture
def admin_instance() -> CatalogServiceAdmin:
    return CatalogServiceAdmin(CatalogService, admin.site)


def _make_service(tenant: Tenant, *, suffix: str = "1") -> CatalogService:
    return CatalogService.all_tenants.create(
        tenant=tenant,
        slug=f"svc-{tenant.slug}-{suffix}",
        name=f"Service {suffix}",
        external_updated_at=timezone.now(),
    )


def _result(*, ran: bool = True, skipped: bool = False, created: int = 0) -> SyncResult:
    return SyncResult(
        ran=ran,
        skipped=skipped,
        services=MirrorCounts(created=created),
    )


class TestForceResyncAction:
    def test_dedups_to_one_task_per_tenant(
        self,
        admin_instance: CatalogServiceAdmin,
        tenant: Tenant,
    ) -> None:
        # Три услуги одного салона — задание ОДНО: оператор, выделивший
        # страницу строк, не должен поставить в очередь страницу прогонов.
        for s in ("1", "2", "3"):
            _make_service(tenant, suffix=s)
        queryset = CatalogService.all_tenants.filter(tenant=tenant)

        with patch("apps.catalog.admin.sync_catalog_for_tenant") as mock_task:
            admin_instance.force_resync_selected_tenants(MagicMock(), queryset)

        assert mock_task.delay.call_count == 1
        mock_task.delay.assert_called_once_with(str(tenant.id))

    def test_multi_tenant_selection_fans_out_per_tenant(
        self,
        admin_instance: CatalogServiceAdmin,
        tenant: Tenant,
        tenant_b: Tenant,
    ) -> None:
        _make_service(tenant, suffix="a")
        _make_service(tenant_b, suffix="b")
        queryset = CatalogService.all_tenants.all()

        with patch("apps.catalog.admin.sync_catalog_for_tenant") as mock_task:
            admin_instance.force_resync_selected_tenants(MagicMock(), queryset)

        assert mock_task.delay.call_count == 2
        ids_enqueued = {call.args[0] for call in mock_task.delay.call_args_list}
        assert ids_enqueued == {str(tenant.id), str(tenant_b.id)}

    def test_empty_selection_enqueues_nothing_and_warns(
        self,
        admin_instance: CatalogServiceAdmin,
    ) -> None:
        request = MagicMock()
        with patch("apps.catalog.admin.sync_catalog_for_tenant") as mock_task:
            with patch.object(admin_instance, "message_user") as mock_message:
                admin_instance.force_resync_selected_tenants(
                    request, CatalogService.all_tenants.none()
                )

        mock_task.delay.assert_not_called()
        assert mock_message.call_count == 1
        assert mock_message.call_args.kwargs.get("level") == messages.WARNING

    def test_message_names_slugs_and_where_to_watch(
        self,
        admin_instance: CatalogServiceAdmin,
        tenant: Tenant,
    ) -> None:
        # Видимый результат — требование задачи: человек обязан увидеть,
        # что нажатие принято, за какие салоны и по какому признаку он
        # поймёт, что прогон закончился.
        _make_service(tenant, suffix="x")
        queryset = CatalogService.all_tenants.filter(tenant=tenant)

        with patch("apps.catalog.admin.sync_catalog_for_tenant"):
            with patch.object(admin_instance, "message_user") as mock_message:
                admin_instance.force_resync_selected_tenants(MagicMock(), queryset)

        assert mock_message.call_count == 1
        text = mock_message.call_args.args[1]
        assert mock_message.call_args.kwargs.get("level") == messages.SUCCESS
        assert tenant.slug in text
        assert "synced_at" in text

    def test_resync_permission_follows_model_perm(
        self,
        admin_instance: CatalogServiceAdmin,
    ) -> None:
        request = MagicMock()
        request.user.has_perm.return_value = True
        assert admin_instance.has_resync_permission(request) is True
        request.user.has_perm.assert_called_with("catalog.change_catalogservice")

        request.user.has_perm.return_value = False
        assert admin_instance.has_resync_permission(request) is False


class TestSyncCatalogForTenantTask:
    def test_runs_service_for_exactly_one_tenant(self, tenant: Tenant) -> None:
        with patch("apps.catalog.tasks.CatalogSyncService") as MockService:
            instance = MockService.return_value
            instance.run.return_value = _result(created=2)
            outcome = sync_catalog_for_tenant(str(tenant.id))

        instance.run.assert_called_once()
        assert instance.run.call_args.args[0].id == tenant.id
        assert outcome["tenant"] == tenant.slug
        assert outcome["ran"] is True
        assert outcome["services_created"] == 2

    def test_lock_skip_is_surfaced_not_silent(self, tenant: Tenant) -> None:
        # Двойное нажатие: второй прогон не встаёт в очередь за первым,
        # а честно возвращает skipped — замок сервиса и есть защита от
        # двойного запуска (требование идемпотентности).
        with patch("apps.catalog.tasks.CatalogSyncService") as MockService:
            instance = MockService.return_value
            instance.run.return_value = _result(ran=False, skipped=True)
            outcome = sync_catalog_for_tenant(str(tenant.id))

        assert outcome["ran"] is False
        assert outcome["skipped"] is True

    def test_error_is_surfaced_not_raised(self, tenant: Tenant) -> None:
        with patch("apps.catalog.tasks.CatalogSyncService") as MockService:
            instance = MockService.return_value
            instance.run.return_value = SyncResult(ran=True, error="fetch 500")
            outcome = sync_catalog_for_tenant(str(tenant.id))

        assert outcome["error"] == "fetch 500"
