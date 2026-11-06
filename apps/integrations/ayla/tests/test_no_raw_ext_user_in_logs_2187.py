"""DRF-2187 — клиенты Ayla не печатают в лог id человека в канале; подсказка цели ждёт недолго.

``external_user_id`` — это ``bot:{channel}:{channel_user_id}``: id человека в
канале, только в обёртке. Клиенты Ayla печатали его в строках отказов: на
02.10 — 27 вызовов логгера в четырёх клиентах, под двумя ключами (``ext_user=``
и ``ext=``). С DRF-2124 анкета питания зовёт ``fetch_decision_context`` на
каждом рендере шага цели, так что строк стало больше.

Статическую сторону — «ни один вызов логгера в ``apps/`` не несёт сырой
внешний id» — держит общий сторож
``apps/observability/tests/test_logs_carry_no_channel_identity.py`` (имя
добавлено в его класс). Здесь — то, чего сторож по коду не видит:

* m — что именно стоит в строке вместо id: маркер не содержит id, одинаков у
  одного человека, разный у разных, и не воспроизводится простым хешем;
* l — живая строка отказа ``goals_client`` на трёх классах отказа несёт маркер
  и не несёт id;
* t — подсказка цели читает документ со своим, коротким бюджетом; остальные
  читатели — с общим.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any

import httpx
import pytest

from apps.integrations.ayla import goals_client as gc
from apps.integrations.ayla.log_ref import external_user_log_ref

EXTERNAL_ID = "bot:max:5551234567"
OTHER_ID = "bot:max:5551234568"


# ── m: the marker ────────────────────────────────────────────────────────────


class TestMTheMarker:
    def test_it_is_twelve_hex_characters_and_holds_no_part_of_the_id(self) -> None:
        marker = external_user_log_ref(EXTERNAL_ID)

        assert len(marker) == 12
        assert set(marker) <= set("0123456789abcdef")
        assert "5551234567" not in marker and "bot" not in marker and "max" not in marker

    def test_one_person_one_marker_two_people_two_markers(self) -> None:
        assert external_user_log_ref(EXTERNAL_ID) == external_user_log_ref(EXTERNAL_ID)
        assert external_user_log_ref(EXTERNAL_ID) != external_user_log_ref(OTHER_ID)

    def test_a_plain_hash_of_the_id_does_not_reproduce_it(self) -> None:
        """Id канала — короткое число: простой хеш перебирается. Маркер ключёван."""
        marker = external_user_log_ref(EXTERNAL_ID)

        assert marker != hashlib.sha256(EXTERNAL_ID.encode()).hexdigest()[:12]
        assert marker != hashlib.md5(EXTERNAL_ID.encode()).hexdigest()[:12]  # noqa: S324

    def test_another_key_gives_another_marker(self, settings) -> None:
        before = external_user_log_ref(EXTERNAL_ID)

        settings.SECRET_KEY = "another-key-for-drf-2187"  # noqa: S105  # pragma: allowlist secret

        assert external_user_log_ref(EXTERNAL_ID) != before

    @pytest.mark.parametrize("nobody", [None, ""])
    def test_nobody_is_not_somebody(self, nobody) -> None:
        assert external_user_log_ref(nobody) == "-"


# ── l: a live refusal line ───────────────────────────────────────────────────


class _Http:
    """Stands where the pooled ``httpx.Client`` stands; records the timeout."""

    def __init__(self, outcome: Any) -> None:
        self.outcome = outcome
        self.timeouts: list[httpx.Timeout] = []
        self.headers: list[str] = []

    def request(self, method, url, *, headers, json, timeout):  # noqa: A002
        self.timeouts.append(timeout)
        self.headers.append(headers["X-External-User-ID"])
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


@pytest.fixture
def wired(settings, monkeypatch):
    settings.AYLA_BASE_URL = "http://ayla.test"
    settings.AYLA_INTERNAL_API_TOKEN = "test-token"  # noqa: S105  # pragma: allowlist secret
    gc.reset_goals_circuit()

    def _wire(outcome: Any) -> _Http:
        http = _Http(outcome)
        monkeypatch.setattr(gc, "_get_client", lambda: http)
        return http

    yield _wire
    gc.reset_goals_circuit()


_REFUSALS = [
    ("network_failure", httpx.ReadTimeout("slow"), gc.GoalsUnavailable),
    ("server_error", httpx.Response(503, json={}), gc.GoalsUnavailable),
    ("client_error", httpx.Response(404, json={"detail": "x"}), gc.GoalsBadRequest),
]


class TestLTheLiveLine:
    @pytest.mark.parametrize(
        ("slug", "outcome", "raised"), _REFUSALS, ids=[r[0] for r in _REFUSALS]
    )
    def test_a_refusal_line_carries_the_marker_and_not_the_id(
        self, wired, caplog, slug, outcome, raised
    ) -> None:
        wired(outcome)
        caplog.set_level(logging.INFO, logger="apps.integrations.ayla.goals_client")

        with pytest.raises(raised):
            gc.fetch_decision_context(external_user_id=EXTERNAL_ID)

        lines = [r.getMessage() for r in caplog.records if f"goals_client.{slug}" in r.getMessage()]
        # Presence first: the refusal WAS logged, so «no id in it» is said of a real line.
        assert len(lines) == 1, [r.getMessage() for r in caplog.records]
        assert f"ext_ref={external_user_log_ref(EXTERNAL_ID)}" in lines[0]
        assert EXTERNAL_ID not in lines[0]
        assert "5551234567" not in lines[0]
        assert "ext_user=" not in lines[0]

    def test_the_id_still_goes_to_ayla(self, wired) -> None:
        """Маркер — для лога. Каталогу по-прежнему уходит сам id, иначе он не узнает человека."""
        http = wired(httpx.Response(200, json={"data": {"version": 2}}))

        document = gc.fetch_decision_context(external_user_id=EXTERNAL_ID)

        assert document == {"version": 2}
        assert http.headers == [EXTERNAL_ID]


# ── t: the hint's budget ─────────────────────────────────────────────────────


class TestTTheHintBudget:
    def test_the_number_and_its_order(self) -> None:
        """Литералом: 2 s — из замера тёплого чтения 0.05–0.57 s, с запасом."""
        assert gc.GOAL_HINT_READ_TIMEOUT_S == 2.0
        assert gc.GOAL_HINT_READ_TIMEOUT_S < gc.RECONCILE_READ_TIMEOUT_S < gc.READ_TIMEOUT_S
        assert gc.READ_TIMEOUT_S == 5.0

    def test_the_budget_reaches_the_wire(self, wired) -> None:
        http = wired(httpx.Response(200, json={"data": {"version": 2}}))

        gc.fetch_decision_context(
            external_user_id=EXTERNAL_ID, read_timeout_s=gc.GOAL_HINT_READ_TIMEOUT_S
        )

        (timeout,) = http.timeouts
        assert timeout.read == 2.0
        assert timeout.connect == gc.CONNECT_TIMEOUT_S

    def test_other_readers_keep_the_common_budget(self, wired) -> None:
        http = wired(httpx.Response(200, json={"data": {"version": 2}}))

        gc.fetch_decision_context(external_user_id=EXTERNAL_ID)

        (timeout,) = http.timeouts
        assert timeout.read == 5.0
