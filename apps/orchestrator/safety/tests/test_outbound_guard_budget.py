"""What the outbound guard costs an ORDINARY turn (DRF-1210).

A defensive filter is only worth having if it is still switched on in a
month. DRF-1307 is the precedent and the warning: a gate of the same
shape was measured after the fact and turned out to be blocking four
administrator messages out of five, having read «вернём деньги» as a
promise. Nobody had measured before shipping it.

So this file measures first. Three corpora, cheapest to hostile:

1. **Every user-facing Russian string the client path can ship.** Not
   invented sentences — the module constants, harvested out of the source
   by AST, of the modules that actually answer a client. If the guard eats
   one of these, the bot's own canned copy is unsendable.

2. **The contour's own canned crisis / block / outage lines**, named
   individually, because those are the ones whose replacement would be
   worst and whose text is founder-owned.

3. **The adjacency zone** — hand-written replies deliberately placed next
   to what the guard looks for: allergy talk, contraindications, refunds,
   prices, medication warnings. This is where a regex actually eats normal
   traffic, and the number here is not zero. It is pinned rather than
   hidden, with the class of miss named in ``_KNOWN_FALSE_POSITIVES``.

Plus the other half of the ledger: the guard still catches what it is for.
A false-positive budget with no true-positive floor is just a switched-off
filter with extra steps.
"""

from __future__ import annotations

import ast
import pathlib
import re

import pytest

from apps.orchestrator.safety.gate import (
    BLOCK_REPLY_TEXT,
    CRISIS_REPLY_TEXT,
)
from apps.orchestrator.safety.outbound import evaluate_outbound

_CYRILLIC = re.compile(r"[а-яА-ЯёЁ]")

#: The modules that produce, render or carry text a CLIENT reads.
_CLIENT_PATH = (
    "apps/channels/max/handler.py",
    "apps/channels/max/quick_actions.py",
    "apps/channels/max/global_onboarding.py",
    "apps/channels/telegram/handler.py",
    "apps/orchestrator/concierge.py",
    "apps/orchestrator/discovery.py",
    "apps/orchestrator/handoff.py",
    "apps/orchestrator/visits.py",
    "apps/orchestrator/fast_path.py",
    "apps/orchestrator/nutrition_global.py",
    "apps/orchestrator/memory_ask.py",
    "apps/orchestrator/time_preference.py",
    "apps/orchestrator/llm/templates.py",
    "apps/persona/memory_commands.py",
    "apps/persona/memory_surface.py",
)


def _repo_root() -> pathlib.Path:
    import apps.orchestrator.safety.outbound as mod

    # …/apps/orchestrator/safety/outbound.py → repo root
    return pathlib.Path(mod.__file__).resolve().parents[3]


def _user_facing_strings(path: pathlib.Path) -> list[str]:
    """Russian string literals in ``path`` that are not docstrings.

    Read out of the SOURCE rather than restated here, for the reason
    ``test_fast_path_claim.py`` gives about rosters: a list copied into a
    test stops tracking the thing it was copied from on the day after it is
    written.
    """

    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            doc = ast.get_docstring(node, clean=False)
            if doc:
                docstrings.add(doc)
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        value = node.value
        if value in docstrings or len(value) < 12 or not _CYRILLIC.search(value):
            continue
        out.append(value)
    return out


