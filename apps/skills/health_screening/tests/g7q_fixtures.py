"""Code fixtures for the documentary ``T-S1-G7Q-*`` set ([OD-BOT §170]).

Source, traced one-to-one by ID: ``docs/safety/reviews/S1_CONTEXT_RECHECK_ADVERSARIAL_FIXTURES_v0.2.md``
v0.2.7, section «G7 question contract fixtures» (25 fixtures), which renders the
immutable owner record ``docs/safety/reviews/OWNER_RULINGS_S1_G7_QUESTION_CONTRACT_2026-09-21.md``
(RECORD SHA-256 ``b4f2f3f11045f5527b2c16f616bf4116e15c682ab309e54ccbea1d1f7c3597d9``).

Each entry keeps the documentary meaning; ``check`` names the executable
assertion in ``test_g7q_fixtures.py``. The clinical verdict of every fixture
stays ``PENDING_INDEPENDENT_PHYSICIAN_SIGNOFF`` in the documentation — a green
code test is a technical fact, not a physician pass.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class G7QFixture:
    id: str
    #: Which executable check proves it (see ``test_g7q_fixtures.py``).
    check: str
    #: The person's turns, verbatim from the documentary fixture. A structured
    #: answer is named by its action value (``yes`` / ``no`` / ``unsure``).
    turns: tuple[str, ...]
    #: The group a STOP must carry, where the fixture names one.
    group: str | None = None
    #: A named, owner-level gap: the fixture runs as a STRICT xfail with this reason.
    gap: str = ""


#: DRF-973 «Acute systemic» older rule (``classifier.py`` ``_RED_FLAG_PATTERNS``:
#: ``температур[аы]\s*\d``) makes an isolated temperature a red flag with no group.
#: The G7 half of NOT-03 holds (not G7, no G7 question); «no S1 at all» would mean
#: removing an older STOP rule — an owner decision, not this package's.
GAP_TEMPERATURE = (
    "DRF-973 older red-flag rule (temperature + digit) STOPs an isolated temperature; "
    "[OD-BOT §170] решение 1 says it is not G7; owner (22.09, №48): leave DRF-973 as is "
    "until a separate clinically checked temperature matrix is registered"
)

#: The S1 tract has no attribution / third-party context (``classifier.py`` notes at
#: :411 and :643: «attribution / quotation context is a gap of the whole S1 tract»).
GAP_THIRD_PARTY = (
    "no third-party attribution in the S1 tract (classifier.py:411, :643): a message "
    "about another person is read as personal — no personal-vs-third-party separation"
)

G7Q_FIXTURES: tuple[G7QFixture, ...] = (
    G7QFixture(
        "T-S1-G7Q-PRI-01", "stop_group", ("Мне резко стало очень плохо, не могу вдохнуть",), "G1"
    ),
    G7QFixture(
        "T-S1-G7Q-PRI-02", "stop_group", ("Внезапно стало совсем плохо, перекосило лицо",), "G4"
    ),
    G7QFixture("T-S1-G7Q-ASK-01", "asks_once", ("Мне резко стало очень плохо",)),
    G7QFixture("T-S1-G7Q-ANS-01", "answer_yes", ("Мне внезапно совсем плохо", "yes"), "G7"),
    G7QFixture(
        "T-S1-G7Q-ANS-02",
        "answer_no_first_turn",
        ("Не понимаю, что со мной, становится хуже", "no"),
    ),
    G7QFixture("T-S1-G7Q-ANS-03", "answer_unsure", ("Мне резко стало очень плохо", "unsure")),
    G7QFixture("T-S1-G7Q-FREE-01", "free_text", ("Мне резко стало очень плохо", "нет")),
    G7QFixture("T-S1-G7Q-FREE-02", "free_text", ("Мне внезапно совсем плохо", "прошло")),
    G7QFixture(
        "T-S1-G7Q-FREE-03", "free_text", ("Мне резко стало очень плохо", "сейчас нормально")
    ),
    G7QFixture(
        "T-S1-G7Q-EXPL-01", "stop_group", ("Мне очень плохо, сейчас упаду, не могу стоять",), "G7"
    ),
    G7QFixture("T-S1-G7Q-NOT-01", "not_g7", ("Очень устала после смены",)),
    G7QFixture("T-S1-G7Q-NOT-02", "not_g7", ("Не выспалась, чувствую себя разбитой",)),
    G7QFixture("T-S1-G7Q-NOT-03", "not_g7", ("У меня температура 37,8",)),
    G7QFixture("T-S1-G7Q-NOT-04", "not_g7", ("Немного подташнивает с утра",)),
    G7QFixture("T-S1-G7Q-NOT-05", "not_g7", ("Слегка кружится голова, когда резко встаю",)),
    G7QFixture("T-S1-G7Q-NOT-06", "not_g7", ("Мне плохо от цены", "Умру от цены")),
    G7QFixture("T-S1-G7Q-NOT-07", "not_g7", ("Год назад мне резко стало очень плохо",)),
    G7QFixture("T-S1-G7Q-ACT-01", "active_ordinary_turn", ("no",)),
    G7QFixture("T-S1-G7Q-ACT-02", "active_answer_no", ("no",)),
    G7QFixture(
        "T-S1-G7Q-RES-01",
        "stop_group",
        ("Полчаса назад не могла стоять и говорить, сейчас прошло",),
        "G7",
    ),
    G7QFixture("T-S1-G7Q-RES-02", "asks_once", ("Было просто очень плохо, но не знаю как",)),
    G7QFixture(
        "T-S1-G7Q-TP-01",
        "third_party",
        ("Маме резко стало очень плохо, она не может стоять",),
        gap=GAP_THIRD_PARTY,
    ),
    G7QFixture(
        "T-S1-G7Q-OTHER-01",
        "other_group_after_no",
        ("Мне резко стало очень плохо", "no", "губы и язык опухли"),
        "G6",
    ),
    G7QFixture("T-S1-G7Q-CTA-01", "no_diagnosis_no_cta", ("Мне резко стало очень плохо", "yes")),
    G7QFixture("T-S1-G7Q-EMRG-01", "emergency_text", ("Мне резко стало очень плохо", "yes")),
)

#: Local counter — changes only together with the entries (declared in the PR).
G7Q_FIXTURES_COUNT = 25
