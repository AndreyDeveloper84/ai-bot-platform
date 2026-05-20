"""Admin REST API — owner/admin tooling for the salon's roster (MM1-MM5).

Separate from :mod:`apps.adminconsole` (which is reserved for Django
admin chrome / forms). This app owns the REST surface that the Ayla Pro
web dashboard + Mini App admin views consume.

PR 2 ships MM1 (roster list) + MM3 (master detail/edit) backend.
MM2 (invite flow), MM4 (services M2M), MM5 (deactivation cascade) land
in follow-up PRs.

All endpoints under ``/api/v1/admin/``. Role-gated via
:func:`apps.admin_api.auth.require_admin_role` — Owner or Admin only;
Receptionist + Master + Customer rejected 403.
"""
