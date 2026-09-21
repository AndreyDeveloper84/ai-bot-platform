"""Сигнал бюджета распознавания доходит до операторов в MAX (DRF-2196).

Каталог (#519, DRF-2145) уже считает снимки и уже знает, что пересёк 80 % и
100 % общего дневного потолка. Дальше сигнал упирается в стену: приёмника
алертов у каталога нет — ``SENTRY_DSN`` на пилоте пуст, и
``_send_signal`` вырождается в no-op, то есть остаётся строка в логе,
которую никто не читает. Единственный работающий канал к людям —
``alerting.page`` → MAX-чат операторов (DRF-2158), и он в боте.

# Что здесь пришпилено

Этот лист описывает **вариант-независимое ядро**: функцию, которой дают
числа (``used`` / ``limit`` / день) и которая решает, звучать ли сигналу и
каким. Как числа доедут — событием от каталога или опросом ручки — решает
владелец; адаптер сверху тонкий, ядро одно и то же.

# Два капкана, ради которых лист и нужен

1. **``page()`` дедуплицирует по ГЛОБАЛЬНОМУ окну** ``ALERTS_DEDUP_TTL_SECONDS``
   (умолчание 300 с, один ключ на весь бот). «Одна страница в сутки» его
   средствами не выражается: поднять окно до суток — значит заглушить
   дедуп всем остальным. Значит свой ключ по дню, как у каталога
   (``food_scan:signal:{day}:{level}``), ДО вызова ``page``.
2. **``severity="critical"`` вообще обходит дедуп** (`alerting._is_duplicate`:
   «never dedup critical»). Поэтому 100 % — это ``error``, а не
   ``critical``: иначе «одна страница в сутки» превратилась бы в страницу
   на КАЖДУЮ попытку скана после исчерпания бюджета — то есть ровно в тот
   шум, от которого оператор глушит канал.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from apps.observability import scan_budget_alert as sba

pytestmark = pytest.mark.django_db

DAY = "2026-09-21"
NEXT_DAY = "2026-09-22"


@pytest.fixture(autouse=True)
def _clean_cache():
    from django.core.cache import cache

    cache.clear()
    yield
    cache.clear()


def _calls(mock) -> list[tuple]:
    return [c.args for c in mock.call_args_list]


class TestThresholds:
    def test_below_80_is_silence(self) -> None:
        """Положительная пара к «на 80 % звучит»: ниже порога — молчание."""
        with patch.object(sba, "page", return_value=True) as paged:
            sba.signal_budget(used=399, limit=500, day=DAY)
        assert paged.call_args_list == []

    def test_80_percent_pages_once(self) -> None:
        with patch.object(sba, "page", return_value=True) as paged:
            sba.signal_budget(used=400, limit=500, day=DAY)
        assert len(paged.call_args_list) == 1
        severity, title, _body = _calls(paged)[0]
        assert severity == "warning"
        assert "80" in title or "80" in _body

    def test_100_percent_names_the_consequence_for_people(self) -> None:
        """Оператор должен прочитать не «счётчик», а что это значит людям."""
        with patch.object(sba, "page", return_value=True) as paged:
            sba.signal_budget(used=500, limit=500, day=DAY)

        bodies = " ".join(f"{t} {b}" for _s, t, b in _calls(paged))
        assert "фото не распознаются" in bodies
        assert "полуночи UTC" in bodies

    def test_100_percent_is_error_not_critical(self) -> None:
        """`critical` обходит дедуп `page()` — страница на каждую попытку."""
        with patch.object(sba, "page", return_value=True) as paged:
            sba.signal_budget(used=500, limit=500, day=DAY)

        severities = {s for s, _t, _b in _calls(paged)}
        assert "error" in severities
        assert "critical" not in severities


class TestOnePagePerDay:
    def test_second_call_same_day_is_silent(self) -> None:
        with patch.object(sba, "page", return_value=True) as paged:
            sba.signal_budget(used=400, limit=500, day=DAY)
            sba.signal_budget(used=410, limit=500, day=DAY)
            sba.signal_budget(used=420, limit=500, day=DAY)

        assert len(paged.call_args_list) == 1

    def test_next_day_pages_again(self) -> None:
        """Положительная пара: дедуп — по суткам, а не «навсегда»."""
        with patch.object(sba, "page", return_value=True) as paged:
            sba.signal_budget(used=400, limit=500, day=DAY)
            sba.signal_budget(used=400, limit=500, day=NEXT_DAY)

        assert len(paged.call_args_list) == 2

    def test_dedup_outlives_the_global_page_window(self) -> None:
        """Свой ключ, а не окно `page()`.

        Окно `ALERTS_DEDUP_TTL_SECONDS` — 5 минут на весь бот. Если бы
        «раз в сутки» держалось им, шестой минутой сигнал зазвучал бы
        снова. Узел падает ровно на этой ошибке: `page` подменён, его
        собственный дедуп в игре не участвует — молчать обязано ЯДРО.
        """
        with patch.object(sba, "page", return_value=True) as paged:
            sba.signal_budget(used=400, limit=500, day=DAY)
            sba.signal_budget(used=400, limit=500, day=DAY)

        assert len(paged.call_args_list) == 1

    def test_two_thresholds_are_deduped_apart(self) -> None:
        """80 % и 100 % — разные факты, у каждого свой счёт в сутки."""
        with patch.object(sba, "page", return_value=True) as paged:
            sba.signal_budget(used=400, limit=500, day=DAY)  # 80 %
            sba.signal_budget(used=500, limit=500, day=DAY)  # 100 %
            sba.signal_budget(used=501, limit=500, day=DAY)  # уже звучало

        severities = [s for s, _t, _b in _calls(paged)]
        assert severities == ["warning", "error"]


class TestNoPersonIdentifiers:
    def test_alert_carries_no_identifiers_of_people(self) -> None:
        with patch.object(sba, "page", return_value=True) as paged:
            sba.signal_budget(used=500, limit=500, day=DAY, cost_usd="12.3456")

        severity, title, body = _calls(paged)[0]
        text = f"{title} {body}"
        # Наличие — первым: строка не пуста и несёт то, зачем её шлют.
        assert "500" in text and DAY in text
        # Отсутствие: в сигнале нечему опознать человека.
        for forbidden in ("user_id", "chat_id", "external_user_id", "bot:", "@"):
            assert forbidden not in text, forbidden
        assert severity in {"warning", "error"}

    def test_cost_is_a_reference_not_a_requirement(self) -> None:
        """Стоимость может не посчитаться — сигнал звучит всё равно."""
        with patch.object(sba, "page", return_value=True) as paged:
            sba.signal_budget(used=500, limit=500, day=DAY, cost_usd=None)
        assert len(paged.call_args_list) == 1


class TestSignalIsNeverMoreImportantThanWork:
    def test_page_raising_does_not_escape(self) -> None:
        """Сигнал не важнее работы: падение алертинга глушится здесь."""
        with patch.object(sba, "page", side_effect=RuntimeError("sink down")):
            sba.signal_budget(used=500, limit=500, day=DAY)  # не бросает

    def test_zero_or_negative_limit_is_silence_not_a_crash(self) -> None:
        with patch.object(sba, "page", return_value=True) as paged:
            sba.signal_budget(used=10, limit=0, day=DAY)
            sba.signal_budget(used=10, limit=-5, day=DAY)
        assert paged.call_args_list == []
