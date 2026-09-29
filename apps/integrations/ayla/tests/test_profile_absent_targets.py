"""DRF-1623 N-c: отсутствие ориентира переживает границу с каталогом.

**Половина этой поставки живёт в другом репозитории** (`beautygo_backend`,
`nutrition/services/profile_upsert_service.py`): каталог перестаёт слать
`norms` с нулями, когда расчёта не было. Слияние парное — любая половина
в одиночку не даёт наблюдаемого эффекта:

* каталог перестал слать ноль, а бот изготовил его заново на границе;
* бот перестал изготавливать, а каталог прислал настоящий ноль.

Человек в обоих случаях видит одно и то же, поэтому это одна поставка.

Здесь проверяется бот-половина: разбор ответа профиля. До правки стояло
``int(norms.get("daily_kcal") or 0)`` при ``daily_kcal: int`` в типе —
«ключа нет» и «ноль» становились неразличимы раньше, чем кто-либо успевал
увидеть разницу.
"""

from __future__ import annotations

from apps.integrations.ayla.nutrition_client import _target_or_none


class TestAnAbsentTargetSurvivesTheBoundary:
    def test_a_missing_key_is_none_not_zero(self) -> None:
        """Пустой ``norms`` — то, что каталог присылает при отказе."""
        assert _target_or_none({}, "daily_kcal") is None

    def test_a_zero_is_also_none(self) -> None:
        """Строки, посчитанные ДО перехода каталога, ещё присылают нули.

        Ориентир ноль калорий в сутки физически невозможен, поэтому ноль
        здесь — молчаливое имя отсутствия, и разница между ним и «нет»
        обязана исчезнуть на границе, а не ждать миграции (N-b).
        """
        assert _target_or_none({"daily_kcal": 0}, "daily_kcal") is None
        assert _target_or_none({"daily_kcal": None}, "daily_kcal") is None

    def test_a_real_target_survives_unchanged(self) -> None:
        """Положительная стража ко всем трём проверкам выше.

        Без неё «отсутствие доезжает отсутствием» зеленело бы и у
        функции, которая возвращает ``None`` ВСЕГДА, — то есть у
        границы, стершей все ориентиры разом.
        """
        assert _target_or_none({"daily_kcal": 1850}, "daily_kcal") == 1850
        # Строкой из JSON тоже: тип на проводе не гарантирован.
        assert _target_or_none({"bmr": "1450"}, "bmr") == 1450

    def test_each_key_is_read_independently(self) -> None:
        """Отсутствие одного ориентира не гасит остальные.

        Иначе «нет калорий» означало бы «нет ничего», и человек с
        посчитанными макросами увидел бы пустую карточку.
        """
        norms = {"daily_protein_g": 95, "daily_fat_g": 60}

        assert _target_or_none(norms, "daily_kcal") is None
        assert _target_or_none(norms, "daily_protein_g") == 95
        assert _target_or_none(norms, "daily_fat_g") == 60
