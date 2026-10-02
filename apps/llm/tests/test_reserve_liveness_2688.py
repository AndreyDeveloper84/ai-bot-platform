"""DRF-2688 — резервный вендор LLM проверяется, пока основной ещё жив.

30.09.2026 ключ Anthropic начал отвечать ``401 invalid``. С 01.10 основной —
OpenAI, Anthropic — резерв, и он мёртв. Проба пути (DRF-1054 / DRF-2065)
смотрит на резерв только ПОСЛЕ отказа основного, поэтому при живом OpenAI
о мёртвой страховке не говорит ничего: узнать о ней можно было только в
момент, когда она понадобится.

Здесь:

* ``TestTheScheduleNoticesADeadReserve`` — свойство целиком: всё, что beat
  запускает для LLM, при живом основном и мёртвом резерве будит оператора.
  На ``dev`` до правки — ноль сообщений;
* ``TestRejectedKey`` — отклонённый ключ назван словами и именем настройки,
  с первой проверки; значение ключа в сообщение не попадает; 403 и 500 —
  не «обнови ключ»;
* ``TestReserveStateMachine`` — на канал только переход: порог, тишина при
  повторе, восстановление, смена кандидата;
* ``TestWhichVendorIsTheReserve`` — проверяется тот, на кого уйдёт живой
  ход, и никто, если уходить некуда;
* ``TestNothingElseMoved`` — тик основного по-прежнему один вызов, а
  кандидатность резерва и порядок провайдеров проверка не трогает;
* ``TestReadyz`` / ``TestBeatShell`` — чтение состояния и оболочка Celery.

Шов — ``health.build_probe_provider``, тот же, что у DRF-1631 и DRF-2065.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.llm import health
from apps.llm.protocol import LLMError

pytestmark = pytest.mark.django_db


# --- исключения с именами SDK: классификация идёт по имени класса ---------


class AuthenticationError(Exception):
    pass


class PermissionDeniedError(Exception):
    pass


class APIConnectionError(Exception):
    pass


class InternalServerError(Exception):
    pass


#: Значения ключей — заведомо ненастоящие; узел проверяет, что они не доходят до текста.
_OPENAI_KEY = "sk-test-primary-0000"  # pragma: allowlist secret
_ANTHROPIC_KEY = "sk-ant-test-reserve-1111"  # pragma: allowlist secret


def _as_provider_raises(vendor: str, sdk_error: Exception) -> LLMError:
    """Ошибка в том виде, в каком её отдаёт настоящий провайдер.

    Оба провайдера заворачивают не-транспортный отказ SDK в ``LLMError``
    с причиной (``raise LLMError(...) from exc``); имя класса SDK проба
    достаёт из ``__cause__``. Узел идёт тем же путём, а не подаёт пробе
    уже развёрнутое исключение.
    """

    wrapped = LLMError(f"{vendor}.complete: {type(sdk_error).__name__}: {sdk_error}")
    wrapped.__cause__ = sdk_error
    return wrapped


def _rejected(vendor: str, *, quoting: str = "") -> LLMError:
    return _as_provider_raises(
        vendor, AuthenticationError(f"Error code: 401 - API key is invalid {quoting}".strip())
    )


class _Fake:
    """Провайдер пробы: отвечает или бросает заданное; пишет, кого построили."""

    def __init__(self, name: str, outcome: Exception | None, calls: list[str]) -> None:
        self.name = name
        self._outcome = outcome
        calls.append(name)

    async def complete(self, messages, **kwargs):
        if self._outcome is not None:
            raise self._outcome
        return object()

    async def aclose(self) -> None:
        return None


@pytest.fixture(autouse=True)
def _isolated(settings):
    """Конфигурация стенда с 01.10: основной openai, резерв anthropic."""

    settings.CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "llm-reserve-2688",
        }
    }
    settings.LLM_HEALTH_PROBE_ENABLED = True
    settings.LLM_HEALTH_RESERVE_PROBE_ENABLED = True
    settings.LLM_HEALTH_FAILURE_THRESHOLD = 2
    settings.LLM_HEALTH_STATE_TTL_S = 3600
    settings.LLM_PROVIDER = "openai"
    settings.SKILL_LLM_PROVIDER = {}
    settings.OPENAI_API_KEY = _OPENAI_KEY
    settings.ANTHROPIC_API_KEY = _ANTHROPIC_KEY
    settings.OPENAI_PROXY = ""
    settings.ANTHROPIC_PROXY = ""
    settings.LLM_QUOTA_FALLBACK_ENABLED = True
    settings.LLM_FALLBACK_ORDER = []
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


def _world(monkeypatch, **outcomes: Exception | None) -> list[str]:
    """Подменить мир: исход по имени вендора; не названный — отвечает."""

    calls: list[str] = []

    def _build(name: str, **kw):
        return _Fake(name, outcomes.get(name), calls)

    monkeypatch.setattr(health, "build_probe_provider", _build)
    return calls


def _run_everything_beat_runs_for_llm() -> list[str]:
    """Запустить по разу каждую задачу ``apps.llm.tasks.*`` из расписания beat.

    Не список задач в узле, а само расписание: свойство «мёртвый резерв
    замечают» принадлежит выкладке, а не функции, которую можно написать
    и забыть поставить в beat.
    """

    import importlib

    from django.conf import settings as dj_settings

    ran: list[str] = []
    for entry in dj_settings.CELERY_BEAT_SCHEDULE.values():
        dotted = entry["task"]
        if not dotted.startswith("apps.llm.tasks."):
            continue
        module_name, func_name = dotted.rsplit(".", 1)
        getattr(importlib.import_module(module_name), func_name)()
        ran.append(dotted)
    return ran


class TestTheScheduleNoticesADeadReserve:
    def test_primary_alive_reserve_key_rejected_pages_the_operator(
        self, monkeypatch, pages
    ) -> None:
        """Положение стенда с 01.10: openai отвечает, ключ anthropic отклонён."""
        calls = _world(monkeypatch, anthropic=_rejected("anthropic"))
        ran = _run_everything_beat_runs_for_llm()
        # Положительная пара: расписание не пусто и основной правда спрошен —
        # иначе «сообщение есть/нет» ни о чём.
        assert "apps.llm.tasks.probe_llm_availability" in ran, ran
        assert "openai" in calls, calls
        assert len(pages) == 1, pages
        assert "anthropic" in pages[0]["body"]

    def test_both_alive_pages_nobody(self, monkeypatch, pages) -> None:
        calls = _world(monkeypatch)
        _run_everything_beat_runs_for_llm()
        assert sorted(calls) == ["anthropic", "openai"], calls
        assert pages == []


class TestRejectedKey:
    def test_reserve_key_rejected_pages_on_the_first_check(self, monkeypatch, pages) -> None:
        _world(monkeypatch, anthropic=_rejected("anthropic"))
        out = health.check_llm_reserve()
        assert out["transition"] == health.TRANSITION_DOWN, out
        assert out["key_rejected"] is True
        (page,) = pages
        assert page["severity"] == "warning"
        assert page["title"] == "LLM: ключ резерва отклонён — нужен новый ключ"
        assert "Нужен новый ключ — настройка ANTHROPIC_API_KEY." in page["body"]
        assert "Основной: openai. Резерв: anthropic" in page["body"]

    def test_the_key_value_never_reaches_the_message(self, monkeypatch, pages) -> None:
        """SDK умеет процитировать ключ в тексте ошибки — в сообщение он не попадает."""
        _world(monkeypatch, anthropic=_rejected("anthropic", quoting=_ANTHROPIC_KEY))
        health.check_llm_reserve()
        (page,) = pages
        # Положительная пара: текст ошибки в сообщение дошёл — вычищено именно значение.
        assert "API key is invalid" in page["body"]
        assert "ANTHROPIC_API_KEY" in page["body"]
        assert _ANTHROPIC_KEY not in page["body"]
        from apps.audit.models import AuditLog

        row = AuditLog.all_tenants.get(action=health.AUDIT_RESERVE_DOWN)
        assert row.payload["key_rejected"] is True
        assert _ANTHROPIC_KEY not in str(row.payload)

    def test_primary_key_rejected_says_which_setting_to_replace(self, monkeypatch, pages) -> None:
        """Тот же класс у основного: «нужен новый ключ» вместо разбора с гипотез."""
        _world(monkeypatch, openai=_rejected("openai"))
        health.check_llm_availability()
        health.check_llm_availability()
        (page,) = pages
        assert "Нужен новый ключ — настройка OPENAI_API_KEY." in page["body"]

    @pytest.mark.parametrize(
        "sdk_error",
        [
            PermissionDeniedError("403 region not supported"),
            InternalServerError("500"),
            APIConnectionError("proxy refused"),
        ],
    )
    def test_other_failures_do_not_send_the_operator_for_a_new_key(
        self, monkeypatch, pages, sdk_error
    ) -> None:
        """403 у OpenAI — ещё и отказ по региону при живом ключе (замер 17.09)."""
        _world(monkeypatch, anthropic=_as_provider_raises("anthropic", sdk_error))
        health.check_llm_reserve()
        health.check_llm_reserve()
        (page,) = pages
        assert page["title"] == "LLM: резерв недоступен"
        assert f"Ошибка: {type(sdk_error).__name__}" in page["body"]
        assert "Нужен новый ключ" not in page["body"]


class TestReserveStateMachine:
    def test_one_blip_pages_nobody_two_do(self, monkeypatch, pages) -> None:
        _world(monkeypatch, anthropic=APIConnectionError("proxy refused"))
        first = health.check_llm_reserve()
        assert first["transition"] == health.TRANSITION_NONE
        assert pages == []
        second = health.check_llm_reserve()
        assert second["transition"] == health.TRANSITION_DOWN
        assert [p["title"] for p in pages] == ["LLM: резерв недоступен"]
        assert "Неудачных проверок подряд: 2" in pages[0]["body"]

    def test_a_success_between_two_blips_resets_the_count(self, monkeypatch, pages) -> None:
        _world(monkeypatch, anthropic=APIConnectionError("proxy refused"))
        health.check_llm_reserve()
        _world(monkeypatch)
        health.check_llm_reserve()
        _world(monkeypatch, anthropic=APIConnectionError("proxy refused"))
        out = health.check_llm_reserve()
        assert out["ok"] is False
        assert pages == []

    def test_still_down_is_said_once(self, monkeypatch, pages) -> None:
        _world(monkeypatch, anthropic=_rejected("anthropic"))
        for _ in range(4):
            health.check_llm_reserve()
        assert len(pages) == 1, pages
        assert health.read_reserve_state()["failures"] == 4

    def test_recovery_is_announced_once(self, monkeypatch, pages) -> None:
        _world(monkeypatch, anthropic=_rejected("anthropic"))
        health.check_llm_reserve()
        _world(monkeypatch)
        out = health.check_llm_reserve()
        assert out["transition"] == health.TRANSITION_UP
        health.check_llm_reserve()
        assert [p["title"] for p in pages] == [
            "LLM: ключ резерва отклонён — нужен новый ключ",
            "LLM: резерв снова доступен",
        ]
        assert health.read_reserve_state()["state"] == health.RESERVE_UP

    def test_an_empty_cache_does_not_announce_a_recovery(self, monkeypatch, pages) -> None:
        _world(monkeypatch)
        out = health.check_llm_reserve()
        assert out["ok"] is True
        assert out["transition"] == health.TRANSITION_NONE
        assert pages == []

    def test_another_vendors_down_is_not_inherited(self, monkeypatch, settings, pages) -> None:
        """Сменили основного — резервом стал другой вендор, счёт у него свой."""
        _world(monkeypatch, anthropic=_rejected("anthropic"))
        health.check_llm_reserve()
        assert health.read_reserve_state()["provider"] == "anthropic"
        settings.LLM_PROVIDER = "anthropic"
        _world(monkeypatch)
        out = health.check_llm_reserve()
        assert out["provider"] == "openai"
        # Не «восстановление»: openai резервом не лежал.
        assert out["transition"] == health.TRANSITION_NONE
        assert len(pages) == 1, pages


class TestWhichVendorIsTheReserve:
    def test_only_the_reserve_is_asked(self, monkeypatch, pages) -> None:
        calls = _world(monkeypatch)
        health.check_llm_reserve()
        assert calls == ["anthropic"], calls

    def test_the_reserve_is_the_vendor_a_live_turn_would_hop_to(self, monkeypatch, pages) -> None:
        from apps.llm.router import serving_fallback_candidates

        calls = _world(monkeypatch)
        health.check_llm_reserve()
        assert calls == [serving_fallback_candidates("openai")[0]]

    def test_hop_switched_off_means_nobody_to_check(self, monkeypatch, settings, pages) -> None:
        settings.LLM_QUOTA_FALLBACK_ENABLED = False
        calls = _world(monkeypatch, anthropic=_rejected("anthropic"))
        assert health.check_llm_reserve() == {
            "skipped": health.SKIP_NO_RESERVE,
            "primary": "openai",
        }
        assert calls == []
        assert pages == []

    def test_no_key_means_nobody_to_check(self, monkeypatch, settings, pages) -> None:
        settings.ANTHROPIC_API_KEY = ""
        calls = _world(monkeypatch)
        assert health.check_llm_reserve()["skipped"] == health.SKIP_NO_RESERVE
        assert calls == []

    @pytest.mark.parametrize(
        "switch", ["LLM_HEALTH_PROBE_ENABLED", "LLM_HEALTH_RESERVE_PROBE_ENABLED"]
    )
    def test_switched_off(self, monkeypatch, settings, pages, switch) -> None:
        setattr(settings, switch, False)
        calls = _world(monkeypatch, anthropic=_rejected("anthropic"))
        assert health.check_llm_reserve() == {"skipped": health.SKIP_DISABLED}
        assert calls == []


class TestNothingElseMoved:
    def test_the_primary_tick_is_still_exactly_one_call(self, monkeypatch, pages) -> None:
        """Цена и частота тика — DRF-1054/1056; резерв живёт на своей записи beat."""
        calls = _world(monkeypatch, anthropic=_rejected("anthropic"))
        out = health.check_llm_availability()
        assert calls == ["openai"], calls
        assert out["path"] == health.PATH_PRIMARY

    def test_a_dead_reserve_is_still_a_candidate(self, monkeypatch, pages) -> None:
        """Проверка сообщает, раздачу не меняет: кандидатность — решение владельца."""
        from apps.llm.router import serving_fallback_candidates

        before = serving_fallback_candidates("openai")
        _world(monkeypatch, anthropic=_rejected("anthropic"))
        health.check_llm_reserve()
        assert health.read_reserve_state()["state"] == health.RESERVE_DOWN
        assert serving_fallback_candidates("openai") == before == ["anthropic"]

    def test_the_path_state_is_not_touched_by_the_reserve_check(self, monkeypatch, pages) -> None:
        _world(monkeypatch)
        health.check_llm_availability()
        path_before = health.read_path_state()
        _world(monkeypatch, anthropic=_rejected("anthropic"))
        health.check_llm_reserve()
        assert health.read_path_state() == path_before
        assert path_before["state"] == health.PATH_PRIMARY


class TestReadyz:
    def test_nothing_measured_is_unknown_not_alive(self) -> None:
        from apps.orchestrator.health import check_llm_path

        assert check_llm_path()["reserve"] == {
            "state": health.RESERVE_UNKNOWN,
            "provider": None,
            "key_rejected": False,
            "checked_at": None,
        }

    def test_a_dead_reserve_is_readable_over_http_body(self, monkeypatch, pages) -> None:
        from apps.orchestrator.health import check_llm_path

        _world(monkeypatch, anthropic=_rejected("anthropic"))
        health.check_llm_availability()
        health.check_llm_reserve()
        body = check_llm_path()
        assert body["state"] == health.PATH_PRIMARY
        assert body["reserve"]["state"] == health.RESERVE_DOWN
        assert body["reserve"]["provider"] == "anthropic"
        assert body["reserve"]["key_rejected"] is True
        # readyz из-за резерва не краснеет — решение DRF-2065 о ``ok``.
        assert body["ok"] is True

    def test_an_old_check_is_unknown(self, monkeypatch, settings, pages) -> None:
        _world(monkeypatch)
        health.check_llm_reserve()
        assert health.read_reserve_state()["state"] == health.RESERVE_UP
        from django.core.cache import cache

        record = cache.get(health.CACHE_KEY_RESERVE)
        record["checked_at"] = (timezone.now() - timedelta(seconds=5401)).isoformat()
        cache.set(health.CACHE_KEY_RESERVE, record, 3600)
        state = health.read_reserve_state()
        assert state["state"] == health.RESERVE_UNKNOWN
        assert state["detail"] == "stale"


class TestBeatShell:
    def test_the_reserve_check_is_scheduled_and_off_the_primary_ticks(self) -> None:
        from django.conf import settings as dj_settings

        entry = dj_settings.CELERY_BEAT_SCHEDULE["llm.probe_reserve"]
        assert entry["task"] == "apps.llm.tasks.probe_llm_reserve"
        minutes = set(entry["schedule"].minute)
        # Литералом, а не из константы расписания: раз в 30 минут, мимо тиков */5.
        assert minutes == {7, 37}
        primary = set(dj_settings.CELERY_BEAT_SCHEDULE["llm.probe_availability"]["schedule"].minute)
        assert len(primary) == 12
        assert minutes.isdisjoint(primary)

    def test_task_returns_the_summary(self, monkeypatch, pages) -> None:
        from apps.llm.tasks import probe_llm_reserve

        _world(monkeypatch)
        out = probe_llm_reserve()
        assert out["ok"] is True
        assert out["provider"] == "anthropic"

    def test_task_contains_an_unexpected_crash(self, monkeypatch) -> None:
        from apps.llm import tasks

        def _boom() -> dict:
            raise RuntimeError("unexpected")

        monkeypatch.setattr(tasks, "check_llm_reserve", _boom)
        assert tasks.probe_llm_reserve() == {"skipped": "error"}
