"""«Забудь всё» обязано снимать красную зону, и повышение зоны — не лазейка (DRF-2132).

Лист Память-1 просит хранить аллергии и непереносимости в красной зоне.
Замер показал, что до самих аллергий стоят две дыры в основании, и обе
живут в коде, который красную зону как раз и охраняет.

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

Сам лист (аллергии в красной зоне) упирается в два решения владельца,
названные в докладе: красные записи сегодня отбрасываются на 100 %
(fail-closed до #597), а DRF-1290 запрещает извлечение фраз про аллергии
до всякого хранения, и DRF-1371 этот запрет подтвердил. Узлы ниже не
трогают ни то, ни другое — они про основание, на котором аллергии потом
лягут.
"""

from __future__ import annotations

import uuid

import pytest
from django.utils import timezone

from apps.identity.models import (
    MemoryEntry,
    RedZoneAccessLog,
    UserPersonalContext,
)
from apps.identity.services.exceptions import MinorProtectionLookupFailed
from apps.identity.services.forget_all_sweep import sweep_forget_all
from apps.identity.services.memory_writer import promote_zone

pytestmark = pytest.mark.django_db


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

    def test_green_rows_do_not_pollute_the_red_zone_log(self) -> None:
        """Положительная пара: журнал красной зоны — про красные строки."""
        upc = _upc()
        green = _entry(upc, MemoryEntry.SENSITIVITY_GREEN)

        sweep_forget_all(upc.user_id)

        logged = set(
            RedZoneAccessLog.objects.filter(user_id=upc.user_id).values_list(
                "memory_entry_id", flat=True
            )
        )
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
            )

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
            )

        assert RedZoneAccessLog.objects.filter(
            user_id=upc.user_id,
            access_type=RedZoneAccessLog.ACCESS_WRITE_REJECTED_DOB,
        ).exists()

    def test_positive_pair_demotion_needs_no_check(self) -> None:
        """Движение к меньшей чувствительности — улучшение, а не риск."""
        upc = _upc(forgotten=False)
        entry = _entry(upc, MemoryEntry.SENSITIVITY_RED)

        promoted = promote_zone(entry=entry, new_zone=MemoryEntry.SENSITIVITY_GREEN)

        assert promoted.sensitivity_zone == MemoryEntry.SENSITIVITY_GREEN

    def test_positive_pair_same_zone_is_not_a_promotion(self) -> None:
        upc = _upc(forgotten=False)
        entry = _entry(upc, MemoryEntry.SENSITIVITY_GREEN)

        promoted = promote_zone(entry=entry, new_zone=MemoryEntry.SENSITIVITY_GREEN)

        assert promoted.sensitivity_zone == MemoryEntry.SENSITIVITY_GREEN


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