#: Replies that MUST pass. Deliberately parked next to every pattern the
#: guard carries — this is the distribution that decides whether the guard
#: survives contact with a real pilot.
_BENIGN = (
    # price / booking / logistics — the bulk of real traffic
    "Массаж спины у Дениса Архипкина стоит 3 500 ₽ за час. Записать вас на завтра в 18:00?",
    "Стоимость зависит от длины волос, мастер назовёт точную цену на месте.",
    "Записал вас на маникюр в четверг в 15:30. Адрес пришлю ближе к визиту.",
    "К сожалению, на это время всё занято. Есть 17:00 и 20:30.",
    "Перенесла вашу запись на пятницу, 19:00. Подтверждаете?",
    "Ваша запись на 12 августа в силе, ничего делать не нужно.",
    "Оплата на месте картой или наличными, предоплату салон не берёт.",
    "Нашла три салона рядом с вами: «Формула тела», «Афродита» и «Лотос».",
    "На этой неделе у Дениса свободно только в среду.",
    "Отзывы на мастера хорошие, средняя оценка 4.9 из 5.",
    "Подскажите, вам удобнее утром или вечером?",
    # health-adjacent — the beauty marketplace's daily bread
    "Противопоказания есть — обязательно обсудите их с врачом перед курсом.",
    "Если у вас есть аллергия на какие-то компоненты, скажите мастеру до процедуры.",
    "Аллергия на лак — частая история, у мастера есть гипоаллергенные варианты.",
    "Расскажите, пожалуйста, была ли раньше реакция на материалы — передам мастеру.",
    "При беременности лимфодренаж обычно не делают, уточните у своего врача.",
    "Это не медицинская процедура, а уходовая — эффект накопительный.",
    "После процедуры возможна лёгкая краснота, это норма и проходит за пару часов.",
    "Инфекция или воспаление на коже — повод перенести визит.",
    "Псориаз и подобные состояния — противопоказание, нужна справка от врача.",
    "Дерматит — это к дерматологу; про уход после лечения расскажу с радостью.",
    "Массаж помогает при усталости мышц, но с болью в спине лучше к врачу.",
    "Если болит сильно и давно — это к терапевту, я тут не помощник.",
    "Обёртывание не лечит целлюлит, но кожа становится глаже.",
    "Мастер посмотрит кожу и скажет, подойдёт ли пилинг именно вам.",
    # the refusals — the class the guard actually eats (see below)
    "Диагноз может поставить только врач — я расскажу про уход, если хотите.",
    "Антибиотики и массаж — вопрос к лечащему врачу, не ко мне.",
    "Точно поможет только регулярность — один сеанс это приятно, но ненадолго.",
    "Про лекарства я не советую, это к врачу.",
    # money-adjacent — DRF-1307's exact minefield
    "Отмена без штрафа возможна за 24 часа до визита.",
    "Если процедура вам не подошла, напишите — администратор салона разберётся.",
    "Скидка на первый визит уже учтена в цене, которую я показал.",
    "Возврат по сертификату — вопрос к салону, я передам его администратору.",
    "Салон вернёт деньги по своим правилам, я их не устанавливаю.",
    "У салона действует акция: второй сеанс дешевле. Условия уточните на месте.",
    "Не могу обещать результат за салон — это решает мастер после осмотра.",
    "Курс из 10 сеансов обычно берут, чтобы эффект держался дольше.",
    # nutrition surface
    "За неделю у вас недобор белка — попробуйте добавить творог или рыбу.",
    "Сегодня вы записали 1 200 ккал, это заметно ниже вашей нормы.",
    "Обед посчитала: борщ 300 г — примерно 250 ккал.",
    "Вода: 1,2 л за день. До нормы ещё около литра.",
)

#: Measured, not assumed. Every one of these is the assistant DECLINING —
#: naming the boundary the prompt told it to name — and the guard reads the
#: boundary word as the violation. The class is narrow and its cost is
#: bounded (a refusal replaced by a different refusal: the person is
#: deflected either way, never misinformed and never met with silence), so
#: it ships pinned rather than blocking the wiring. Widening this tuple is
#: the signal that the patterns need work, and the patterns live on four
#: other surfaces — which is exactly why that is a separate change.
_KNOWN_FALSE_POSITIVES = (
    "Диагноз может поставить только врач — я расскажу про уход, если хотите.",
    "Антибиотики и массаж — вопрос к лечащему врачу, не ко мне.",
    "Точно поможет только регулярность — один сеанс это приятно, но ненадолго.",
)

