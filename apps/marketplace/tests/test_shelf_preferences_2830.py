"""DRF-2830 — любимый мастер из подтверждённой памяти для личной полки.

Решение владельца 06.10 (O-1b): названного человеком мастера можно поднять в
полке его СОБСТВЕННЫХ салонов — и только там. Здесь проверяется сборщик
элементов ``preferences``; отправки ещё нет (каталог пока принимает мастера
только по id), и узел ``n3`` это держит.

Память и согласия — настоящие строки, не подмены: предмет узлов — что из
лежащего в памяти ПОПАДАЕТ в элементы, а что нет.

* e1 — элемент: имя как прозвучало, основы, soft, confirmed_memory;
* o1 — происхождение: глобальный бот → литерал ``global_bot``; салон → его id;
  неизвестное (NULL) не едет;
* g1 — гейты: заявка на удаление, нет связки с Ayla, SHADOW-оболочка, закрытая
  зелёная память;
* u1 — неподтверждённый вывод не едет; чужие ключи памяти не едут;
* u2 — ПОДТВЕРЖДЁННЫЙ вывод тоже не едет (DRF-2864): едет только сказанное;
* s1 — основы имени: короткие и длинные имена, два слова, не-имя;
* l1 — не больше трёх, без повторов;
* f1 — сбой чтения памяти — пусто, не исключение;
* n1 — межсалонный запрос полки (MARKETPLACE) ``preferences`` не несёт;
* n2 — модуль запроса полки память не читает и сборщик не зовёт;
* n3 — у сборщика нет ни одного производственного вызывающего.
"""

from __future__ import annotations

import ast
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest
from django.utils import timezone

from apps.consent.models import ConsentRecord
from apps.identity.models import BotUser, MemoryEntry, UserPersonalContext
from apps.identity.services.deletion_gate import mark_deletion_requested
from apps.identity.services.global_tenant import find_global_bot_tenant
from apps.identity.services.memory_key_policy import read_current_view
from apps.marketplace import resolver_request, shelf_preferences
from apps.marketplace.resolver_request import build_shelf_request
from apps.marketplace.shelf_preferences import name_stems, preferences_from_memory
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

_ROOT = Path(__file__).resolve().parents[3]
_MEMORY_READERS = {
    "read_green_entries",
    "read_current_view",
    "read_personal_context",
    "preferences_from_memory",
}


@pytest.fixture
def salon(db) -> Tenant:
    return Tenant.objects.create(slug="shelf-2830", name="Shelf 2830")


@pytest.fixture
def global_bot(db) -> Tenant:
    sentinel = find_global_bot_tenant()
    assert sentinel is not None, "сентинел global_bot ставит сид-миграция"
    return sentinel


@pytest.fixture
def person(salon) -> BotUser:
    """Человек со связкой с Ayla, открытой зелёной памятью и пустой памятью."""
    ayla_id = uuid.uuid4()
    bot_user = BotUser.all_tenants.create(
        tenant=salon,
        channel="max",
        channel_user_id="92830",
        display_name="Клиент",
        ayla_user_id=ayla_id,
        # Салонная оболочка пользуется контекстом человека, только когда связана (§2.4).
        customer_status=BotUser.CustomerStatus.LINKED,
    )
    ConsentRecord.all_tenants.create(
        tenant=salon,
        bot_user=bot_user,
        consent_type=ConsentRecord.ConsentType.PERSONAL_DATA.value,
        granted=True,
        source="test:2830",
    )
    UserPersonalContext.objects.create(user_id=ayla_id)
    return bot_user


def _remember(
    person: BotUser,
    name: str,
    *,
    origin: uuid.UUID | None,
    key: str = "favorite_masters",
    source: str = MemoryEntry.SOURCE_EXPLICIT,
    confirmed: bool = False,
) -> MemoryEntry:
    said = source == MemoryEntry.SOURCE_EXPLICIT
    inferred_provenance = MemoryEntry.PROVENANCE_USER_CONFIRMED_INFERENCE if confirmed else None
    ayla_user_id = person.ayla_user_id
    assert ayla_user_id is not None
    return MemoryEntry.objects.create(
        user_id=ayla_user_id,
        personal_context=UserPersonalContext.objects.get(user_id=ayla_user_id),
        sensitivity_zone=MemoryEntry.SENSITIVITY_GREEN,
        source=source,
        # Предложение Ayla, которое человек не подтвердил, метки происхождения не несёт.
        provenance=MemoryEntry.PROVENANCE_USER_STATED if said else inferred_provenance,
        kind="preference",
        consent_at=timezone.now(),
        # У вывода обязано стоять время вывода (ограничение таблицы).
        last_inferred_at=None if said else timezone.now(),
        content={"key": key, "value": name},
        source_tenant_id=origin,
    )


