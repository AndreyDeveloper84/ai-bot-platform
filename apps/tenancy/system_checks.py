"""Django system checks for tenant-scoping invariants.

Tenancy retro «missing abstract base»: a model author who declares
``tenant = models.ForeignKey('tenancy.Tenant', ...)`` is expected to
also wire:

  * ``objects = TenantScopedManager()``  — scopes reads to current tenant.
  * ``all_tenants = models.Manager()``    — explicit escape hatch.

Neither omission fails CI today; the row writes/reads silently and
the cross-tenant invariant is violated invisibly. A short Django
``checks`` integration walks installed models on startup and raises
W900 / W901 warnings (per-CI-policy) for any tenant-bearing model
that's missing the canonical manager pair.

Why warnings and not errors:
  * The platform has third-party-ish models (e.g. catalog
    snapshots, audit cousins) that ARE tenant-bound by FK but
    intentionally use a different manager strategy (system / global
    rows mixed in with tenant rows).
  * Existing models pre-date the convention; flipping to errors
    breaks ``migrate`` on every dev box overnight.
  * Operators / reviewers see the warnings via ``./manage.py check``
    in CI and can either fix the model OR add a documented
    suppression with `models.IGNORE_TENANCY_W900 = True` (an opt-out
    sentinel attribute on the model class).

The check is registered at TenancyConfig.ready() time.
"""

from __future__ import annotations

from typing import Any

from django.apps import apps as django_apps
from django.core import checks


# Sentinel attribute name a model can set to skip the check, e.g.::
#
#     class WeirdGlobalModel(models.Model):
#         tenant = models.ForeignKey(...)
#         # Justified deviation — see docs/adr/...
#         _IGNORE_TENANT_MANAGER_CHECK = True
#
_IGNORE_FLAG = "_IGNORE_TENANT_MANAGER_CHECK"


@checks.register(checks.Tags.models)
def check_tenant_scoped_managers(
    app_configs: Any | None = None,  # noqa: ANN401 — Django check signature
    **kwargs: Any,
) -> list[checks.CheckMessage]:
    """W900 / W901 warnings for tenant-bearing models without proper managers.

    Walks every concrete (non-abstract, non-proxy) model that has a
    ``tenant`` field pointing at ``tenancy.Tenant`` and verifies:

      * Its default manager (``_default_manager``) is a
        ``TenantScopedManager`` instance — W900.
      * It exposes an ``all_tenants`` Manager attribute — W901.

    Models that opt out via ``_IGNORE_TENANT_MANAGER_CHECK = True``
    are skipped (with no message — silent intentional deviation).
    """

    # Avoid an import-time cycle: TenantScopedManager pulls from the
    # tenancy.context ContextVar machinery, which is fine, but defer
    # so this module can be imported by the AppConfig before app
    # registry settles.
    from apps.tenancy.managers import TenantScopedManager
    from django.db.models import Manager

    messages: list[checks.CheckMessage] = []

    for model in django_apps.get_models():
        if model._meta.abstract or model._meta.proxy:
            continue
        if getattr(model, _IGNORE_FLAG, False):
            continue

        tenant_field = next(
            (f for f in model._meta.get_fields() if f.name == "tenant"),
            None,
        )
        if tenant_field is None:
            continue
        # Confirm the FK points at Tenant (avoid false positives for a
        # field named ``tenant`` that happens to FK something else).
        related = getattr(tenant_field, "related_model", None)
        if related is None or related._meta.label != "tenancy.Tenant":
            continue

        # The model has a tenant FK — verify the manager pair.
        default_manager = getattr(model, "_default_manager", None)
        if not isinstance(default_manager, TenantScopedManager):
            messages.append(
                checks.Warning(
                    f"{model._meta.label}: tenant FK present but "
                    f"`_default_manager` is "
                    f"{type(default_manager).__name__}, not "
                    "TenantScopedManager. Reads will not be scoped "
                    "to current_tenant().",
                    hint=(
                        "Declare `objects = TenantScopedManager()` + "
                        "`all_tenants = models.Manager()` on the model "
                        "(canonical pair — see apps/audit/models.py for "
                        "the reference shape), OR set "
                        "`_IGNORE_TENANT_MANAGER_CHECK = True` with a "
                        "documented justification (worked examples in "
                        "apps/eventbus/models.py and apps/tools/models.py)."
                    ),
                    obj=model,
                    id="tenancy.W900",
                )
            )

        all_tenants = getattr(model, "all_tenants", None)
        if not isinstance(all_tenants, Manager):
            messages.append(
                checks.Warning(
                    f"{model._meta.label}: tenant FK present but no "
                    "`all_tenants` Manager escape hatch is defined.",
                    hint=(
                        "Declare `all_tenants = models.Manager()` "
                        "alongside `objects = TenantScopedManager()` "
                        "so admin / replay code can read across "
                        "tenants explicitly (see apps/audit/models.py "
                        "for the canonical shape)."
                    ),
                    obj=model,
                    id="tenancy.W901",
                )
            )

    return messages
