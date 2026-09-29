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

import inspect
from fractions import Fraction
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

        # Регистр не проверяем: «Фото» в начале предложения — это тот же
        # факт, что «фото» в середине. Проверяется смысл, а не заглавная.
        bodies = " ".join(f"{t} {b}" for _s, t, b in _calls(paged)).lower()
        assert "фото не распознаются" in bodies
        assert "полуночи utc" in bodies
        # Положительная пара: это НЕ предупреждение «кончится», а факт.
        assert "кончится" not in bodies

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

    def test_a_jump_straight_to_the_limit_pages_once_not_twice(self) -> None:
        """Расход прыгнул с нуля к потолку — звучит СТАРШИЙ порог.

        Обе отметки пересечены одним вызовом; слать обе значило бы выдать
        оператору предупреждение о том, что уже случилось. Младший порог
        не выбрасывается, а помечается израсходованным — иначе он
        прозвучал бы следующим сканом, ПОСЛЕ страницы про 100 %.
        """
        with patch.object(sba, "page", return_value=True) as paged:
            sba.signal_budget(used=500, limit=500, day=DAY)
            sba.signal_budget(used=505, limit=500, day=DAY)

        severities = [s for s, _t, _b in _calls(paged)]
        assert severities == ["error"]

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

    def test_the_signature_admits_no_person(self) -> None:
        """«По построению» — это про подпись, а не про литерал.

        Осмотр текста не может упасть, пока кто-нибудь не отредактирует сам
        литерал. Подпись — может: добавьте `user_id`, и узел покраснеет.
        Идентификатору человека просто неоткуда взяться: каталог считает
        снимки, а не тех, кто их прислал.
        """
        params = set(inspect.signature(sba.signal_budget).parameters)
        assert params == {"used", "limit", "day", "cost_usd"}

    def test_cost_is_a_reference_not_a_requirement(self) -> None:
        """Стоимость может не посчитаться — сигнал звучит всё равно."""
        with patch.object(sba, "page", return_value=True) as paged:
            sba.signal_budget(used=500, limit=500, day=DAY, cost_usd=None)
        assert len(paged.call_args_list) == 1


class TestForeignNumbersDoNotCrashTheGuard:
    """Находка ревью: «никогда не бросает» должно относиться и к ЧУЖИМ числам.

    В варианте (а) числа приезжают разобранным JSON, где строка на месте
    числа — обычное дело (каталог и стоимость отдаёт строкой). Незащищённое
    сравнение уронило бы `TypeError` внутри обработчика ingest: страж уронил
    бы не свой сигнал, а входящее событие. mypy здесь не спасает — payload
    приходит как `Any`, подпись `used: int` проходит проверку, падение
    остаётся рантаймовым.
    """

    #: Мусор: назвать этим числом нечего, сигнал молчит.
    GARBAGE = [
        ("used пустой", {"used": None, "limit": 1000}),
        ("limit пустой", {"used": 812, "limit": None}),
        ("used не число", {"used": object(), "limit": 1000}),
        ("limit не число", {"used": 812, "limit": object()}),
        ("used не разбирается", {"used": "восемьсот", "limit": 1000}),
        # `True` — не «одно распознавание»: булев отсекается отдельно,
        # иначе `int(True) == 1` тихо превратил бы флаг в счётчик.
        ("used булев", {"used": True, "limit": 1000}),
        ("limit булев", {"used": 812, "limit": True}),
    ]

    #: Числовая строка — НЕ мусор: JSON так числа и возит, её принимают.
    COERCIBLE = [
        ("used строкой", {"used": "812", "limit": 1000}),
        ("limit строкой", {"used": 812, "limit": "1000"}),
        ("оба строкой", {"used": "812", "limit": "1000"}),
    ]

    @pytest.mark.parametrize(("name", "kwargs"), GARBAGE, ids=[n for n, _ in GARBAGE])
    def test_garbage_is_silence_not_an_exception(self, name, kwargs) -> None:
        with patch.object(sba, "page", return_value=True) as paged:
            sba.signal_budget(day=DAY, **kwargs)  # не бросает
        assert paged.call_args_list == []

    @pytest.mark.parametrize(("name", "kwargs"), COERCIBLE, ids=[n for n, _ in COERCIBLE])
    def test_numeric_strings_are_accepted_not_swallowed(self, name, kwargs) -> None:
        """Положительная пара к мусору: молчать на всё подряд — тоже дефект.

        Адаптер (а) отдаёт разобранный JSON; проглотить «812» значило бы
        потерять сигнал молча — ровно то, от чего лист и заведён.
        """
        with patch.object(sba, "page", return_value=True) as paged:
            sba.signal_budget(day=DAY, **kwargs)
        assert len(paged.call_args_list) == 1, name

    def test_huge_numbers_do_not_overflow(self) -> None:
        """`used / limit` на большом числе даёт OverflowError — доля считается
        точной дробью, поэтому потолок в 10**400 переживается."""
        with patch.object(sba, "page", return_value=True) as paged:
            sba.signal_budget(used=10**400, limit=10**400, day=DAY)
        # Положительная часть: это не молчание «на всякий случай» — сигнал
        # звучит, просто без переполнения.
        assert len(paged.call_args_list) == 1

    def test_huge_limit_below_threshold_is_silence(self) -> None:
        with patch.object(sba, "page", return_value=True) as paged:
            sba.signal_budget(used=1, limit=10**400, day=DAY)
        assert paged.call_args_list == []