class TestTheElement:
    def test_e1_the_name_as_said_with_its_stems(self, person, global_bot) -> None:
        _remember(person, "Анне", origin=global_bot.id)

        assert preferences_from_memory(person) == (
            {
                "kind": "master",
                "name": "Анне",
                "name_stems": ["анн"],
                "strength": "soft",
                "origin": "confirmed_memory",
                "source_tenant_id": "global_bot",
            },
        )

    def test_e1_there_is_no_id_in_the_element(self, person, global_bot) -> None:
        """Имя, а не внутренний id — решение владельца 28.09 п.12."""
        _remember(person, "Анне", origin=global_bot.id)

        (element,) = preferences_from_memory(person)

        # Все поля элемента названы поимённо: ``ref`` и id среди них нет.
        assert sorted(element) == [
            "kind",
            "name",
            "name_stems",
            "origin",
            "source_tenant_id",
            "strength",
        ]
        assert element["source_tenant_id"] == "global_bot"


class TestTheOrigin:
    def test_o1_said_to_the_global_bot_rides_as_the_literal(self, person, global_bot) -> None:
        _remember(person, "Анне", origin=global_bot.id)

        (element,) = preferences_from_memory(person)

        assert element["source_tenant_id"] == "global_bot"

    def test_o1_said_in_a_salon_rides_with_that_salon(self, person, salon) -> None:
        _remember(person, "Анне", origin=salon.id)

        (element,) = preferences_from_memory(person)

        assert element["source_tenant_id"] == str(salon.id)

    def test_o1_unknown_origin_does_not_ride(self, person, global_bot) -> None:
        _remember(person, "Ольге", origin=None)
        _remember(person, "Анне", origin=global_bot.id)

        # Положительная пара: память читается, факт с известным происхождением едет.
        assert [e["name"] for e in preferences_from_memory(person)] == ["Анне"]

    def test_o1_without_the_sentinel_a_global_fact_is_never_widened(self, person, global_bot):
        """Сентинела нет — глобальный факт уезжает как салонный, не как «везде»."""
        _remember(person, "Анне", origin=global_bot.id)

        with patch.object(shelf_preferences, "_global_bot_id", return_value=None):
            (element,) = preferences_from_memory(person)

        assert element["source_tenant_id"] == str(global_bot.id)


class TestTheGates:
    def test_g1_open_gates_let_the_fact_through(self, person, global_bot) -> None:
        _remember(person, "Анне", origin=global_bot.id)

        assert len(preferences_from_memory(person)) == 1

    def test_g1_a_deletion_request_stops_it(self, person, global_bot) -> None:
        _remember(person, "Анне", origin=global_bot.id)
        assert len(preferences_from_memory(person)) == 1

        mark_deletion_requested(person.ayla_user_id, request_id=str(uuid.uuid4()))

        assert preferences_from_memory(person) == ()

    def test_g1_a_deletion_request_means_memory_is_not_even_read(self, person, global_bot):
        """Заявка на удаление: память не читается вовсе, а не «читается и пуста»."""
        _remember(person, "Анне", origin=global_bot.id)
        target = "apps.identity.services.memory_key_policy.read_current_view"
        with patch(target, wraps=read_current_view) as spy:
            assert len(preferences_from_memory(person)) == 1
            assert spy.call_count == 1

            mark_deletion_requested(person.ayla_user_id, request_id=str(uuid.uuid4()))

            assert preferences_from_memory(person) == ()
            assert spy.call_count == 1

    def test_g1_no_link_to_ayla_means_nothing(self, person, global_bot) -> None:
        _remember(person, "Анне", origin=global_bot.id)
        assert len(preferences_from_memory(person)) == 1

        person.ayla_user_id = None

        assert preferences_from_memory(person) == ()

    def test_g1_a_shadow_shell_gets_nothing(self, person, global_bot) -> None:
        """SHADOW не даёт права на память человека — решение владельца 11.09 §2.4."""
        _remember(person, "Анне", origin=global_bot.id)
        assert len(preferences_from_memory(person)) == 1

        person.customer_status = BotUser.CustomerStatus.SHADOW
        person.save(update_fields=["customer_status"])

        assert preferences_from_memory(person) == ()

    def test_g1_closed_green_memory_means_nothing(self, person, global_bot) -> None:
        _remember(person, "Анне", origin=global_bot.id)
        assert len(preferences_from_memory(person)) == 1

        ConsentRecord.all_tenants.filter(bot_user=person).update(withdrawn_at=timezone.now())

        assert preferences_from_memory(person) == ()


