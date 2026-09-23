"""Режим оплаты объявлен, а не спрятан в третьем аргументе ``getattr`` (DRF-2340).

Вырос из оговорки к DRF-2339. ``AYLA_PAYMENTS_TEST_MODE`` не был объявлен
НИ В ОДНОМ файле настроек: единственным чтением был
``getattr(settings, "AYLA_PAYMENTS_TEST_MODE", True)`` внутри клиента
платежей. Умолчание «тест» жило третьим аргументом, и снаружи режим не
виден вообще ничем.

Опасность не в стенде (там замер показал: переменной нет, ссылка
заглушечная — это ожидаемо). Опасность в бою: **не задал — получил
поддельную ссылку**, люди «оплачивают» и никто не платит, а узнать об этом
неоткуда.

Форма взята у двух прецедентов этого репозитория, а не придумана:

* ``config/settings/production.py`` уже падает на импорте (``ImproperlyConfigured``,
  не ``assert`` — ``python -O`` ассерты срезает) при пустом
  ``AYLA_INTERNAL_API_TOKEN`` / ``SENTRY_DSN``;
* ``apps/handoff/checks.py`` — системная проверка Django, чей докстринг
  объясняет, ПОЧЕМУ загрузка: «цена неверного ответа — выкладка, которая
  не стартует», а не отказ в момент, когда человек уже ждёт.

* d1 — настройка объявлена во всех контурах и читается как обычная;
* d2 — мусорное значение («maybe», пустая строка, «0 » с пробелом) —
  отказ, а не молчаливое «бой»;
* p1 — в боевом контуре ОТСУТСТВИЕ значения не означает «тест»: импорт
  падает с именем переменной;
* p2 — заданное значение в бою принимается (обе стороны);
* c1 — клиент читает настройку, а не своё умолчание;
* c2 — тестовый режим по-прежнему не ходит в сеть (поведение не менялось);
* k1 — системная проверка называет режим одним словом: ни адресов, ни
  ключей, ни сумм (``manage.py check`` гоняют там, где вывод сохраняется);
* e1 — объявленная поверхность совпадает с живой: ключ есть в
  ``.env.example`` и ``.env.staging.template`` (иначе ``env_file_drift``
  получит новую версию той же болезни).
"""

from __future__ import annotations

import importlib
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

REPO = Path(__file__).resolve().parents[2]
REQUIRED_ENV = {
    "AYLA_INTERNAL_API_TOKEN": "ayla-token-abc",  # pragma: allowlist secret
    "CHROMA_AUTH_TOKEN": "chroma-token-abc",  # pragma: allowlist secret
    "SENTRY_DSN": "https://public@sentry.example.com/1",
    "MYSITE_WEBHOOK_HMAC_SECRET": "hmac-secret-abc",  # pragma: allowlist secret
}


@pytest.fixture
def _restore_production_module() -> Iterator[None]:
    """Как в ``test_catalog_settings``: импорт с побочным эффектом — предмет теста."""
    import sys

    saved = sys.modules.pop("config.settings.production", None)
    yield
    if saved is not None:
        sys.modules["config.settings.production"] = saved
    else:
        sys.modules.pop("config.settings.production", None)


def _with_required(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in REQUIRED_ENV.items():
        monkeypatch.setenv(name, value)


class TestDeclared:
    def test_the_setting_is_a_setting(self) -> None:
        # Присутствие: имя объявлено и читается как обычная настройка.
        assert hasattr(settings, "AYLA_PAYMENTS_TEST_MODE")
        assert isinstance(settings.AYLA_PAYMENTS_TEST_MODE, bool)

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("true", True),
            ("True", True),
            (" TRUE ", True),
            ("1", True),
            ("false", False),
            ("False", False),
            (" 0 ", False),
        ],
    )
    def test_plain_values_are_read(self, raw: str, expected: bool) -> None:
        from config.settings.base import payments_test_mode_from

        assert payments_test_mode_from(raw) is expected

    @pytest.mark.parametrize("raw", ["maybe", "", "  ", "yes-ish", "0 1", "none"])
    def test_garbage_is_refused_not_read_as_live(self, raw: str) -> None:
        """Мусор — отказ. Прочитать «maybe» как «бой» опаснее падения."""
        from config.settings.base import payments_test_mode_from

        with pytest.raises(ImproperlyConfigured) as exc:
            payments_test_mode_from(raw)
        assert "AYLA_PAYMENTS_TEST_MODE" in str(exc.value)


