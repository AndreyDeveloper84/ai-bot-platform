"""DRF-2308 B — сторож «удаление аккаунта ⊇ забудь всё», поведенческий, от матрицы.

Два писателя стирания по человеку: «забудь всё» (``sweep_forget_all``, исходы —
:data:`test_forget_all_matrix.OUTCOMES`) и удаление аккаунта (D3,
``account_deletion.execute_bot_half`` → ``privacy.delete_personal_data``).
Списки того, что они трогают, ведутся руками и расходятся молча: #1980 научил
«забудь всё» стирать ключ цели карточки, а D3 его оставлял (DRF-2308 A). В
каталоге ту же сверку держит сторож по исходнику (beautygo_backend #539); в
боте двух списков для AST нет — хранилища выводятся из реестра экспорта, исходы
лежат в матрице. Поэтому сторож поведенческий: тот же посев
(``seed_person``), тот же снимок (``snapshot``), прогон D3, сравнение по
исходу «забудь всё».

# Правило «не меньше»

* ``DELETE`` у «забудь всё» → после D3 живого не осталось (для ``Conversation``
  «удаление» — пустой ``skill_state`` при ``anonymized_through``, как в матрице);
* ``ANONYMISE`` → после D3 строки нет, или поля ПДн, которые чистит «забудь
  всё», пусты (удаление аккаунта может чистить и больше — это не нарушение);
* ``RETAIN`` → ничего не требуется: D3 вправе стирать то, что «забудь всё»
  оставляет (настройки, оболочку, согласия).

# Что бот не наблюдает

Каталожные хранилища (:data:`CATALOG_STORES`) при D3 стирает исполнитель в
каталоге (``users.deletion_executor``) ДО запроса к боту; снимок бота по ним —
задания readback ``AylaErasureJob``, которых у D3 нет по устройству. Сверка
каталожной половины — сторож #539. Здесь они названы, а не молча пропущены.

# Цепочка D3

``execute_bot_half`` (каскад C5: память, согласия, оболочки, нити ассистента,
диалог, карточки) → плановый ``sweep_pending_forget_all``: шаг памяти D3 ставит
«забудь всё» в очередь, и жёлтую/красную зоны снимает подметальщик — как в бою.
"""

from __future__ import annotations

from typing import Any

import pytest

from apps.identity.services.account_deletion import execute_bot_half
from apps.identity.services.forget_all_sweep import sweep_pending_forget_all
from apps.identity.tests.test_forget_all_matrix import (  # noqa: F401 — фикстура по имени
    ANONYMISE,
    CATALOG_STORES,
    DELETE,
    OUTCOMES,
    RETAIN,
    _erased_status,
    _FakeCatalog,
    _present,
    fake_redis,
    seed_person,
    snapshot,
)
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

#: Поля ПДн, которые «забудь всё» чистит в хранилищах с исходом ANONYMISE, —
#: и значение «пусто» для каждого. Сторож требует от D3 того же (или удаления).
ANONYMISED_FIELDS: dict[str, dict[str, Any]] = {
    "identity.UserPersonalContext": {
        "summary": None,
        "display_name_preferred": None,
        "language_preferred": None,
    },
    "conversations.Message": {
        "content": "",
        "rendered_text": "",
        "action_data": None,
        "tool_call": None,
    },
    "conversations.AiDraft": {"content": ""},
    "recommendation.Recommendation": {"why": [], "facts": {}, "goal_id": ""},
}

#: Хранилища, которые бот при D3 не наблюдает, — с причиной.
NOT_OBSERVABLE_IN_BOT: dict[str, str] = {
    store: "каталожная половина: стирает users.deletion_executor в каталоге до запроса "
    "к боту; сверка — сторож beautygo_backend #539"
    for store in CATALOG_STORES
}

#: Хранилища, где D3 СОЗНАТЕЛЬНО слабее «забудь всё», — с причиной. Список
#: точный: запись, у которой разрыва нет, тоже красная.
DELETION_WEAKER_ON_PURPOSE: dict[str, str] = {}


def _fingerprint_erased(row: dict) -> bool:
    return str(row["fingerprint"]).startswith("erased:")


