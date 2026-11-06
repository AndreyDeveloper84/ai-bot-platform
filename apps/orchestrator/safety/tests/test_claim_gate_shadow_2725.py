"""DRF-2725 — теневой затвор утверждений: считает и пишет, ответа не меняет.

Контракт — DRF-2718. Решение владельца 02.10.2026: сначала теневой режим.

Узлы взвешивают две вещи поровну.

**Ответ не меняется.** Это главное свойство листа, и оно доказывается не
«функция вернула то же», а матрицей: один и тот же набор черновиков через хук
без флага, с выключенным и с включённым флагом, с лицензией и без, — исходы
совпадают целиком. Ожидания ключевых строк — литералом, чтобы матрица не
превратилась в сравнение функции с самой собой.

**Журнал не несёт слов.** Теневая запись делается на каждый ответ; попади в
неё текст — журнал стал бы копией переписки. Узлы ищут в записи и слова
черновика, и слова утверждений.

Чего здесь НЕТ — и это предел листа, а не пропуск: ни один узел не проверяет
«утверждение покрыто / не покрыто». Этого теневой затвор 3.1 не умеет (нужен
разбор прозы, следующий лист), и узел, который бы это изображал, проверял бы
заглушку.
"""

from __future__ import annotations

import ast
import logging
import pathlib
from datetime import UTC, datetime, timedelta

import pytest

from apps.orchestrator.decision_readiness.shadow import FlagSource
from apps.orchestrator.knowledge_licence import (
    KnowledgeLicence,
    LicensedClaim,
    LicensedSubject,
    SubjectState,
)
from apps.orchestrator.safety import claim_gate
from apps.orchestrator.safety.claim_gate import (
    LICENCE_ABSENT,
    LICENCE_EMPTY,
    LICENCE_PRESENT,
    SHADOW_SETTING,
    ShadowAssessment,
    assess,
    shadow_flag,
)
from apps.orchestrator.safety.gate import guard_outbound
from apps.orchestrator.safety.outbound import ACTION_PROMISE_TEXT, REPLACEMENT_TEXT

pytestmark = pytest.mark.django_db

ROOT = pathlib.Path(__file__).resolve().parents[4]

READ_AT = datetime(2026, 10, 2, 12, 0, 0, tzinfo=UTC)
#: Проверка «на выходе» — через пять секунд после чтения.
NOW = READ_AT + timedelta(seconds=5)

SHADOW_LOGGER = "apps.orchestrator.safety.claim_gate"
SHADOW_EVENT = "safety.claim_gate.shadow "


def _claim(claim_id: str, *, valid_until: datetime | None = None) -> LicensedClaim:
    return LicensedClaim(
        claim_id=claim_id, kind="capability", key="temporary_relaxation", valid_until=valid_until
    )


def _licence(*subjects: LicensedSubject) -> KnowledgeLicence:
    return KnowledgeLicence(read_at=READ_AT, subjects=tuple(subjects))


KNOWN = LicensedSubject(
    template_id="tpl-massage",
    state=SubjectState.KNOWN,
    claims=(_claim("claim-1"), _claim("claim-2", valid_until=READ_AT + timedelta(days=30))),
)
UNKNOWN = LicensedSubject(template_id="tpl-peeling", state=SubjectState.UNKNOWN)
UNAVAILABLE = LicensedSubject(template_id="tpl-laser", state=SubjectState.UNAVAILABLE)


@pytest.fixture
def shadow_on(settings):
    settings.CLAIM_GATE_SHADOW_ENABLED = "true"


@pytest.fixture
def shadow_records(caplog):
    """Записи теневого затвора за время узла."""

    caplog.set_level(logging.INFO, logger=SHADOW_LOGGER)

    def _records() -> list[str]:
        return [
            r.getMessage()
            for r in caplog.records
            if r.name == SHADOW_LOGGER and r.getMessage().startswith(SHADOW_EVENT)
        ]

    return _records


