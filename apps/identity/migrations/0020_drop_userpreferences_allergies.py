# DRF-1371 — drop UserPreferences.allergies.
#
# Free-text contraindications lived in a plain TextField: a special
# category under 152-ФЗ ст. 10 outside MemoryEntry's zone / consent / TTL
# machinery, with a help_text (and a Mini App caption) promising the value
# reached the master, which no code ever did. Owner decision 2026-08-25:
# "мастеру противопоказания видеть не должен" — so the column goes rather
# than gets a perimeter.
#
# ### Data loss on apply
# None on the pilot: measured 2026-08-25, `identity_userpreferences` held
# one row and zero non-empty `allergies`. Applying this DROPs the column,
# so any value written between that measurement and deploy is gone.
#
# ### Rollback
# `migrate identity 0019` reverses cleanly — Django's RemoveField is its
# own inverse and re-adds `allergies` as `TextField(blank=True,
# default="")`, matching 0004. Column values are NOT restored; a rolled
# back schema gets empty strings. That is the intended outcome, not a
# defect: the code on the other side of the rollback is `dev` before this
# PR, which treats "" as "not filled in".
#
# ConsentType.HEALTH stays declared (apps/consent/models.py) — showing
# contraindications to a master would be a new, gated feature.

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("identity", "0019_memoryentry_lifecycle_constraints"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="userpreferences",
            name="allergies",
        ),
    ]