class TestOnlyWhatThePersonSaid:
    def test_u1_an_unconfirmed_inference_does_not_ride(self, person, global_bot) -> None:
        _remember(person, "Ольге", origin=global_bot.id, source=MemoryEntry.SOURCE_INFERRED)
        _remember(person, "Анне", origin=global_bot.id)

        assert [e["name"] for e in preferences_from_memory(person)] == ["Анне"]

    def test_u2_a_confirmed_inference_does_not_ride_either(self, person, global_bot) -> None:
        """«Да, запомни» в ответ на догадку Ayla — не «назвал сам» (DRF-2864)."""
        from apps.consent import preference_inference

        assert preference_inference.grant(
            person, document_version=preference_inference.PREFERENCE_INFERENCE_DOCUMENT_VERSION
        )
        _remember(
            person,
            "Ольге",
            origin=global_bot.id,
            source=MemoryEntry.SOURCE_INFERRED,
            confirmed=True,
        )
        _remember(person, "Анне", origin=global_bot.id)

        # Положительная пара: читатель памяти подтверждённый вывод ОТДАЁТ —
        # отсекает его именно сборщик, а не пустой вид.
        view = read_current_view(person.ayla_user_id)
        assert sorted(
            (fact.content["value"], fact.source)
            for fact in view.green_facts
            if fact.content.get("key") == "favorite_masters"
        ) == [("Анне", "explicit"), ("Ольге", "inferred")]
        assert [e["name"] for e in preferences_from_memory(person)] == ["Анне"]

    def test_u1_other_memory_keys_do_not_ride(self, person, global_bot) -> None:
        _remember(person, "Пенза", origin=global_bot.id, key="city")
        _remember(person, "Анне", origin=global_bot.id)

        assert [e["name"] for e in preferences_from_memory(person)] == ["Анне"]


class TestTheStems:
    @pytest.mark.parametrize(
        ("name", "stems"),
        [
            ("Анне", ("анн",)),
            ("Анной", ("анн",)),
            ("Марии", ("мари",)),
            ("Денис", ("денис",)),
            ("Архипкину", ("архипки",)),
            ("Анне Архипкиной", ("анн", "архипкин")),
        ],
    )
    def test_s1_the_stem_survives_the_case_ending(self, db, name: str, stems: tuple) -> None:
        assert name_stems(name) == stems

    @pytest.mark.parametrize(
        ("name", "stems"),
        [("Ян", ()), ("Ия", ()), ("Ян Ковалёв", ("ковал",))],
    )
    def test_s1_a_stem_shorter_than_three_letters_does_not_ride(
        self, db, name: str, stems: tuple
    ) -> None:
        """Каталог основу короче трёх знаков не принимает — и отклонил бы весь запрос."""
        # Положительная пара: тот же разбор на обычном имени основу даёт.
        assert name_stems("Анне") == ("анн",)
        assert name_stems(name) == stems

    @pytest.mark.parametrize("not_a_name", ["", "   ", "к", "—"])
    def test_s1_what_is_not_a_name_has_no_stems(self, db, not_a_name: str) -> None:
        # Положительная пара: тот же разбор на имени основы даёт.
        assert name_stems("Анне") == ("анн",)
        assert name_stems(not_a_name) == ()

    def test_s1_a_fact_without_a_name_does_not_ride(self, person, global_bot) -> None:
        _remember(person, "—", origin=global_bot.id)
        _remember(person, "Анне", origin=global_bot.id)

        assert [e["name"] for e in preferences_from_memory(person)] == ["Анне"]


