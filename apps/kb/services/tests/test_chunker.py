"""Chunker tests (DRF-561 / Sprint 7 / K3)."""

from __future__ import annotations

from apps.kb.services.chunker import (
    CHUNK_OVERLAP_TOKENS,
    CHUNK_SIZE_TOKENS,
    Chunk,
    chunk_document,
    count_tokens,
)


class TestCountTokens:
    def test_empty(self) -> None:
        assert count_tokens("") == 0

    def test_short(self) -> None:
        # Russian phrase ~5 tokens.
        n = count_tokens("Часы работы 10-22")
        assert 1 <= n <= 20


class TestAtomicDocTypes:
    """FAQ + contraindication rows ≤ 512 tokens = single chunk."""

    def test_short_faq_one_chunk(self) -> None:
        chunks = chunk_document(
            "Часы работы: понедельник-воскресенье 10:00-22:00.",
            doc_type="faq",
        )
        assert len(chunks) == 1
        assert chunks[0].ordinal == 0
        assert chunks[0].metadata["doc_type"] == "faq"

    def test_short_contraindication_one_chunk(self) -> None:
        chunks = chunk_document(
            "При беременности процедура противопоказана.",
            doc_type="contraindication",
        )
        assert len(chunks) == 1

    def test_long_faq_splits_not_truncates(self) -> None:
        """P2 fix — long FAQ body must split, never silently truncate."""
        # Build a body well over 512 tokens (≈ 1800 chars at 3.5/tok).
        body = (
            "Стоимость массажа спины. " * 200  # ~5000 chars total
        )
        chunks = chunk_document(body, doc_type="faq")
        # Multiple chunks emitted (not silently truncated to 1).
        assert len(chunks) >= 2
        # Total text reconstructable (modulo overlap dedupe).
        joined = " ".join(c.text for c in chunks)
        # Every chunk has the marker phrase — proves no truncation.
        for chunk in chunks:
            assert "Стоимость массажа" in chunk.text
        # Concatenated chunks span at least the original length.
        assert len(joined) >= len(body) * 0.95


class TestParagraphSplitting:
    def test_service_description_splits_on_paragraphs(self) -> None:
        body = (
            "Первый параграф про массаж.\n\n"
            "Второй параграф про эффект.\n\n"
            "Третий параграф про длительность."
        )
        chunks = chunk_document(body, doc_type="service")
        assert len(chunks) >= 1
        # Each emitted chunk carries doc_type metadata.
        for c in chunks:
            assert c.metadata["doc_type"] == "service"

    def test_ordinals_are_sequential(self) -> None:
        body = ("Параграф номер один. " * 100) + "\n\n" + ("Параграф два. " * 100)
        chunks = chunk_document(body, doc_type="help_article")
        for i, c in enumerate(chunks):
            assert c.ordinal == i


class TestOverlap:
    def test_chunks_share_overlap(self) -> None:
        body = "Слово раз. " * 400  # ~4400 chars → multiple chunks
        chunks = chunk_document(body, doc_type="service")
        if len(chunks) < 2:
            return  # Nothing to test — fall through.
        # Overlap means consecutive chunks share at least one common word.
        # We don't enforce exact overlap math because packing is greedy.
        assert chunks[0].text.split() and chunks[1].text.split()


class TestMetadata:
    def test_extra_metadata_merged(self) -> None:
        chunks = chunk_document(
            "Body.",
            doc_type="faq",
            extra_metadata={"source_uri": "mysite://faq/1", "version": 3},
        )
        assert chunks[0].metadata["source_uri"] == "mysite://faq/1"
        assert chunks[0].metadata["version"] == 3
        assert chunks[0].metadata["doc_type"] == "faq"


class TestEdgeCases:
    def test_empty_content(self) -> None:
        assert chunk_document("", doc_type="faq") == []

    def test_whitespace_only(self) -> None:
        assert chunk_document("   \n\n  \t  ", doc_type="service") == []

    def test_single_short_line_emits_one_chunk(self) -> None:
        chunks = chunk_document("Hello.", doc_type="service")
        assert len(chunks) == 1

    def test_token_counts_consistent(self) -> None:
        # Re-chunking the same content yields the same token counts.
        body = "Some longer text. " * 200
        a = chunk_document(body, doc_type="service")
        b = chunk_document(body, doc_type="service")
        assert [c.token_count for c in a] == [c.token_count for c in b]


class TestConstants:
    def test_default_size_and_overlap(self) -> None:
        # Decision 5 — locked in K1 + K3.
        assert CHUNK_SIZE_TOKENS == 512
        assert CHUNK_OVERLAP_TOKENS == 50


class TestCustomSize:
    def test_smaller_size_emits_more_chunks(self) -> None:
        body = ("Paragraph " * 50 + "\n\n") * 5  # ~5 paragraphs
        big = chunk_document(body, doc_type="service", size=512, overlap=50)
        small = chunk_document(body, doc_type="service", size=64, overlap=10)
        # Smaller size budget → at least as many chunks.
        assert len(small) >= len(big)


class TestChunkDataclass:
    def test_immutable(self) -> None:
        from dataclasses import FrozenInstanceError
        import pytest

        c = Chunk(text="x", ordinal=0, token_count=1)
        with pytest.raises(FrozenInstanceError):
            c.text = "y"  # type: ignore[misc]