# --------------------------------------------------------------------------- #
# 1. Лицензия: свежесть                                                        #
# --------------------------------------------------------------------------- #
class TestFreshness:
    def test_a_claim_ends_exactly_at_valid_until(self):
        """Граница та же, что у читателя каталога: в сам момент срока — уже нет."""
        deadline = datetime(2026, 10, 2, 12, 0, 3, tzinfo=UTC)
        claim = _claim("c", valid_until=deadline)

        assert claim.is_stale(now=deadline - timedelta(seconds=1)) is False
        assert claim.is_stale(now=deadline) is True
        assert claim.is_stale(now=deadline + timedelta(seconds=1)) is True

    def test_a_claim_without_a_deadline_never_goes_stale(self):
        assert _claim("c").is_stale(now=READ_AT + timedelta(days=3650)) is False

    def test_a_claim_that_expired_between_reading_and_sending_is_named(self):
        """Сняли снимок в 12:00:00, срок — 12:00:03, отправляем в 12:00:05."""
        expiring = _claim("claim-expiring", valid_until=READ_AT + timedelta(seconds=3))
        licence = _licence(
            LicensedSubject(
                template_id="tpl-massage",
                state=SubjectState.KNOWN,
                claims=(_claim("claim-forever"), expiring),
            )
        )

        # сначала присутствие: к моменту отправки истекло ровно одно — названное
        assert licence.stale_claim_ids(now=NOW) == ("claim-expiring",)
        # и только потом отсутствие: в момент чтения то же утверждение ещё годно
        assert licence.stale_claim_ids(now=READ_AT) == ()


# --------------------------------------------------------------------------- #
# 2. Теневая оценка — чистая функция                                           #
# --------------------------------------------------------------------------- #
class TestAssessment:
    def test_no_reader_this_turn_is_absent_not_empty(self):
        """``None`` и пустая лицензия — разные факты, и в журнале они разные."""
        absent = assess(None, now=NOW)
        empty = assess(_licence(), now=NOW)

        assert absent.licence == LICENCE_ABSENT == "absent"
        assert absent.licence_age_s is None
        assert empty.licence == LICENCE_EMPTY == "empty"
        assert empty.licence_age_s == 5

    def test_unknown_and_unavailable_are_counted_apart(self):
        """«Не знаем» и «не смогли спросить» чинятся по-разному."""
        verdict = assess(_licence(KNOWN, UNKNOWN, UNAVAILABLE, UNAVAILABLE), now=NOW)

        assert verdict.licence == LICENCE_PRESENT == "present"
        assert (verdict.subjects_known, verdict.subjects_unknown) == (1, 1)
        assert verdict.subjects_unavailable == 2
        assert verdict.claims == 2
        assert verdict.stale_claim_ids == ()
        assert verdict.has_stale is False

    def test_the_form_guards_categories_ride_along(self):
        verdict = assess(None, form_categories=("planning", "medical"), now=NOW)

        assert verdict.form_categories == ("planning", "medical")

    def test_the_verdict_has_nowhere_to_put_words(self):
        """Поля оценки — счётчики, состояния и идентификаторы; текста среди них нет.

        Список литералом: новое поле в оценке обязано пройти через этот узел и
        через вопрос «не слова ли это».
        """
        assert set(ShadowAssessment.__dataclass_fields__) == {
            "licence",
            "subjects_known",
            "subjects_unknown",
            "subjects_unavailable",
            "claims",
            "stale_claim_ids",
            "licence_age_s",
            "form_categories",
        }


# --------------------------------------------------------------------------- #
# 3. Флаг                                                                      #
# --------------------------------------------------------------------------- #
class TestFlag:
    def test_absent_key_is_off_and_says_so(self, settings):
        if hasattr(settings, SHADOW_SETTING):
            delattr(settings, SHADOW_SETTING)

        reading = shadow_flag()

        assert reading.value is False
        assert reading.source is FlagSource.READ_DEFAULT

    def test_the_setting_name_is_the_decided_one(self):
        assert SHADOW_SETTING == "CLAIM_GATE_SHADOW_ENABLED"

    @pytest.mark.parametrize(
        ("raw", "value"),
        [("true", True), ("1", True), ("on", True), ("false", False), ("0", False), ("", False)],
    )
    def test_operator_words_are_read_as_written(self, settings, raw, value):
        setattr(settings, SHADOW_SETTING, raw)

        reading = shadow_flag()

        assert reading.value is value
        assert reading.source is FlagSource.SETTINGS

    @pytest.mark.parametrize("raw", ["enabled", "да", "2", 1, None])
    def test_an_unreadable_value_switches_nothing_on(self, settings, raw):
        """``bool("enabled")`` — ``True``. Флаг, который нельзя прочитать, ничего не включает."""
        setattr(settings, SHADOW_SETTING, raw)

        reading = shadow_flag()

        assert reading.value is False
        assert reading.source is FlagSource.MALFORMED


