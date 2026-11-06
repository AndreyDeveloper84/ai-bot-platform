"""DRF-2766 — «Без чисел»: один читатель выбора для всех поверхностей.

``numbers_hidden_for`` читают Mini App, карточки и реплики чата, отчёт за день
и контекст модели. Он не бросает: реплика не должна падать из-за настройки
отображения. Не прочитали — выбора не знаем, числа видны (режим
добровольный; скрывать за человека — то, что владелец снял 04.10).
"""

from __future__ import annotations

from unittest.mock import patch

from apps.nutrition_proactive import prefs


class TestTheOneReader:
    def test_the_choice_is_read(self) -> None:
        with patch.object(prefs, "get_prefs", return_value={"numbers_hidden": True}):
            assert prefs.numbers_hidden_for(object()) is True

    def test_no_choice_means_numbers_are_shown(self) -> None:
        with patch.object(prefs, "get_prefs", return_value={}):
            assert prefs.numbers_hidden_for(object()) is False

    def test_only_a_real_true_hides(self) -> None:
        for value in ("true", 1, "yes", None):
            with patch.object(prefs, "get_prefs", return_value={"numbers_hidden": value}):
                assert prefs.numbers_hidden_for(object()) is False, value

    def test_a_failed_read_never_raises_and_shows_numbers(self) -> None:
        # Положительная пара: тот же читатель с исправным чтением видит выбор.
        with patch.object(prefs, "get_prefs", return_value={"numbers_hidden": True}):
            assert prefs.numbers_hidden_for(object()) is True
        with patch.object(prefs, "get_prefs", side_effect=RuntimeError("boom")):
            assert prefs.numbers_hidden_for(object()) is False
