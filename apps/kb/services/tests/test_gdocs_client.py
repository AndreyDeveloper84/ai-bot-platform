"""Google Docs client tests (KB-RAG Sub-4b / GH #128).

The client fetches Google Docs via the public Markdown export endpoint
(`/document/d/<id>/export?format=md`) — no auth, no service account.
These tests mock `httpx.Client.get` so the suite stays hermetic.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx
import pytest

from apps.kb.services.gdocs_client import (
    GoogleDoc,
    GoogleDocsAccessError,
    GoogleDocsClient,
    GoogleDocsNotFoundError,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _load_sample_markdown() -> str:
    return (FIXTURES_DIR / "gdocs_sample_response.md").read_text(encoding="utf-8")


def _make_response(status_code: int, text: str = "") -> httpx.Response:
    """Build a stand-in httpx.Response with the given status + body.

    `httpx.Response` is real (not a mock) so the client's attribute
    accesses (`.status_code`, `.text`, `.headers`) are validated against
    the actual API surface — protecting us from drift if httpx changes.
    """
    return httpx.Response(
        status_code=status_code,
        text=text,
        request=httpx.Request("GET", "https://docs.google.com/document/d/doc-id/export?format=md"),
    )


def _patch_get(response: httpx.Response | Exception) -> Any:
    """Patch `httpx.Client.get` to return `response` (or raise it if an Exception)."""
    if isinstance(response, Exception):
        return patch("apps.kb.services.gdocs_client.httpx.Client.get", side_effect=response)
    return patch("apps.kb.services.gdocs_client.httpx.Client.get", return_value=response)


def test_fetches_doc_and_parses_headings() -> None:
    """Realistic Markdown export → GoogleDoc with right title + paragraph count."""
    response = _make_response(200, _load_sample_markdown())
    with _patch_get(response):
        doc = GoogleDocsClient().fetch_document("doc-stub-id-1234567890")

    assert isinstance(doc, GoogleDoc)
    assert doc.doc_id == "doc-stub-id-1234567890"
    # First H1 in the fixture is the title.
    assert doc.title == "Часто задаваемые вопросы"
    # Headings + body paragraphs, blank lines filtered out.
    texts = [p.text for p in doc.paragraphs]
    assert "Часто задаваемые вопросы" in texts
    assert "Часы работы" in texts
    assert "Запись на услуги" in texts
    assert "Через бота" in texts
    assert "Понедельник — воскресенье, 10:00–22:00." in texts
    # No empty paragraphs leaked through.
    assert all(p.text.strip() for p in doc.paragraphs)


def test_parses_heading_levels_1_through_6() -> None:
    """`#`..`######` map to heading_level 1..6; plain lines → None."""
    md = (
        "# H1 line\n"
        "## H2 line\n"
        "### H3 line\n"
        "#### H4 line\n"
        "##### H5 line\n"
        "###### H6 line\n"
        "Plain paragraph here.\n"
    )
    with _patch_get(_make_response(200, md)):
        doc = GoogleDocsClient().fetch_document("anydocid")

    by_text = {p.text: p.heading_level for p in doc.paragraphs}
    assert by_text["H1 line"] == 1
    assert by_text["H2 line"] == 2
    assert by_text["H3 line"] == 3
    assert by_text["H4 line"] == 4
    assert by_text["H5 line"] == 5
    assert by_text["H6 line"] == 6
    assert by_text["Plain paragraph here."] is None


def test_skips_empty_lines() -> None:
    """Blank / whitespace-only lines do not emit paragraphs."""
    md = "# Title\n\n   \n\t\nReal text.\n\n\nMore text.\n"
    with _patch_get(_make_response(200, md)):
        doc = GoogleDocsClient().fetch_document("anydocid")

    texts = [p.text for p in doc.paragraphs]
    assert texts == ["Title", "Real text.", "More text."]


def test_first_h1_becomes_title() -> None:
    """When multiple H1s exist, the FIRST one becomes the title."""
    md = "# First Title\n\nbody\n\n# Second Title\n\nmore body\n"
    with _patch_get(_make_response(200, md)):
        doc = GoogleDocsClient().fetch_document("anydocid")
    assert doc.title == "First Title"


def test_no_h1_falls_back_to_first_heading_with_warn(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Doc with no H1 but with H2/H3 → title = first heading, WARN logged."""
    md = "## Section\n\nbody\n\n### Sub\n\nmore body\n"
    with caplog.at_level(logging.WARNING, logger="apps.kb.services.gdocs_client"):
        with _patch_get(_make_response(200, md)):
            doc = GoogleDocsClient().fetch_document("anydocid")
    assert doc.title == "Section"
    assert any("no_h1" in rec.getMessage().lower() for rec in caplog.records)


def test_no_headings_at_all_falls_back_to_untitled_with_warn(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Headingless doc → title='Untitled', WARN logged."""
    md = "first line\nsecond line\n"
    with caplog.at_level(logging.WARNING, logger="apps.kb.services.gdocs_client"):
        with _patch_get(_make_response(200, md)):
            doc = GoogleDocsClient().fetch_document("anydocid")
    assert doc.title == "Untitled"
    assert any("no_headings" in rec.getMessage().lower() for rec in caplog.records)


def test_raises_not_found_on_404() -> None:
    """HTTP 404 → GoogleDocsNotFoundError with the doc_id in the message."""
    with _patch_get(_make_response(404, "")):
        with pytest.raises(GoogleDocsNotFoundError) as exc_info:
            GoogleDocsClient().fetch_document("missing-doc-id")
    msg = str(exc_info.value)
    assert "missing-doc-id" in msg
    assert "not found" in msg.lower() or "still exists" in msg.lower()


def test_raises_access_error_on_403_with_link_sharing_hint() -> None:
    """HTTP 403 → GoogleDocsAccessError that names the link-sharing fix."""
    with _patch_get(_make_response(403, "")):
        with pytest.raises(GoogleDocsAccessError) as exc_info:
            GoogleDocsClient().fetch_document("forbidden-doc-id")
    msg = str(exc_info.value).lower()
    assert "forbidden-doc-id" in str(exc_info.value)
    # Operator-facing hint must reference the link-sharing flip.
    assert "link" in msg and "share" in msg


def test_raises_access_error_on_500() -> None:
    """5xx → GoogleDocsAccessError with the status code surfaced."""
    with _patch_get(_make_response(500, "boom")):
        with pytest.raises(GoogleDocsAccessError) as exc_info:
            GoogleDocsClient().fetch_document("any-doc-id")
    assert "500" in str(exc_info.value)
    assert "any-doc-id" in str(exc_info.value)