# --------------------------------------------------------------------------- #
# 4. Ответ не меняется                                                         #
# --------------------------------------------------------------------------- #
#: (метка, черновик, acted, заблокирован ли сторожем формы, что прочитает человек).
#: Ожидания — литералом: это сегодняшнее поведение хука, и лист его не трогает.
_CLEAN = "Массаж спины у Дениса стоит 3 500 ₽ за час. Записать на завтра в 18:00?"
_EFFECT = "Массаж помогает снять мышечное напряжение и временно расслабляет."
_UNFOUNDED = "После курса кожа станет ровнее."
_DRAFTS = [
    ("clean", _CLEAN, None, False, _CLEAN),
    # Утверждения об эффекте сторож формы сегодня пропускает — и подтверждённые,
    # и выдуманные. Теневой затвор 3.1 этого НЕ исправляет; узел держит факт.
    ("effect", _EFFECT, None, False, _EFFECT),
    ("unfounded_effect", _UNFOUNDED, None, False, _UNFOUNDED),
    ("medical", "Прими две таблетки утром и вечером.", None, True, REPLACEMENT_TEXT),
    ("promise", "Я гарантирую результат уже после первого сеанса.", None, True, REPLACEMENT_TEXT),
    ("planning", "Между сеансами нужно три-четыре недели.", None, True, REPLACEMENT_TEXT),
    ("contact", "Позвони мастеру напрямую: +7 999 123-45-67.", None, True, REPLACEMENT_TEXT),
    ("action_promise", "Сейчас проверю и вернусь с ответом.", False, True, ACTION_PROMISE_TEXT),
    ("empty", "", None, False, ""),
]

_STALE = _licence(
    LicensedSubject(
        template_id="tpl-massage",
        state=SubjectState.KNOWN,
        claims=(_claim("claim-old", valid_until=READ_AT - timedelta(days=1)),),
    )
)
_LICENCES = [
    ("absent", None),
    ("empty", _licence()),
    ("known", _licence(KNOWN)),
    ("unknown", _licence(UNKNOWN)),
    ("unavailable", _licence(UNAVAILABLE)),
    ("stale", _STALE),
]

_FLAGS = ["unset", "false", "true"]


def _set_flag(settings, flag: str) -> None:
    if flag == "unset":
        if hasattr(settings, SHADOW_SETTING):
            delattr(settings, SHADOW_SETTING)
    else:
        setattr(settings, SHADOW_SETTING, flag)


class TestTheReplyIsUnchanged:
    @pytest.mark.parametrize("flag", _FLAGS)
    @pytest.mark.parametrize(("licence_label", "licence"), _LICENCES)
    @pytest.mark.parametrize(("label", "draft", "acted", "blocked", "read"), _DRAFTS)
    def test_no_flag_and_no_licence_changes_what_the_person_reads(
        self, settings, flag, licence_label, licence, label, draft, acted, blocked, read
    ):
        _set_flag(settings, flag)

        outcome = guard_outbound(draft, surface="test", acted=acted, knowledge=licence)

        assert outcome.blocked is blocked, (label, licence_label, flag)
        assert outcome.text == read, (label, licence_label, flag)

    def test_the_matrix_is_not_empty(self):
        """Пустой прогон не доказывает ничего: размер матрицы — литералом."""
        assert len(_DRAFTS) * len(_LICENCES) * len(_FLAGS) == 162

    def test_a_broken_shadow_does_not_cost_the_person_a_reply(self, shadow_on, monkeypatch, caplog):
        def boom(*args, **kwargs):
            raise RuntimeError("shadow is broken")

        monkeypatch.setattr(claim_gate, "assess", boom)
        caplog.set_level(logging.ERROR, logger=SHADOW_LOGGER)

        outcome = guard_outbound(_CLEAN, surface="test", knowledge=_licence(KNOWN))

        assert outcome.allowed
        assert outcome.text == _CLEAN
        assert any("safety.claim_gate.shadow_failed" in r.getMessage() for r in caplog.records)


