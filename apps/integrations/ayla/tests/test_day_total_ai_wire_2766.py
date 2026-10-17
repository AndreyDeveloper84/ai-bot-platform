"""DRF-2766 фаза 2 — клиент читает итог дня с оценками ИИ с живого провода.

Ответы — из ``fixtures/day_total_ai_live_2766.json``: сняты с настоящих
ручек каталога (``internal/summary``, ``internal/diary/days``) на ветке
каталожного PR фазы 2, а не написаны от руки. День: борщ 147 (проверенное),
шакшука 300 (оценка ИИ), одна запись без какого-либо значения.

* w1 — сводка: итог 447, одна запись оценкой, одна без калорий;
* w2 — день только из записей без калорий: итога нет — ``None``, не ``0.0``
  (раньше ``or 0.0`` давал «0 из N» человеку, записавшему еду);
* w3 — неделя: то же правило и те же счётчики в строке дня.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from apps.integrations.ayla import nutrition_client as nc

_LIVE = json.loads(
    (Path(__file__).parent / "fixtures" / "day_total_ai_live_2766.json").read_text(encoding="utf-8")
)["responses"]


@pytest.fixture(autouse=True)
def _patch_async_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    original = httpx.AsyncClient
    holder: dict[str, httpx.MockTransport | None] = {"transport": None}

    def _factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        if holder["transport"] is not None:
            kwargs["transport"] = holder["transport"]
        return original(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", _factory)
    _patch_async_client.holder = holder  # type: ignore[attr-defined]
    yield
    holder["transport"] = None


def _answering(case: str) -> nc.NutritionClient:
    live = _LIVE[case]
    _patch_async_client.holder["transport"] = httpx.MockTransport(  # type: ignore[attr-defined]
        lambda request: httpx.Response(live["status"], json=live["body"])
    )
    return nc.NutritionClient(base_url="https://ayla.test", service_token="t")


class TestTheLiveWire:
    @pytest.mark.asyncio
    async def test_w1_the_summary_total_includes_the_estimate_and_counts_it(self) -> None:
        client = _answering("summary_mixed")

        summary = await client.daily_summary(external_user_id="bot:max:2766", date="2026-09-16")

        assert summary.calories_total == 447.0
        assert summary.calories_ai_included == 1
        assert summary.calories_unscored == 1

    @pytest.mark.asyncio
    async def test_w2_no_total_stays_absent_not_zero(self) -> None:
        client = _answering("summary_unvalued_only")

        summary = await client.daily_summary(external_user_id="bot:max:2766", date="2026-09-16")

        assert summary.calories_unscored == 1
        assert summary.calories_total is None

    @pytest.mark.asyncio
    async def test_w3_the_week_row_carries_the_same_rule(self) -> None:
        client = _answering("diary_days_mixed")

        week = await client.diary_days(
            external_user_id="bot:max:2766", date_from="2026-09-16", date_to="2026-09-16"
        )

        (row,) = week.days
        assert row.meals_count == 3
        assert row.kcal == 447.0
        assert row.kcal_ai_included == 1
        assert row.uncounted_meals == 1
