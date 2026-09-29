"""DRF-2065 — readyz говорит о пути к LLM: основной / резерв / оба лежат / неизвестно.

До DRF-2065 readyz о доступности LLM молчал: ``check_intent_router`` честно
пишет ``checked: False`` и смотрит на breaker старого пути, по которому
консьерж не ходит. 16–17.09 стенд 9 ч отвечал аварийным текстом при
зелёном readyz.

Правила этого файла:

* readyz **не зовёт LLM** — читает состояние, которое оставила
  периодическая проба (``apps.llm.health``); иначе readyz зависит от
  внешнего API и стоит денег на каждый опрос;
* неизмеренное и устаревшее — ``unknown``, а не «жив»: если beat умер,
  readyz не должен вечно показывать последний зелёный тик;
* LLM **не переворачивает readyz в 503** (решение на GO): деплой-смоук
  (``deploy-dev.yml``: ``curl -fsS /readyz/``) упал бы ровно тогда, когда
  выкатывают починку, а бот при лежащей LLM всё равно отвечает лучшим, что
  у него есть — аварийным текстом. Состояние видно в теле, не в коде ответа.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.core.cache import cache
from django.utils import timezone

from apps.llm import health
from apps.orchestrator.health import pipeline_health

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _isolated(settings):
    settings.CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "readyz-llm-2065",
        }
    }
    settings.CHROMA_HTTP_HOST = ""
    cache.clear()
    yield
    cache.clear()


def _store(state: str, *, fallback: str | None = None, age_s: int = 0, direct=None) -> None:
    health.write_path_state(
        state=state,
        primary="anthropic",
        fallback=fallback,
        direct_path=direct,
        checked_at=(timezone.now() - timedelta(seconds=age_s)).isoformat(),
    )


def _llm() -> dict:
    return pipeline_health()["llm"]


class TestThreeStatesAndUnknown:
    def test_primary(self) -> None:
        _store(health.PATH_PRIMARY)
        assert _llm()["state"] == "primary"

    def test_on_fallback_names_the_vendor(self) -> None:
        _store(health.PATH_FALLBACK, fallback="openai")
        check = _llm()
        assert check["state"] == "fallback"
        assert check["fallback"] == "openai"
        assert check["primary"] == "anthropic"

    def test_both_down(self) -> None:
        _store(health.PATH_DOWN, direct=True)
        check = _llm()
        assert check["state"] == "down"
        assert check["direct_path"] is True

    def test_never_measured_is_unknown(self) -> None:
        assert _llm()["state"] == "unknown"

    def test_stale_measurement_is_unknown_not_green(self, settings) -> None:
        """Проба раз в 5 минут; три пропущенных тика — это уже не знание."""
        settings.LLM_HEALTH_PATH_STALE_S = 900
        _store(health.PATH_PRIMARY, age_s=901)
        check = _llm()
        assert check["state"] == "unknown", check
        assert check.get("detail") == "stale", check

    def test_fresh_measurement_is_not_stale(self, settings) -> None:
        """Положительная пара к «устаревшее — unknown»."""
        settings.LLM_HEALTH_PATH_STALE_S = 900
        _store(health.PATH_PRIMARY, age_s=60)
        assert _llm()["state"] == "primary"


class TestReadyzDoesNotDependOnTheLLM:
    def test_no_llm_call_from_readyz(self, monkeypatch) -> None:
        def _boom(*a, **k):
            raise AssertionError("readyz построил провайдера LLM")

        monkeypatch.setattr("apps.llm.router.build_provider", _boom)
        monkeypatch.setattr(health, "build_probe_provider", _boom)
        _store(health.PATH_PRIMARY)
        assert _llm()["state"] == "primary"

    @pytest.mark.parametrize("state", ["primary", "fallback", "down", "unknown"])
    def test_llm_state_never_flips_readiness(self, state: str) -> None:
        if state != "unknown":
            _store(state, fallback="openai" if state == "fallback" else None)
        assert _llm()["ok"] is True


class TestReadyzEndpoint:
    def test_body_carries_the_llm_block(self, client) -> None:
        _store(health.PATH_FALLBACK, fallback="openai")
        response = client.get("/readyz/")
        body = response.json()
        assert body["checks"]["llm"]["state"] == "fallback", body