class TestUndeliveredPageDoesNotEatTheDay:
    """Находка ревью: «одна страница в сутки» — про страницу, не про попытку.

    На пилоте MAX — единственный живой сток (`ALERTS_TELEGRAM_CHAT_ID`,
    `TELEGRAM_BOT_TOKEN`, `SENTRY_DSN` пусты), и он умеет отваливаться. Один
    сетевой сбой ровно в момент пересечения 100 % крал бы сигнал до полуночи
    UTC — пока распознавание лежит у всех.
    """

    def test_failed_delivery_is_retried_on_the_next_call(self) -> None:
        with patch.object(sba, "page", return_value=False) as paged:
            sba.signal_budget(used=500, limit=500, day=DAY)
            sba.signal_budget(used=505, limit=500, day=DAY)
        assert len(paged.call_args_list) == 2

    def test_raising_sink_is_retried_too(self) -> None:
        with patch.object(sba, "page", side_effect=RuntimeError("sink down")) as paged:
            sba.signal_budget(used=500, limit=500, day=DAY)
            sba.signal_budget(used=505, limit=500, day=DAY)
        assert len(paged.call_args_list) == 2

    def test_positive_pair_delivered_page_still_eats_the_day(self) -> None:
        """Иначе правка выродилась бы в «дедупа нет»."""
        with patch.object(sba, "page", return_value=True) as paged:
            sba.signal_budget(used=500, limit=500, day=DAY)
            sba.signal_budget(used=505, limit=500, day=DAY)
        assert len(paged.call_args_list) == 1


class TestEachThresholdHasItsOwnCount:
    """Находка ревью: счёт — у ПОРОГА, а не у уровня.

    Пока порогов два и уровни у них разные, разницы не видно. Третий порог с
    уже занятым уровнем — одна строка расстояния — и гашение младшего
    закрыло бы счёт соседнему: оператор не узнал бы НИЧЕГО.
    """

    THREE = (
        (Fraction(1, 2), "warning"),
        (Fraction(4, 5), "warning"),
        (Fraction(1, 1), "error"),
    )

    def test_two_thresholds_sharing_a_level_do_not_close_each_other(self) -> None:
        with (
            patch.object(sba, "THRESHOLDS", self.THREE),
            patch.object(sba, "page", return_value=True) as paged,
        ):
            sba.signal_budget(used=250, limit=500, day=DAY)  # 50 %
            sba.signal_budget(used=400, limit=500, day=DAY)  # 80 % — свой счёт

        percents = [t for _s, t, _b in _calls(paged)]
        assert len(percents) == 2, percents

    def test_the_highest_crossed_threshold_wins_not_the_last_listed(self) -> None:
        """«Старший» — наибольший порог, а не последний в кортеже."""
        unsorted_thresholds = (
            (Fraction(1, 1), "error"),
            (Fraction(4, 5), "warning"),
        )
        with (
            patch.object(sba, "THRESHOLDS", unsorted_thresholds),
            patch.object(sba, "page", return_value=True) as paged,
        ):
            sba.signal_budget(used=500, limit=500, day=DAY)

        assert [s for s, _t, _b in _calls(paged)] == ["error"]


