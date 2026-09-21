"""DRF-2065 — проба LLM различает «основной путь жив», «работаем на резерве», «оба лежат».

Инцидент 16–17.09: прокси отказывал на уровне TCP (ConnectError за 1 с), а
прямой путь к Anthropic был (403 за 0.1 с). Простой 9 ч 15 мин. Проба знала
только «сеть или прокси» — из чата не было видно, что менять надо прокси.

После DRF-2147 бот умеет уйти на запасного провайдера (``FallbackProvider``),
но проба об этом не знает: при живом резерве она кричит «Бот сейчас отвечает
клиентам аварийным текстом» — и это неправда. Здесь:

* ``TestPathState`` — после тика в кеше лежит состояние пути: primary /
  fallback / down, и его читает readyz (см. ``test_readyz_llm_path_2065``);
* ``TestFallbackProbedOnlyWhenNeeded`` — резерв пробуется только когда
  основной не ответил: в штатное время тик стоит ровно один вызов, как
  сегодня (цена и частота — DRF-1054/1056);
* ``TestDirectPath`` — при сетевом отказе ЧЕРЕЗ ПРОКСИ проба пробует прямой
  путь и пишет в алерт «прямой путь: есть/нет»; любой HTTP-ответ (даже 403
  региона) значит «сеть жива — меняйте прокси»;
* ``TestDownMessageTellsTheTruth`` — текст алерта не обещает аварийный текст,
  когда клиентам отвечает резерв.

Шов — ``health.build_probe_provider``: тот же, что у DRF-1631.
"""

from __future__ import annotations

import pytest

from apps.llm import health

pytestmark = pytest.mark.django_db


# --- исключения с именами SDK: классификация идёт по имени класса ---------


class APIConnectionError(Exception):
    pass


class PermissionDeniedError(Exception):
    pass


class InternalServerError(Exception):
    pass


class _Fake:
    """Провайдер пробы: отвечает или бросает заданное; пишет, как его построили."""

    def __init__(self, name: str, outcome: Exception | None, calls: list, kw: dict) -> None:
        self.name = name
        self._outcome = outcome
        calls.append((name, kw.get("proxy", "<settings>")))

    async def complete(self, messages, **kwargs):
        if self._outcome is not None:
            raise self._outcome
        return object()

    async def aclose(self) -> None:
        return None


@pytest.fixture(autouse=True)
def _isolated(settings):
    settings.CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "llm-path-2065",
        }
    }
    settings.LLM_HEALTH_PROBE_ENABLED = True
    settings.LLM_HEALTH_FAILURE_THRESHOLD = 1
    settings.LLM_HEALTH_STATE_TTL_S = 3600
    settings.LLM_PROVIDER = "anthropic"
    settings.ANTHROPIC_API_KEY = "sk-ant-test"  # pragma: allowlist secret
    settings.OPENAI_API_KEY = "sk-test"  # pragma: allowlist secret
    settings.ANTHROPIC_PROXY = "http://proxy.example:3128"
    settings.OPENAI_PROXY = ""
    settings.LLM_QUOTA_FALLBACK_ENABLED = True
    settings.LLM_FALLBACK_ORDER = ["anthropic", "openai"]
    settings.HANDOFF_NOTIFY_MAX_CHAT_IDS = []
    settings.HANDOFF_NOTIFY_MAX_USER_IDS = []
    from django.core.cache import cache

    cache.clear()
    health.reset_state()
    yield
    health.reset_state()


@pytest.fixture
def pages(monkeypatch) -> list[dict]:
    sent: list[dict] = []

    def _page(severity, title, body, *, dedup_key=None, **_):
        sent.append({"severity": severity, "title": title, "body": body})
        return True

    monkeypatch.setattr("apps.observability.alerting.page", _page)
    return sent


def _world(monkeypatch, *, primary=None, fallback=None, direct=None) -> list:
    """Подменить мир: исход основного (через прокси), резерва и прямого пути."""

    calls: list = []

    def _build(name: str, **kw):
        if name == "anthropic" and kw.get("proxy") == "":
            return _Fake(name, direct, calls, kw)
        if name == "anthropic":
            return _Fake(name, primary, calls, kw)
        return _Fake(name, fallback, calls, kw)

    monkeypatch.setattr(health, "build_probe_provider", _build)
    return calls


def _path() -> dict:
    return health.read_path_state()


