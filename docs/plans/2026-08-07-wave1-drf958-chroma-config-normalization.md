# DRF-958 Chroma Config Normalization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Normalize `CHROMA_HTTP_HOST` and `CHROMA_AUTH_TOKEN` once in the settings layer so readiness probes and the actual Chroma client use identical values, closing the false-green whitespace divergence found in post-merge review of #1153.

**Architecture:** Add `.strip()` at the two authoritative import-time bindings in `config/settings/base.py`, harden the production fail-fast guard in `config/settings/production.py` against whitespace-only tokens, and add a smoke-level symmetry test that proves probes and `_build_chromadb_client` make the same remote-vs-embedded decision for the same raw env input.

**Tech Stack:** Python 3.12, Django settings, pytest, `chromadb`, `httpx`, uv, ruff, mypy.

## Global Constraints

- Work from branch `fix/wave1-drf958-chroma-config-normalization` based on `origin/dev` (current `ea978bb`).
- Do NOT deploy, restart services, or mutate real runtime env/secrets.
- Do NOT redesign port handling, readyz detail strings, FAQ/RAG scope, or CI gates.
- Keep `.strip()` centralized in settings; probes may keep their existing defensive `.strip()`.
- Embedded mode (empty host → `PersistentClient`) remains unchanged.
- All new tests must be isolated and restore global state (settings modules, env vars, LRU cache).

---

### Task 1: Normalize Chroma settings in `config/settings/base.py`

**Files:**
- Modify: `config/settings/base.py:1141-1143`

**Interfaces:**
- Consumes: `os.environ["CHROMA_HTTP_HOST"]`, `os.environ["CHROMA_AUTH_TOKEN"]`
- Produces: `CHROMA_HTTP_HOST: str` (stripped), `CHROMA_AUTH_TOKEN: str` (stripped)

- [ ] **Step 1: Apply `.strip()` to host and token bindings**

```python
CHROMA_HTTP_HOST = os.environ.get("CHROMA_HTTP_HOST", "").strip()
CHROMA_HTTP_PORT = int(os.environ.get("CHROMA_HTTP_PORT", "8001"))
CHROMA_AUTH_TOKEN = os.environ.get("CHROMA_AUTH_TOKEN", "").strip()
```

- [ ] **Step 2: Update the adjacent comment to document whitespace normalization**

Add one sentence after the existing explanation: `Both values are stripped at import time so consumers see a single normalized value.`

- [ ] **Step 3: Commit**

```bash
git add config/settings/base.py
git commit -m "fix(config): strip CHROMA_HTTP_HOST and CHROMA_AUTH_TOKEN at settings import"
```

---

### Task 2: Harden production guard against whitespace-only tokens

**Files:**
- Modify: `config/settings/production.py:62-69`

**Interfaces:**
- Consumes: `os.environ["CHROMA_AUTH_TOKEN"]`
- Produces: `ImproperlyConfigured` on whitespace-only token; normalized `CHROMA_AUTH_TOKEN` available via star-import from base

- [ ] **Step 1: Strip the token in the production guard**

```python
CHROMA_AUTH_TOKEN = os.environ.get("CHROMA_AUTH_TOKEN", "").strip()
if not CHROMA_AUTH_TOKEN:
    raise ImproperlyConfigured(
        "CHROMA_AUTH_TOKEN is required in production. "
        "Set it in the environment to a value matching the "
        "CHROMA_SERVER_AUTHN_CREDENTIALS configured on the ChromaDB "
        "container (see infra/README.md → 'ChromaDB Bearer auth')."
    )
```

- [ ] **Step 2: Commit**

```bash
git add config/settings/production.py
git commit -m "fix(config): reject whitespace-only CHROMA_AUTH_TOKEN in production"
```

---

### Task 3: Add regression and symmetry tests

**Files:**
- Create: `tests/smoke/test_chroma_config_normalization.py`

**Interfaces:**
- Consumes: `config.settings.base` (reloaded), `config.settings.production` (imported), `apps.orchestrator.health.check_chromadb_auth`, `apps.orchestrator.views._ping_chromadb`, `apps.kb.chromadb_client._build_chromadb_client`
- Produces: passing assertions for normalization, production guard, and probe/client symmetry

- [ ] **Step 1: Write base normalization tests**

