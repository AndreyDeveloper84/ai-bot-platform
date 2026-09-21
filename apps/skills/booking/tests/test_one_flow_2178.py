"""Один поток записи: где спрашивают время (DRF-2178, Э-4).

Этап 4 из 4 — единственный, где трогается Python, и самый большой по
радиусу. Утверждения здесь о ПОВЕДЕНИИ, а не о файлах: сторож,
считающий имена модулей, замолчит в первый же день, когда кто-нибудь
файл переименует.

## Признак, по которому считается «поток»

Поток записи — это **место, где у человека спрашивают время визита**.
Не файл, не функция, не скилл: два разных места выбора времени и есть
два потока, даже если лежат в одном модуле.

## Чат остаётся там, где приложения нет

`web_app` и `miniapp_url` берутся из реестра ботов и у бота могут быть
пусты (`miniapp_config.miniapp_target`). Там кнопке `open_app` взяться
неоткуда, и чат — единственный путь записи. Значит правило не «чатового
выбора нет», а «чатовый выбор — только там, где в приложение не войти».
Область за границей макета, а не отступление от него.

## Что заперто

1. приложение доступно → предикат запрещает чатовый выбор, и вход в
   приложение строится;
2. приложения нет → предикат разрешает чат, а входа в приложение не
   существует (кнопке взяться неоткуда) — положительная половина пары;
3. **обе стороны одного факта согласованы**: не бывает так, что чат
   запрещён, а войти некуда, — иначе человек остался бы без записи
   вовсе. Это и есть «никогда не два потока и никогда не ноль».
"""

from __future__ import annotations

import pytest

from apps.skills.booking.one_flow import (
    ENTRY_TEXT,
    OPEN_LABEL,
    PROVIDER_PAYLOAD_PREFIX,
    chat_step_by_step_allowed,
    miniapp_entry_button,
    miniapp_entry_result,
)


@pytest.fixture
def with_miniapp(settings):
    settings.MAX_BOT_WEB_APP = "aylabot"
    return settings


@pytest.fixture
def without_miniapp(settings):
    settings.MAX_BOT_WEB_APP = ""
    settings.MAX_BOT_MINIAPP_URL = ""
    return settings


class TestWhereTimeIsAsked:
    def test_with_the_app_the_chat_must_not_ask(self, with_miniapp):
        assert chat_step_by_step_allowed() is False

    def test_without_the_app_the_chat_is_the_only_path(self, without_miniapp):
        assert chat_step_by_step_allowed() is True

    def test_the_two_sides_of_one_fact_agree(self, with_miniapp, without_miniapp):
        """Ни двух потоков, ни нуля: запрет чата и наличие входа — одно и то же.

        Разойдись они — человек остался бы без способа записаться вовсе:
        чат молчит, а в приложение не войти.
        """
        for settings_fixture in (with_miniapp, without_miniapp):
            del settings_fixture
            chat_allowed = chat_step_by_step_allowed()
            entry = miniapp_entry_button(master_id="mst-1")
            assert chat_allowed is (entry is None)


class TestMiniappEntry:
    def test_entry_carries_the_chosen_provider(self, with_miniapp):
        button = miniapp_entry_button(master_id="mst-1")
        assert button is not None
        assert button["label"] == OPEN_LABEL
        assert button["callback"] == f"{PROVIDER_PAYLOAD_PREFIX}mst-1"

    def test_payload_fits_the_channel_grammar(self, with_miniapp):
        """Двоеточий грамматика `open_app` не знает — узел на это."""
        from apps.channels.max.outbound import OPEN_APP_PAYLOAD_RE

        button = miniapp_entry_button(master_id="mst-1")
        assert button is not None
        assert OPEN_APP_PAYLOAD_RE.fullmatch(button["callback"])

    def test_entry_result_invites_and_carries_the_button(self, with_miniapp):
        result = miniapp_entry_result(master_id="mst-1")
        assert result is not None
        assert result.reply_text == ENTRY_TEXT
        assert result.action_data is not None
        # Имени в приглашении нет намеренно: человек только что сам
        # выбрал специалиста, а лишнее чтение ростера — лишняя хрупкость.
        assert "{" not in result.reply_text

    def test_no_entry_where_there_is_nowhere_to_go(self, without_miniapp):
        """Войти некуда — и входа нет; вызывающий остаётся на чатовом пути."""
        assert miniapp_entry_result(master_id="mst-1") is None


class TestTheSecondPickerStaysUnreachable:
    """«Выбрать дату» гейта не получил — и не должен его получить.

    Второй вызов `_render_date_picker` (разворот полного списка дат)
    достижим ТОЛЬКО из первого: кнопку с этим callback рисует сам
    пикер, а пикер рисуется лишь когда предикат разрешил чат. Ставить
    предикат дважды значило бы завести две ветки вместо одной.

    Но через год кто-нибудь может нарисовать ту же кнопку из меню — и
    обойти предикат, ничего не заметив. Узел держит именно это: у
    callback'а ровно один производитель, и он внутри пикера.
    """

    def test_only_the_picker_emits_the_more_dates_button(self):
        import inspect

        from apps.skills.booking import skill as booking_skill

        source = inspect.getsource(booking_skill)
        emitters = [
            line
            for line in source.splitlines()
            if "CALLBACK_BOOK_MORE_DATES_PREFIX" in line and '"callback"' in line
        ]
        assert len(emitters) == 1, (
            "Кнопку «Выбрать дату» рисует кто-то ещё. Проверь, что этот "
            "путь проходит через chat_step_by_step_allowed(), иначе он "
            "обходит единственное место, где решается, где спрашивать время."
        )

    def test_the_producers_of_date_chips_are_the_known_three(self):
        """Перепись производителей, а не имён файлов.

        Чипы дат собирает один помощник, но зовут его три места, и все
        три — продолжения уже начатого чатового пути: сам пикер, его
        разворот и «другие даты» после пустого дня. Появится четвёртое —
        узел покраснеет, и автор обязан будет спросить себя, проходит ли
        его путь через `chat_step_by_step_allowed()`. Без этого через год
        кто-нибудь нарисует те же чипы из меню и обойдёт предикат, ничего
        не заметив.
        """
        import inspect

        from apps.skills.booking import skill as booking_skill

        source = inspect.getsource(booking_skill).splitlines()
        callers: set[str] = set()
        current = ""
        for line in source:
            if line.startswith("def "):
                current = line[4:].split("(", 1)[0]
            if "_action_data_for_date_pick(" in line and not line.startswith("def "):
                callers.add(current)
        assert callers == {"_render_other_dates", "_render_date_picker"}, (
            f"Чипы дат собирает кто-то ещё: {sorted(callers)}. Проверь, что "
            "этот путь проходит через chat_step_by_step_allowed()."
        )
