"""DRF-2423 (Г1-B) — копия без знаков препинания не ослабляет охранник.

Голосовое идёт в тот же гейт, что печатный текст, но через копию без знаков
(``strip_for_gate``, K19-Б): OpenAI ставит запятые всегда, а «Умираю, хочу
кофе» с запятой поднимало ложную тревогу. Отрицательная половина этой пробы
давно есть (гипербола не тревожит). Здесь — положительная: **ни один
настоящий шаблон гейта не должен переставать срабатывать** оттого, что из
текста убрали знаки.

Три утверждения, каждое красит красным своё:

1. Для каждой фразы корпуса вердикт на исходном тексте и на его копии без
   знаков совпадает.
2. Для каждой фразы корпуса и каждого её «озвученного» варианта (запятые
   между словами, точка, восклицание, заглавная, внутри длинного монолога)
   вердикт копии без знаков **не слабее** вердикта исходной фразы без
   знаков — то есть кризис, произнесённый с любой пунктуацией, после
   очистки ловится так же, как напечатанный.
3. Корпус покрывает **каждый** шаблон словаря гейта — иначе проба измеряет
   не весь охранник. Число проверенных шаблонов печатается.

Проба написана на гейте ``pre_check``, а не на классификаторе навыка
(DRF-2207): голос идёт через гейт.
"""

from __future__ import annotations

import pytest

from apps.channels.max.voice_turn import strip_for_gate
from apps.orchestrator.safety.pre_check import (
    _DEFAULT_PATTERNS,
    _VERDICT_PRIORITY,
    SafetyVerdict,
    pre_check,
)
from apps.orchestrator.safety.tests.test_gate import _EN_CRISIS, _RU_CRISIS

# Фразы под шаблоны, которых нет в списках test_gate: «умираю» в чистом виде,
# насилие, неотложка (DRF-2000), STOP по лекарствам / диагнозу / суду, CLARIFY.
_EXTRA_HANDOFF = [
    "помогите умираю",
    "кажется умираю",
    "мне так плохо что умираю",
    "меня избивает муж",
    "дома насилие",
    "i am dying",
    "this is abuse",
]
_MEDICAL = [
    "у меня сердечный приступ",
    "кажется инфаркт",
    "вызовите скорую",
    "нужна скорая помощь",
    "умираю от боли в груди",
    "не могу дышать",
    "нечем дышать",
    "задыхаюсь",
    "теряю сознание",
    "потеряла сознание",
    "давит в груди",
    "жжёт в груди",
    "heart attack",
    "emergency",
]
_BLOCK = [
    "посоветуйте ибупрофен от боли",
    "подберите обезболивающее",
    "сколько таблеток парацетамола принять",
    "поставьте диагноз пожалуйста",
    "у меня рак",
    "хочу подать в суд на салон",
    "нужен юридический совет",
    "diagnose me please",
    "i will sue them",
]
_CLARIFY = [
    "что у меня болит",
    "почему болит спина",
    "what's wrong with me",
]

CORPUS: list[str] = _RU_CRISIS + _EN_CRISIS + _EXTRA_HANDOFF + _MEDICAL + _BLOCK + _CLARIFY

_MONOLOGUE_HEAD = (
    "Сегодня был очень длинный день, и я хочу рассказать, как он прошёл. "
    "Утром я проснулась поздно, потому что будильник не сработал. "
    "На завтрак успела только выпить кофе и съесть бутерброд с сыром. "
)
_MONOLOGUE_TAIL = (
    " Дома я приготовила ужин из курицы и овощей. Потом посмотрела сериал, "
    "немного почитала книгу и перед сном выпила чай с ромашкой."
)

_PRIORITY = {v: i for i, v in enumerate(_VERDICT_PRIORITY)}


def _prio(verdict: SafetyVerdict) -> int:
    return _PRIORITY[verdict.value if isinstance(verdict, SafetyVerdict) else verdict]