def covers(store: str, after: Any) -> tuple[bool, str]:
    """Покрывает ли состояние после D3 исход «забудь всё» по ``store``."""
    expected = OUTCOMES[store].outcome
    if expected == RETAIN:
        return True, "RETAIN — без требований"
    if expected == DELETE:
        if store == "conversations.Conversation":
            ok = all(r["skill_state"] == {} and r["anonymized_through"] for r in after)
            return ok, f"skill_state/anonymized_through: {after}"
        return not _present(after), f"живое после D3: {after}"
    assert expected == ANONYMISE
    if not _present(after):
        return True, "строки нет — сильнее обезличивания"
    fields = ANONYMISED_FIELDS[store]
    bad = [
        {k: row.get(k) for k in fields if row.get(k) != empty}
        for row in after
        for k, empty in fields.items()
        if row.get(k) != empty
    ]
    if store == "recommendation.Recommendation":
        bad += [{"fingerprint": r["fingerprint"]} for r in after if not _fingerprint_erased(r)]
    return not bad, f"поля ПДн не пусты: {bad}"


@pytest.fixture
def deleted(settings, fake_redis, monkeypatch):  # noqa: F811
    """Посеять человека и соседа во всех хранилищах, прогнать D3 над человеком."""
    import apps.identity.services.privacy as privacy

    settings.STRICT_TENANT_SCOPE = "off"
    catalog = _FakeCatalog(_erased_status())
    monkeypatch.setattr(privacy, "PersonalContextHttpClient", lambda *a, **kw: catalog)

    def _run():
        tenant = Tenant.objects.create(slug="d3-covers-2308", name="D3 ⊇ forget-all")
        person = seed_person(tenant, fake_redis, "person")
        neighbour = seed_person(tenant, fake_redis, "neighbour")
        before = {
            who.label: {store: snapshot(store, who, fake_redis) for store in OUTCOMES}
            for who in (person, neighbour)
        }
        outcome = execute_bot_half(
            ayla_user_id=person.ayla_user_id, external_user_ids=[], request_id="drf-2308-b"
        )
        summary = sweep_pending_forget_all()
        after = {store: snapshot(store, person, fake_redis) for store in OUTCOMES}
        return person, neighbour, before, after, outcome, summary

    return _run


def _observed_stores() -> list[str]:
    return [s for s in OUTCOMES if s not in NOT_OBSERVABLE_IN_BOT]


class TestAccountDeletionCoversForgetAll:
    def test_the_bot_half_reports_success(self, deleted) -> None:
        _, _, _, _, outcome, summary = deleted()
        assert outcome.all_ok, outcome.failed_steps
        assert summary["errors"] == 0

    @pytest.mark.parametrize("store", _observed_stores())
    def test_store(self, deleted, store) -> None:
        _, _, before, after, _, _ = deleted()
        if OUTCOMES[store].outcome != RETAIN:
            assert _present(before["person"][store]), f"{store}: посев пуст — сверять не по чему"
        ok, why = covers(store, after[store])
        if store in DELETION_WEAKER_ON_PURPOSE:
            assert not ok, f"{store}: в исключениях, а разрыва нет — убрать запись"
            return
        assert ok, f"{store}: удаление аккаунта слабее «забудь всё» — {why}"

    def test_the_neighbour_is_untouched(self, deleted, fake_redis) -> None:  # noqa: F811
        _, neighbour, before, _, _, _ = deleted()
        now = {store: snapshot(store, neighbour, fake_redis) for store in _observed_stores()}
        changed = {
            s: (before["neighbour"][s], now[s]) for s in now if before["neighbour"][s] != now[s]
        }
        assert any(_present(v) for v in now.values())  # положительно: сосед на месте
        assert changed == {}, changed  # empty-assert-ok: присутствие соседа строкой выше


class TestTheGuardIsHonest:
    def test_every_store_is_judged_or_named(self) -> None:
        """Каждое хранилище матрицы либо сверяется, либо названо с причиной."""
        observed = set(_observed_stores())
        assert observed  # положительно: сверять есть что
        assert observed | set(NOT_OBSERVABLE_IN_BOT) == set(OUTCOMES)
        for store in observed:
            if OUTCOMES[store].outcome == ANONYMISE:
                assert store in ANONYMISED_FIELDS, f"{store}: ANONYMISE без списка полей ПДн"

    def test_the_guard_sees_a_gap(self, deleted, monkeypatch) -> None:
        """Контроль чувствительности: карточки не обезличены — сторож красный.

        Карточки при D3 закрыты дважды: шаг 7 каскада и поставленный шагом
        памяти «забудь всё», чей подметальщик (#1980) обезличивает их сам.
        Разрыв возникает, только если молчат оба, — оба и выключены.
        """
        import apps.identity.services.forget_all_sweep as sweep
        import apps.recommendation.erasure as reco_erasure

        monkeypatch.setattr(reco_erasure, "anonymize_recommendations", lambda ids: 0)
        monkeypatch.setattr(sweep, "anonymise_recommendations", lambda ids: 0)
        _, _, _, after, _, _ = deleted()
        ok, why = covers("recommendation.Recommendation", after["recommendation.Recommendation"])
        assert not ok, why
