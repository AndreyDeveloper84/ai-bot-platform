"""Golden-query smoke tests for the seeded ``global_kb`` corpus (KB-RAG Sub-6 / GH #119).

These tests exercise the **full retrieval stack against real seeded data
and real OpenAI embeddings**:

* a fresh non-``global_kb`` test tenant (created per-test via the ``db``
  fixture) — so there's nothing in the tenant's own collection;
* the live ``global_kb`` collection in the operator's local ChromaDB
  store (seeded by ``seed_kb_from_gdocs`` in this same PR);
* a real :class:`apps.llm.providers.openai_provider.OpenAIProvider`,
  configured from settings ``OPENAI_API_KEY``.

Every successful hit must therefore travel the **global-fallback**
branch of :func:`apps.kb.services.retriever.search_kb` — i.e.
``metadata.kb_source == "global"``. That single assertion proves the
seed lives in the right collection AND that the retriever fans out to
it for shared doc_types.

### Why opt-in via ``pytest.mark.smoke``

* Real OpenAI embedding calls cost money (negligible — ~$0.0001 per
  run — but non-zero and unbillable to CI).
* Real OpenAI calls fail closed when the key is missing or the network
  is partitioned, which would gate the regular suite on infra
  availability instead of on code correctness.
* The seeded data only exists on the operator's local dev DB + (later)
  staging / prod. On a fresh CI box the ``global_kb`` collection is
  empty and these tests would trivially fail.

The default ``pytest`` invocation excludes them via ``-m "not smoke"``
in ``pyproject.toml``. Run them explicitly:

.. code-block:: bash

    pytest apps/kb/services/tests/test_global_fallback_smoke.py -m smoke -v

### Golden queries (from issue #119)

The five queries mirror the high-value end-user intents the bot must
answer in production:

1. service discovery by user intent ("I want to relax — which massage?")
2. contraindication screening ("I'm pregnant, can I do laser?")
3. aftercare lookup ("what should I do after biorevitalization?")
4. symptom triage ("redness after procedure — is it normal?")
5. cross-type lookup ("my back hurts, what do you suggest?")

Each test asserts:

* at least one hit returned (the seed corpus actually surfaces);
* the top hit carries ``metadata.kb_source == "global"`` (the fallback
  branch actually fired — not a stale local tenant chunk);
* a lenient substring check against ``section_title`` (matching meaning
  not slugs — the operator can rename headings without breaking us).

If any one of these fails, the most likely causes (in order) are:

* the seed didn't run on this environment (``KbDocument.all_tenants
  .filter(tenant=global_kb).count() == 0``);
* embeddings didn't run (``embedded_at IS NULL`` on every seeded row,
  so nothing in ChromaDB);
* the heading text in the source Google Doc moved, so the substring
  assertion is too tight — update the expected substring;
* the OpenAI embedding model produced an unexpectedly distant match
  for that query — bump ``k`` to 5 to see further down the candidate
  list before tightening the assertion.
"""

from __future__ import annotations

import os
import uuid
from typing import Any

import pytest
from dotenv import load_dotenv

from apps.kb.services.global_tenant import get_global_kb_tenant
from apps.kb.services.retriever import search_kb
from apps.llm.providers.openai_provider import OpenAIProvider
from apps.tenancy.models import Tenant

# Smoke tests rely on the operator's local ``.env`` (which holds
# ``OPENAI_API_KEY``). ``manage.py`` autoloads dotenv via PR #135, but
# pytest is invoked directly via the venv's python.exe — no
# ``manage.py`` shim — so we have to load explicitly here. No-op when
# the file is absent (CI invokes with env vars set, dev shell has them
# pre-loaded, etc.); ``override=False`` keeps existing env vars winning.
load_dotenv(override=False)

pytestmark = [pytest.mark.smoke]


