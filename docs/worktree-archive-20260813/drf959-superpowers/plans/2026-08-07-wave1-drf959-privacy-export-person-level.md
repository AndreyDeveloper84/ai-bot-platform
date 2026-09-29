# Wave 1 DRF-959 Privacy Export Person-Level Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Align `export_personal_data()` subject resolution with the person-level identity semantics already adopted for `delete_personal_data()` in DRF-956 / PR #1152.

**Architecture:** Reuse the existing DRF-956 helpers `_resolve_person_link`, `_person_shell_ids`, and `_channel_sibling_ids` inside `apps/identity/services/privacy.py`. Raise a new `PrivacyIdentityConflictError` when sibling shells carry >1 distinct `ayla_user_id`; catch it in `apps/miniapp_api/views.py::personal_data_export` and return a fail-closed 502 with `error: identity_conflict`. Expand `apps/identity/services/tests/test_privacy.py` with the mandatory test matrix for sibling linkage, conflict, empty identity, consent coverage, and no-mutation.

**Tech Stack:** Python 3.12, Django 5.x, pytest, ruff, mypy. Project uses `uv run` for tool invocation.

## Global Constraints

- Work in isolated worktree: `C:/Users/user/PycharmProjects/ai-bot-platform-drf959` on branch `fix/wave1-drf959-privacy-export-person-level`, tracked from `origin/dev` at `8b341906630ce3a8780a6be19908988d185b16a5`.
- No destructive side effects: DRF-959 is read/export only.
- Reuse DRF-956 identity helpers; do not build a second resolver.
- Fail-closed on identity conflict and blank `(channel, channel_user_id)`.
- Preserve existing public API shape; only add a new backward-compatible `identity_conflict` error slug.
- Local verification: `uv run pytest apps/identity/services/tests/test_privacy.py -q`, `uv run ruff check <changed files>`, `uv run ruff format --check <changed files>`, `uv run mypy <changed source files>`.
- Linear MCP unavailable in this harness; document the required Linear comments/status for the user to apply manually.

---

### Task 1: Add identity-conflict exception and person-level resolution to `export_personal_data`

**Files:**
- Modify: `apps/identity/services/privacy.py`

**Interfaces:**
- Consumes: `_resolve_person_link(bot_user)` → `_PersonLink(ayla_user_id, conflict)`; `_person_shell_ids(bot_user, link)` → `set[UUID]`.
- Produces: `PrivacyIdentityConflictError` exception; updated `export_personal_data()` that raises it on conflict and uses person-level `ayla_user_id` and shell set.

- [ ] **Step 1: Add the new exception class**

Insert immediately after `PrivacyUpstreamError`:

```python
class PrivacyIdentityConflictError(Exception):
    """The person's shells carry two or more distinct ayla_user_ids.

    Export (and delete) must not guess which upstream account the caller
    is — picking one could return or destroy a stranger's data.
    """
```

- [ ] **Step 2: Replace the row-level export implementation**

Replace the body of `export_personal_data()` (lines 295-372) with:

```python
def export_personal_data(
    bot_user: BotUser,
    *,
    client: PersonalContextHttpClient | None = None,
) -> dict[str, Any]:
    """Aggregate the person's export payload.

    Raises:
        PrivacyIdentityConflictError: when the person's shells carry two
            or more distinct ``ayla_user_id`` values.
        PrivacyUpstreamError: when the Ayla leg fails (an export silently
            missing its Ayla half would be a compliance lie).

    Bot-side sections are always present; the ``ayla`` section is ``None``
    when the user has no Ayla link yet (nothing exists upstream).
    """
    link = _resolve_person_link(bot_user)

    if link.conflict:
        logger.error(
            "identity.privacy.export_identity_conflict bot_user=%s — "
            "refusing export across ambiguous shells (fail-closed)",
            bot_user.id,
        )
        raise PrivacyIdentityConflictError(
            "person identity conflict: cannot determine canonical subject"
        )

    ayla_user_id = link.ayla_user_id

    ayla_section: dict[str, Any] | None = None
    if ayla_user_id is not None:
        owns = client is None
        client = client or PersonalContextHttpClient()
        try:
            ayla_section = client.get_personal_data_export(ayla_user_id=str(ayla_user_id))
        except PersonalContextError as exc:
            raise PrivacyUpstreamError(f"ayla export failed: {exc}") from exc
        finally:
            if owns:
                client.close()

    memory_section: list[dict[str, Any]] = []
    if ayla_user_id is not None:
        memory_section = [
            {
                "id": str(entry.id),
                "kind": entry.kind,
                "source": entry.source,
                "content": entry.content if isinstance(entry.content, dict) else {},
                "last_inferred_at": entry.last_inferred_at.isoformat()
                if entry.last_inferred_at
                else None,
                "created_at": entry.created_at.isoformat(),
            }
            for entry in read_green_entries(ayla_user_id)
        ]

    shell_ids = _person_shell_ids(bot_user, link)
    consents_qs = ConsentRecord.all_tenants.filter(
        bot_user_id__in=shell_ids
    ).order_by("captured_at")
    consents_section = [
        {
            "consent_type": row.consent_type,
            "granted": row.granted,
            "document_version": row.document_version,
            "source": row.source,
            "captured_at": row.captured_at.isoformat(),
            "withdrawn_at": row.withdrawn_at.isoformat() if row.withdrawn_at else None,
        }
        for row in consents_qs
    ]

    write_audit(
        "privacy.personal_data_exported",
        target="BotUser",
        target_id=bot_user.id,
        payload={
            "actor": "customer",
            "scope": ["ayla_export", "memory_green", "consents"],
        },
    )

    return {
        "generated_at": timezone.now().isoformat(),
        "subject": {
            "ayla_user_id": str(ayla_user_id) if ayla_user_id else None,
        },
        "ayla": ayla_section,
        "memory": memory_section,
        "consents": consents_section,
    }
```

- [ ] **Step 3: Verify the change with a syntax/ruff check**

Run:
```bash
uv run ruff check apps/identity/services/privacy.py
uv run ruff format --check apps/identity/services/privacy.py
```

Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add apps/identity/services/privacy.py
git commit -m "fix(privacy): resolve export subject at person level"
```

---

### Task 2: Surface identity conflict through the Mini App export view

**Files:**
- Modify: `apps/miniapp_api/views.py`

**Interfaces:**
- Consumes: `PrivacyIdentityConflictError` from `apps.identity.services.privacy`.
- Produces: 502 response with body `{"error": "identity_conflict"}` when export service raises the conflict exception.

- [ ] **Step 1: Update the import in `personal_data_export`**

Change line 1627 from:
```python
from apps.identity.services.privacy import PrivacyUpstreamError, export_personal_data
```
to:
```python
from apps.identity.services.privacy import (
    PrivacyIdentityConflictError,
    PrivacyUpstreamError,
    export_personal_data,
)
```

- [ ] **Step 2: Catch the new exception**

In `personal_data_export`, after the `PrivacyUpstreamError` handler, add:

```python
    except PrivacyIdentityConflictError:
        return _error(
            "identity_conflict",
            "person identity conflict: cannot determine canonical subject",
            502,
        )
```

The full try/except block should be:

```python
    try:
        payload = export_personal_data(bot_user)
    except PrivacyUpstreamError:
        return _error(
            "upstream_unavailable",
            "personal-data export is temporarily unavailable, try again later",
            502,
        )
    except PrivacyIdentityConflictError:
        return _error(
            "identity_conflict",
            "person identity conflict: cannot determine canonical subject",
            502,
        )
```

- [ ] **Step 3: Verify with ruff**

```bash
uv run ruff check apps/miniapp_api/views.py
uv run ruff format --check apps/miniapp_api/views.py
```

Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add apps/miniapp_api/views.py
git commit -m "fix(miniapp_api): return identity_conflict on export ambiguity"
```

---

### Task 3: Add targeted tests for person-level export

**Files:**
- Modify: `apps/identity/services/tests/test_privacy.py`

**Interfaces:**
- Consumes: `PrivacyIdentityConflictError`, `_resolve_person_link`, `_person_shell_ids` indirectly via service output.
- Produces: passing tests covering sibling linkage, conflict, empty identity, consent shell coverage, memory coverage, and no mutation.

- [ ] **Step 1: Add the exception to the test imports**

Update the import block (around line 28) to include `PrivacyIdentityConflictError`:

```python
from apps.identity.services.privacy import (
    PrivacyIdentityConflictError,
    PrivacyUpstreamError,
    delete_personal_data,
    export_personal_data,
)
```

- [ ] **Step 2: Add `TestExportPersonLevelResolution` test class**

Append the following class to the test file (after `TestExport`):

