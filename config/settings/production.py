"""Production settings.

Hardening focuses on **fail-fast on boot**: any required env var missing
makes the WSGI worker explode before it can serve a single request with
a stale or empty value. Catch-block uses :class:`ImproperlyConfigured`
(NOT plain ``assert``) — Python invoked with ``-O`` strips asserts at
bytecode-compile time, which would silently disable the check.

Each fail-fast group ties back to the sprint that introduced the
dependency. New required env vars MUST add to the matching block.
"""

from __future__ import annotations

import os

from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F401,F403

DEBUG = False


# Sprint 7 / C8 (DRF-578) — Catalog sync requires a service-token to
# auth against `mysite/api/v1/catalog/*`. Missing token → sync silently
# 401s every 15 minutes, the catalog mirrors drift from source, and the
# FAQ skill (DRF-589) starts retrieving stale chunks. Fail fast instead.
#
# Reading os.environ directly (instead of importing the resolved
# `MYSITE_CATALOG_SERVICE_TOKEN` symbol from .base) so test reloads of
# this module pick up the current env without also reloading base.py.
MYSITE_CATALOG_SERVICE_TOKEN = os.environ.get("MYSITE_CATALOG_SERVICE_TOKEN", "")
if not MYSITE_CATALOG_SERVICE_TOKEN:
    raise ImproperlyConfigured(
        "MYSITE_CATALOG_SERVICE_TOKEN is required in production. "
        "Set it in the environment (matching the token configured on "
        "mysite via M2 / DRF-593)."
    )


# Sprint 7 / M4 (DRF-595) — ChromaDB authentication. ChromaDB stores
# every tenant's KB embeddings; an unauthenticated server lets anyone
# on the docker network read or wipe `tenant_<uuid>` collections. The
# `chromadb` container ships a Bearer-auth gate; the client must
# present a matching ``CHROMA_AUTH_TOKEN`` on every request. A missing
# value here would silently downgrade to anonymous and the FAQ skill
# would start serving 401s — fail fast on boot instead.
CHROMA_AUTH_TOKEN = os.environ.get("CHROMA_AUTH_TOKEN", "")
if not CHROMA_AUTH_TOKEN:
    raise ImproperlyConfigured(
        "CHROMA_AUTH_TOKEN is required in production. "
        "Set it in the environment to a value matching the "
        "CHROMA_SERVER_AUTHN_CREDENTIALS configured on the ChromaDB "
        "container (see infra/README.md → 'ChromaDB Bearer auth')."
    )
