"""Google Docs read-only client (KB-RAG Sub-4 / GH #117).

Thin wrapper around the official ``google-api-python-client`` Discovery
SDK that flattens a Google Doc into a list of paragraphs + heading
levels. The KB pipeline (Sub-5 seed command, follow-ups) calls this to
ingest salon-managed FAQ / policy documents without humans having to
copy-paste content into the platform.

### Why service-account auth (not OAuth)

The platform is the consumer; humans don't sign in. Each operator
provisions one GCP project + one service account, downloads the JSON
key, and shares each source document with the SA's email as Viewer.
No interactive auth flow, no token refresh edge cases, no per-user
consent screen — just a static credential mounted into the workers.

### Why lazy auth

The module must import cleanly in CI and on a freshly-cloned laptop
where the credentials file isn't yet on disk. The Sub-5 management
command does a dry-run / help mode that doesn't actually hit Google;
that path must not fail at import. Credentials load on first
:meth:`GoogleDocsClient.fetch_document` call and are cached for the
lifetime of the client instance.

### What we parse out

Only paragraphs with non-empty text. We do not currently descend into
tables, structural elements, or rich formatting beyond heading level.
Sub-5 (seed command) is the consumer; that ticket's chunker is
heading-aware, which is the only structural signal we need. If a later
ticket wants links, tables, or inline images, this module gets
extended — don't read this dataclass as a contract for a generic
gdocs-to-markdown converter.

### Security posture

* The credentials path is logged exactly **once** at first auth, at
  ``logging.INFO``, so an operator debugging "wrong file mounted" can
  see it. After that, errors that surface to users (HttpError wraps
  below) never include the path — leaking it tells an attacker which
  file to target.
* The credentials *contents* are NEVER logged. We pass the path to
  ``from_service_account_file`` and forget it.
* HttpError 403 / 404 are wrapped into module-specific exceptions with
  human-readable hints (most-likely-cause), not bare tracebacks — the
  Sub-5 command surfaces these to a salon operator who doesn't speak
  HTTP.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from google.oauth2 import service_account
from googleapiclient.discovery import build  # type: ignore[import-untyped]
from googleapiclient.errors import HttpError  # type: ignore[import-untyped]

logger = logging.getLogger(__name__)


# Read-only scopes — documents.readonly is the minimum we need; the
# drive.readonly scope is required to fetch a doc that lives on a
# user's Drive (vs a "shared with you" link that only carries the doc
# scope). Adding it costs nothing because the SA only has access to
# explicitly-shared documents anyway.
_SCOPES = [
    "https://www.googleapis.com/auth/documents.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]


# Google Docs API encodes heading level as a string. Map back to int 1..6;
# everything else (NORMAL_TEXT, TITLE, SUBTITLE, absent) → None.
_HEADING_STYLE_TO_LEVEL: dict[str, int] = {
    "HEADING_1": 1,
    "HEADING_2": 2,
    "HEADING_3": 3,
    "HEADING_4": 4,
    "HEADING_5": 5,
    "HEADING_6": 6,
}


@dataclass(frozen=True)
class GoogleDocParagraph:
    """One paragraph in a Google Doc — possibly a heading."""

    text: str
    # Heading level 1..6 if this paragraph is a heading, else None.
    heading_level: int | None = None


@dataclass(frozen=True)
class GoogleDoc:
    """In-memory representation of a fetched Google Doc."""

    doc_id: str
    title: str
    paragraphs: list[GoogleDocParagraph] = field(default_factory=list)


class GoogleDocsError(Exception):
    """Base class for module-specific errors. Catch this for "any gdocs failure"."""


class GoogleDocsAccessError(GoogleDocsError):
    """The service account can't read the document.

    Almost always a "doc not shared with the SA email" mistake. The Sub-5
    command surfaces this message verbatim to operators.
    """


class GoogleDocsNotFoundError(GoogleDocsError):
    """The document ID doesn't exist (typo, deleted, or wrong project)."""


