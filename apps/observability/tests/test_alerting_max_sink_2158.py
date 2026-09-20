"""DRF-2158 — третий приёмник ``alerting.page``: MAX-чат операторов.

Факт стенда 20.09: ``ALERTS_TELEGRAM_CHAT_ID`` / ``TELEGRAM_BOT_TOKEN`` /
``SENTRY_DSN`` не заданы, поэтому каждый ``page(...)`` заканчивался записью
``alerting.telegram.skipped reason=no_credentials`` — тишиной. Единственный
работающий операторский канал — MAX по ``HANDOFF_NOTIFY_MAX_CHAT_IDS``, куда
до этого ходил только ``llm/health.py`` напрямую.

Сторожа здесь:

* Telegram и Sentry не заданы, MAX-список задан → MAX отправлен, ``page``
  вернул True, в аудите ``max_sent=true`` — ``TestMaxIsTheThirdSink``;
* список пуст → False + лог ``alerting.page.no_sinks`` —
  ``TestNoSinks``;
* dedup общий: второй ``page`` за 5 минут не даёт второго MAX —
  ``TestSharedDedup``;
* текст одной строкой «⚠️ [LEVEL] {title} — {body}», ≤ 1000 знаков, без
  телефонов и e-mail (ложный вход — телефон в ``body`` → замаскирован) —
  ``TestMaxText``;
* сбой MAX проглочен, ``page`` не падает — ``TestMaxFailureIsSwallowed``.

MAX подменён на уровне ``apps.handoff.notify.send_message`` — тот же шов,
что у ``apps/llm/tests/test_health.py``: сквозь ``send_max_notification`` и
выбор ключа адресации (``HANDOFF_NOTIFY_MAX_USER_IDS`` вытесняет
``_CHAT_IDS``) проходит настоящий код.
"""

from __future__ import annotations

import logging
from collections.abc import Generator
from unittest.mock import patch

import pytest
from django.core.cache import cache

from apps.audit.models import AuditLog
from apps.observability import alerting
from apps.observability.alerting import page

pytestmark = pytest.mark.django_db

NOTIFY_SEND = "apps.handoff.notify.send_message"
AUDIT_PAGED = "observability.alert.paged"

# Тестовый диапазон (префикс 900) — см. tools/lint/pii_guard.py.
TEST_PHONE = "+7 900 123-45-67"
TEST_EMAIL = "client@example.org"


@pytest.fixture(autouse=True)
def _stand_like(settings) -> Generator[None, None, None]:
    """Как на стенде: Telegram и Sentry не заданы, MAX-список из одного диалога."""

    settings.TELEGRAM_BOT_TOKEN = ""
    settings.ALERTS_TELEGRAM_CHAT_ID = ""
    settings.SENTRY_DSN = ""
    settings.HANDOFF_NOTIFY_MAX_CHAT_IDS = ["opchat-1"]
    settings.HANDOFF_NOTIFY_MAX_USER_IDS = []
    settings.ALERTS_DEDUP_TTL_SECONDS = 300
    cache.clear()
    yield
    cache.clear()


class SendRecorder:
    """Подмена ``channels.max.outbound.send_message`` — запоминает адрес и текст."""

    def __init__(self, exc: Exception | None = None) -> None:
        self.calls: list[dict] = []
        self.exc = exc

    def __call__(self, *, chat_id=None, user_id=None, text, attachments=None, timeout=10.0):
        self.calls.append({"chat_id": chat_id, "user_id": user_id, "text": text})
        if self.exc is not None:
            raise self.exc
        return {}

    @property
    def texts(self) -> list[str]:
        return [c["text"] for c in self.calls]


@pytest.fixture
def max_sent(monkeypatch) -> SendRecorder:
    rec = SendRecorder()
    monkeypatch.setattr(NOTIFY_SEND, rec)
    return rec


def _paged_row() -> AuditLog:
    return AuditLog.all_tenants.get(action=AUDIT_PAGED)


class TestMaxIsTheThirdSink:
    def test_page_with_only_max_configured_delivers_and_returns_true(self, max_sent) -> None:
        assert page("error", "Stale sync", "12 records behind", dedup_key="stale:1") is True

        assert len(max_sent.calls) == 1
        assert max_sent.calls[0]["chat_id"] == "opchat-1"
        assert max_sent.calls[0]["user_id"] is None
        assert max_sent.texts[0] == "⚠️ [ERROR] Stale sync — 12 records behind"

        row = _paged_row()
        assert row.payload["max_sent"] is True
        assert row.payload["telegram_sent"] is False
        assert row.payload["sentry_sent"] is False

    def test_warning_and_critical_carry_their_level(self, max_sent) -> None:
        page("warning", "Budget", "80% used", dedup_key="w")
        page("critical", "Scope", "flip", dedup_key="c")

        assert max_sent.texts[0].startswith("⚠️ [WARNING] Budget — 80% used")
        assert max_sent.texts[1].startswith("⚠️ [CRITICAL] Scope — flip")

    def test_user_ids_replace_chat_ids(self, settings, max_sent) -> None:
        """DRF-1559: люди вытесняют диалоги, а не дополняют их — иначе два сообщения."""

        settings.HANDOFF_NOTIFY_MAX_USER_IDS = ["person-1"]

        assert page("error", "T", "B", dedup_key="k") is True
        assert [(c["user_id"], c["chat_id"]) for c in max_sent.calls] == [("person-1", None)]

    def test_telegram_still_a_sink_when_configured(self, settings, max_sent) -> None:
        settings.TELEGRAM_BOT_TOKEN = "test-token"
        settings.ALERTS_TELEGRAM_CHAT_ID = "-100"
        with patch("apps.observability.alerting.requests", create=True) as req:
            req.post.return_value.ok = True
            assert page("error", "T", "B", dedup_key="k") is True
            assert req.post.call_count == 1

        assert len(max_sent.calls) == 1
        row = _paged_row()
        assert row.payload["telegram_sent"] is True
        assert row.payload["max_sent"] is True