class TestPathState:
    def test_primary_alive(self, monkeypatch, pages) -> None:
        _world(monkeypatch)
        health.check_llm_availability()
        state = _path()
        assert state["state"] == health.PATH_PRIMARY, state
        assert state["primary"] == "anthropic"
        assert state["checked_at"], state

    def test_primary_down_fallback_alive(self, monkeypatch, pages) -> None:
        _world(monkeypatch, primary=APIConnectionError("proxy refused"))
        health.check_llm_availability()
        state = _path()
        assert state["state"] == health.PATH_FALLBACK, state
        assert state["fallback"] == "openai", state

    def test_both_down(self, monkeypatch, pages) -> None:
        _world(
            monkeypatch,
            primary=APIConnectionError("proxy refused"),
            fallback=APIConnectionError("proxy refused"),
        )
        health.check_llm_availability()
        assert _path()["state"] == health.PATH_DOWN

    def test_nothing_measured_is_unknown_not_healthy(self) -> None:
        """Пустой кеш — не «основной жив»: неизмеренное здоровьем не называется."""
        assert _path()["state"] == health.PATH_UNKNOWN

    def test_fallback_switched_off_means_down_not_fallback(
        self, monkeypatch, settings, pages
    ) -> None:
        settings.LLM_QUOTA_FALLBACK_ENABLED = False
        calls = _world(monkeypatch, primary=APIConnectionError("proxy refused"))
        health.check_llm_availability()
        assert _path()["state"] == health.PATH_DOWN
        assert "openai" not in [name for name, _ in calls], calls

    def test_no_fallback_key_means_down(self, monkeypatch, settings, pages) -> None:
        settings.OPENAI_API_KEY = ""
        _world(monkeypatch, primary=APIConnectionError("proxy refused"))
        health.check_llm_availability()
        state = _path()
        assert state["state"] == health.PATH_DOWN
        assert state["fallback"] is None, state


class TestFallbackProbedOnlyWhenNeeded:
    def test_healthy_tick_is_exactly_one_call(self, monkeypatch, pages) -> None:
        """Положительная пара к «резерв пробуется»: в штатное время — нет."""
        calls = _world(monkeypatch)
        health.check_llm_availability()
        assert calls == [("anthropic", "<settings>")], calls

    def test_primary_down_probes_the_router_candidate(self, monkeypatch, pages) -> None:
        """Кандидат — из ``fallback_candidates`` DRF-2147, а не из списка здесь."""
        calls = _world(monkeypatch, primary=InternalServerError("500"))
        health.check_llm_availability()
        assert ("openai", "<settings>") in calls, calls


class TestDirectPath:
    def test_network_failure_through_proxy_tries_direct(self, monkeypatch, pages) -> None:
        calls = _world(
            monkeypatch,
            primary=APIConnectionError("proxy refused"),
            direct=PermissionDeniedError("403 region"),
        )
        health.check_llm_availability()
        assert ("anthropic", "") in calls, calls
        assert _path()["direct_path"] is True

    def test_direct_also_network_failure_means_no_direct_path(self, monkeypatch, pages) -> None:
        _world(
            monkeypatch,
            primary=APIConnectionError("proxy refused"),
            direct=APIConnectionError("no route"),
        )
        health.check_llm_availability()
        assert _path()["direct_path"] is False

    def test_provider_error_does_not_try_direct(self, monkeypatch, pages) -> None:
        """500 от провайдера — не про сеть; прямой путь ничего не прояснит."""
        calls = _world(monkeypatch, primary=InternalServerError("500"))
        health.check_llm_availability()
        # Положительная пара: основной правда пробовался — иначе «прямой не звали» пусто.
        assert ("anthropic", "<settings>") in calls, calls
        assert ("anthropic", "") not in calls, calls
        assert _path()["direct_path"] is None

    def test_no_proxy_configured_does_not_try_direct(self, monkeypatch, settings, pages) -> None:
        """Без прокси упавший путь и есть прямой — второй раз его не зовём."""
        settings.ANTHROPIC_PROXY = ""
        settings.OPENAI_PROXY = ""
        calls = _world(monkeypatch, primary=APIConnectionError("no route"))
        health.check_llm_availability()
        # Положительная пара: основной правда пробовался — иначе «прямой не звали» пусто.
        assert ("anthropic", "<settings>") in calls, calls
        assert ("anthropic", "") not in calls, calls

    def test_openai_proxy_fallback_counts_as_a_proxy(self, monkeypatch, settings, pages) -> None:
        """``anthropic_provider.py:61``: пустой ANTHROPIC_PROXY → OPENAI_PROXY. Проба видит обе."""
        settings.ANTHROPIC_PROXY = ""
        settings.OPENAI_PROXY = "http://other-proxy.example:3128"
        calls = _world(
            monkeypatch,
            primary=APIConnectionError("proxy refused"),
            direct=PermissionDeniedError("403 region"),
        )
        health.check_llm_availability()
        assert ("anthropic", "") in calls, calls