Reload `config.settings.base` under patched `os.environ` and assert:

| env value | normalized host | normalized token |
|-----------|-----------------|------------------|
| missing / `""` | `""` | `""` |
| `"   "` | `""` | `""` |
| `"chromadb"` | `"chromadb"` | `"chromadb"` |
| `" chromadb "` | `"chromadb"` | `"chromadb"` |
| `" token "` | — | `"token"` |

Use the pattern from `tests/test_openai_api_key_setting.py`:

```python
import importlib
import os
from unittest import mock

import config.settings.base as base_settings


def _reload_base(env_patch: dict[str, str]) -> None:
    with mock.patch.dict(os.environ, env_patch, clear=False):
        importlib.reload(base_settings)


def test_chroma_http_host_whitespace_normalized_to_empty() -> None:
    _reload_base({"CHROMA_HTTP_HOST": "   "})
    assert base_settings.CHROMA_HTTP_HOST == ""
    importlib.reload(base_settings)
```

- [ ] **Step 2: Write production whitespace-token guard test**

Add to the same new file (or extend `tests/smoke/test_catalog_settings.py`). Prefer the new file to keep scope clean:

```python
import importlib
import os
from collections.abc import Iterator

import pytest
from django.core.exceptions import ImproperlyConfigured


@pytest.fixture
def _restore_production_module() -> Iterator[None]:
    import sys

    saved = sys.modules.pop("config.settings.production", None)
    yield
    if saved is not None:
        sys.modules["config.settings.production"] = saved
    else:
        sys.modules.pop("config.settings.production", None)


def test_whitespace_chroma_token_raises_improperly_configured(
    monkeypatch: pytest.MonkeyPatch,
    _restore_production_module: None,
) -> None:
    monkeypatch.setenv("AYLA_INTERNAL_API_TOKEN", "ayla-token-abc")
    monkeypatch.setenv("SENTRY_DSN", "https://public@sentry.example.com/1")
    monkeypatch.setenv("MYSITE_WEBHOOK_HMAC_SECRET", "hmac-secret-abc")
    monkeypatch.setenv("CHROMA_AUTH_TOKEN", "   ")
    with pytest.raises(ImproperlyConfigured) as exc_info:
        importlib.import_module("config.settings.production")
    assert "CHROMA_AUTH_TOKEN" in str(exc_info.value)
```

- [ ] **Step 3: Write the symmetry test**

For each raw host value `["", " ", "\t", "chromadb", " chromadb", "chromadb "]`, assert that `check_chromadb_auth`, `_ping_chromadb`, and `_build_chromadb_client` all agree on remote-vs-embedded mode. Mock `httpx` / `chromadb.HttpClient` to avoid network.

```python
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from django.test import override_settings

from apps.kb import chromadb_client as cc
from apps.orchestrator import views as readyz_views
from apps.orchestrator.health import check_chromadb_auth


@pytest.mark.parametrize(
    "raw_host,expected_host,is_remote",
    [
        ("", "", False),
        (" ", "", False),
        ("\t", "", False),
        ("chromadb", "chromadb", True),
        (" chromadb", "chromadb", True),
        ("chromadb ", "chromadb", True),
    ],
)
def test_probe_and_client_agree_on_remote_vs_embedded(
    raw_host: str,
    expected_host: str,
    is_remote: bool,
    settings: pytest.FixtureRequest,
) -> None:
    settings.CHROMA_HTTP_HOST = expected_host
    settings.CHROMA_HTTP_PORT = 8001
    settings.CHROMA_AUTH_TOKEN = "token"
    cc.reset_client_cache()

    auth_result = check_chromadb_auth()
    assert auth_result["ok"] is True
    assert (auth_result.get("detail") != "no_remote_chromadb") is is_remote

    with patch("httpx.AsyncClient") as mock_async_client:
        mock_response = AsyncMock()
        mock_response.raise_for_status = AsyncMock()
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_async_client.return_value = mock_client
        result = asyncio.run(readyz_views._ping_chromadb())

    if is_remote:
        mock_client.get.assert_awaited_once_with(
            f"http://{expected_host}:8001/api/v2/heartbeat"
        )
    else:
        mock_client.get.assert_not_awaited()

    with patch("chromadb.HttpClient", return_value=object()) as mock_http:
        client = cc._build_chromadb_client()

    if is_remote:
        mock_http.assert_called_once()
        _, kwargs = mock_http.call_args
        assert kwargs["host"] == expected_host
    else:
        mock_http.assert_not_called()
```

