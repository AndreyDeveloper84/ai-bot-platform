"""Google Docs client tests (KB-RAG Sub-4 / GH #117).

All tests mock the googleapiclient layer. No live API calls are made — the
client must remain testable on a fresh laptop without any service-account
credentials on disk.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from apps.kb.services.gdocs_client import (
    GoogleDoc,
    GoogleDocsAccessError,
    GoogleDocsClient,
    GoogleDocsNotFoundError,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _load_sample_response() -> dict[str, Any]:
    return json.loads((FIXTURES_DIR / "gdocs_sample_response.json").read_text(encoding="utf-8"))


def _make_http_error(status: int, reason: str = "") -> Exception:
    """Build a googleapiclient.errors.HttpError with the given status.

    HttpError signature accepts (resp, content); the client introspects
    ``resp.status``. We mock the resp so tests don't need to construct
    real httplib2 Response objects.
    """
    from googleapiclient.errors import HttpError  # type: ignore[import-untyped]

    resp = MagicMock()
    resp.status = status
    resp.reason = reason or ("Forbidden" if status == 403 else "Not Found")
    return HttpError(resp=resp, content=b'{"error": {"message": "stub"}}')


@pytest.fixture
def stub_credentials_file(tmp_path: Path) -> Path:
    """A placeholder file that is NOT a real service-account key.

    The literal "BEGIN STUB" marker makes it impossible for this fixture
    to be mistaken for a usable Google credential by a human reader or
    by detect-secrets (no PrivateKeyDetector hit).
    """
    creds = tmp_path / "fake-sa.json"
    creds.write_text(
        json.dumps(
            {
                "type": "service_account",
                "project_id": "stub-project",
                "private_key_id": "stub-id",
                "private_key": "-----BEGIN STUB-----\nnot-a-real-key\n-----END STUB-----\n",
                "client_email": "stub@stub-project.iam.gserviceaccount.com",
                "client_id": "0",
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        ),
        encoding="utf-8",
    )
    return creds


def _build_client_with_mock(
    stub_credentials_file: Path,
    api_response: dict[str, Any] | None = None,
    raises: Exception | None = None,
) -> tuple[GoogleDocsClient, MagicMock, MagicMock]:
    """Construct a GoogleDocsClient with mocked auth + discovery.

    Returns (client, creds_loader_mock, build_mock) so tests can assert
    on call counts (e.g. lazy / cached auth).
    """
    fake_service = MagicMock()
    fake_get = MagicMock()
    if raises is not None:
        fake_get.execute.side_effect = raises
    else:
        fake_get.execute.return_value = api_response or {}
    fake_service.documents.return_value.get.return_value = fake_get

    creds_loader = patch(
        "apps.kb.services.gdocs_client.service_account.Credentials.from_service_account_file",
        return_value=MagicMock(name="creds"),
    )
    build = patch(
        "apps.kb.services.gdocs_client.build",
        return_value=fake_service,
    )
    creds_mock = creds_loader.start()
    build_mock = build.start()
    client = GoogleDocsClient(credentials_path=str(stub_credentials_file))
    # Caller is responsible for stopping the patches via the returned mocks
    # — pytest doesn't support yield-from helpers cleanly here. Tests use
    # the explicit stop() in a try/finally pattern below.
    client._patch_handles = (creds_loader, build)  # type: ignore[attr-defined]
    return client, creds_mock, build_mock


def _teardown_client(client: GoogleDocsClient) -> None:
    handles = getattr(client, "_patch_handles", None)
    if handles is not None:
        for h in handles:
            h.stop()


def test_fetches_paragraphs_and_headings(stub_credentials_file: Path) -> None:
    """Parser returns a GoogleDoc with the expected paragraphs + heading levels."""
    client, _, _ = _build_client_with_mock(
        stub_credentials_file, api_response=_load_sample_response()
    )
    try:
        doc = client.fetch_document("doc-stub-id-1234567890")
    finally:
        _teardown_client(client)

    assert isinstance(doc, GoogleDoc)
    assert doc.doc_id == "doc-stub-id-1234567890"
    assert doc.title == "Salon FAQ — Stub"

    # Empty paragraph (just "\n") should have been filtered out.
    texts = [p.text for p in doc.paragraphs]
    assert "Часто задаваемые вопросы" in texts
    assert "Часы работы" in texts
    assert "Понедельник — воскресенье, 10:00–22:00." in texts
    assert "Запись на услуги" in texts
    assert "Записаться можно через бота или администратора." in texts

    by_text = {p.text: p for p in doc.paragraphs}
    assert by_text["Часто задаваемые вопросы"].heading_level == 1
    assert by_text["Часы работы"].heading_level == 2
    assert by_text["Запись на услуги"].heading_level == 3
    # NORMAL_TEXT and missing namedStyleType → heading_level is None.
    assert by_text["Понедельник — воскресенье, 10:00–22:00."].heading_level is None
    assert by_text["Записаться можно через бота или администратора."].heading_level is None


def test_parses_heading_levels_correctly(stub_credentials_file: Path) -> None:
    """Each HEADING_N style maps to N; NORMAL_TEXT / missing → None."""
    response: dict[str, Any] = {
        "title": "Heading Levels",
        "body": {
            "content": [
                {
                    "paragraph": {
                        "elements": [{"textRun": {"content": f"H{n}\n"}}],
                        "paragraphStyle": {"namedStyleType": f"HEADING_{n}"},
                    }
                }
                for n in range(1, 7)
            ]
            + [
                {
                    "paragraph": {
                        "elements": [{"textRun": {"content": "Normal\n"}}],
                        "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
                    }
                },
                {"paragraph": {"elements": [{"textRun": {"content": "MissingStyle\n"}}]}},
            ]
        },
    }
    client, _, _ = _build_client_with_mock(stub_credentials_file, api_response=response)
    try:
        doc = client.fetch_document("anydocid")
    finally:
        _teardown_client(client)

    by_text = {p.text: p.heading_level for p in doc.paragraphs}
    for n in range(1, 7):
        assert by_text[f"H{n}"] == n
    assert by_text["Normal"] is None
    assert by_text["MissingStyle"] is None


def test_skips_empty_paragraphs(stub_credentials_file: Path) -> None:
    """Paragraphs whose text is empty or whitespace-only are filtered."""
    response: dict[str, Any] = {
        "title": "Empties",
        "body": {
            "content": [
                {"paragraph": {"elements": [{"textRun": {"content": ""}}]}},
                {"paragraph": {"elements": [{"textRun": {"content": "\n"}}]}},
                {"paragraph": {"elements": [{"textRun": {"content": "   \t\n"}}]}},
                {"paragraph": {"elements": [{"textRun": {"content": "Real text\n"}}]}},
                # A non-paragraph structural element (e.g. table) — must not blow up.
                {"sectionBreak": {"sectionStyle": {}}},
            ]
        },
    }
    client, _, _ = _build_client_with_mock(stub_credentials_file, api_response=response)
    try:
        doc = client.fetch_document("anydocid")
    finally:
        _teardown_client(client)

    assert [p.text for p in doc.paragraphs] == ["Real text"]


def test_raises_on_missing_credentials_file(tmp_path: Path) -> None:
    """A non-existent credentials path → FileNotFoundError on first use."""
    client = GoogleDocsClient(credentials_path=str(tmp_path / "no-such-file.json"))
    with pytest.raises(FileNotFoundError):
        client.fetch_document("anything")


def test_raises_access_error_on_403(stub_credentials_file: Path) -> None:
    """HttpError 403 wraps into GoogleDocsAccessError with a helpful message."""
    client, _, _ = _build_client_with_mock(stub_credentials_file, raises=_make_http_error(403))
    try:
        with pytest.raises(GoogleDocsAccessError) as exc_info:
            client.fetch_document("forbidden-doc-id")
    finally:
        _teardown_client(client)
    msg = str(exc_info.value).lower()
    # Helpful message hints at the most likely cause.
    assert "share" in msg or "shared" in msg or "viewer" in msg or "service account" in msg
    # Must NOT leak the credentials path.
    assert "fake-sa.json" not in str(exc_info.value)


def test_raises_not_found_error_on_404(stub_credentials_file: Path) -> None:
    """HttpError 404 wraps into GoogleDocsNotFoundError."""
    client, _, _ = _build_client_with_mock(stub_credentials_file, raises=_make_http_error(404))
    try:
        with pytest.raises(GoogleDocsNotFoundError) as exc_info:
            client.fetch_document("missing-doc-id")
    finally:
        _teardown_client(client)
    msg = str(exc_info.value).lower()
    assert "not found" in msg or "doc_id" in msg or "deleted" in msg
    assert "fake-sa.json" not in str(exc_info.value)


def test_credentials_loaded_once_across_calls(stub_credentials_file: Path) -> None:
    """Lazy + cached auth: second fetch doesn't reload the credentials file."""
    client, creds_mock, build_mock = _build_client_with_mock(
        stub_credentials_file, api_response=_load_sample_response()
    )
    try:
        # Pre-call: credentials are NOT loaded eagerly at __init__.
        assert creds_mock.call_count == 0
        assert build_mock.call_count == 0

        client.fetch_document("doc-a")
        client.fetch_document("doc-b")

        # Credentials loaded exactly once; build called exactly once.
        assert creds_mock.call_count == 1
        assert build_mock.call_count == 1
    finally:
        _teardown_client(client)
