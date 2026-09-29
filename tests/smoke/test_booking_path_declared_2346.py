"""Путь записи объявлен, а не подразумевается умолчанием (DRF-2346).

``BOOKING_VIA_AYLA_REST`` читался единственным способом —
``getattr(settings, "BOOKING_VIA_AYLA_REST", False)`` по коду, — и умолчание
«местные таблицы» жило третьим аргументом. Снаружи путь записи не был виден
ни в одном файле настроек.

Опасность не в разработке (там местные таблицы и есть то, что нужно). Опасность
в контуре, где записи принадлежат Ayla: незаданная переменная включала местный
двухшаговый путь отмены, а он до DRF-2346 завершался таймером на странице —
человек слышал «Запись отменена», запись оставалась «отмена запрошена», мастер
ждал.

Форма взята у режима оплаты (DRF-2340) и у токенов, падающих на импорте: цена
неверного ответа — выкладка, которая не стартует, а не человек, отменивший
запись в никуда.

* d1 — настройка объявлена и читается как обычная;
* d2 — мусорное значение — отказ, а не молчаливое «местный путь»;
* p1 — в бою ОТСУТСТВИЕ значения не означает «false»: импорт падает с именем
  переменной;
* p2 — заданное значение в бою принимается, обе стороны;
* e1 — объявленная поверхность совпадает с живой: ключ есть в
  ``.env.example`` и ``.env.staging.template``.
"""

from __future__ import annotations

import importlib
from collections.abc import Iterator
from pathlib import Path

import pytest
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

REPO = Path(__file__).resolve().parents[2]
REQUIRED_ENV = {
    "AYLA_INTERNAL_API_TOKEN": "ayla-token-abc",  # pragma: allowlist secret
    "CHROMA_AUTH_TOKEN": "chroma-token-abc",  # pragma: allowlist secret
    "SENTRY_DSN": "https://public@sentry.example.com/1",
    "MYSITE_WEBHOOK_HMAC_SECRET": "hmac-secret-abc",  # pragma: allowlist secret
    # Режим оплаты падает раньше пути записи — без него сценарий не дойдёт
    # до своего предмета.
    "AYLA_PAYMENTS_TEST_MODE": "false",
}


@pytest.fixture
def _restore_production_module() -> Iterator[None]:
    """Импорт с побочным эффектом — предмет теста; модуль за собой убираем."""
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
        assert hasattr(settings, "BOOKING_VIA_AYLA_REST")
        assert isinstance(settings.BOOKING_VIA_AYLA_REST, bool)

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
        from config.settings.base import booking_via_ayla_rest_from

        assert booking_via_ayla_rest_from(raw) is expected

    @pytest.mark.parametrize("raw", ["maybe", "", "  ", "yes-ish", "0 1", "none"])
    def test_garbage_is_refused_not_read_as_local(self, raw: str) -> None:
        """Мусор — отказ. Прочитать «maybe» как «местный путь» опаснее падения."""
        from config.settings.base import booking_via_ayla_rest_from

        with pytest.raises(ImproperlyConfigured) as exc:
            booking_via_ayla_rest_from(raw)
        assert "BOOKING_VIA_AYLA_REST" in str(exc.value)


class TestProductionCannotDefaultToLocalTables:
    def test_missing_value_refuses_to_boot(
        self, monkeypatch: pytest.MonkeyPatch, _restore_production_module: None
    ) -> None:
        _with_required(monkeypatch)
        monkeypatch.delenv("BOOKING_VIA_AYLA_REST", raising=False)
        with pytest.raises(ImproperlyConfigured) as exc:
            importlib.import_module("config.settings.production")
        assert "BOOKING_VIA_AYLA_REST" in str(exc.value)

    def test_garbage_value_refuses_to_boot(
        self, monkeypatch: pytest.MonkeyPatch, _restore_production_module: None
    ) -> None:
        _with_required(monkeypatch)
        monkeypatch.setenv("BOOKING_VIA_AYLA_REST", "maybe")
        with pytest.raises(ImproperlyConfigured) as exc:
            importlib.import_module("config.settings.production")
        assert "BOOKING_VIA_AYLA_REST" in str(exc.value)

    @pytest.mark.parametrize(("raw", "expected"), [("false", False), ("true", True)])
    def test_a_named_value_is_accepted(
        self,
        monkeypatch: pytest.MonkeyPatch,
        _restore_production_module: None,
        raw: str,
        expected: bool,
    ) -> None:
        _with_required(monkeypatch)
        monkeypatch.setenv("BOOKING_VIA_AYLA_REST", raw)
        module = importlib.import_module("config.settings.production")
        assert module.BOOKING_VIA_AYLA_REST is expected


class TestTheDeclaredSurfaceMatchesTheLiveOne:
    @pytest.mark.parametrize("name", [".env.example", ".env.staging.template"])
    def test_the_key_is_in_the_template(self, name: str) -> None:
        """Иначе объявление живёт только в коде и разъезжается с контуром."""
        assert "BOOKING_VIA_AYLA_REST" in (REPO / name).read_text(encoding="utf-8")
