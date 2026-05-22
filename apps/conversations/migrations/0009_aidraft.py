"""Master M6 AI drafts — add ``AiDraft`` model (Bundle B / item 4 backend).

A transient artifact representing the latest LLM-generated reply
suggestion for a master to act on. Drafts are NOT messages — they
remain in this side-table until the master either sends them as
themselves (creating a real ``Message`` row with
``action_type='master_compose'``), releases them to AI auto-send
(creating a plain assistant ``Message``), regenerates (replaces this
row with a fresh one) or dismisses (terminal status only — endpoint
not in this PR).

### Partial unique constraint

One ``ACTIVE`` draft per conversation at a time — enforced via
``UniqueConstraint(fields=['conversation'], condition=Q(status='active'))``.
The service layer additionally takes a row-level lock on prior ACTIVE
rows before marking REPLACED + inserting the new ACTIVE row, so the
DB constraint is defence-in-depth rather than the only line.

### Migration safety

Pure additive — new table only. Zero-downtime on a populated
``conversations_conversation``.
"""

from __future__ import annotations

import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("conversations", "0008_conversation_tier_fields"),
        # AiDraft FKs CatalogMaster + Tenant; the catalog migration
        # carrying the master model already shipped well before this.
        ("catalog", "0005_catalogmaster_archive_reason"),
        ("tenancy", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="AiDraft",
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
                    "content",
                    models.TextField(
                        help_text="The LLM-generated reply text — 1-3 sentences typical.",
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("active", "Active"),
                            ("sent_as_master", "Sent by master"),
                            ("released_to_ai", "Released to AI auto-send"),
                            ("replaced", "Replaced by newer draft"),
                            ("dismissed", "Dismissed by master"),
                        ],
                        db_index=True,
                        default="active",
                        max_length=32,
                    ),
                ),
                (
                    "llm_provider",
                    models.CharField(
                        blank=True,
                        default="",
                        help_text=(
                            "Provider name as reported by the LLM router ('openai' / 'anthropic')."
                        ),
                        max_length=32,
                    ),
                ),
                (
                    "llm_model",
                    models.CharField(
                        blank=True,
                        default="",
                        help_text="Model id echoed by the provider (e.g. 'gpt-4o-mini').",
                        max_length=64,
                    ),
                ),
                (
                    "llm_cost_usd",
                    models.DecimalField(
                        decimal_places=6,
                        default=0,
                        help_text=(
                            "USD cost of the single LLM call. Computed via "
                            "apps.llm.pricing.compute_cost. 0 on stub / unknown model."
                        ),
                        max_digits=8,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "conversation",
                    models.ForeignKey(
                        help_text="Conversation this draft suggests a reply for.",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="ai_drafts",
                        to="conversations.conversation",
                    ),
                ),
                (
                    "master",
                    models.ForeignKey(
                        help_text="Master the draft is suggested to.",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="+",
                        to="catalog.catalogmaster",
                    ),
                ),
                (
                    "tenant",
                    models.ForeignKey(
                        help_text=(
                            "Owning tenant. CASCADE — drafts are transient "
                            "artifacts; tenant deletion already cascades "
                            "through booking + conversation."
                        ),
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="ai_drafts",
                        to="tenancy.tenant",
                    ),
                ),
                (
                    "trigger_message",
                    models.ForeignKey(
                        blank=True,
                        help_text=(
                            "The most recent customer message at generation time. "
                            "Used for the 60s idempotency window: if the master "
                            "taps «Generate» twice within 60 seconds AND no new "
                            "customer message arrived in between, we return the "
                            "existing draft instead of billing another LLM call."
                        ),
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to="conversations.message",
                    ),
                ),
            ],
            options={
                "verbose_name": "AI draft",
                "verbose_name_plural": "AI drafts",
                "ordering": ["-created_at"],
            },
        ),
        migrations.AddConstraint(
            model_name="aidraft",
            constraint=models.UniqueConstraint(
                condition=models.Q(("status", "active")),
                fields=("conversation",),
                name="ai_draft_one_active_per_conversation",
            ),
        ),
        migrations.AddIndex(
            model_name="aidraft",
            index=models.Index(
                fields=["tenant", "master", "status"],
                name="conv_aidraft_tnt_mst_stat_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="aidraft",
            index=models.Index(
                fields=["conversation", "-created_at"],
                name="conv_aidraft_conv_created_idx",
            ),
        ),
    ]