class TestQuenchingTheLowerThreshold:
    def test_a_fallback_into_the_warning_zone_stays_silent(self) -> None:
        """Сценарий, ради которого младший порог гасится, а не выбрасывается.

        Расход откатывается обратно в зону 80–100 % — каталог делает `_decr`
        при `BudgetExhausted`, оператор может поднять потолок. Без гашения
        предупреждение «80 %» прозвучало бы ПОСЛЕ страницы про 100 %.
        Узел падает ровно на удалении цикла гашения.
        """
        with patch.object(sba, "page", return_value=True) as paged:
            sba.signal_budget(used=500, limit=500, day=DAY)  # 100 %
            sba.signal_budget(used=450, limit=500, day=DAY)  # откат в 90 %

        assert [s for s, _t, _b in _calls(paged)] == ["error"]

    def test_positive_pair_the_warning_zone_alone_does_page(self) -> None:
        """Иначе узел выше проходил бы и на «80 % не звучит никогда»."""
        with patch.object(sba, "page", return_value=True) as paged:
            sba.signal_budget(used=450, limit=500, day=DAY)

        assert [s for s, _t, _b in _calls(paged)] == ["warning"]


class TestDedupTtlSurvivesTheMidnightEdge:
    def test_ttl_is_not_measured_from_now_to_midnight(self) -> None:
        """Находка ревью: TTL «до полуночи» в 23:59:59 давал ключ на секунду.

        День зашит в ключ, выравнивать TTL по нему незачем — нужно пережить
        опоздавшую доставку (у рельса каталога есть ретраи и dead-letter).
        """
        with (
            patch.object(sba, "page", return_value=True),
            patch.object(sba.cache, "add", return_value=True) as added,
        ):
            sba.signal_budget(used=400, limit=500, day=DAY)

        timeouts = [c.kwargs["timeout"] for c in added.call_args_list]
        # Наличие — первым: ключ вообще занимается, и TTL у него задан.
        assert timeouts, "ключ занимается с явным TTL"
        assert all(int(t) >= 24 * 60 * 60 for t in timeouts), timeouts

    def test_the_day_is_in_the_key(self) -> None:
        """Положительная пара к TTL: сутки различаются ключом, а не временем."""
        with (
            patch.object(sba, "page", return_value=True),
            patch.object(sba.cache, "add", return_value=True) as added,
        ):
            sba.signal_budget(used=400, limit=500, day=DAY)
            sba.signal_budget(used=400, limit=500, day=NEXT_DAY)

        keys = [c.args[0] for c in added.call_args_list]
        assert any(DAY in k for k in keys) and any(NEXT_DAY in k for k in keys)


class TestCacheLoss:
    def test_dedup_cache_down_is_silence_not_a_storm(self) -> None:
        """Потеря кэша читается как «уже звучал».

        `page()` дедуплицирует ЧЕРЕЗ ТОТ ЖЕ кэш, поэтому открытый отказ
        дал бы не одну лишнюю страницу, а страницу на каждую попытку
        скана — то самое, от чего оператор глушит канал.
        """
        with (
            patch.object(sba.cache, "add", side_effect=RuntimeError("redis down")),
            patch.object(sba, "page", return_value=True) as paged,
        ):
            sba.signal_budget(used=500, limit=500, day=DAY)

        assert paged.call_args_list == []


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


class TestTheOutcomeIsReturned:
    """DRF-2196 (по ревью бот-стороны): ядро говорит вызывающему, что не справилось.

    У события каталога вызов на сутки и порог один — «следующий скан
    переспросит» там неправда. Вызывающий обязан отличать «звучать не нужно»
    от «страница не ушла», чтобы на втором отказаться принимать событие.
    """

    def test_delivered(self) -> None:
        with patch.object(sba, "page", return_value=True):
            assert sba.signal_budget(used=500, limit=500, day=DAY) == "delivered"

    def test_not_delivered_when_no_sink_took_it(self) -> None:
        with patch.object(sba, "page", return_value=False):
            assert sba.signal_budget(used=500, limit=500, day=DAY) == "not_delivered"

    def test_not_delivered_when_the_sink_raised(self) -> None:
        with patch.object(sba, "page", side_effect=RuntimeError("sink down")):
            assert sba.signal_budget(used=500, limit=500, day=DAY) == "not_delivered"

    @pytest.mark.parametrize(
        ("used", "limit"),
        [(100, 500), (None, 500), (500, 0)],
        ids=["ниже порога", "мусор", "нет потолка"],
    )
    def test_skipped_when_there_is_nothing_to_say(self, used, limit) -> None:
        with patch.object(sba, "page", return_value=True) as paged:
            assert sba.signal_budget(used=used, limit=limit, day=DAY) == "skipped"
        assert paged.call_args_list == []

    def test_skipped_when_it_already_sounded_today(self) -> None:
        """Положительная пара к `delivered`: второй раз за сутки — не страница."""
        with patch.object(sba, "page", return_value=True):
            assert sba.signal_budget(used=500, limit=500, day=DAY) == "delivered"
            assert sba.signal_budget(used=500, limit=500, day=DAY) == "skipped"