class GoogleDocsClient:
    """Read-only client for Google Docs via service account.

    Lazy auth — credentials are loaded on first call, not at import. Allows
    the module to import cleanly in environments without the key file (CI
    unit tests, Sub-5 management command dry-run on a fresh laptop).
    """

    def __init__(self, credentials_path: str | None = None) -> None:
        # Store the path; don't touch the filesystem here. Validation +
        # loading happen on the first fetch.
        self._credentials_path = credentials_path
        # Memoised Discovery service handle. None until first fetch.
        self._service: object | None = None

    def fetch_document(self, doc_id: str) -> GoogleDoc:
        """Fetch one Google Doc and parse it into paragraphs.

        Args:
            doc_id: the long opaque ID from the Doc URL
                (``https://docs.google.com/document/d/<DOC_ID>/edit``).

        Returns:
            :class:`GoogleDoc` with title + ordered paragraphs.

        Raises:
            FileNotFoundError: when ``credentials_path`` doesn't exist
                on disk. Caught at first fetch — see lazy-auth rationale
                in the module docstring.
            GoogleDocsAccessError: HttpError 403 — usually means the doc
                wasn't shared with the service-account email.
            GoogleDocsNotFoundError: HttpError 404 — wrong doc_id or the
                doc was deleted.
            GoogleDocsError: any other HttpError. Subclass of Exception
                so callers can broad-catch without importing
                googleapiclient.
        """
        service = self._get_service()
        try:
            result = service.documents().get(documentId=doc_id).execute()  # type: ignore[attr-defined]
        except HttpError as e:
            self._raise_for_http_error(e, doc_id)

        title: str = result.get("title", "") or ""
        body = result.get("body", {}) or {}
        content = body.get("content", []) or []

        paragraphs = [p for p in (_parse_paragraph(el) for el in content) if p is not None]

        return GoogleDoc(doc_id=doc_id, title=title, paragraphs=paragraphs)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _get_service(self) -> object:
        """Lazily build and cache the Discovery service handle.

        First call validates the credentials path exists, loads the JSON,
        and builds the docs/v1 service. Subsequent calls return the
        cached handle — credentials are not re-read from disk.
        """
        if self._service is not None:
            return self._service

        if self._credentials_path is None:
            raise FileNotFoundError(
                "GoogleDocsClient: no credentials path configured. Set "
                "GOOGLE_DOCS_SERVICE_ACCOUNT_FILE in the environment "
                "(see docs/operations/google-docs-credentials.md)."
            )

        creds_path = Path(self._credentials_path)
        if not creds_path.is_file():
            # Setup-time error — include the path so the operator can
            # fix the mount. After this point, user-visible errors must
            # NOT include the path (see module docstring).
            raise FileNotFoundError(
                f"GoogleDocsClient: credentials file not found at "
                f"{creds_path}. See docs/operations/google-docs-credentials.md "
                f"for setup steps."
            )

        # One-time INFO log so operators have a breadcrumb in case the
        # wrong file is mounted. Path only — never the file contents.
        logger.info("gdocs.auth.load path=%s", creds_path)

        credentials = service_account.Credentials.from_service_account_file(
            str(creds_path), scopes=_SCOPES
        )
        # cache_discovery=False silences the deprecation warning on the
        # default file-cache (httplib2 doesn't ship one on py3) and
        # makes the discovery call deterministic for tests.
        self._service = build("docs", "v1", credentials=credentials, cache_discovery=False)
        return self._service

    def _raise_for_http_error(self, error: HttpError, doc_id: str) -> None:
        """Translate an HttpError into a module-specific exception.

        The original HttpError is chained via ``raise ... from error`` so
        a debugger can still inspect the underlying response, but the
        user-visible message never mentions the credentials path.
        """
        status = getattr(getattr(error, "resp", None), "status", None)
        if status == 403:
            raise GoogleDocsAccessError(
                f"Access denied to Google Doc '{doc_id}'. The most likely "
                f"cause is that the document hasn't been shared with the "
                f"service-account email as Viewer. See "
                f"docs/operations/google-docs-credentials.md → 'Adding a "
                f"new doc to the whitelist'."
            ) from error
        if status == 404:
            raise GoogleDocsNotFoundError(
                f"Google Doc '{doc_id}' not found. Either the doc_id is "
                f"wrong (check the URL) or the document has been deleted."
            ) from error
        raise GoogleDocsError(
            f"Google Docs API error (status={status}) while fetching document '{doc_id}'."
        ) from error


# ---------------------------------------------------------------------------
# Body-content parser (module-level so tests can pin behaviour later if needed)
# ---------------------------------------------------------------------------


def _parse_paragraph(content_element: dict) -> GoogleDocParagraph | None:
    """Convert one ``body.content[]`` entry into a paragraph (or None).

    Returns None when:
      * the element is structural (e.g. ``sectionBreak``, ``table``) and
        carries no ``paragraph`` key, OR
      * the paragraph's concatenated text is empty / whitespace-only.

    Non-textRun child elements (inline images, page breaks, etc.) are
    silently skipped — we only care about prose text for the KB chunker.
    """
    paragraph = content_element.get("paragraph")
    if paragraph is None:
        return None

    elements = paragraph.get("elements", []) or []
    text_parts: list[str] = []
    for el in elements:
        text_run = el.get("textRun") if isinstance(el, dict) else None
        if text_run is None:
            # Skip inline images, page breaks, person mentions, etc.
            continue
        text_parts.append(text_run.get("content", "") or "")

    text = "".join(text_parts).rstrip("\n").strip()
    if not text:
        return None

    style = (paragraph.get("paragraphStyle") or {}).get("namedStyleType")
    heading_level = _HEADING_STYLE_TO_LEVEL.get(style) if isinstance(style, str) else None

    return GoogleDocParagraph(text=text, heading_level=heading_level)