class TestProductionCannotDefaultToTest:
    def test_missing_value_refuses_to_boot(
        self, monkeypatch: pytest.MonkeyPatch, _restore_production_module: None
    ) -> None:
        _with_required(monkeypatch)
        monkeypatch.delenv("AYLA_PAYMENTS_TEST_MODE", raising=False)
        with pytest.raises(ImproperlyConfigured) as exc:
            importlib.import_module("config.settings.production")
        assert "AYLA_PAYMENTS_TEST_MODE" in str(exc.value)

    def test_garbage_value_refuses_to_boot(
        self, monkeypatch: pytest.MonkeyPatch, _restore_production_module: None
    ) -> None:
        _with_required(monkeypatch)
        monkeypatch.setenv("AYLA_PAYMENTS_TEST_MODE", "maybe")
        with pytest.raises(ImproperlyConfigured) as exc:
            importlib.import_module("config.settings.production")
        assert "AYLA_PAYMENTS_TEST_MODE" in str(exc.value)

    @pytest.mark.parametrize(("raw", "expected"), [("false", False), ("true", True)])
    def test_a_named_value_is_accepted(
        self,
        monkeypatch: pytest.MonkeyPatch,
        _restore_production_module: None,
        raw: str,
        expected: bool,
    ) -> None:
        _with_required(monkeypatch)
        monkeypatch.setenv("AYLA_PAYMENTS_TEST_MODE", raw)
        module = importlib.import_module("config.settings.production")
        assert module.AYLA_PAYMENTS_TEST_MODE is expected


class TestTheClientReadsTheSetting:
    def test_no_hidden_default_in_the_client(self) -> None:
        source = (REPO / "apps/integrations/ayla_payments/client.py").read_text(encoding="utf-8")
        # Присутствие: клиент действительно читает настройку.
        assert "AYLA_PAYMENTS_TEST_MODE" in source
        assert 'getattr(settings, "AYLA_PAYMENTS_TEST_MODE"' not in source

    def test_the_singleton_takes_the_declared_value(self, settings) -> None:  # noqa: F811
        from apps.integrations.ayla_payments import (
            get_ayla_payments_client,
            reset_ayla_payments_client,
        )

        settings.AYLA_PAYMENTS_TEST_MODE = False
        reset_ayla_payments_client()
        try:
            assert get_ayla_payments_client().test_mode is False
        finally:
            reset_ayla_payments_client()

    def test_test_mode_still_makes_no_network_call(self) -> None:
        """Поведение самого тестового режима не менялось."""
        from decimal import Decimal
        from uuid import uuid4

        from apps.integrations.ayla_payments import AylaPaymentsClient

        client = AylaPaymentsClient(base_url="https://ayla.test", api_token="", test_mode=True)
        with patch.object(client._session, "request") as request:
            result = client.create_payment(
                amount_rub=Decimal("1500.00"), description="Сертификат", idempotence_key=uuid4()
            )
        assert result.test is True  # присутствие: заглушка вернулась
        request.assert_not_called()