@pytest.fixture(scope="session")
def django_db_setup() -> None:
    """Override pytest-django's DB bootstrapper to point at the **dev DB**.

    The default ``django_db_setup`` fixture creates / migrates a
    ``test_<NAME>`` database and tears it down on session end. That's
    wrong for these smoke tests — the whole point is to query the
    operator's **seeded dev DB** (and its ChromaDB collection), not a
    fresh empty schema.

    By overriding here we tell pytest-django "the DB is already live
    and seeded; don't create, migrate, or destroy it". The autouse
    precondition fixture below then guards against running against
    an empty DB by skipping when ``global_kb`` has no chunks.
    """
    # No-op: caller has already set up the DB (the operator ran
    # `python manage.py migrate` + the seed cmd). pytest-django will
    # now use ``settings.DATABASES["default"]`` directly without
    # rewriting NAME → "test_<NAME>" via ``--reuse-db``.
    return None


@pytest.fixture
def test_tenant(db: Any) -> Any:
    """A fresh non-``global_kb`` tenant used by every smoke test.

    Created with a UUID-suffixed slug so re-runs against the dev DB
    don't collide on the ``Tenant.slug`` unique constraint (the test
    can't roll back the row because we're talking to the real DB).
    We explicitly clean up on test exit.

    The tenant has no KB content of its own — so any hit surfaced by
    :func:`search_kb` HAS to come from the ``global_kb`` fallback
    branch. That's the load-bearing precondition for the
    ``kb_source == "global"`` assertions below.
    """
    slug = f"kb-smoke-{uuid.uuid4().hex[:12]}"
    tenant = Tenant.objects.create(slug=slug, name="KB Smoke Test Tenant")
    yield tenant
    # Real DB → we have to clean up manually. CASCADE on tenant FK
    # drops any incidental rows that the retriever path may have
    # touched (event rows, etc. — though the retriever shouldn't
    # write any tenant-scoped data, this is defence-in-depth).
    tenant.delete()


@pytest.fixture
def provider() -> OpenAIProvider:
    """Real OpenAI provider, configured from ``OPENAI_API_KEY``.

    Construction is cheap; the SDK client is built lazily on first
    embedding call.

    .. note::

       ``OpenAIProvider().__init__`` reads ``getattr(settings,
       "OPENAI_API_KEY", "")`` by default — but the platform settings
       module doesn't currently expose that as a Django setting; it
       only reads ``OPENAI_PROXY``. We bridge by reading the raw
       environment variable here (``.env`` is autoloaded into
       ``os.environ`` at process start via the ``manage.py`` wiring
       from PR #135). The key is passed explicitly as ``api_key=...``
       so the test doesn't depend on the settings gap being closed.
       Settings wiring is tracked separately — see PR body for the
       follow-up issue link.
    """
    api_key = os.environ.get("OPENAI_API_KEY") or ""
    if not api_key:
        pytest.skip("OPENAI_API_KEY not set in environment — required for smoke tests.")
    return OpenAIProvider(api_key=api_key)


@pytest.fixture(autouse=True)
def _ensure_global_kb_seeded(db: Any) -> None:
    """Hard precondition — the ``global_kb`` tenant exists and is non-empty.

    Skip the test (not fail) when the precondition is missing; smoke
    tests are operator-facing diagnostics, not gate tests. A skip
    surfaces "seed your environment first" more clearly than a
    cryptic AssertionError on hit count.

    Depends on ``db`` so pytest-django enables DB access; with our
    overridden ``django_db_setup`` the ``db`` fixture is a no-op but
    we still need to declare the dependency for ``Tenant.objects``
    to be callable.
    """
    from apps.kb.chromadb_client import get_chroma_client

    # The cache may be stale if a prior test cleared the row; refresh.
    get_global_kb_tenant.cache_clear()
    gkb = get_global_kb_tenant()
    if gkb is None:
        pytest.skip(
            "global_kb tenant not provisioned — run "
            "`python manage.py create_tenant --slug global_kb --name 'Global Knowledge Base' --system`."
        )
    chunk_count = get_chroma_client().collection_count(gkb)
    if chunk_count == 0:
        pytest.skip(
            "global_kb ChromaDB collection is empty — run "
            "`python manage.py seed_kb_from_gdocs ...` and trigger "
            "`embed_pending_kb_documents` before running smoke tests."
        )


