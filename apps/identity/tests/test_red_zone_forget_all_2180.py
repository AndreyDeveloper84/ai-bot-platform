"""«Забудь всё» снимает все зоны, и повышение зоны — не лазейка (DRF-2180).

Лист Память-5 (DRF-2180) — долг из матрицы удаления #1896. Найден при
замере DRF-2132 («аллергии в красной зоне»): до самих аллергий стоят две
дыры в основании, и обе живут в коде, который красную зону как раз и
охраняет. DRF-2132 остаётся открытым — он упирается в два решения
владельца (см. хвост докстринга), а это основание нужно при любом из них.

# Дыра 1. Свип «забудь всё» красное не снимает — а матрица требует

``forget_all_sweep.sweep_forget_all`` фильтрует ``sensitivity_zone=GREEN``
и в докстринге называет это осознанным решением: «yellow/red erasure
carries extra rules … and is not in scope here».

Матрица удаления (DRF-2134, ``test_forget_all_matrix.py``) говорит ровно
обратное и НОВЕЕ:

    "identity.MemoryEntry:red": Outcome(
        DELETE,
        "никто — развёртка объявлена green-only; red_zone_reader умеет только по одной",
        "специальная категория (152-ФЗ ст. 10) не должна переживать «забудь всё»",
    )

То есть требование уже записано вместе с причиной, и записано как долг:
«никто» — это признание, что сегодня не делает никто. По правилу CLAUDE.md
(«если источники расходятся — следовать более свежему») выигрывает матрица,
а докстринг свипа устарел и должен перестать утверждать обратное.

Жёлтая зона в матрице объявлена так же (``DELETE``, «никто»), и снимается
она тем же фильтром: убрать ``sensitivity_zone=GREEN`` — значит закрыть обе
строки матрицы разом, а оставить жёлтую — значит оставить матрицу наполовину
неправдой. Поэтому узлы ниже держат все три зоны.

# Дыра 2. Защита несовершеннолетних не держится на повышении зоны

``write_entry`` на жёлтую/красную запись зовёт ``_check_minor_protection``
(сегодня — заглушка, которая ВСЕГДА бросает, потому что ручки DOB у Ayla
нет, #597) и ``minor_lock``. А ``promote_zone`` — путь green → red — не
зовёт НИ ТОГО, НИ ДРУГОГО: достаточно записать зелёную строку и повысить
её. Сегодня ``promote_zone`` в бою никто не зовёт, так что дыра латентная.
Но DRF-2132 — ровно тот лист, который начнёт ею пользоваться: «запиши
аллергию» естественно ложится в «запиши и повысь».

# Чего эти узлы НЕ решают

Сам DRF-2132 (аллергии в красной зоне) упирается в два решения
владельца: красные записи сегодня отбрасываются на 100 %
(fail-closed до #597), а DRF-1290 запрещает извлечение фраз про аллергии
до всякого хранения, и DRF-1371 этот запрет подтвердил. Узлы ниже не
трогают ни то, ни другое — они про основание, на котором аллергии потом
лягут.
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from django.db import connection, transaction
from django.utils import timezone

from apps.identity.models import (
    MemoryEntry,
    RedZoneAccessLog,
    UserPersonalContext,
)
from apps.identity.services.exceptions import MinorProtectionLookupFailed
from apps.identity.services.forget_all_sweep import sweep_forget_all
from apps.identity.services.memory_deleter import soft_delete_all_zones_for_forget_all
from apps.identity.services.memory_writer import promote_zone

pytestmark = pytest.mark.django_db

#: RLS — механизм Postgres; под SQLite проверять нечего.
_PG_ONLY = pytest.mark.skipif(
    connection.vendor != "postgresql", reason="RLS/GUC — механизм Postgres."
)


def _upc(*, forgotten: bool = True, minor_lock: bool = False) -> UserPersonalContext:
    return UserPersonalContext.objects.create(
        user_id=uuid.uuid4(),
        forget_all_requested_at=timezone.now() if forgotten else None,
        minor_lock=minor_lock,
    )


def _entry(upc: UserPersonalContext, zone: str, key: str = "k") -> MemoryEntry:
    """Живая запись нужной зоны.

    Жёлтая и красная требуют ``consent_at`` (CHECK 2) — ставим его прямо,
    а не через ``write_entry``: писатель сегодня их отбрасывает на 100 %
    (fail-closed до #597), и стенд про свип не должен от этого зависеть.
    """
    return MemoryEntry.objects.create(
        user_id=upc.user_id,
        personal_context=upc,
        sensitivity_zone=zone,
        source=MemoryEntry.SOURCE_EXPLICIT,
        provenance=MemoryEntry.PROVENANCE_USER_STATED,
        consent_at=None if zone == MemoryEntry.SENSITIVITY_GREEN else timezone.now(),
        content={"key": key, "value": "nuts"},
    )


def _live(user_id: uuid.UUID, zone: str) -> int:
    return MemoryEntry.objects.filter(
        user_id=user_id, sensitivity_zone=zone, soft_deleted_at__isnull=True
    ).count()


class TestForgetAllReachesTheRedZone:
    def test_red_entries_do_not_survive_forget_all(self) -> None:
        """Сердце листа: специальная категория не переживает «забудь всё»."""
        upc = _upc()
        red = _entry(upc, MemoryEntry.SENSITIVITY_RED)

        sweep_forget_all(upc.user_id)

        red.refresh_from_db()
        assert red.soft_deleted_at is not None
        assert red.deletion_reason == MemoryEntry.DELETION_REASON_FORGET_ALL
        assert _live(upc.user_id, MemoryEntry.SENSITIVITY_RED) == 0

    def test_yellow_entries_do_not_survive_either(self) -> None:
        """Матрица объявляет жёлтую тем же ``DELETE`` и тем же «никто»."""
        upc = _upc()
        yellow = _entry(upc, MemoryEntry.SENSITIVITY_YELLOW)

        sweep_forget_all(upc.user_id)

        yellow.refresh_from_db()
        assert yellow.soft_deleted_at is not None
        assert _live(upc.user_id, MemoryEntry.SENSITIVITY_YELLOW) == 0

    def test_positive_pair_green_is_still_swept(self) -> None:
        """Иначе правка выродилась бы в «красное вместо зелёного»."""
        upc = _upc()
        green = _entry(upc, MemoryEntry.SENSITIVITY_GREEN)

        sweep_forget_all(upc.user_id)

        green.refresh_from_db()
        assert green.soft_deleted_at is not None
        assert green.deletion_reason == MemoryEntry.DELETION_REASON_FORGET_ALL

    def test_the_count_names_every_zone_it_buried(self) -> None:
        """Счёт — честный: «сколько сняли», а не «сколько зелёных сняли»."""
        upc = _upc()
        for zone in (
            MemoryEntry.SENSITIVITY_GREEN,
            MemoryEntry.SENSITIVITY_YELLOW,
            MemoryEntry.SENSITIVITY_RED,
        ):
            _entry(upc, zone)

        result = sweep_forget_all(upc.user_id)

        assert result.entries_deleted == 3

    def test_every_red_row_leaves_an_access_log_line(self) -> None:
        """Правило красной зоны: доступ к строке — строка журнала.

        Свип трогает красные строки в обход ``red_zone_reader`` (он умеет
        по одной), поэтому журнал обязан вести сам свип — иначе массовое
        снятие специальной категории проходит без следа.
        """
        upc = _upc()
        first = _entry(upc, MemoryEntry.SENSITIVITY_RED, key="a")
        second = _entry(upc, MemoryEntry.SENSITIVITY_RED, key="b")

        sweep_forget_all(upc.user_id)

        logged = set(
            RedZoneAccessLog.objects.filter(
                user_id=upc.user_id, access_type=RedZoneAccessLog.ACCESS_DELETE
            ).values_list("memory_entry_id", flat=True)
        )
        assert {first.id, second.id} <= logged

    def test_exactly_one_log_row_per_red_entry(self) -> None:
        """«По одной строке на запись» — счёт, а не вхождение.

        Проверка подмножеством (`{a, b} <= logged`) дубликатов не видит:
        мутация «писать каждую строку дважды» проходила её насквозь. Для
        аудитора двойная строка — это два обращения к специальной
        категории там, где было одно.
        """
        upc = _upc()
        red = _entry(upc, MemoryEntry.SENSITIVITY_RED, key="a")
        other = _entry(upc, MemoryEntry.SENSITIVITY_RED, key="b")

        sweep_forget_all(upc.user_id)

        for entry in (red, other):
            assert (
                RedZoneAccessLog.objects.filter(
                    memory_entry_id=entry.id,
                    access_type=RedZoneAccessLog.ACCESS_DELETE,
                ).count()
                == 1
            ), entry.id

    def test_one_request_id_for_the_whole_sweep(self) -> None:
        """Одна просьба «забудь всё» — одно обращение, разбитое на строки.

        Иначе по журналу нельзя собрать «что сняли за этот раз»: строки
        со случайными идентификаторами не соединяются ни во что.
        """
        upc = _upc()
        for key in ("a", "b", "c"):
            _entry(upc, MemoryEntry.SENSITIVITY_RED, key=key)

        sweep_forget_all(upc.user_id)

        request_ids = set(
            RedZoneAccessLog.objects.filter(
                user_id=upc.user_id, access_type=RedZoneAccessLog.ACCESS_DELETE
            ).values_list("request_id", flat=True)
        )
        assert len(request_ids) == 1, request_ids

    def test_the_log_row_names_who_and_why(self) -> None:
        """Строка журнала без «кто» и «зачем» доказывает только факт запроса."""
        upc = _upc()
        _entry(upc, MemoryEntry.SENSITIVITY_RED)

        sweep_forget_all(upc.user_id)

        row = RedZoneAccessLog.objects.filter(
            user_id=upc.user_id, access_type=RedZoneAccessLog.ACCESS_DELETE
        ).first()
        assert row is not None
        assert row.accessor_role == RedZoneAccessLog.ACCESSOR_SYSTEM_JOB
        assert row.accessor_principal.startswith("forget_all_sweep:")
        assert "забыть всё" in row.purpose

    def test_a_second_sweep_adds_no_log_rows(self) -> None:
        """Идемпотентность ЖУРНАЛА, а не только результата.

        Второй прогон живых красных не находит — значит и строк журнала
        завести не должен. Иначе повторные прогоны (а свип идёт по
        расписанию) размножали бы доказательство одного обращения.
        """
        upc = _upc()
        _entry(upc, MemoryEntry.SENSITIVITY_RED)

        sweep_forget_all(upc.user_id)
        after_first = RedZoneAccessLog.objects.filter(user_id=upc.user_id).count()
        sweep_forget_all(upc.user_id)

        assert RedZoneAccessLog.objects.filter(user_id=upc.user_id).count() == after_first
        assert after_first == 1

    def test_a_superseded_red_row_is_swept_too(self) -> None:
        """Живость — про надгробие, а не про статус.

        Строка со `status='superseded'` и пустым надгробием всё ещё лежит
        в базе и всё ещё специальная категория. Получено умолчанием
        (фильтр по надгробию), поэтому пришпилено: следующая правка
        фильтра иначе отняла бы это молча.
        """
        upc = _upc()
        red = _entry(upc, MemoryEntry.SENSITIVITY_RED)
        MemoryEntry.objects.filter(id=red.id).update(status=MemoryEntry.STATUS_SUPERSEDED)

        sweep_forget_all(upc.user_id)

        red.refresh_from_db()
        assert red.soft_deleted_at is not None

    def test_green_rows_do_not_pollute_the_red_zone_log(self) -> None:
        """Положительная пара: журнал красной зоны — про красные строки."""
        upc = _upc()
        green = _entry(upc, MemoryEntry.SENSITIVITY_GREEN)
        red = _entry(upc, MemoryEntry.SENSITIVITY_RED, key="r")

        sweep_forget_all(upc.user_id)

        logged = set(
            RedZoneAccessLog.objects.filter(user_id=upc.user_id).values_list(
                "memory_entry_id", flat=True
            )
        )
        # Наличие — первым: журнал вообще пишется в этом же прогоне. Иначе
        # «зелёной строки там нет» было бы правдой и у пустого журнала.
        assert red.id in logged
        assert green.id not in logged

    def test_minor_lock_still_survives(self) -> None:
        """Защита, а не факт о человеке: снятие её было бы понижением безопасности."""
        upc = _upc(minor_lock=True)
        _entry(upc, MemoryEntry.SENSITIVITY_RED)

        sweep_forget_all(upc.user_id)

        upc.refresh_from_db()
        assert upc.minor_lock is True

    def test_a_user_who_never_asked_is_untouched(self) -> None:
        """Положительная пара к «снимает всё»: снимает только у попросивших."""
        upc = _upc(forgotten=False)
        red = _entry(upc, MemoryEntry.SENSITIVITY_RED)

        sweep_forget_all(upc.user_id)

        red.refresh_from_db()
        assert red.soft_deleted_at is None


class TestZonePromotionIsNotABackDoor:
    """Защита несовершеннолетних обязана держаться на ОБОИХ путях в красное."""

    def test_promotion_to_red_is_fail_closed_like_a_direct_write(self) -> None:
        upc = _upc(forgotten=False)
        entry = _entry(upc, MemoryEntry.SENSITIVITY_GREEN)

        with pytest.raises(MinorProtectionLookupFailed):
            promote_zone(
                entry=entry,
                new_zone=MemoryEntry.SENSITIVITY_RED,
                consent_token="t",
                request_id=uuid.uuid4(),
                purpose="тест: повышение зоны",
            )

        entry.refresh_from_db()
        assert entry.sensitivity_zone == MemoryEntry.SENSITIVITY_GREEN

    def test_promotion_to_yellow_is_fail_closed_too(self) -> None:
        upc = _upc(forgotten=False)
        entry = _entry(upc, MemoryEntry.SENSITIVITY_GREEN)

        with pytest.raises(MinorProtectionLookupFailed):
            promote_zone(
                entry=entry,
                new_zone=MemoryEntry.SENSITIVITY_YELLOW,
                consent_token="t",
                request_id=uuid.uuid4(),
                purpose="тест: повышение зоны",
            )

        entry.refresh_from_db()
        assert entry.sensitivity_zone == MemoryEntry.SENSITIVITY_GREEN

    def test_rejected_promotion_leaves_the_forensic_row(self) -> None:
        """Отказ писать специальную категорию — доказательство по 152-ФЗ гл. 3.

        У прямой записи такая строка есть (``write_rejected_dob_lookup``);
        у повышения её не было, потому что и проверки не было.
        """
        upc = _upc(forgotten=False)
        entry = _entry(upc, MemoryEntry.SENSITIVITY_GREEN)

        with pytest.raises(MinorProtectionLookupFailed):
            promote_zone(
                entry=entry,
                new_zone=MemoryEntry.SENSITIVITY_RED,
                consent_token="t",
                request_id=uuid.uuid4(),
                purpose="тест: повышение зоны",
            )

        assert RedZoneAccessLog.objects.filter(
            user_id=upc.user_id,
            access_type=RedZoneAccessLog.ACCESS_WRITE_REJECTED_DOB,
        ).exists()

    def test_positive_pair_demotion_needs_no_check(self) -> None:
        """Движение к меньшей чувствительности — улучшение, а не риск."""
        upc = _upc(forgotten=False)
        entry = _entry(upc, MemoryEntry.SENSITIVITY_RED)

        promoted = promote_zone(
            entry=entry,
            new_zone=MemoryEntry.SENSITIVITY_GREEN,
            request_id=uuid.uuid4(),
            purpose="тест: понижение зоны",
        )

        assert promoted.sensitivity_zone == MemoryEntry.SENSITIVITY_GREEN

    def test_positive_pair_same_zone_is_not_a_promotion(self) -> None:
        upc = _upc(forgotten=False)
        entry = _entry(upc, MemoryEntry.SENSITIVITY_GREEN)

        promoted = promote_zone(
            entry=entry,
            new_zone=MemoryEntry.SENSITIVITY_GREEN,
            request_id=uuid.uuid4(),
            purpose="тест: понижение зоны",
        )

        assert promoted.sensitivity_zone == MemoryEntry.SENSITIVITY_GREEN


@_PG_ONLY
class TestTheDeleterSeesRedUnderTheAppRole:
    """Регрессия под ``ayla_app`` — иначе сторож ничего не стережёт.

    Политика ``memory_entry_non_red_visible`` (миграция 0008) прячет
    красные строки от SELECT без ``ayla.red_zone_access_context``, и
    **WHERE у UPDATE подчиняется той же политике**. Вся сюита идёт под
    суперпользователем, который RLS обходит, поэтому запрос без GUC был
    бы зелёным во всех тестах и отказал бы ровно в день перехода
    приложения на ``ayla_app`` (ADR-0011 §16, фаза 2, шаг 5) — молча:
    красные строки не попали бы в выборку, счёт занизился бы, журнал
    остался бы пуст, а свип вернул бы успех.

    # Почему узел на делетере, а не на всём свипе

    Миграция 0008 выдала ``ayla_app`` гранты ровно на три таблицы —
    ``identity_memoryentry``, ``identity_userpersonalcontext``,
    ``identity_redzoneaccesslog``, — и это ровно то, что трогает
    делетер. Свип идёт дальше: аудит, ``BotUser``, обезличивание
    диалогов. Под этой ролью он падает на ``permission denied`` для
    таблиц, к красной зоне отношения не имеющих, — то есть узел красился
    бы по причине, к его предмету не относящейся, и сторожил бы не то.
    Недостающие гранты — предмет миграции §16, здесь они названы, а не
    подпёрты.

    ``write_audit`` подменён по той же причине: таблица аудита в списке
    грантов отсутствует.
    """

    def test_the_red_row_is_tombstoned_and_logged_under_ayla_app(self) -> None:
        upc = _upc()
        red = _entry(upc, MemoryEntry.SENSITIVITY_RED)
        green = _entry(upc, MemoryEntry.SENSITIVITY_GREEN)
        request_id = uuid.uuid4()

        with (
            patch("apps.identity.services.memory_deleter.write_audit"),
            transaction.atomic(),
        ):
            with connection.cursor() as cur:
                cur.execute("SET LOCAL ROLE ayla_app")
            try:
                deleted, red_count = soft_delete_all_zones_for_forget_all(
                    upc.user_id, request_id=request_id
                )
            finally:
                # `SET LOCAL ROLE` снимается в конце ТРАНЗАКЦИИ, а не
                # savepoint'а, а pytest-django держит транзакцию теста
                # открытой — без явного сброса проверки ниже пошли бы под
                # `ayla_app` без GUC и не нашли бы красную строку вовсе.
                # Та же тонкость, что у `_reset_red_zone_guc`.
                with connection.cursor() as cur:
                    cur.execute("RESET ROLE")

        assert red_count == 1, (
            "красная строка не попала в выборку под ролью приложения — "
            "запрос идёт без GUC, и RLS его не пускает"
        )
        assert deleted == 2
        red.refresh_from_db()
        green.refresh_from_db()
        assert red.soft_deleted_at is not None
        assert green.soft_deleted_at is not None
        assert RedZoneAccessLog.objects.filter(
            memory_entry_id=red.id,
            access_type=RedZoneAccessLog.ACCESS_DELETE,
            request_id=request_id,
        ).exists()

    def test_positive_pair_the_role_really_is_restricted(self) -> None:
        """Иначе узел выше прошёл бы и на роли, которая RLS обходит."""
        upc = _upc(forgotten=False)
        _entry(upc, MemoryEntry.SENSITIVITY_RED)

        with transaction.atomic():
            with connection.cursor() as cur:
                cur.execute("SET LOCAL ROLE ayla_app")
                try:
                    cur.execute(
                        "SELECT count(*) FROM identity_memoryentry "
                        "WHERE sensitivity_zone = 'red' AND user_id = %s",
                        [str(upc.user_id)],
                    )
                    visible = cur.fetchone()[0]
                finally:
                    cur.execute("RESET ROLE")

        assert visible == 0, (
            "под ayla_app красная строка видна без GUC — значит роль не "
            "ограничена, и узел выше ничего не доказывает"
        )


class TestPromoteZoneContract:
    def test_a_durable_forensic_row_forbids_an_outer_atomic(self) -> None:
        """Контракт, унаследованный от ``write_entry``, — назван и пришпилен.

        Судебная строка коммитится ``atomic(durable=True)``, чтобы её не
        унёс откат вызывающего. Плата: внутри чужого ``atomic()`` это
        ``RuntimeError``, а не ``MinorProtectionLookupFailed`` — то есть
        вызывающий поймает не то, что ловит. Пусть это будет решением, а
        не сюрпризом для следующего.
        """
        upc = _upc(forgotten=False)
        entry = _entry(upc, MemoryEntry.SENSITIVITY_GREEN)

        with pytest.raises(RuntimeError, match="durable"), transaction.atomic():
            promote_zone(
                entry=entry,
                new_zone=MemoryEntry.SENSITIVITY_RED,
                consent_token="t",
                request_id=uuid.uuid4(),
                purpose="тест: повышение внутри чужой транзакции",
            )

    def test_request_id_and_purpose_are_required(self) -> None:
        """Доказательство без предмета — не доказательство.

        У ``write_entry`` оба обязательны, чтобы судебную строку можно
        было соединить с запросом, который её породил. Умолчание выдало
        бы случайный id, не присоединяемый ни к чему, и один и тот же
        текст причины на все отказы.
        """
        import inspect

        params = inspect.signature(promote_zone).parameters
        assert params["request_id"].default is inspect.Parameter.empty
        assert params["purpose"].default is inspect.Parameter.empty


class TestRedNeverReachesThePrompt:
    def test_the_surfacing_reader_is_green_only_by_construction(self) -> None:
        """Перепись: путь в промпт фильтрует зелёное, а не «не-красное».

        Свойство сегодня верно — узел держит его фактом файла, чтобы
        аллергии, когда они появятся, не уехали в модель расширением
        фильтра до «зелёное и жёлтое».
        """
        from pathlib import Path

        src = Path("apps/identity/services/memory_reader.py").read_text(encoding="utf-8")
        assert "sensitivity_zone=MemoryEntry.SENSITIVITY_GREEN" in src
        assert "SENSITIVITY_RED" not in src