- [ ] **Step 4: Commit**

```bash
git add tests/smoke/test_chroma_config_normalization.py
git commit -m "test(config): prove Chroma host/token normalization and probe/client symmetry"
```

---

### Task 4: Run targeted tests and quality checks

**Files:** none (verification)

- [ ] **Step 1: Run smoke tests**

```bash
uv run pytest tests/smoke/ -v
```

Expected: all pass.

- [ ] **Step 2: Run orchestrator and kb tests locally**

```bash
uv run pytest apps/orchestrator/tests/test_healthz.py apps/orchestrator/tests/test_readyz_extended.py apps/orchestrator/tests/test_readyz_settings.py apps/kb/tests/test_chromadb_client.py -q
```

Expected: all pass.

- [ ] **Step 3: Run ruff and mypy**

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy apps config tests
```

Expected: clean.

---

### Task 5: Independent review

**Files:** diff of `config/settings/base.py`, `config/settings/production.py`, `tests/smoke/test_chroma_config_normalization.py`

- [ ] **Step 1: Dispatch a coder subagent with the full diff and red-team inputs**

Prompt the subagent to answer:

> "Can /readyz be green while the actual Chroma client uses a logically different host/token value?"

Red-team inputs: `""`, `" "`, `"\t"`, `"chromadb"`, `" chromadb"`, `"chromadb "`, `" token "`.

- [ ] **Step 2: Address any P0/P1 findings and re-run targeted tests**

---

### Task 6: Open PR, wait for CI, merge

**Files:** none

- [ ] **Step 1: Push branch and open PR via `gh`**

```bash
git push -u origin fix/wave1-drf958-chroma-config-normalization
gh pr create --base dev --title "fix(config): normalize Chroma runtime settings before pilot" --body-file pr-body.md
```

PR body template:

```markdown
## Why
Independent post-merge review of #1153 found readiness/client config divergence.

## Root cause
Probes trimmed host, actual client consumed raw host. Auth token also allowed whitespace-only config.

## Config invariant
One raw config value → one normalized settings value → same behavior in probes and actual client.

## Changes
- `config/settings/base.py`: strip `CHROMA_HTTP_HOST` and `CHROMA_AUTH_TOKEN` at import time.
- `config/settings/production.py`: reject whitespace-only `CHROMA_AUTH_TOKEN`.
- `tests/smoke/test_chroma_config_normalization.py`: normalization, production guard, and probe/client symmetry tests.

## Tests
- Local: `uv run pytest tests/smoke/ -v` and targeted orchestrator/kb tests (green).
- CI: authoritative `ci.yml` gate.

## Independent review
P0/P1 findings: 0.

## Out of scope
- port fallback / empty-port handling
- readyz embedded-mode detail string
- FAQ/RAG data population
- CI redesign for orchestrator tests
- deploy/runtime config changes

Relates: DRF-958, DRF-955, PR #1153
```

- [ ] **Step 2: Wait for CI green**

- [ ] **Step 3: Squash-merge via GitHub UI**

---

### Task 7: Update Linear

**Files:** none

- [ ] **Step 1: Comment on DRF-958**

```
CODE FIX MERGED

PR: <url>
Merge SHA: <sha>
Local targeted tests: green
Authoritative CI: green
Independent review: no P0/P1

Acceptance Criteria:
- host normalized
- token normalized
- probe/client symmetry proven
- false-green whitespace cases closed
```

- [ ] **Step 2: Move DRF-958 to Done if all AC are met**

- [ ] **Step 3: Comment on parent DRF-955**

```
DRF-958 CLOSED

Chroma config normalization is now safe for runtime preflight.

DRF-955 remains In Progress: runtime/deploy acceptance pending.
```

---

## Notes / Blockers

- **Linear MCP is not available in this environment.** The task text requires opening/updating DRF-958 via Linear MCP, but no Linear tool or credential is present. Before code execution, confirm whether the agent should proceed with the code/PR path while the user handles Linear, or whether an alternative Linear integration exists.