def _assert_global_hit(
    *,
    hits: list[Any],
    expected_substring: str,
    query: str,
) -> None:
    """Shared assertion bundle for every smoke case.

    Three checks, in order of importance:

    1. at least one hit (the corpus surfaces something);
    2. the **top** hit's provenance is ``"global"`` (the fallback branch
       actually fired — a per-tenant hit slipping in would mean the
       fixture leaked content, which is itself a regression to surface);
    3. lenient substring match on the section title (case-insensitive)
       — proves the retrieved content is *topically* on-target without
       pinning the assertion to brittle slug text.
    """
    assert len(hits) >= 1, f"no hits returned for query {query!r}"
    top = hits[0]
    assert top.metadata.get("kb_source") == "global", (
        f"top hit for {query!r} is not from global_kb — "
        f"got kb_source={top.metadata.get('kb_source')!r}, "
        f"section_title={top.metadata.get('section_title')!r}"
    )
    # Search across section_title + source_uri + text so a heading
    # rename in the source doc doesn't break us if the body still
    # mentions the keyword.
    haystack = " ".join(
        [
            str(top.metadata.get("section_title", "")),
            str(top.metadata.get("source_uri", "")),
            str(getattr(top, "text", "")),
        ]
    ).lower()
    assert expected_substring.lower() in haystack, (
        f"expected substring {expected_substring!r} not found in top hit for {query!r}; "
        f"section_title={top.metadata.get('section_title')!r}, "
        f"source_uri={top.metadata.get('source_uri')!r}"
    )


def test_service_intent_relax(
    test_tenant: Tenant,
    provider: OpenAIProvider,
) -> None:
    """Q1 — user describes mood-driven intent; bot must suggest a service."""
    query = "хочу расслабиться, какой массаж"
    result = search_kb(
        test_tenant,
        query,
        provider=provider,
        k=3,
        doc_types=["service"],
    )
    _assert_global_hit(
        hits=result.hits,
        expected_substring="расслаб",
        query=query,
    )


def test_contraindication_pregnancy_laser(
    test_tenant: Tenant,
    provider: OpenAIProvider,
) -> None:
    """Q2 — health-screening lookup. Must match the contraindication corpus."""
    query = "беременна, можно ли лазер"
    result = search_kb(
        test_tenant,
        query,
        provider=provider,
        k=3,
        doc_types=["contraindication"],
    )
    _assert_global_hit(
        hits=result.hits,
        expected_substring="беремен",
        query=query,
    )


def test_aftercare_biorevitalization(
    test_tenant: Tenant,
    provider: OpenAIProvider,
) -> None:
    """Q3 — aftercare lookup. ``"после"`` or ``"уход"`` should surface."""
    query = "что делать после биоревитализации"
    result = search_kb(
        test_tenant,
        query,
        provider=provider,
        k=3,
        doc_types=["help_article"],
    )
    # Either "после" (aftercare) or "уход" (care) should surface in
    # the most relevant heading — accept either via a permissive
    # substring split. We assert generously here because Doc 3 +
    # Doc 4 both feed into the help_article surface and we don't want
    # to tie this test to one specific heading wording.
    assert len(result.hits) >= 1, f"no hits for {query!r}"
    top = result.hits[0]
    assert top.metadata.get("kb_source") == "global", (
        f"top hit for {query!r} not from global_kb — kb_source={top.metadata.get('kb_source')!r}"
    )
    haystack = " ".join(
        [
            str(top.metadata.get("section_title", "")),
            str(top.metadata.get("source_uri", "")),
            str(getattr(top, "text", "")),
        ]
    ).lower()
    # Russian root variation — accept any of the aftercare-adjacent
    # stems. "уход" / "ухаживать" / "ухода" all share "ухаж" or "ухо".
    # "после" is the strict aftercare marker. Aftercare tables in Doc 3
    # use column headers "Что делать" / "Чего избегать" — those are also
    # aftercare-by-concept. Widen the net so the test is robust to which
    # specific procedure's aftercare card surfaces (cosine may return
    # threads-aftercare instead of biorevitalization-aftercare; both are
    # legitimate aftercare hits proving the corpus is queryable).
    aftercare_tokens = (
        "после",
        "ухаж",
        "ухода",
        "уход",
        "восстанов",
        "реабилит",
        "что делать",
        "чего избегать",
        "красные флаги",
    )
    assert any(token in haystack for token in aftercare_tokens), (
        f"no aftercare-adjacent substring in top hit for {query!r}; "
        f"source_uri={top.metadata.get('source_uri')!r}, "
        f"text_preview={str(getattr(top, 'text', ''))[:200]!r}"
    )