# --------------------------------------------------------------------------- #
# 5. Журнал: одна запись, без слов                                             #
# --------------------------------------------------------------------------- #
class TestTheShadowRecord:
    def test_flag_off_writes_nothing_and_flag_on_writes_exactly_one(self, settings, shadow_records):
        """Пара: «ноль записей» без положительного контроля не отличим от сломанного журнала."""
        _set_flag(settings, "unset")
        guard_outbound(_CLEAN, surface="test", knowledge=_licence(KNOWN))
        assert shadow_records() == []

        _set_flag(settings, "false")
        guard_outbound(_CLEAN, surface="test", knowledge=_licence(KNOWN))
        assert shadow_records() == []

        _set_flag(settings, "true")
        guard_outbound(_CLEAN, surface="test", knowledge=_licence(KNOWN))
        assert len(shadow_records()) == 1

    def test_the_record_names_the_licence_and_not_the_words(self, shadow_on, shadow_records):
        guard_outbound(
            _EFFECT, surface="concierge", knowledge=_licence(KNOWN, UNAVAILABLE), trace_id="t-1"
        )

        (record,) = shadow_records()
        assert "surface=concierge" in record
        assert "licence=present" in record
        assert "subjects_known=1" in record
        assert "subjects_unavailable=1" in record
        assert "claims=2" in record
        assert "form_blocked=False" in record
        assert "trace=t-1" in record
        # ни слов черновика, ни ключа смысла утверждения
        for word in ("Массаж", "напряжение", "расслабляет", "temporary_relaxation"):
            assert word not in record

    def test_a_blocked_draft_is_recorded_with_its_form_category(self, shadow_on, shadow_records):
        guard_outbound("Между сеансами нужно три-четыре недели.", surface="max", knowledge=None)

        (record,) = shadow_records()
        assert "licence=absent" in record
        assert "form_blocked=True" in record
        assert "form_categories=planning" in record
        assert "недели" not in record

    def test_a_stale_claim_is_named_by_id(self, shadow_on, shadow_records):
        guard_outbound(_CLEAN, surface="max", knowledge=_STALE)

        (record,) = shadow_records()
        assert "stale=1" in record
        assert "stale_claim_ids=claim-old" in record

    def test_absent_and_empty_licences_read_differently_in_the_log(self, shadow_on, shadow_records):
        guard_outbound(_CLEAN, surface="max", knowledge=None)
        guard_outbound(_CLEAN, surface="max", knowledge=_licence())

        absent, empty = shadow_records()
        assert "licence=absent" in absent
        assert "licence=empty" in empty


# --------------------------------------------------------------------------- #
# 6. Перепись вызовов хука                                                     #
# --------------------------------------------------------------------------- #
#: Вызовы, которые НЕСУТ лицензию: файл → сколько таких вызовов.
CARRIES_LICENCE: dict[str, int] = {
    "apps/orchestrator/concierge.py": 1,
    "apps/channels/max/handler.py": 1,
}

#: Вызовы, которым нести её неоткуда: файл → (сколько, почему). Новая запись —
#: решение, а не способ позеленеть: вызов без лицензии судит ответ как «знания
#: не читали».
NO_LICENCE: dict[str, tuple[int, str]] = {
    "apps/channels/max/handler.py": (
        4,
        "текст о передаче сотруднику, служебная строка под ответом, эхо голосового, "
        "ответ навыка салона — ни один из них не ответ консьержа, читавшего знание",
    ),
    "apps/channels/telegram/handler.py": (
        1,
        "путь навыков салона; читателя знания на нём нет",
    ),
}


def _guard_calls() -> dict[str, list[bool]]:
    """Файл → по одному признаку «передан ли ``knowledge=``» на каждый вызов хука."""
    found: dict[str, list[bool]] = {}
    for path in sorted((ROOT / "apps").rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        if "/tests/" in rel or rel.endswith("conftest.py"):
            continue
        source = path.read_text(encoding="utf-8")
        if "guard_outbound" not in source:
            continue
        for node in ast.walk(ast.parse(source)):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = getattr(func, "id", None) or getattr(func, "attr", None)
            if name != "guard_outbound":
                continue
            found.setdefault(rel, []).append(any(kw.arg == "knowledge" for kw in node.keywords))
    return found


class TestEveryCallOfTheHookHasDecided:
    """Каждый вызов хука либо несёт лицензию, либо назван среди тех, кому неоткуда.

    Без переписи восьмой вызов появится молча и будет судить без лицензии —
    сегодня это ничего не меняет, а после включения затвора станет дырой.
    """

    def test_there_are_exactly_seven_calls(self):
        calls = _guard_calls()

        assert sum(len(v) for v in calls.values()) == 7, calls

    def test_the_calls_that_carry_the_licence_are_the_named_two(self):
        carrying = {f: sum(flags) for f, flags in _guard_calls().items() if any(flags)}

        assert carrying == CARRIES_LICENCE

    def test_the_calls_without_a_licence_are_the_declared_ones(self):
        bare = {f: flags.count(False) for f, flags in _guard_calls().items() if flags.count(False)}

        assert bare == {f: n for f, (n, _why) in NO_LICENCE.items()}
        assert all(why.strip() for _n, why in NO_LICENCE.values())

    def test_the_scanner_sees_a_call_it_is_shown(self):
        """Положительный контроль разбора: вызов с именем и вызов через атрибут."""
        tree = ast.parse("guard_outbound(t, surface='x')\ngate.guard_outbound(t, knowledge=k)\n")
        seen = [
            any(kw.arg == "knowledge" for kw in node.keywords)
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and (getattr(node.func, "id", None) or getattr(node.func, "attr", None))
            == "guard_outbound"
        ]

        assert sorted(seen) == [False, True]