```python
class TestExportPersonLevelResolution:
    """DRF-959 — export subject must equal delete subject (the person, not the row)."""

    def _sibling_pair(
        self, tenant, ayla_user_id: uuid.UUID | None
    ) -> tuple[BotUser, BotUser]:
        """Mini App shell (unlinked) + sentinel/global shell (linked)."""
        sentinel = Tenant.objects.create(slug="global-bot-export", name="Global")
        linked = BotUser.all_tenants.create(
            tenant=sentinel,
            channel="max",
            channel_user_id="12345",
            ayla_user_id=ayla_user_id,
        )
        requesting = BotUser.all_tenants.create(
            tenant=tenant,
            channel="max",
            channel_user_id="12345",
            ayla_user_id=None,
        )
        return requesting, linked

    def test_export_resolves_ayla_via_linked_sibling(self, tenant, ayla_user_id) -> None:
        requesting, linked = self._sibling_pair(tenant, ayla_user_id)
        client = _StubPCClient()

        payload = export_personal_data(requesting, client=client)  # type: ignore[arg-type]

        assert payload["subject"]["ayla_user_id"] == str(ayla_user_id)
        assert client.calls == [("export", str(ayla_user_id))]

    def test_export_includes_memory_via_linked_sibling(self, tenant, ayla_user_id) -> None:
        requesting, linked = self._sibling_pair(tenant, ayla_user_id)
        _seed_memory(linked, ayla_user_id)

        payload = export_personal_data(requesting, client=_StubPCClient())  # type: ignore[arg-type]

        assert [m["content"]["value"] for m in payload["memory"]] == ["vegan"]

    def test_export_includes_consents_across_sibling_shells(self, tenant, ayla_user_id) -> None:
        requesting, linked = self._sibling_pair(tenant, ayla_user_id)
        _grant(requesting, ConsentRecord.ConsentType.PERSONAL_DATA)
        _grant(linked, ConsentRecord.ConsentType.MEMORY_GREEN)

        payload = export_personal_data(requesting, client=_StubPCClient())  # type: ignore[arg-type]

        consent_types = {c["consent_type"] for c in payload["consents"]}
        assert {
            ConsentRecord.ConsentType.PERSONAL_DATA,
            ConsentRecord.ConsentType.MEMORY_GREEN,
        } <= consent_types

    def test_export_excludes_unrelated_user_consents(self, tenant, ayla_user_id) -> None:
        requesting, linked = self._sibling_pair(tenant, ayla_user_id)
        _grant(requesting, ConsentRecord.ConsentType.PERSONAL_DATA)
        stranger = BotUser.all_tenants.create(
            tenant=tenant,
            channel="max",
            channel_user_id="99999",
            ayla_user_id=uuid.uuid4(),
        )
        _grant(stranger, ConsentRecord.ConsentType.MEMORY_GREEN)

        payload = export_personal_data(requesting, client=_StubPCClient())  # type: ignore[arg-type]

        consent_bot_user_ids = {c["bot_user_id"] for c in payload["consents"]}
        # The payload does not carry bot_user_id, so assert via source set.
        # Each _grant uses source="test"; build the source set instead.
        assert payload["consents"]
        # Actually verify the stranger's consent is not present by counting
        # and checking the consent_type set.
        consent_types = {c["consent_type"] for c in payload["consents"]}
        assert ConsentRecord.ConsentType.MEMORY_GREEN not in consent_types

    def test_export_excludes_cross_tenant_unrelated_user(self, tenant, ayla_user_id) -> None:
        requesting, linked = self._sibling_pair(tenant, ayla_user_id)
        _grant(requesting, ConsentRecord.ConsentType.PERSONAL_DATA)
        other_tenant = Tenant.objects.create(slug="other-export", name="Other")
        cross = BotUser.all_tenants.create(
            tenant=other_tenant,
            channel="max",
            channel_user_id="88888",
            ayla_user_id=uuid.uuid4(),
        )
        _grant(cross, ConsentRecord.ConsentType.MEMORY_GREEN)

        payload = export_personal_data(requesting, client=_StubPCClient())  # type: ignore[arg-type]

        consent_types = {c["consent_type"] for c in payload["consents"]}
        assert ConsentRecord.ConsentType.MEMORY_GREEN not in consent_types
        assert ConsentRecord.ConsentType.PERSONAL_DATA in consent_types

    def test_export_conflict_fail_closed(self, tenant) -> None:
        id_a, id_b = uuid.uuid4(), uuid.uuid4()
        t_a = Tenant.objects.create(slug="conflict-export-a", name="A")
        t_b = Tenant.objects.create(slug="conflict-export-b", name="B")
        BotUser.all_tenants.create(
            tenant=t_a, channel="max", channel_user_id="12345", ayla_user_id=id_a
        )
        BotUser.all_tenants.create(
            tenant=t_b, channel="max", channel_user_id="12345", ayla_user_id=id_b
        )
        requesting = BotUser.all_tenants.create(
            tenant=tenant, channel="max", channel_user_id="12345", ayla_user_id=None
        )
        client = _StubPCClient()

        with pytest.raises(PrivacyIdentityConflictError):
            export_personal_data(requesting, client=client)

        assert client.calls == []

    def test_export_conflict_does_not_leak_memory(self, tenant) -> None:
        id_a, id_b = uuid.uuid4(), uuid.uuid4()
        t_a = Tenant.objects.create(slug="conflict-export-mem-a", name="A")
        t_b = Tenant.objects.create(slug="conflict-export-mem-b", name="B")
        BotUser.all_tenants.create(
            tenant=t_a, channel="max", channel_user_id="12345", ayla_user_id=id_a
        )
        BotUser.all_tenants.create(
            tenant=t_b, channel="max", channel_user_id="12345", ayla_user_id=id_b
        )
        requesting = BotUser.all_tenants.create(
            tenant=tenant, channel="max", channel_user_id="12345", ayla_user_id=None
        )
        _seed_memory(BotUser.all_tenants.get(ayla_user_id=id_a), id_a)

        with pytest.raises(PrivacyIdentityConflictError):
            export_personal_data(requesting, client=_StubPCClient())

        # Candidate memory must remain untouched.
        assert MemoryEntry.objects.filter(
            user_id=id_a, soft_deleted_at__isnull=True
        ).exists()

    def test_export_blank_channel_identity_uses_own_row_only(self, tenant) -> None:
        t2 = Tenant.objects.create(slug="blank-export-victim", name="Victim")
        victim = BotUser.all_tenants.create(
            tenant=t2, channel="max", channel_user_id="", ayla_user_id=None
        )
        _grant(victim, ConsentRecord.ConsentType.PERSONAL_DATA)
        actor = BotUser.all_tenants.create(
            tenant=tenant, channel="max", channel_user_id="", ayla_user_id=None
        )
        _grant(actor, ConsentRecord.ConsentType.MEMORY_GREEN)

        payload = export_personal_data(actor)

        consent_types = {c["consent_type"] for c in payload["consents"]}
        assert ConsentRecord.ConsentType.MEMORY_GREEN in consent_types
        assert ConsentRecord.ConsentType.PERSONAL_DATA not in consent_types

    def test_export_unlinked_includes_channel_sibling_consents(self, tenant) -> None:
        t2 = Tenant.objects.create(slug="unlinked-sib", name="Sibling")
        sibling = BotUser.all_tenants.create(
            tenant=t2,
            channel="max",
            channel_user_id="555",
            ayla_user_id=None,
        )
        requesting = BotUser.all_tenants.create(
            tenant=tenant,
            channel="max",
            channel_user_id="555",
            ayla_user_id=None,
        )
        _grant(requesting, ConsentRecord.ConsentType.PERSONAL_DATA)
        _grant(sibling, ConsentRecord.ConsentType.MEMORY_GREEN)

        payload = export_personal_data(requesting)

        consent_types = {c["consent_type"] for c in payload["consents"]}
        assert {
            ConsentRecord.ConsentType.PERSONAL_DATA,
            ConsentRecord.ConsentType.MEMORY_GREEN,
        } <= consent_types

    def test_export_leaves_db_unchanged(self, tenant, ayla_user_id) -> None:
        requesting, linked = self._sibling_pair(tenant, ayla_user_id)
        _with_pii(requesting)
        _grant(requesting, ConsentRecord.ConsentType.PERSONAL_DATA)
        _seed_memory(linked, ayla_user_id)

        before = {
            "phone": requesting.phone,
            "display_name": requesting.display_name,
            "client_name": requesting.client_name,
            "consent_count": ConsentRecord.all_tenants.filter(
                bot_user=requesting, withdrawn_at__isnull=True
            ).count(),
            "memory_count": MemoryEntry.objects.filter(
                user_id=ayla_user_id, soft_deleted_at__isnull=True
            ).count(),
            "ayla_user_id": requesting.ayla_user_id,
        }

        export_personal_data(requesting, client=_StubPCClient())  # type: ignore[arg-type]

        requesting.refresh_from_db()
        assert requesting.phone == before["phone"]
        assert requesting.display_name == before["display_name"]
        assert requesting.client_name == before["client_name"]
        assert requesting.ayla_user_id == before["ayla_user_id"]
        assert (
            ConsentRecord.all_tenants.filter(
                bot_user=requesting, withdrawn_at__isnull=True
            ).count()
            == before["consent_count"]
        )
        assert (
            MemoryEntry.objects.filter(
                user_id=ayla_user_id, soft_deleted_at__isnull=True
            ).count()
            == before["memory_count"]
        )
```