def test_symptom_redness_after_procedure(
    test_tenant: Tenant,
    provider: OpenAIProvider,
) -> None:
    """Q4 — post-procedure symptom triage. Doc 4 surfaces in the same surface
    as Doc 2 (contraindication) — see PR body for Doc 4 classification.
    """
    query = "после процедуры покраснело, нормально?"
    # Filter by both shared surfaces so the test works regardless of
    # whether Doc 4 ended up as contraindication or help_article. The
    # global fallback fires when ALL doc_types in the filter are in the
    # whitelist {service, contraindication, help_article} — both of
    # ours are, so the merge happens cleanly.
    result = search_kb(
        test_tenant,
        query,
        provider=provider,
        k=5,
        doc_types=["contraindication", "help_article"],
    )
    # Lenient substring — top hit should mention redness ("краснот"),
    # a generic symptom ("симптом"), or the post-procedure window
    # ("после"). Any of these proves the symptom corpus surfaced.
    assert len(result.hits) >= 1, f"no hits for {query!r}"
    top = result.hits[0]
    assert top.metadata.get("kb_source") == "global", (
        f"top hit for {query!r} not from global_kb — kb_source={top.metadata.get('kb_source')!r}"
    )
    haystack = " ".join(
        [
            str(top.metadata.get("section_title", "")),
            str(top.metadata.get("source_uri", "")),
            str(getattr(top, "text", "")),
        ]
    ).lower()
    assert any(
        token in haystack for token in ("краснот", "покрасн", "симптом", "после", "реакц")
    ), (
        f"no symptom-related substring in top hit for {query!r}; "
        f"section_title={top.metadata.get('section_title')!r}, "
        f"source_uri={top.metadata.get('source_uri')!r}"
    )


def test_cross_type_back_pain(
    test_tenant: Tenant,
    provider: OpenAIProvider,
) -> None:
    """Q5 — cross-type lookup. Mixed service+contraindication filter.

    Demonstrates that the global fallback works when both filter
    doc_types are in the whitelist. At least one hit must come back
    from global; we don't pin which doc_type, only that the corpus
    surfaces *something* for this intent.
    """
    query = "болит спина, что предложите"
    result = search_kb(
        test_tenant,
        query,
        provider=provider,
        k=5,
        doc_types=["service", "contraindication"],
    )
    assert len(result.hits) >= 1, f"no hits returned for cross-type query {query!r}"
    # At least one hit must be from global — we don't require ALL of
    # them to be global because the merge may surface the empty
    # tenant collection too (which is fine; it just won't contribute).
    global_sources = [h for h in result.hits if h.metadata.get("kb_source") == "global"]
    assert len(global_sources) >= 1, (
        f"no global-sourced hits for {query!r}; "
        f"sources={[h.metadata.get('kb_source') for h in result.hits]}"
    )