class TestDownMessageTellsTheTruth:
    def test_on_fallback_the_alert_does_not_promise_the_emergency_text(
        self, monkeypatch, pages
    ) -> None:
        _world(monkeypatch, primary=APIConnectionError("proxy refused"))
        health.check_llm_availability()
        assert pages, "основной путь лёг — операторы должны узнать"
        body = pages[-1]["body"]
        assert "резерв" in body.lower() and "openai" in body, body
        assert "аварийным текстом" not in body, body

    def test_both_down_the_alert_says_emergency_text(self, monkeypatch, pages) -> None:
        """Положительная пара: когда клиентам правда отвечает заглушка — так и сказано."""
        _world(
            monkeypatch,
            primary=APIConnectionError("proxy refused"),
            fallback=APIConnectionError("proxy refused"),
        )
        health.check_llm_availability()
        assert "аварийным текстом" in pages[-1]["body"]

    def test_direct_path_verdict_is_in_the_alert(self, monkeypatch, pages) -> None:
        _world(
            monkeypatch,
            primary=APIConnectionError("proxy refused"),
            fallback=APIConnectionError("proxy refused"),
            direct=PermissionDeniedError("403 region"),
        )
        health.check_llm_availability()
        body = pages[-1]["body"]
        assert "Прямой путь к провайдеру: есть" in body, body
        assert "прокси" in body.lower(), body

    def test_proxy_credentials_hidden_host_kept(self, monkeypatch, settings, pages) -> None:
        settings.ANTHROPIC_PROXY = (
            "http://user:s3cret@proxy.example:3128"  # pragma: allowlist secret
        )
        _world(
            monkeypatch,
            primary=APIConnectionError(
                "cannot CONNECT http://user:s3cret@proxy.example:3128"
            ),  # pragma: allowlist secret
            fallback=APIConnectionError("x"),
            direct=PermissionDeniedError("403"),
        )
        health.check_llm_availability()
        body = pages[-1]["body"]
        # Учётные данные — никогда.
        assert "s3cret" not in body and "user:" not in body, body
        # Хост — остаётся (решение на GO DRF-2065): оператору он нужен, чтобы
        # понять, КАКОЙ прокси менять. Узел, чтобы следующая правка
        # redact_secrets не спрятала его молча.
        assert "proxy.example:3128" in body, body


class TestPathChangeWhileDown:
    """Основной лежит, а резерв умер или ожил — объявленное перестало быть правдой."""

    def test_fallback_lost_is_paged_once(self, monkeypatch, pages) -> None:
        _world(monkeypatch, primary=APIConnectionError("proxy refused"))
        health.check_llm_availability()
        assert _path()["state"] == health.PATH_FALLBACK
        before = len(pages)

        _world(
            monkeypatch,
            primary=APIConnectionError("proxy refused"),
            fallback=APIConnectionError("proxy refused"),
        )
        health.check_llm_availability()
        assert _path()["state"] == health.PATH_DOWN
        new = pages[before:]
        assert len(new) == 1, new
        assert new[0]["severity"] == "critical", new
        assert "аварийным текстом" in new[0]["body"], new

        # Тот же расклад на следующем тике — не новость.
        health.check_llm_availability()
        assert len(pages) == before + 1, pages[before:]

    def test_fallback_regained_is_paged(self, monkeypatch, pages) -> None:
        _world(
            monkeypatch,
            primary=APIConnectionError("proxy refused"),
            fallback=APIConnectionError("proxy refused"),
        )
        health.check_llm_availability()
        before = len(pages)
        _world(monkeypatch, primary=APIConnectionError("proxy refused"))
        health.check_llm_availability()
        new = pages[before:]
        assert len(new) == 1, new
        assert "резерв" in new[0]["body"].lower() and "аварийным текстом" not in new[0]["body"]