class TestTheLimits:
    def test_l1_no_more_than_three(self, person, global_bot) -> None:
        for name in ("Анне", "Ольге", "Марии", "Денису"):
            _remember(person, name, origin=global_bot.id)

        assert [e["name"] for e in preferences_from_memory(person)] == ["Анне", "Ольге", "Марии"]

    def test_l1_the_same_master_said_twice_rides_once(self, person, global_bot) -> None:
        _remember(person, "Анне", origin=global_bot.id)
        _remember(person, "Анну", origin=global_bot.id)

        assert [e["name"] for e in preferences_from_memory(person)] == ["Анне"]

    def test_l1_the_same_name_from_two_origins_is_two_facts(
        self, person, salon, global_bot
    ) -> None:
        _remember(person, "Анне", origin=global_bot.id)
        _remember(person, "Анне", origin=salon.id)

        assert [e["source_tenant_id"] for e in preferences_from_memory(person)] == [
            "global_bot",
            str(salon.id),
        ]


class TestAFailureIsNotAShelfWithoutAShelf:
    def test_f1_a_broken_memory_read_gives_nothing(self, person, global_bot) -> None:
        _remember(person, "Анне", origin=global_bot.id)
        assert len(preferences_from_memory(person)) == 1

        with patch(
            "apps.identity.services.memory_key_policy.read_current_view",
            side_effect=RuntimeError("memory down"),
        ):
            assert preferences_from_memory(person) == ()


def _calls(path: Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Call):
            func = node.func
            name = (
                func.id
                if isinstance(func, ast.Name)
                else func.attr
                if isinstance(func, ast.Attribute)
                else None
            )
            if name:
                names.add(name)
    return names


class TestTheCrossSalonShelfNeverGetsIt:
    def test_n1_the_marketplace_request_carries_no_preferences(self) -> None:
        body = build_shelf_request(goal_key="relax")

        # Положительная пара: это и есть межсалонный запрос полки.
        assert body["scope"]["mode"] == "MARKETPLACE"
        assert "preferences" not in body

    def test_n2_the_request_module_neither_reads_memory_nor_calls_the_collector(self) -> None:
        path = Path(resolver_request.__file__)
        calls = _calls(path)

        # Положительная пара: обход видит вызовы модуля.
        assert "uuid4" in calls
        assert calls & _MEMORY_READERS == set()
        # Импорты модуля названы поимённо: сборщика и слоя памяти среди них нет.
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported = sorted(
            alias.name if isinstance(node, ast.Import) else f"{node.module}.{alias.name}"
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        )
        assert imported == ["__future__.annotations", "typing.Any", "typing.Final", "uuid"]

    def test_n2_the_census_would_see_a_reader_planted_there(self, tmp_path) -> None:
        """Сторож сам: подсаженное чтение памяти в модуле запроса он видит."""
        planted = tmp_path / "resolver_request.py"
        planted.write_text(
            "def build_shelf_request(bot_user):\n"
            "    return {'preferences': preferences_from_memory(bot_user)}\n",
            encoding="utf-8",
        )

        assert _calls(planted) & _MEMORY_READERS == {"preferences_from_memory"}

    def test_n3_nothing_in_production_sends_it_yet(self) -> None:
        """Отправка подключается, когда каталог примет имя, — и поправит этот узел."""
        callers: list[str] = []
        scanned = 0
        for path in sorted((_ROOT / "apps").rglob("*.py")):
            rel = path.relative_to(_ROOT).as_posix()
            if "/tests/" in rel or rel == "apps/marketplace/shelf_preferences.py":
                continue
            scanned += 1
            if "preferences_from_memory" in path.read_text(encoding="utf-8"):
                callers.append(rel)

        assert scanned > 500, scanned
        assert callers == []  # empty-assert-ok: обход не слеп — число файлов утверждено выше