class TestNoSinks:
    def test_empty_max_list_returns_false_and_logs_no_sinks(
        self, settings, max_sent, caplog
    ) -> None:
        settings.HANDOFF_NOTIFY_MAX_CHAT_IDS = []

        with caplog.at_level(logging.INFO, logger="apps.observability.alerting"):
            assert page("error", "T", "B", dedup_key="k") is False

        assert max_sent.calls == []
        assert any("alerting.page.no_sinks" in r.getMessage() for r in caplog.records)
        row = _paged_row()
        assert row.payload["max_sent"] is False
        assert row.payload["sentry_sent"] is False

    def test_sentry_without_dsn_is_not_counted_as_delivered(self, settings, max_sent) -> None:
        """До правки ``_send_sentry`` возвращал True, если sentry_sdk просто
        импортируется — ``page`` «доставлял» в ничто."""

        import sentry_sdk

        assert sentry_sdk.is_initialized() is False
        settings.HANDOFF_NOTIFY_MAX_CHAT_IDS = []

        assert page("error", "T", "B", dedup_key="k") is False


class TestSharedDedup:
    def test_second_page_within_window_sends_no_second_max(self, max_sent) -> None:
        first = page("error", "Same", "Same body")
        second = page("error", "Same", "Same body")

        assert first is True
        assert second is False
        assert len(max_sent.calls) == 1
        assert AuditLog.all_tenants.filter(action="observability.alert.deduped").count() == 1

    def test_dedup_window_is_five_minutes(self, max_sent) -> None:
        with patch.object(alerting.cache, "add", wraps=alerting.cache.add) as add:
            page("error", "Same", "Same body", dedup_key="five")

        assert add.call_count == 1
        assert add.call_args.kwargs["timeout"] == 300

    def test_different_keys_page_independently(self, max_sent) -> None:
        page("error", "T", "B", dedup_key="a")
        page("error", "T", "B", dedup_key="b")

        assert len(max_sent.calls) == 2


class TestMaxText:
    def test_phone_in_body_is_masked(self, max_sent) -> None:
        """Ложный вход: телефон подсажен в ``body`` — до MAX доходит маска."""

        body = f"клиент {TEST_PHONE} не дозвонился"
        assert TEST_PHONE in body  # вход действительно содержит телефон

        page("error", "Escalation", body, dedup_key="p")

        text = max_sent.texts[0]
        assert "[PHONE]" in text
        assert TEST_PHONE not in text
        assert "900 123" not in text

    def test_email_in_title_is_masked(self, max_sent) -> None:
        page("error", f"Письмо от {TEST_EMAIL}", "b", dedup_key="e")

        text = max_sent.texts[0]
        assert "[EMAIL]" in text
        assert TEST_EMAIL not in text

    def test_multiline_body_becomes_one_line(self, max_sent) -> None:
        body = "🔴 LLM недоступна\nНеудачных проверок подряд: 2\n\nПричина: сеть"
        assert "\n" in body

        page("critical", "LLM недоступна", body, dedup_key="m")

        text = max_sent.texts[0]
        assert "\n" not in text
        assert text == (
            "⚠️ [CRITICAL] LLM недоступна — 🔴 LLM недоступна · "
            "Неудачных проверок подряд: 2 · Причина: сеть"
        )

    def test_text_is_capped_at_1000_chars(self, max_sent) -> None:
        page("error", "Long", "x" * 3000, dedup_key="l")

        text = max_sent.texts[0]
        assert len(text) <= 1000
        assert text.startswith("⚠️ [ERROR] Long — xxx")
        assert text.endswith("…")

    def test_phone_is_masked_before_the_cut_not_after(self, max_sent) -> None:
        """Обрезка после маскировки: телефон на границе 1000 знаков не
        оставляет половину цифр."""

        body = "x" * 985 + " " + TEST_PHONE
        page("error", "T", body, dedup_key="cut")

        text = max_sent.texts[0]
        assert "x" * 100 in text
        assert "900 123" not in text
        assert "45-67" not in text


class TestMaxFailureIsSwallowed:
    def test_max_exception_returns_false_never_raises(self, monkeypatch, caplog) -> None:
        from apps.channels.max.outbound import MaxAPIError

        rec = SendRecorder(exc=MaxAPIError(500, "boom"))
        monkeypatch.setattr(NOTIFY_SEND, rec)

        with caplog.at_level(logging.WARNING):
            assert page("error", "T", "B", dedup_key="k") is False

        assert len(rec.calls) == 1
        assert _paged_row().payload["max_sent"] is False