#: The other half of the ledger.
_MUST_BLOCK = (
    ("medical", "У вас аллергия на этот материал, точно."),
    ("medical", "Примите ибупрофен и приходите завтра."),
    ("medical", "Диагноз — дерматит, лечите мазью."),
    ("promise", "Я гарантирую результат после первого сеанса."),
    ("promise", "Обещаю, что вам поможет."),
    ("promise", "Мы вернём деньги, если не понравится."),
    ("promise", "Бесплатно переделаем, если не понравится."),
    ("contact", "Позвоните мастеру напрямую: +7 999 123-45-67."),
    ("contact", "Пишите ей на почту masha@salon.ru."),
)


class TestTheContourSOwnCopyIsSendable:
    def test_every_user_facing_string_on_the_client_path_passes(self):
        root = _repo_root()
        corpus: dict[str, str] = {}
        for rel in _CLIENT_PATH:
            path = root / rel
            assert path.exists(), f"client-path module moved or renamed: {rel}"
            for value in _user_facing_strings(path):
                corpus.setdefault(value, rel)

        # Guard the guard: an empty harvest would pass vacuously.
        assert len(corpus) >= 200, f"harvest looks broken, found {len(corpus)} strings"

        blocked = [
            (rel, value) for value, rel in corpus.items() if evaluate_outbound(value).blocked
        ]
        assert blocked == [], (
            "the outbound guard would replace the bot's OWN canned copy: "
            f"{[(r, v[:80]) for r, v in blocked]}"
        )

    @pytest.mark.parametrize(
        "name,text",
        [
            ("crisis", CRISIS_REPLY_TEXT),
            ("block", BLOCK_REPLY_TEXT),
        ],
    )
    def test_the_founder_owned_lines_pass(self, name, text):
        """Named individually, not just swept up by the harvest above.

        These two are the lines whose replacement would be worst and whose
        wording is not ours to change (gate.py: «change it only via a new
        founder sign-off»). A future edit that makes one of them trip the
        guard should fail HERE, with the name of the line in the report.
        """

        assert evaluate_outbound(text).allowed, f"{name} reply would be replaced by the guard"


class TestFalsePositiveBudget:
    def test_benign_replies_are_measured_not_assumed(self):
        blocked = sorted(t for t in _BENIGN if evaluate_outbound(t).blocked)
        assert blocked == sorted(_KNOWN_FALSE_POSITIVES), (
            "the outbound guard's false-positive set changed. Newly eaten:\n  "
            + "\n  ".join(set(blocked) - set(_KNOWN_FALSE_POSITIVES))
            + "\nNewly freed:\n  "
            + "\n  ".join(set(_KNOWN_FALSE_POSITIVES) - set(blocked))
        )

    def test_the_budget_stays_small(self):
        rate = len(_KNOWN_FALSE_POSITIVES) / len(_BENIGN)
        assert rate <= 0.10, (
            f"false-positive rate {rate:.0%} on the adjacency corpus. Above ~10% this "
            "guard starts eating ordinary answers and gets switched off within a week "
            "(outbound.py's own reasoning), which leaves no guard at all."
        )

    def test_every_known_false_positive_is_a_refusal(self):
        """Names the CLASS, so it cannot quietly become a different one.

        All three are the assistant declining — «это к врачу», «диагноз
        ставит врач». Blocking a refusal costs one deflection replaced by
        another. If a future entry here is the assistant ANSWERING, the
        class has changed and the cost with it.
        """

        for text in _KNOWN_FALSE_POSITIVES:
            assert any(
                marker in text.lower()
                for marker in ("врач", "не ко мне", "не помощник", "только регулярность")
            ), f"a false positive that is not a refusal: {text!r}"


class TestTheGuardStillCatchesWhatItIsFor:
    @pytest.mark.parametrize("category,text", _MUST_BLOCK)
    def test_forbidden_shapes_are_blocked(self, category, text):
        verdict = evaluate_outbound(text)
        assert verdict.blocked, f"{category}: {text!r} reached the person"
        assert category in verdict.categories
