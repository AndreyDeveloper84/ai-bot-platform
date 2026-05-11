"""Initial Tenant table (DRF-417 / Sprint 1 / A1).

Ported from Ayla `origin/dev:tenants/migrations/0001_initial.py` (blob c96b0c08).
Pure additive — no FK rewires yet. Subsequent migrations across other apps
add `tenant` FKs to their models (Sprint 2+).
"""

import uuid

from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="Tenant",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    "slug",
                    models.SlugField(
                        help_text=(
                            "Lowercase identifier used in URLs and the X-Tenant header. "
                            "Letters, digits, hyphen, underscore. Must start with a letter "
                            "or digit. Cannot be changed after creation."
                        ),
                        max_length=50,
                        unique=True,
                    ),
                ),
                (
                    "name",
                    models.CharField(
                        help_text="Human-readable name shown in admin and billing.",
                        max_length=200,
                    ),
                ),
                (
                    "is_active",
                    models.BooleanField(
                        default=True,
                        help_text=(
                            "False hides the tenant from default queries. Soft-disable a "
                            "tenant without dropping data — billing freezes, scoping "
                            "middleware returns 403 (strict mode) or routes to None (audit "
                            "mode)."
                        ),
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "Tenant",
                "verbose_name_plural": "Tenants",
                "ordering": ["name"],
            },
        ),
        migrations.AddIndex(
            model_name="tenant",
            index=models.Index(
                fields=["is_active", "slug"],
                name="tenancy_ten_is_acti_689a6f_idx",
            ),
        ),
    ]
