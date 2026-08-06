# Keep this module EMPTY — no imports, no side effects.
#
# `config/settings/base.py` imports `apps.eventbus.ingest_allowlist` at
# settings-load time (T-02: the two ingest allowlists are parsed and
# validated before Django is configured). Importing that leaf executes this
# package `__init__` first, so anything added here runs before
# `django.conf.settings` exists.
#
# An import here that touches settings, models, or the app registry turns
# every `manage.py` invocation into an opaque settings-recursion or
# AppRegistryNotReady failure, with a traceback pointing at Django
# internals rather than at the line you added.
#
# `apps/__init__.py` is on the same path and under the same rule. Both are
# pinned by `apps.eventbus.tests.test_ingest_pilot_allowlist
# ::TestSettingsDefaults::test_parent_packages_stay_import_free` — a module
# docstring is allowed, any statement is not.
