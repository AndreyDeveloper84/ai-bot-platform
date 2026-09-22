"""Каждый раздел выгрузки каталога (C5.1) объявлен в боте (DRF-2307).

Бот вкладывает ответ C5.1 в свою выгрузку целиком (раздел ``ayla``) и ключей
не разбирает — поэтому новый раздел каталога доходит до человека сам, а вот
**состав** выгрузки (``export_coverage``) и матрица «забудь всё» о нём молчат,
пока их не впишут руками. Так и случилось: beautygo_backend #545 добавил
``notification_history`` и ``app_ai_chat``, #548 — ``favorite_specialists``, а
бот знал пять разделов из #544.

Сторож — по прецеденту ``apps/integrations/ayla/tests/test_contract_route_table``:
**зеркало ключей C5.1 в коде бота** (``export_coverage.CATALOG_EXPORT_SECTIONS``)
и статическая сверка, что каждый раздел зеркала объявлен — в ``KNOWN_LIMITS``
словами, в ``NON_REGISTRY_SECTIONS`` как ``catalog.<раздел> → ayla`` и строкой
исхода в матрице. Честная граница: статика доказывает «зеркало ↔ объявления»,
а не «зеркало ↔ каталог». Вторую половину держит ночной живой узел
``tests/e2e/test_ayla_integration.py::TestPersonalDataExportSections`` — он
читает настоящий C5.1 на стенде и краснеет на разделе, которого нет в зеркале.
"""

from __future__ import annotations

import pytest

from apps.identity import export_coverage as cov

#: Разделы профиля: объявлены строкой ``KNOWN_LIMITS`` и ``CATALOG_STORE``
#: (профиль предпочтений), а не строками ``catalog.<раздел>`` — они про
#: аккаунт, а не про запомненное.
PROFILE_SECTIONS = ("profile", "personal_context", "specialist_profile")

#: Служебные ключи ответа — не данные о человеке.
ENVELOPE_KEYS = ("user_id", "exported_at", "linked_identities")


def _mirror() -> tuple[str, ...]:
    mirror = getattr(cov, "CATALOG_EXPORT_SECTIONS", None)
    assert mirror, "нет зеркала ключей C5.1 в export_coverage"
    return tuple(mirror)


def _ayla_limit() -> str:
    (line,) = [limit for limit in cov.KNOWN_LIMITS if limit.startswith("Раздел ayla")]
    return line


def _remembered_sections() -> list[str]:
    return [s for s in _mirror() if s not in (*PROFILE_SECTIONS, *ENVELOPE_KEYS)]


class TestTheMirrorIsTheCatalogOfToday:
    def test_the_mirror_names_every_section_c51_carries(self) -> None:
        """Зеркало — ключи C5.1 на каталоге dev 80affd14 (после #544, #545, #548)."""
        assert set(_mirror()) == {
            *ENVELOPE_KEYS,
            *PROFILE_SECTIONS,
            "goals",
            "wellness_plan",
            "nutrition_profile",
            "food_diary",
            "shown_hints",
            "notification_history",
            "app_ai_chat",
            "favorite_specialists",
        }


class TestEverySectionIsDeclared:
    def test_every_data_section_is_named_in_the_ayla_limit(self) -> None:
        line = _ayla_limit()
        missing = [s for s in _remembered_sections() if f"({s})" not in line]
        assert _remembered_sections()  # наличие
        assert missing == [], missing

    def test_every_remembered_section_is_carried_under_ayla(self) -> None:
        missing = [
            s
            for s in _remembered_sections()
            if cov.NON_REGISTRY_SECTIONS.get(f"catalog.{s}") != "ayla"
        ]
        assert _remembered_sections()  # наличие
        assert missing == [], missing

    def test_no_catalog_declaration_outlives_its_section(self) -> None:
        """Обратная сторона: объявленный ``catalog.<раздел>``, которого нет в C5.1, — ложь."""
        declared = {
            key.removeprefix("catalog.")
            for key in cov.NON_REGISTRY_SECTIONS
            if key.startswith("catalog.")
        }
        assert declared  # наличие
        assert declared <= set(_mirror()), declared - set(_mirror())


class TestEverySectionHasAnOutcome:
    def test_every_remembered_section_has_a_matrix_row(self) -> None:
        from apps.identity.tests.test_forget_all_matrix import OUTCOMES

        missing = [s for s in _remembered_sections() if f"catalog.{s}" not in OUTCOMES]
        assert _remembered_sections()  # наличие
        assert missing == [], missing

    @pytest.mark.parametrize(
        ("section", "outcome"),
        [
            ("notification_history", "DELETE"),
            ("app_ai_chat", "DELETE"),
            ("favorite_specialists", "RETAIN"),
        ],
    )
    def test_the_new_sections_have_the_owners_outcome(self, section, outcome) -> None:
        """§72 п.3: уведомления и ИИ-чат стираются; избранные мастера остаются."""
        from apps.identity.tests.test_forget_all_matrix import OUTCOMES

        assert f"catalog.{section}" in OUTCOMES
        assert OUTCOMES[f"catalog.{section}"].outcome == outcome