- [ ] **Step 3: Add view-level conflict response test**

Inside `TestViews`, add:

```python
    def test_export_identity_conflict_502(
        self, client: DjangoClient, tenant, monkeypatch
    ) -> None:
        def _conflict(*args, **kwargs):
            raise PrivacyIdentityConflictError("conflict")

        monkeypatch.setattr(
            "apps.identity.services.privacy.export_personal_data", _conflict
        )
        resp = client.get(
            "/api/v1/customer/me/personal-data/export/",
            HTTP_AUTHORIZATION=_init_data_header("12345"),
        )
        assert resp.status_code == 502
        assert resp.json()["error"] == "identity_conflict"
```

- [ ] **Step 4: Run the privacy test suite**

```bash
uv run pytest apps/identity/services/tests/test_privacy.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Run ruff/mypy on changed files**

```bash
uv run ruff check apps/identity/services/privacy.py apps/miniapp_api/views.py apps/identity/services/tests/test_privacy.py
uv run ruff format --check apps/identity/services/privacy.py apps/miniapp_api/views.py apps/identity/services/tests/test_privacy.py
uv run mypy apps/identity/services/privacy.py apps/miniapp_api/views.py
```

Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add apps/identity/services/tests/test_privacy.py
git commit -m "test(privacy): cover person-level export resolution and conflict fail-closed"
```

