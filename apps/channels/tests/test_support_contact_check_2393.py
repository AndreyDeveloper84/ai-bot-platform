"""Сторож: контур обещает поддержку — значит называет, куда писать (DRF-2393).

### Что измерено

`AYLA_SUPPORT_CONTACT` не имеет умолчания (`config/settings/base.py`), и при
пустом значении `_support_text()` (`apps/channels/max/salon_handler.py`)
отвечает «Напишите в поддержку Ayla.» То есть на вопрос «как связаться с
поддержкой» человек получает «свяжитесь с поддержкой». Замер стенда
24.09.2026: пусто. Переменной не было ни в `.env.example`, ни в
`.env.staging.template` — поднимающий контур о ней не узнавал.

### Почему Warning, а не Error

Пустой контакт — законное состояние местной разработки и CI, там кнопку
никто не нажимает. Ошибка остановила бы выкладку из-за настройки, без
которой контур работает. Предмет сторожа — не «неверное значение», а
**молчание**: сегодня никто нигде не говорит, что обещание без адресата.

### Почему только там, где это не отладка

`DEBUG` истинен ровно на тех контурах, где тавтология безвредна. Печатать
предупреждение в каждом зелёном прогоне — способ научить читателя
пропускать строку `System check identified`, и тогда следующее
предупреждение той же формы, настоящее, уйдёт тем же путём. Это решение
DRF-2021, и `0 silenced` обязано оставаться нулём.

### Чего этот сторож НЕ проверяет

Что по указанному адресу кто-то отвечает. Он видит строку настройки, а не
человека за ней.
"""

from __future__ import annotations

import pytest
from django.test import override_settings

from apps.channels.checks import SUPPORT_CONTACT_CHECK_ID, check_support_contact_named


@override_settings(AYLA_SUPPORT_CONTACT="", DEBUG=False)
def test_empty_contact_on_a_deployed_contour_is_reported() -> None:
    """Контур не для отладки, адреса нет — сторож говорит."""

    found = check_support_contact_named(None)
    assert len(found) == 1
    assert found[0].id == SUPPORT_CONTACT_CHECK_ID
    # Текст называет настройку и последствие — и ничего больше: вывод
    # `manage.py check` уезжает в логи выкладки и в тикеты.
    assert "AYLA_SUPPORT_CONTACT" in (found[0].hint or "")


@override_settings(AYLA_SUPPORT_CONTACT="@ayla_support", DEBUG=False)
def test_named_contact_is_silent() -> None:
    """Адрес задан — сторожу сказать нечего."""

    # Наличие первым: сторож вообще вызывается и умеет возвращать список.
    assert check_support_contact_named(None) == []


@override_settings(AYLA_SUPPORT_CONTACT="", DEBUG=True)
def test_debug_contour_is_silent() -> None:
    """Местная разработка — предупреждение стало бы обоями (DRF-2021)."""

    assert check_support_contact_named(None) == []


@pytest.mark.parametrize("value", ["   ", "\t"])
@override_settings(DEBUG=False)
def test_blank_is_not_an_address(value: str) -> None:
    """Пробелы — не адрес: `_support_text()` их тоже отбрасывает."""

    with override_settings(AYLA_SUPPORT_CONTACT=value):
        found = check_support_contact_named(None)
    assert len(found) == 1
    assert found[0].id == SUPPORT_CONTACT_CHECK_ID


def test_check_is_registered() -> None:
    """Сторож действительно в реестре, а не просто объявлен.

    Узел стоит потому, что этот лист начался ровно с такой ошибки:
    проверка существовала, была объявлена ошибкой и не выполнялась там,
    где живёт человек.
    """

    from django.core.checks import registry

    names = {
        f"{c.__module__}.{c.__name__}"
        for c in registry.registry.get_checks(include_deployment_checks=False)
    }
    assert "apps.channels.checks.check_support_contact_named" in names


class TestDeclaredSurfaceMatchesTheLiveOne:
    """Настройка описана там, где её ищет поднимающий контур.

    Этот узел — предмет самого листа, а не украшение: дефект начался с
    того, что переменной **не было в шаблонах**, и человек, поднимающий
    контур, о ней не узнавал. Документация без сторожа удаляется молча —
    и дефект возвращается ровно туда, откуда пришёл (найдено ревью).

    Образец взят у соседнего листа: `tests/smoke/
    test_payments_mode_declared_2340.py::TestDeclaredSurfaceMatchesTheLiveOne`.
    """

    @pytest.mark.parametrize(
        ("name", "anchor"),
        [
            (".env.example", "DJANGO_SETTINGS_MODULE"),
            (".env.staging.template", "AYLA_BASE_URL"),
        ],
    )
    def test_keys_are_in_the_env_templates(self, name: str, anchor: str) -> None:
        from pathlib import Path

        repo = Path(__file__).resolve().parents[3]
        text = (repo / name).read_text(encoding="utf-8")
        # Присутствие первым, и якорь у каждого файла свой: `.env.example`
        # про местную разработку, `AYLA_*` там нет.
        assert anchor in text, name
        assert "AYLA_SUPPORT_CONTACT" in text, name
        # Дежурные — там же: `handoff.E001` единственный у нас ошибка, и
        # обнулить его пару можно только зная, что она существует.
        assert "HANDOFF_DUTY_OPERATORS" in text, name
        assert "HANDOFF_DUTY_QUEUE" in text, name
