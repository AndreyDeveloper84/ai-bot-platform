"""DRF-2288 (решение владельца №41, CD §76) — ориентиры жиров и углеводов на Главной.

Живой проход 22.09 (DRF-2281, находка 4): «Б 0 / 123 · Ж 0 · У 0 г» — ориентир
был только у белка. DRF-1844 отдавал ``pfc.protein_target_g`` из профиля под
признаком происхождения калорий; каталог хранит и ``daily_fat_g`` /
``daily_carbs_g``, а бот их уже читал (``ProfileResponse.fat_g`` / ``carbs_g``) —
не отдавал. Решение: жиры и углеводы — тем же признаком.

* t1 — калории подтверждены: у Б, Ж и У свои ориентиры;
* t2 — калории не подтверждены: ни одного ориентира (профиль числа знает);
* t3 — у профиля нет ориентира по жирам — ключа жиров нет, остальные есть.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.test import Client

from apps.identity.models import BotUser
from apps.miniapp_api.tests.test_wellness_today import (  # noqa: F401 — фикстуры по имени
    _bot_token,
    _diary_consent_open,
    _FakeProfile,
    _FakeSummary,
    _FakeWater,
    _init_data_header,
    _nutrition_contour_on,
    _patch_nutrition,
    _url,
    bot_user,
    goals_stub,
    tenant,
)


@dataclass
class _ProfileWithMacros(_FakeProfile):
    protein_g: float | None = 130.4
    fat_g: float | None = 60.6
    carbs_g: float | None = 219.5


def _get(client: Client, bot_user: BotUser, profile) -> dict:  # noqa: F811
    with _patch_nutrition(
        summary=_FakeSummary(calories_goal=2000), water=_FakeWater(), profile=profile
    ):
        resp = client.get(_url(), HTTP_AUTHORIZATION=_init_data_header(bot_user.channel_user_id))
    assert resp.status_code == 200, resp.content
    return resp.json()


class TestT1AllThreeTargetsRideTogether:
    def test_confirmed_calories_bring_fat_and_carbs_targets(
        self,
        client: Client,
        bot_user: BotUser,  # noqa: F811
    ) -> None:
        pfc = _get(client, bot_user, _ProfileWithMacros())["pfc"]
        assert pfc["protein_target_g"] == 130
        assert pfc["fat_target_g"] == 61
        assert pfc["carbs_target_g"] == 220


class TestT2NoProvenanceNoTargets:
    def test_unconfirmed_calories_send_none(self, client: Client, bot_user: BotUser) -> None:  # noqa: F811
        data = _get(client, bot_user, _ProfileWithMacros(calories_override=False))
        assert "calories_eaten" in data  # наличие: срез питания в ответе есть
        pfc = data["pfc"]
        assert pfc["protein_g"] is not None  # наличие: строка БЖУ в ответе есть
        assert not {"protein_target_g", "fat_target_g", "carbs_target_g"} & set(pfc)


class TestT3EachTargetOnItsOwn:
    def test_a_missing_fat_target_drops_only_its_key(
        self,
        client: Client,
        bot_user: BotUser,  # noqa: F811
    ) -> None:
        pfc = _get(client, bot_user, _ProfileWithMacros(fat_g=None))["pfc"]
        assert pfc["protein_target_g"] == 130 and pfc["carbs_target_g"] == 220
        assert "fat_target_g" not in pfc