class TestTheCheckSaysTheModeOutLoud:
    """payments.W001 — голос только там, где ответ неожиданный (DRF-2021)."""

    def test_stub_links_on_a_deploy_shaped_contour_are_named(self, settings) -> None:  # noqa: F811
        from django.core.checks import Warning as CheckWarning

        from apps.orders.checks import check_payments_mode_declared

        settings.AYLA_PAYMENTS_TEST_MODE = True
        settings.DEBUG = False
        settings.AYLA_BASE_URL = "https://ayla.example"
        settings.AYLA_INTERNAL_API_TOKEN = "super-secret-token"  # noqa: S105  # pragma: allowlist secret
        messages = check_payments_mode_declared(None)

        assert len(messages) == 1  # присутствие: проверка говорит
        message = messages[0]
        # Предупреждение, а не ошибка: тестовый контур — законная настройка,
        # и Error останавливал бы выкладку верного стенда.
        assert isinstance(message, CheckWarning)
        assert message.id == "payments.W001"
        text = f"{message.msg} {message.hint or ''}"
        assert "test" in text.lower()
        # Ни ключей, ни адресов, ни сумм: вывод ``manage.py check`` сохраняют.
        assert "super-secret-token" not in text
        assert "ayla.example" not in text

    @pytest.mark.parametrize(
        ("test_mode", "debug", "why"),
        [
            (False, False, "живой режим — ответ ожидаемый"),
            (True, True, "локальная разработка и CI — заглушка очевидна"),
        ],
    )
    def test_the_expected_answer_is_silent(
        self,
        settings,
        test_mode: bool,
        debug: bool,
        why: str,  # noqa: F811
    ) -> None:
        """DRF-2021: предупреждение в каждом зелёном прогоне учат пропускать глазами."""
        from apps.orders.checks import check_payments_mode_declared

        settings.AYLA_PAYMENTS_TEST_MODE = test_mode
        settings.DEBUG = debug
        assert check_payments_mode_declared(None) == [], why

    def test_the_check_is_registered_and_runs_in_manage_py_check(self, settings) -> None:  # noqa: F811
        from django.core.checks import registry

        # Присутствие: проверка зарегистрирована, а не просто написана.
        names = {getattr(fn, "__name__", "") for fn in registry.registry.get_checks()}
        assert "check_payments_mode_declared" in names


class TestDeclaredSurfaceMatchesTheLiveOne:
    @pytest.mark.parametrize(
        ("name", "anchor"),
        [
            (".env.example", "DJANGO_SETTINGS_MODULE"),
            (".env.staging.template", "AYLA_BASE_URL"),
        ],
    )
    def test_the_key_is_in_the_env_templates(self, name: str, anchor: str) -> None:
        text = (REPO / name).read_text(encoding="utf-8")
        # Присутствие: файл прочитан и это он (якорь свой у каждого —
        # ``.env.example`` про локальную разработку, AYLA_* там нет).
        assert anchor in text
        assert "AYLA_PAYMENTS_TEST_MODE" in text


class TestEveryContourDeclaresIt:
    """Ни один контур не молчит — иначе режим снова живёт в третьем аргументе."""

    @pytest.mark.parametrize("module", ["base", "local", "staging", "production"])
    def test_the_name_appears_in_the_settings_file(self, module: str) -> None:
        text = (REPO / "config" / "settings" / f"{module}.py").read_text(encoding="utf-8")
        # Присутствие: файл тот самый.
        assert "DEBUG" in text or "from .base import" in text
        assert "AYLA_PAYMENTS_TEST_MODE" in text

    @pytest.mark.parametrize("module", ["local", "staging"])
    def test_an_explicit_value_is_not_discarded(
        self, monkeypatch: pytest.MonkeyPatch, module: str
    ) -> None:
        """`manage.py`, celery и воркеры садятся на local по умолчанию, а
        docker-compose задаёт его через ``environment:`` — жёсткое ``True``
        означало бы заглушечные ссылки вопреки контуру (DRF-1391 про ту же
        механику)."""
        import importlib
        import sys

        monkeypatch.setenv("AYLA_PAYMENTS_TEST_MODE", "false")
        name = f"config.settings.{module}"
        saved = sys.modules.pop(name, None)
        try:
            loaded = importlib.import_module(name)
            assert loaded.AYLA_PAYMENTS_TEST_MODE is False
        finally:
            sys.modules.pop(name, None)
            if saved is not None:
                sys.modules[name] = saved
