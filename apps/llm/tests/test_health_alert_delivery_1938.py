"""DRF-1938 — алерт «LLM недоступна» доходит не только до одного чата MAX.

Инцидент 15.09: проба перешла в DOWN в 07:40, сообщение ушло в единственный
чат MAX (``HANDOFF_NOTIFY_MAX_CHAT_IDS``) и осталось незамеченным; Telegram-канал
алертов на пилоте не настроен. Здесь:

* класс причины в тексте: сеть/прокси, провайдер, «не классифицирована» —
  ``TestCause``;
* переход DOWN/UP идёт и в ``alerting.page`` (Telegram + Sentry) рядом с MAX —
  ``TestPagedNextToMax``;
* ненастроенный Telegram виден в аудите, а не молчит; сбой канала не ломает
  пробу — ``TestDeliveryGapIsVisible``.

MAX подменён на уровне ``apps.handoff.notify.send_message`` — тот же шов, что
у ``test_health.py``.
"""

from __future__ import annotations

import pytest

from apps.audit.models import AuditLog
from apps.llm import health
from apps.llm.health import (
    CAUSE_NETWORK,
    CAUSE_PROVIDER,
    CAUSE_UNCLASSIFIED,
    TRANSITION_DOWN,
    TRANSITION_UP,
    ProbeResult,
    build_down_message,
    classify_cause,
    evaluate_probe,
)

pytestmark = pytest.mark.django_db

NOTIFY_SEND = "apps.handoff.notify.send_message"


@pytest.fixture(autouse=True)
def _isolated(settings):
    settings.CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "llm-health-1938",
        }
    }
    settings.LLM_HEALTH_FAILURE_THRESHOLD = 2
    settings.LLM_HEALTH_STATE_TTL_S = 3600
    settings.HANDOFF_NOTIFY_MAX_CHAT_IDS = ["opchat-1"]
    settings.HANDOFF_NOTIFY_MAX_USER_IDS = []
    health.reset_state()
    from django.core.cache import cache

    cache.clear()
    yield
    health.reset_state()


@pytest.fixture
def max_sent(monkeypatch) -> list[str]:
    texts: list[str] = []

    def _send(*, chat_id=None, user_id=None, text, attachments=None, timeout=10.0):
        texts.append(text)
        return {}

    monkeypatch.setattr(NOTIFY_SEND, _send)
    return texts


@pytest.fixture
def pages(monkeypatch) -> list[dict]:
    calls: list[dict] = []

    def _page(severity, title, body, *, dedup_key=None):
        calls.append({"severity": severity, "title": title, "body": body, "dedup_key": dedup_key})
        return True

    monkeypatch.setattr("apps.observability.alerting.page", _page)
    return calls


def _fail(cls: str = "APIConnectionError") -> ProbeResult:
    return ProbeResult(
        ok=False, latency_s=0.3, provider="anthropic", error_class=cls, error_message="refused"
    )


def _ok() -> ProbeResult:
    return ProbeResult(ok=True, latency_s=2.9, provider="anthropic")


class TestCause:
    @pytest.mark.parametrize("cls", ["APIConnectionError", "APITimeoutError", "ConnectError"])
    def test_network_classes(self, cls):
        assert classify_cause(cls) == CAUSE_NETWORK

    @pytest.mark.parametrize(
        "cls",
        [
            "InternalServerError",
            "RateLimitError",
            "LLMVendorCreditsExhausted",
            "AuthenticationError",
        ],
    )
    def test_provider_classes(self, cls):
        assert classify_cause(cls) == CAUSE_PROVIDER

    @pytest.mark.parametrize("cls", ["TimeoutError", "MysteryError", "", None])
    def test_unknown_is_not_guessed(self, cls):
        assert classify_cause(cls) == CAUSE_UNCLASSIFIED

    @pytest.mark.parametrize(
        ("cls", "line"),
        [
            ("APIConnectionError", "Причина: сеть или прокси недоступны"),
            ("InternalServerError", "Причина: провайдер отвечает ошибкой"),
            ("MysteryError", "Причина: причина не классифицирована"),
        ],
    )
    def test_down_message_names_the_cause_before_the_exception(self, cls, line):
        text = build_down_message(_fail(cls), failures=2)

        assert line in text.splitlines()
        assert text.index(line) < text.index(f"Ошибка: {cls}")


class TestPagedNextToMax:
    def test_down_goes_to_max_and_to_alerting_with_the_same_text(self, max_sent, pages):
        assert evaluate_probe(_fail()) != TRANSITION_DOWN
        assert evaluate_probe(_fail()) == TRANSITION_DOWN

        assert len(max_sent) == 1
        assert len(pages) == 1
        assert pages[0]["severity"] == "critical"
        assert pages[0]["title"] == "LLM недоступна"
        assert pages[0]["body"] == max_sent[0]
        assert "Причина: сеть или прокси недоступны" in pages[0]["body"]

    def test_recovery_goes_to_alerting_too(self, max_sent, pages):
        evaluate_probe(_fail())
        evaluate_probe(_fail())

        assert evaluate_probe(_ok()) == TRANSITION_UP

        assert len(max_sent) == 2
        assert [p["severity"] for p in pages] == ["critical", "warning"]
        assert pages[1]["body"] == max_sent[1]

    def test_no_repeat_page_while_still_down(self, max_sent, pages):
        for _ in range(5):
            evaluate_probe(_fail())

        assert len(pages) == 1

    def test_pages_even_when_max_has_no_recipients(self, settings, max_sent, pages):
        settings.HANDOFF_NOTIFY_MAX_CHAT_IDS = []
        evaluate_probe(_fail())
        evaluate_probe(_fail())

        assert max_sent == [] and len(pages) == 1


class TestDeliveryGapIsVisible:
    def test_unconfigured_telegram_leaves_an_audit_row_not_silence(self, settings, max_sent):
        settings.TELEGRAM_BOT_TOKEN = ""
        settings.ALERTS_TELEGRAM_CHAT_ID = ""

        evaluate_probe(_fail())
        evaluate_probe(_fail())

        rows = list(AuditLog.all_tenants.filter(action="observability.alert.paged"))
        assert len(rows) == 1
        assert rows[0].payload["telegram_sent"] is False
        assert rows[0].payload["title"] == "LLM недоступна"

    def test_a_failing_alert_channel_does_not_break_the_state_machine(self, monkeypatch, max_sent):
        def _boom(*_a, **_k):
            raise RuntimeError("alerting down")

        monkeypatch.setattr("apps.observability.alerting.page", _boom)

        evaluate_probe(_fail())
        assert evaluate_probe(_fail()) == TRANSITION_DOWN
        assert len(max_sent) == 1