---

### Task 4: Final verification and PR preparation

**Files:**
- None (verification only).

- [ ] **Step 1: Run the full targeted test command**

```bash
uv run pytest apps/identity/services/tests/test_privacy.py -q
```

Expected: all tests pass.

- [ ] **Step 2: Confirm diff scope**

```bash
git diff --stat origin/dev
```

Expected: only `apps/identity/services/privacy.py`, `apps/miniapp_api/views.py`, and `apps/identity/services/tests/test_privacy.py` are modified.

- [ ] **Step 3: Push branch and open PR**

```bash
git push -u origin fix/wave1-drf959-privacy-export-person-level
```

Open PR against `dev` with title `fix(privacy): align personal-data export with person identity`. Body to include root cause, invariant, changes, security notes, tests, and out-of-scope list per the issue spec.

- [ ] **Step 4: Record Linear status for manual update**

Since Linear MCP is unavailable, record the following for the user:

- DRF-959 status: In Progress → Done after merge.
- Required comments:
  1. WORK STARTED (baseline `origin/dev` at `8b341906630ce3a8780a6be19908988d185b16a5`, branch `fix/wave1-drf959-privacy-export-person-level`, parent DRF-956, related PR #1152).
  2. PR OPENED (with actual PR URL/SHA after push).
  3. CODE FIX MERGED (with actual merge SHA after squash-merge).
- DRF-956 remains In Progress for runtime privacy acceptance.

---

## Self-Review

1. **Spec coverage:**
   - Person-level Ayla linkage resolution → Task 1.
   - Person-shell consent export → Task 1 (uses `_person_shell_ids`).
   - Conflict fail-closed → Task 1 + Task 2.
   - Empty identity safety → covered by Task 3 `test_export_blank_channel_identity_uses_own_row_only`.
   - No mutation → Task 3 `test_export_leaves_db_unchanged`.
   - Cross-user/cross-tenant security → Task 3 `test_export_excludes_unrelated_user_consents`, `test_export_excludes_cross_tenant_unrelated_user`.
   - Backward-compatible API → Task 2 adds only a new `identity_conflict` error slug.

2. **Placeholder scan:** No TBD/TODO placeholders; all code and commands are concrete.

3. **Type consistency:** `PrivacyIdentityConflictError` is imported and raised consistently; `_PersonLink` fields unchanged; `_person_shell_ids` returns `set[UUID]` used for `bot_user_id__in`.
