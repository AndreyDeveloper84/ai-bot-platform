"""DRF-2267, слой 1 сторожа — что уходит НА ПРОВОД, а не что написано в коде.

Слой 2 (``apps/orchestrator/tests/test_no_dead_ends_guard_2267.py``) читает
дерево кода и потому не видит трёх вещей:

1. **``action_data=<переменная>``.** Разбор дерева считает такой ответ
   «с кнопками», а строитель на ходу возвращает ``None``: выключен
   ``PILOT_CONVERSATIONAL_UX``, колбэк не влез в 64 байта, профиля нет.
   В коде кнопка есть, на проводе её нет.
2. **Ответ, собранный строкой.** ``try_handle_opt_out``,
   ``try_handle_surface_stop``, ``try_handle_report_hour`` живут вне
   каталогов, которые сканирует слой 2; обработчик заворачивает их строку
   в ответ уже у себя, и по дереву видно только «где-то в лестнице
   безкнопочные места», без имени ветки.
3. **Порядок лестницы.** Какая ветка ответит на конкретную фразу, дерево
   не решает; живой прогон решает.

Поэтому здесь — прогон настоящей лестницы обработчика
(``handle_global_max_event``) по названной таблице входов и проверка того,
что реально ушло: текст и вложения. Каждая ветка либо несёт кнопку, либо
названа в :data:`LIVE_EXCEPTIONS` с причиной.

### Порядок строк — по цене ошибки

Сначала **запись** (человек остаётся без записи или с ложной уверенностью —
класс DRF-2337), затем **согласие и личные данные** (тупик там означает «не
могу ни получить, ни отказать»), затем остальное. **Деньги таблицей не
закрепляются**: ветки повтора платежа ждут владельца (#2014), и сторож не
должен фиксировать поведение, которое может измениться в тот же день.

### Строка обязана уметь падать

Строка, зелёная при любой правке кода, не проверяет, а украшает.
:class:`TestARowCanFail` показывает это на живой ветке записи: стоит снять
клавиатуру — и проверка краснеет.

### Предел этого слоя — назван, а не спрятан

* **Видно только то, что в таблице.** Ветки, для которых нужен живой
  каталог, живая модель или Mini App, сюда не входят; их отсутствие в
  таблице не означает, что они с кнопками.
* **Только глобальный путь.** Салонный бот (``salon_handler``) своей
  лестницей не прогоняется вовсе.
* **Только эта дверь.** Проактивные отправки и напоминания уходят своим
  путём (``send_message`` из задач), и сюда не попадают — их состав держит
  ``apps/nutrition_proactive/tests/test_no_dead_ends_proactive_2267.py``.
* **Один ход.** Что человек увидит на СЛЕДУЮЩЕМ ходу, нажав кнопку, этот
  слой не проверяет: за это отвечает правило DRF-1492 и узлы веток.
* **Подмена — ЧАСТЬ ПРЕДМЕТА, а не удобство.** Там, где ветке нужно
  состояние согласия или ответ каталога, подставляется ровно то, что ветка
  читает, и ничего сверх. В тот день, когда каталог начнёт отвечать иначе,
  строка обязана УПАСТЬ, а не тихо разойтись с жизнью.

Оба слоя нужны и не заменяют друг друга: дерево даёт охват без запуска,
провод — правду про то, что дошло.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any
from unittest.mock import MagicMock

import pytest

from apps.channels.max import handler as max_handler
from apps.orchestrator.discovery import DiscoveryReply
from apps.orchestrator.memory import short_term

pytestmark = pytest.mark.django_db(transaction=True)

#: Ветки, которые уходят БЕЗ кнопок по правилу — с причиной.
LIVE_EXCEPTIONS: dict[str, str] = {
    "opt_out": (
        "«не пиши мне» — человек попросил замолчать; кнопка здесь спорила бы "
        "с просьбой, которую бот только что принял"
    ),
    "report_hour_off": (
        "«не присылай итоги» — просьба замолчать, сказанная словами; тот же "
        "род, что тап «Не присылать»"
    ),
    "surface_stop": (
        "тап «Не присылать» — та же просьба, только про одну поверхность: "
        "предлагать следующий шаг в ответ на «хватит» нельзя"
    ),
}


@pytest.fixture
def mock_send(monkeypatch):
    calls: list[dict] = []

    def fake_send(*, chat_id, text, attachments=None, timeout=10.0):
        calls.append({"chat_id": chat_id, "text": text, "attachments": attachments})
        return {"ok": True}

    monkeypatch.setattr(max_handler, "send_message", fake_send)
    return calls


@pytest.fixture
def fake_redis(monkeypatch):
    from apps.orchestrator.memory.tests.test_short_term import _FakeRedis

    fake = _FakeRedis()
    monkeypatch.setattr(short_term, "_redis_client", lambda: fake)
    return fake


@pytest.fixture(autouse=True)
def _no_chat_action(monkeypatch):
    import apps.channels.max.outbound as outbound

    monkeypatch.setattr(outbound, "send_chat_action", lambda **kw: None)


@pytest.fixture(autouse=True)
def _nutrition_on(settings):
    settings.NUTRITION_ENABLED = True
    settings.NUTRITION_PROACTIVE_ENABLED = True
    settings.PILOT_CONVERSATIONAL_UX = True


def _msg(text: str, *, mid: str) -> dict:
    return {
        "update_type": "message_created",
        "timestamp": 1731320000000,
        "message": {
            "sender": {"user_id": 909, "name": "Иван"},
            "recipient": {"chat_id": 909, "chat_type": "dialog"},
            "body": {"mid": mid, "seq": 1, "text": text, "attachments": []},
        },
    }


def _run(text: str, *, mid: str) -> None:
    max_handler.handle_global_max_event(_msg(text, mid=mid), trace_id=str(uuid.uuid4()))


def _buttons(call: dict[str, Any]) -> list[dict[str, str]]:
    """Кнопки, которые реально уехали в MAX, из любого конверта."""
    out: list[dict[str, str]] = []
    for att in call.get("attachments") or []:
        rows = (att.get("payload") or {}).get("buttons") or []
        for row in rows:
            cells = row if isinstance(row, list) else [row]
            out.extend(cells)
    return out


def _no_personal_data(monkeypatch) -> None:
    """Согласия на личные данные нет — записи читать нельзя (срез 7)."""
    monkeypatch.setattr(
        "apps.orchestrator.personal_surface.personal_records_consent_open", lambda _u: False
    )


#: Вход → имя ветки → примета ответа → подмена, без которой ветка недостижима.
#:
#: Примета обязательна: без неё сторож проверяет «хоть что-то ответило», а
#: не названную ветку. Замер 23.09 поймал ровно это — фраза, которую я
#: считала командой часа отчёта, уходила в модель, и сторож зеленел на
#: чужом ответе с чужими кнопками.
LADDER: tuple[tuple[str, str, str, Callable[[Any], None] | None], ...] = (
    # ── запись: самый дорогой промах (класс DRF-2337) ────────────────────
    (
        "cb:book:cancel:00000000-0000-0000-0000-000000000000",
        "booking_callback_unresolved",
        "Не нахожу этого мастера в каталоге",
        None,
    ),
    # ── согласие и личные данные ─────────────────────────────────────────
    ("что я ел сегодня", "diary_without_consent", "нужно согласие", _no_personal_data),
    # ── просьбы замолчать и настройка проактива ──────────────────────────
    ("не пиши мне", "opt_out", "больше не пишу первой", None),
    ("cb:nutri:stop:report", "surface_stop", "больше не присылаю", None),
    ("присылай итоги в 21:00", "report_hour_set", "итоги дня — в", None),
    ("не присылай итоги дня", "report_hour_off", "больше не присылаю", None),
)


def _assert_way_on(branch: str, buttons: list[dict[str, str]]) -> None:
    """Правило слоя: кнопка есть — или ветка названа безкнопочной с причиной."""
    if branch in LIVE_EXCEPTIONS:
        assert buttons == [], (
            f"{branch}: ветка объявлена безкнопочной ({LIVE_EXCEPTIONS[branch]}) — "
            "кнопки появились, сними исключение"
        )
        return
    assert buttons, (
        f"{branch}: на провод ушёл завершённый шаг без единой кнопки. "
        "Добавь 1–2 кнопки следующего шага и «Меню» (§72) "
        "или внеси ветку в LIVE_EXCEPTIONS с причиной"
    )


class TestWhatReachesTheWireCarriesAWayOn:
    @pytest.mark.parametrize(
        ("text", "branch", "marker", "setup"), LADDER, ids=[b for _, b, _, _ in LADDER]
    )
    def test_each_branch(
        self, text: str, branch: str, marker: str, setup, mock_send, fake_redis, monkeypatch
    ) -> None:
        if setup is not None:
            setup(monkeypatch)
        # Модель обязана молчать: если ход дошёл до неё, ответила НЕ та ветка,
        # и мерить её кнопки бессмысленно.
        monkeypatch.setattr(
            "apps.orchestrator.concierge.generate_concierge_reply",
            MagicMock(side_effect=AssertionError(f"{branch}: ход ушёл в модель")),
        )
        _run(text, mid=f"live-{branch}")

        assert len(mock_send) == 1, f"{branch}: ход не ответил ни разу"
        sent = mock_send[0]
        assert marker in sent["text"], f"{branch}: ответила другая ветка — {sent['text'][:80]!r}"

        _assert_way_on(branch, _buttons(sent))

    def test_the_positive_pair_a_reply_with_buttons_is_seen_as_such(
        self, mock_send, fake_redis, monkeypatch
    ) -> None:
        """Сторож видит кнопки там, где они есть, — иначе он зелен на пустоте."""
        monkeypatch.setattr(
            "apps.orchestrator.concierge.generate_concierge_reply",
            MagicMock(
                return_value=DiscoveryReply(
                    text="Расскажу про уход.",
                    action_data={"buttons": [{"label": "Меню", "callback": "cb:menu:help"}]},
                )
            ),
        )

        _run("расскажи про уход за кожей", mid="live-prose")

        assert len(mock_send) == 1
        assert [b.get("payload") or b.get("callback") for b in _buttons(mock_send[0])] == [
            "cb:menu:help"
        ]


class TestARowCanFail:
    """Строка обязана уметь падать — иначе она украшение, а не проверка."""

    def test_stripping_the_keyboard_turns_the_booking_row_red(
        self, mock_send, fake_redis, monkeypatch
    ) -> None:
        """Снимаем клавиатуру у живой ветки записи — правило краснеет.

        Ветка выбрана по цене ошибки: отказ «не нахожу мастера» без выхода
        оставляет человека с несделанной записью и без следующего шага.
        """
        from apps.orchestrator import handoff

        monkeypatch.setattr(
            "apps.orchestrator.concierge.generate_concierge_reply",
            MagicMock(side_effect=AssertionError("ход ушёл в модель")),
        )
        # Положительная пара: сегодня ветка кнопку отдаёт.
        _run("cb:book:cancel:00000000-0000-0000-0000-000000000000", mid="live-alive")
        assert _buttons(mock_send[0])

        # А теперь снимаем клавиатуру в самом коде — и гоняем живую лестницу
        # ещё раз: краснеет вся цепочка «прогон → провод → правило», а не
        # только правило на выдуманном списке.
        mock_send.clear()
        monkeypatch.setattr(handoff, "_chips", lambda text, buttons: DiscoveryReply(text=text))

        _run("cb:book:cancel:00000000-0000-0000-0000-000000000000", mid="live-mutation")

        sent = mock_send[0]
        assert "Не нахожу этого мастера" in sent["text"]  # ветка та же
        with pytest.raises(AssertionError, match="без единой кнопки"):
            _assert_way_on("booking_callback_unresolved", _buttons(sent))


class TestTheExceptionsAreDeclaredHonestly:
    def test_every_exception_names_a_branch_in_the_ladder(self) -> None:
        """Исключение без ветки — забытая строка, ветка без прогона — дыра."""
        names = {branch for _, branch, _, _ in LADDER}
        assert set(LIVE_EXCEPTIONS) <= names
        assert names - set(LIVE_EXCEPTIONS), "положительная пара: не все ветки — исключения"
