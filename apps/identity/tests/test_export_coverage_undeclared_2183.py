"""Выгрузка по 152-ФЗ ст. 14 называет четыре хранилища, о которых молчала (DRF-2183).

Таблица состава выгрузки (``export_coverage``) сверяется с реестром
персональных полей, а тот находит КОЛОНКИ МОДЕЛЕЙ. Четыре хранилища этой
формы не имеют — и в документе, который человек получает на «выгрузить мои
данные», о них не было ни слова:

* ``consent.ConsentRecord`` — согласия. Они ВЫГРУЖАЮТСЯ (раздел ``consents``),
  но таблица состава об этом молчала: ни «вошло», ни «не вошло»;
* ``conversations.AiDraft`` — неотправленный черновик мастера, который
  цитирует клиента дословно;
* ``redis.short_term`` — окно сырых реплик в Redis (TTL сутки);
* ``redis.pii_tokenmap`` — обратная карта «токен → настоящий телефон».

Матрица удаления (DRF-2134) держит их strict-xfail в ``UNDECLARED_IN_EXPORT``
с пометкой «лист следом». Этот лист их и называет.

# Главное различие — выгружено или нет

Согласия выгружаются, а таблица умела говорить «выгружено» только о слотах
реестра; для хранилища вне реестра у неё было лишь «не выгружено»
(``NON_REGISTRY_STORES`` рендерится в ``withheld``). Положить согласия туда
значило бы соврать в юридическом документе: сказать «не выгружаем» о том, что
лежит в этой же выгрузке двумя абзацами выше. Поэтому нужен второй вид строки
— «хранилище вне реестра, выгружено в раздел X», — и узел, который не даст
объявить раздел, которого в выгрузке нет.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, cast

import pytest

from apps.identity.export_coverage import build_coverage_section
from apps.identity.models import BotUser
from apps.identity.services.privacy import export_personal_data
from apps.identity.tests.test_export_coverage import _NoAyla
from apps.tenancy.models import Tenant

if TYPE_CHECKING:
    from apps.integrations.ayla.personal_context_client import PersonalContextHttpClient

pytestmark = pytest.mark.django_db


def _no_ayla() -> "PersonalContextHttpClient":
    """Заглушка Ayla (пустая выгрузка) — утверждается только половина бота.

    Приведение явное: заглушка повторяет ровно два метода, которые зовёт
    выгрузка, а не весь клиент. Соседний тест того же файла не требует
    этого лишь потому, что его методы без аннотаций и mypy их тела не
    проверяет.
    """
    return cast("PersonalContextHttpClient", _NoAyla())


WITHHELD_NOW = ("conversations.AiDraft", "redis.short_term", "redis.pii_tokenmap")


def _withheld_by_store() -> dict[str, str]:
    """``withheld`` строками по хранилищу (``conversations.AiDraft.content`` → ``conversations.AiDraft``)."""
    rows: dict[str, str] = {}
    for row in build_coverage_section()["withheld"]:
        field = row["field"]
        store = field if field.startswith("redis.") else ".".join(field.split(".")[:2])
        rows.setdefault(store, row["reason"])
    return rows


@pytest.fixture
def bot_user() -> BotUser:
    tenant = Tenant.objects.create(slug="cov-2183", name="Coverage 2183")
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="2183",
        chat_id="2183",
        ayla_user_id=uuid.uuid4(),
    )


class TestConsentsAreDeclaredAsCarried:
    def test_consents_are_named_under_the_section_that_carries_them(self) -> None:
        included = build_coverage_section()["included"]
        assert "consent.ConsentRecord" in included.get("consents", [])

    def test_consents_are_not_declared_withheld(self) -> None:
        """Анти-ложь: «не выгружаем» о том, что лежит в этой же выгрузке."""
        withheld = _withheld_by_store()
        # Наличие — первым: таблица вообще что-то объявляет «не выгруженным».
        assert "conversations.Message" in withheld
        assert "consent.ConsentRecord" not in withheld


class TestTheThreeWithheldStoresAreNamed:
    @pytest.mark.parametrize("store", WITHHELD_NOW)
    def test_the_store_is_named_with_a_reason(self, store) -> None:
        withheld = _withheld_by_store()
        assert store in withheld, f"{store}: в выгрузке ни слова"
        # Та же планка, что у всех причин таблицы: причина, а не отписка.
        assert len(withheld[store]) >= 120, withheld[store]


class TestTheDeclarationMatchesTheDocument:
    def test_every_declared_section_exists_in_the_real_export(self, bot_user) -> None:
        """Нельзя объявить «выгружено в раздел X», если раздела X в выгрузке нет.

        Иначе декларация врала бы в обратную сторону — обещала бы то, чего
        человек в полученном файле не найдёт.
        """
        payload = export_personal_data(bot_user, client=_no_ayla())
        included = payload["coverage"]["included"]

        assert "consents" in included  # наличие
        for section in included:
            assert section in payload, f"объявлен раздел «{section}», а в выгрузке его нет"

    def test_the_real_export_carries_the_same_declaration(self, bot_user) -> None:
        """Не только функция таблицы — сам файл, который получает человек."""
        payload = export_personal_data(bot_user, client=_no_ayla())

        assert "consent.ConsentRecord" in payload["coverage"]["included"]["consents"]
        withheld_fields = {row["field"] for row in payload["coverage"]["withheld"]}
        for store in WITHHELD_NOW:
            assert any(f == store or f.startswith(store + ".") for f in withheld_fields), store


class TestTheMatrixDebtIsClosed:
    def test_the_four_stores_are_no_longer_undeclared(self) -> None:
        """Матрица держала их strict-xfail «лист следом» — долг снят."""
        from apps.identity.tests.test_forget_all_matrix import (
            UNDECLARED_IN_EXPORT,
            stores_declared_by_export_coverage,
        )

        declared = stores_declared_by_export_coverage()
        for store in ("consent.ConsentRecord", *WITHHELD_NOW):
            assert store in declared, store
            assert store not in UNDECLARED_IN_EXPORT, store


class TestTheNewKindOfLineHasItsOwnRules:
    def test_a_non_registry_section_is_not_a_registry_slot(self) -> None:
        """Слот реестра объявляется через `SECTIONS`; второй путь дал бы два объявления."""
        from apps.identity.export_coverage import NON_REGISTRY_SECTIONS
        from apps.identity.personal_fields import PERSONAL_FIELDS

        registry = {f.site for f in PERSONAL_FIELDS}
        assert NON_REGISTRY_SECTIONS  # наличие
        assert not (set(NON_REGISTRY_SECTIONS) & registry)

    def test_a_store_is_never_both_carried_and_withheld(self) -> None:
        """Одно хранилище — одно решение: «выгружено» и «не выгружено» разом — ложь."""
        from apps.identity.export_coverage import NON_REGISTRY_SECTIONS, NON_REGISTRY_STORES

        def store(key: str) -> str:
            return key if key.startswith("redis.") else ".".join(key.split(".")[:2])

        carried = {store(k) for k in NON_REGISTRY_SECTIONS}
        withheld = {store(k) for k in NON_REGISTRY_STORES}
        assert carried  # наличие
        assert not (carried & withheld)
