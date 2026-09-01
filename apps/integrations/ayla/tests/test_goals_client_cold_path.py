"""DRF-1435 — цель, выбранная в приложении, не должна теряться на холодном
контуре пилота.

Что воспроизводится
-------------------
Замер на пилоте (2026-09-01, хост 194.87.99.126, 2 vCPU, loadavg 6–15,
iowait 54–86%, ~1.6 ГБ в свопе, 7–10 процессов постоянно в D-state):

===================================  ==========  =========
фаза                                 холодный    тёплый
===================================  ==========  =========
TLS-рукопожатие (appconnect-connect)   3.77 s      0.025 s
обработка запроса (ttfb-appconnect)    0.38 s      0.048 s
итого                                  4.04 s      0.09 s
===================================  ==========  =========

Владелец на живом пилоте видел то же самое на более загруженном хосте:
20.8 s на первом запросе и 0.25 / 0.08 s на следующих.

То есть бэкенд не медленный — медленным оказывается ПЕРВОЕ обращение к
странице памяти, которую пришлось поднять с насыщенного диска. Отсюда два
дефекта в этом клиенте, и оба закрываются здесь.

1. ``_request`` строил ``httpx.Client`` НА КАЖДЫЙ вызов, то есть платил
   полное DNS+TCP+TLS рукопожатие на каждое нажатие пользователя. На
   пилоте это измеренные 3.77 s накладных расходов, которые мы налагаем
   на себя сами. Экран целей открывается через GET decision-context, и
   сразу за ним идёт POST goals/select — второй запрос обязан ехать по
   уже открытому соединению.

2. POST, истёкший по чтению, НЕ означает, что запись не прошла:
   ReadTimeout — это «сервер получил запрос и не успел ответить», и в
   журнале владельца именно ReadTimeout. Сейчас пользователь получает
   красную плашку при фактически сохранённой цели, жмёт ещё раз — и в
   бэкенде появляется вторая строка ClientGoal и второе событие воронки
   GOAL_SELECTED (``goals/api.py:_emit_goal_selected`` документирует это
   прямо). Лечится сверкой состояния, а не повтором записи.

Каждое отрицательное утверждение ниже закрыто положительной стражей на
тех же данных — иначе тест проходит и у клиента, который вообще ничего
не делает.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from apps.integrations.ayla import goals_client as gc
from apps.integrations.ayla.goals_client import (
    GoalsUnavailable,
    fetch_decision_context,
    post_goal_select,
    reset_goals_circuit,
)


EXT_USER = "bot:max:83146139"


class _State:
    """Что «хранит» поддельная Ayla между запросами теста."""

    def __init__(self) -> None:
        self.stored_goal: dict[str, Any] | None = None
        self.post_delay_s: float = 0.0
        self.commit_on_post: bool = True
        self.connections: int = 0
        self.requests: list[str] = []


def _document(state: _State) -> dict[str, Any]:
    goal = state.stored_goal
    return {
        "version": 1,
        "known": {"goal": goal},
        "missing": [] if goal else [{"kind": "goal", "prompt": "Что хочешь изменить?"}],
        "suggestions": [{"key": "relax", "label": "Расслабиться"}],
        "intents": [],
    }


def _make_server(state: _State) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        # keep-alive: без HTTP/1.1 сервер закрывает соединение сам и
        # «переиспользование» стало бы непроверяемым.
        protocol_version = "HTTP/1.1"

        def log_message(self, *args: Any) -> None:  # тишина в выводе pytest
            return

        def _reply(self, payload: dict[str, Any]) -> None:
            body = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802 — имя задано stdlib
            state.requests.append("GET " + self.path)
            self._reply(_document(state))

        def do_POST(self) -> None:  # noqa: N802 — имя задано stdlib
            state.requests.append("POST " + self.path)
            length = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(length) or b"{}")
            if state.commit_on_post:
                # Запись коммитится ДО задержки — ровно как на бэкенде, где
                # transaction.atomic() отрабатывает, а медленным оказывается
                # уже возврат ответа.
                state.stored_goal = {
                    "goal_key": payload.get("goal_key"),
                    "goal_text": payload.get("goal_text"),
                    "selected_at": "2026-09-01T08:00:00+00:00",
                    "source_channel": payload.get("source_channel"),
                }
            if state.post_delay_s:
                threading.Event().wait(state.post_delay_s)
            self._reply(_document(state))

    class Server(ThreadingHTTPServer):
        daemon_threads = True
        allow_reuse_address = True

        def get_request(self):  # type: ignore[override]
            state.connections += 1
            return super().get_request()

    return Server(("127.0.0.1", 0), Handler)


def _close_client() -> None:
    """Закрыть пул, если он уже существует (до правки такой функции нет)."""
    closer = getattr(gc, "close_goals_client", None)
    if closer is not None:
        closer()


@pytest.fixture
def ayla(settings) -> Any:
    """Поддельная Ayla на локальном порту + чистые пул и предохранитель."""
    state = _State()
    server = _make_server(state)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    # ``server_address`` в typeshed допускает и AF_UNIX, где адрес — ``str``
    # ИЛИ ``bytes``. Сервер выше связан с ("127.0.0.1", 0), то есть это всегда
    # AF_INET-пара, но сузить тип надо явно: при подстановке ``bytes`` в URL
    # попало бы "b'127.0.0.1'", и тест падал бы с неочевидным отказом
    # соединения. Поэтому байты именно ДЕКОДИРУЮТСЯ, а не приводятся к строке.
    address = server.server_address
    assert isinstance(address, tuple), "поддельная Ayla обязана слушать AF_INET"
    raw_host, port = address[0], address[1]
    host = raw_host.decode() if isinstance(raw_host, bytes) else raw_host
    settings.AYLA_BASE_URL = f"http://{host}:{port}"
    settings.AYLA_INTERNAL_API_TOKEN = "test-token"  # noqa: S105  # pragma: allowlist secret
    reset_goals_circuit()
    _close_client()
    try:
        yield state
    finally:
        _close_client()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        reset_goals_circuit()


class TestConnectionIsReusedAcrossCalls:
    """Дефект 1: полное рукопожатие на каждое нажатие пользователя."""

    def test_two_calls_share_one_connection(self, ayla: _State) -> None:
        """Экран целей = GET decision-context, следом POST goals/select.

        На пилоте второе рукопожатие стоило измеренные 3.77 s. Оно обязано
        не происходить вовсе.
        """
        fetch_decision_context(external_user_id=EXT_USER)
        post_goal_select(
            external_user_id=EXT_USER,
            payload={"goal_key": "relax", "source_channel": "miniapp"},
        )

        assert ayla.requests == [
            "GET /api/v1/internal/me/decision-context/",
            "POST /api/v1/internal/me/goals/select/",
        ]
        assert ayla.connections == 1, (
            "ожидалось одно TCP-соединение на оба вызова, открыто "
            "{0} — клиент строит пул на каждый запрос".format(ayla.connections)
        )

    def test_closing_the_pool_does_open_a_new_connection(self, ayla: _State) -> None:
        """Положительная стража к предыдущему тесту.

        Без неё «соединение одно» проходило бы и у клиента, который вообще
        не ходит по сети: здесь на тех же данных показано, что счётчик
        соединений живой и растёт, когда пул честно закрыт.
        """
        fetch_decision_context(external_user_id=EXT_USER)
        assert ayla.connections == 1

        _close_client()
        fetch_decision_context(external_user_id=EXT_USER)

        assert ayla.connections == 2, (
            "после закрытия пула следующий вызов обязан открыть новое "
            "соединение — иначе счётчик ничего не измеряет"
        )


class TestWriteSurvivesAReadTimeout:
    """Дефект 2: ReadTimeout по POST трактуется как потеря выбора."""

    def test_committed_goal_is_reconciled_instead_of_502(
        self, ayla: _State, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Ровно случай владельца: сервер принял запись и не успел ответить.

        Цель в бэкенде уже стоит. Пользователь обязан увидеть свой выбор, а
        не «Не получилось отправить» — иначе он жмёт ещё раз и получает
        вторую ClientGoal и второе событие воронки.
        """
        monkeypatch.setattr(gc, "READ_TIMEOUT_S", 0.3, raising=False)
        ayla.commit_on_post = True
        ayla.post_delay_s = 1.5

        body = post_goal_select(
            external_user_id=EXT_USER,
            payload={"goal_key": "relax", "source_channel": "miniapp"},
        )

        assert body["known"]["goal"] is not None
        assert body["known"]["goal"]["goal_key"] == "relax"
        assert "GET /api/v1/internal/me/decision-context/" in ayla.requests, (
            "сверка обязана быть отдельным дешёвым GET, а не повтором POST"
        )
        assert sum(1 for r in ayla.requests if r.startswith("POST")) == 1, (
            "POST не должен повторяться: он не идемпотентен по событию воронки GOAL_SELECTED"
        )

    def test_lost_write_still_fails(self, ayla: _State, monkeypatch: pytest.MonkeyPatch) -> None:
        """Положительная стража: если запись НЕ прошла, отказ остаётся отказом.

        Без неё «сверка чинит таймаут» проходила бы и у клиента, который
        просто перестал сообщать об ошибках.
        """
        monkeypatch.setattr(gc, "READ_TIMEOUT_S", 0.3, raising=False)
        ayla.commit_on_post = False  # запись до бэкенда не доехала
        ayla.post_delay_s = 1.5

        with pytest.raises(GoalsUnavailable):
            post_goal_select(
                external_user_id=EXT_USER,
                payload={"goal_key": "relax", "source_channel": "miniapp"},
            )

    def test_guidance_intent_is_not_reconciled(
        self, ayla: _State, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``intent=need_guidance`` не создаёт ClientGoal, значит сверять
        его нечем — и ходить за документом не надо вовсе.

        Проверяется именно ОТСУТСТВИЕ запроса, а не только исключение:
        отказ тут поднимется в любом случае (в документе цели нет, сверка
        всё равно не сойдётся), поэтому ``pytest.raises`` сам по себе
        ничего не пиннит. Ценность стражи `leaves_durable_trace` в том,
        что человек не ждёт лишний RECONCILE_READ_TIMEOUT_S ради заведомо
        бесполезного запроса — это и утверждается.
        """
        monkeypatch.setattr(gc, "READ_TIMEOUT_S", 0.3, raising=False)
        ayla.commit_on_post = False
        ayla.post_delay_s = 1.5

        with pytest.raises(GoalsUnavailable):
            post_goal_select(
                external_user_id=EXT_USER,
                payload={"intent": "need_guidance", "source_channel": "miniapp"},
            )

        assert not [r for r in ayla.requests if r.startswith("GET")], (
            "сверка по документу для need_guidance бессмысленна и не должна "
            "выполняться: ClientGoal у неё нет, а ожидание она удлиняет"
        )


class TestTimeoutBudgetIsSplit:
    """Один общий бюджет прятал, какая именно фаза его съела."""

    def test_connect_and_read_budgets_are_separate(self) -> None:
        """Соединение (редкое, холодное) и чтение (частое, тёплое) не могут
        делить одно число: на пилоте их стоимости отличаются в ~80 раз."""
        assert gc.CONNECT_TIMEOUT_S != gc.READ_TIMEOUT_S
        assert gc.CONNECT_TIMEOUT_S >= 4.0, "холодное рукопожатие измерено в 3.77 s"