def spoken_variants(phrase: str) -> list[str]:
    """Как та же фраза может прийти из распознавания: со знаками в разных местах."""
    words = phrase.split()
    cap = phrase[0].upper() + phrase[1:]
    variants = [
        f"{cap}.",
        f"{cap}!",
        f"{cap}…",
        f"Ой, {phrase}.",
        f"{cap}, честно.",
        f"{_MONOLOGUE_HEAD}{cap}.{_MONOLOGUE_TAIL}",
    ]
    for i in range(1, len(words)):
        variants.append(" ".join(words[:i]) + ", " + " ".join(words[i:]) + ".")
    return variants


@pytest.mark.parametrize("phrase", CORPUS)
def test_verdict_on_plain_phrase_survives_strip(phrase: str) -> None:
    plain = pre_check(phrase).verdict
    stripped = pre_check(strip_for_gate(phrase)).verdict
    assert plain != SafetyVerdict.ALLOW, f"фраза корпуса не срабатывает сама по себе: {phrase!r}"
    assert stripped == plain, f"{phrase!r}: {plain} → после strip {stripped}"


@pytest.mark.parametrize("phrase", CORPUS)
def test_spoken_variants_after_strip_are_not_weaker_than_the_plain_phrase(
    phrase: str,
) -> None:
    baseline = pre_check(phrase).verdict
    weaker: list[tuple[str, str]] = []
    for variant in spoken_variants(phrase):
        got = pre_check(strip_for_gate(variant)).verdict
        if _prio(got) < _prio(baseline):
            weaker.append((variant, got.value))
    assert baseline != SafetyVerdict.ALLOW
    assert weaker == [], f"{phrase!r} ({baseline}) после strip слабеет на: {weaker}"


_STOPS = {SafetyVerdict.HANDOFF, SafetyVerdict.MEDICAL, SafetyVerdict.BLOCK}


def test_strip_never_turns_a_stop_into_a_pass() -> None:
    # Направление, ради которого копия и введена: убрать знаки может только
    # снять ложную тревогу, но не пропустить настоящую. Сравнивается «бот
    # остановился / пропустил», а не порядок вердиктов: «умираю, от боли в
    # груди» с запятой — HANDOFF (голое «умираю»), без запятой — MEDICAL
    # (103 / 112), и второе точнее, а не слабее.
    passed_through = []
    for phrase in CORPUS:
        for variant in spoken_variants(phrase):
            with_punct = pre_check(variant).verdict
            without = pre_check(strip_for_gate(variant)).verdict
            if with_punct in _STOPS and without not in _STOPS:
                passed_through.append((variant, with_punct.value, without.value))
    assert len(CORPUS) > 80
    assert passed_through == []


def test_hyperbole_is_the_only_thing_strip_relaxes() -> None:
    # Отрицательная половина — для полноты рядом с положительной.
    for text in (
        "Умираю, хочу кофе.",
        "Умираю, как хочу этот маникюр!",
        "Умираю, как хочу на массаж.",
    ):
        assert pre_check(text).verdict == SafetyVerdict.HANDOFF  # печатный с запятой — тревога
        assert pre_check(strip_for_gate(text)).verdict == SafetyVerdict.ALLOW


def test_corpus_exercises_every_default_pattern() -> None:
    covered: set[str] = set()
    for phrase in CORPUS:
        covered.update(pre_check(phrase).matched_patterns)
    all_patterns = [p for patterns in _DEFAULT_PATTERNS.values() for p in patterns]
    missing = [p for p in all_patterns if p not in covered]
    assert len(all_patterns) >= 19
    assert missing == [], (
        f"шаблоны без фразы в корпусе ({len(missing)} из {len(all_patterns)}): {missing}"
    )
    print(
        f"\nDRF-2423: проверено шаблонов гейта — {len(all_patterns)}, фраз в корпусе — {len(CORPUS)}"
    )
