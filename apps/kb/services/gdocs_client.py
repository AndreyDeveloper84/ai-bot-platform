"""Google Docs read-only client (KB-RAG Sub-4b / GH #128).

Fetches Google Docs via the **public Markdown export endpoint**
(`https://docs.google.com/document/d/<id>/export?format=md`) — no auth,
no service account, no SDK. Each source Doc is link-shareable
("Anyone with the link can view") and the export URL works
anonymously.

### Why public-link export, not a service account

The previous implementation (PR #121 / Sub-4) used a GCP service
account. Operators on orgs with the `iam.disableServiceAccountKeyCreation`
"Secure by Default" policy can't generate the JSON key — so SA-based
auth is a dead end for the bot's deploy environment. The four source
documents are owned by the salon operator and are already
link-shareable; flipping a per-doc share toggle is something a
non-engineer can do in 10 seconds. We pivoted (GH #128) to the public
export endpoint and dropped `google-api-python-client` + `google-auth`.

### Security posture

* No credentials on disk, no env vars to leak.
* Doc IDs are 24+ char opaque strings; brute-force discovery is
  infeasible.
* Doc IDs live in env / management-command args, never in any URL the
  bot serves to end users — so there's no oracle.
* If tightening to a specific SA later becomes necessary (e.g. for
  audit logging), the pivot is single-file: swap `httpx.Client` for
  `googleapiclient.discovery.build` and put the SA load back in
  `__init__`. The public surface (dataclasses + `fetch_document`) does
  not change.

### Parsing model

The Markdown export Google produces uses ATX-style headings
(`# ` … `###### `) and one blank line between blocks. We do a
line-by-line parse:

* `^# `   → heading_level=1, text=rest
* `^## `  → 2, …, `^###### ` → 6
* anything else → heading_level=None, text=line (trailing whitespace stripped)
* empty / whitespace-only lines are dropped (no empty paragraphs)

Title resolution:

* first H1 if any → its text
* else first heading at any level → its text + WARN log
* else "Untitled" + WARN log

We deliberately do NOT try to parse Markdown formatting (lists, bold,
links, tables). The KB chunker (Sub-5) is heading-aware and treats the
rest as opaque prose; richer parsing is a future ticket if it ever
becomes valuable.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

import httpx

logger = logging.getLogger(__name__)


# Google's anonymous export endpoint. `follow_redirects=True` is
# REQUIRED — Google issues a 307 to a single-use `googleusercontent.com`
# URL that httpx follows transparently. Without it we'd see the redirect
# as the "response" and the body would be empty.
_EXPORT_URL_TEMPLATE = "https://docs.google.com/document/d/{doc_id}/export?format=md"

# Per-request timeout (connect + read). Generous because Google's export
# pipeline occasionally takes a few seconds for large docs. We don't
# retry — one shot, fail loud; the operator can re-run the seed command.
_DEFAULT_TIMEOUT_SECONDS = 30.0

# ATX heading detection: leading `#`s (1..6), then a space, then text.
# A trailing `#` decoration (`# Heading #`) is uncommon in the export
# format but we strip it defensively to match CommonMark.
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")


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
    """The doc isn't link-shareable (or some other non-404 failure).

    The most common cause is that the operator hasn't flipped the Doc
    to "Anyone with the link can view". The Sub-5 seed command surfaces
    this message verbatim to operators.
    """


class GoogleDocsNotFoundError(GoogleDocsError):
    """The document ID doesn't exist (typo, deleted, or wrong project)."""


class GoogleDocsClient:
    """Read-only client for Google Docs via the public Markdown export URL.

    Takes no credentials. Safe to construct anywhere — there is no
    filesystem or network I/O at `__init__` time.
    """

    def __init__(self, timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS) -> None:
        self._timeout = timeout_seconds

    def fetch_document(self, doc_id: str) -> GoogleDoc:
        """Fetch one Google Doc and parse its Markdown export into paragraphs.

        Args:
            doc_id: the long opaque ID from the Doc URL
                (`https://docs.google.com/document/d/<DOC_ID>/edit`).

        Returns:
            :class:`GoogleDoc` with title + ordered paragraphs.

        Raises:
            GoogleDocsNotFoundError: HTTP 404 — wrong `doc_id` or the
                doc was deleted.
            GoogleDocsAccessError: HTTP 403 (doc not link-shareable) or
                any other non-2xx response. Subclass of
                :class:`GoogleDocsError` so callers can broad-catch
                without importing `httpx`.
        """
        url = _EXPORT_URL_TEMPLATE.format(doc_id=doc_id)
        with httpx.Client(
            timeout=self._timeout,
            follow_redirects=True,
        ) as client:
            response = client.get(url)

        status = response.status_code
        if status == 404:
            raise GoogleDocsNotFoundError(
                f"Doc not found: {doc_id}. Check the ID is correct and the doc still exists."
            )
        if status == 403:
            raise GoogleDocsAccessError(
                f"Doc not link-shareable: {doc_id}. The doc must be set to "
                f"'Anyone with the link can view' in Google Docs sharing "
                f"settings."
            )
        if status >= 400:
            raise GoogleDocsAccessError(f"HTTP {status} fetching {doc_id}")

        return _parse_markdown(doc_id=doc_id, body=response.text)


# ---------------------------------------------------------------------------
# Markdown parsing (module-level so tests can pin behaviour)
# ---------------------------------------------------------------------------


def _parse_markdown(doc_id: str, body: str) -> GoogleDoc:
    """Convert a Markdown export body into a :class:`GoogleDoc`.

    Strategy:
      * line-by-line scan
      * ATX headings (`#`..`######`) → :class:`GoogleDocParagraph` with
        heading_level
      * any other non-empty line → paragraph with heading_level=None
      * empty / whitespace-only lines are dropped

    Title:
      * first H1 if any
      * else first heading at any level (+ WARN log)
      * else "Untitled" (+ WARN log)
    """
    paragraphs: list[GoogleDocParagraph] = []
    for raw_line in body.splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            continue
        match = _HEADING_RE.match(line)
        if match is not None:
            level = len(match.group(1))
            paragraphs.append(GoogleDocParagraph(text=match.group(2), heading_level=level))
        else:
            paragraphs.append(GoogleDocParagraph(text=line, heading_level=None))

    title = _resolve_title(doc_id=doc_id, paragraphs=paragraphs)
    return GoogleDoc(doc_id=doc_id, title=title, paragraphs=paragraphs)


def _resolve_title(doc_id: str, paragraphs: list[GoogleDocParagraph]) -> str:
    """Resolve the title with the documented fallback chain.

    H1 → first-heading-any-level (+ WARN) → "Untitled" (+ WARN).
    """
    first_h1 = next((p.text for p in paragraphs if p.heading_level == 1), None)
    if first_h1 is not None:
        return first_h1

    first_any = next((p.text for p in paragraphs if p.heading_level is not None), None)
    if first_any is not None:
        logger.warning(
            "gdocs.title.fallback doc_id=%s reason=no_h1 using=%r",
            doc_id,
            first_any,
        )
        return first_any

    logger.warning("gdocs.title.fallback doc_id=%s reason=no_headings using=Untitled", doc_id)
    return "Untitled"
