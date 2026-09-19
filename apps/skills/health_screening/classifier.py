"""Pain mention + red-flag classifier.

Sprint 9 / P7 (DRF-824). Pure regex — no LLM, no Ayla. Two-tier output:

* :data:`PainSignal.NONE` — no pain context detected; skill doesn't match.
* :data:`PainSignal.SOFT` — mention of pain or discomfort that warrants
  the diagnostic-first response (1-2 clarifying questions before any
  service recommendation, per DRF-358 T04).
* :data:`PainSignal.RED_FLAG` — symptom set indicating "see a doctor,
  massage is not appropriate". Skill responds with the redirect text
  and skips the booking flow.

## Design

The mysite DRF-358 fix kept the diagnostic-first rule as a system-prompt
nudge plus voice examples — it did NOT detect pain in code. We add a
classifier here because Sprint 9 doesn't yet have the booking skill
that would consume the system prompt. The skill catches the same
incidents (dev-bot 2026-05-08 09:10, cold "не могу с заказом") before
the LLM-driven path picks them up.

False-positive cost: one extra empathic question. False-negative cost:
the user gets a tone-deaf "вот наши услуги" reply for "болит спина".
We tune for low false-negative — broad stem list.

## DRF-973 — the stem is a WORD, not a substring

The seed list above was matched with ``stem in lower``. That is a rule
about the SHAPE of a word rather than its meaning, and in Russian it
misfires constantly: «спасибо **бол**ьшое» — a person saying goodbye —
was classified as a pain report and answered with «где именно болит?».
So were «я **бол**ьше не приду» (a cancellation), «**стрел**ки»
(eyeliner — a service we sell), «**хрустал**ьный маникюр», «зажим для
волос» and «напряжённая неделя».

The fix has two halves, and BOTH are written so they can only ever
remove a false positive:

1. :data:`_NOT_PAIN` — phrases that contain a pain stem and are not
   pain. They are BLANKED from the text (replaced by a space, never
   deleted, so masking cannot join two halves into a new match) before
   any pain test runs. Whatever else the message says is still read:
   «болтали про то, что болит спина» keeps its «болит».
2. Stems match at a WORD START only, modulo a closed list of Russian
   verbal prefixes (:data:`_STEM_PREFIXES`) so «**прострел** в
   пояснице» and «за**бол**ела спина» keep matching. This is what
   drops «фут**бол**» without needing a line for every ball game.

Neither half can create a false NEGATIVE that the substring rule did
not already have: half 1 only ever removes text that is listed here by
name, half 2 only ever rejects a stem occurrence that starts in the
middle of a word with an unlisted prefix. The corpus in
``tests/test_classifier.py`` pins both directions — a genuine complaint
must keep reaching the screening, which is the whole reason this gate
exists.

## DRF-973 (found while measuring) — two red flags that never fired

«онемела рука» and «немеет рука» — the two ways a person actually
reports numbness — matched NOTHING: the red-flag pattern was
``\bонемен``, which only covers the noun «онемение» and the masculine
«онемел», and neither phrase carries a soft-pain stem either. A
red-flag miss is the expensive direction for this module, so the
pattern was widened to the verb forms of both «онеметь» and «неметь».
"""

from __future__ import annotations

import re
from enum import Enum


class PainSignal(str, Enum):
    NONE = "none"
    SOFT = "soft"
    RED_FLAG = "red_flag"


# ─── pain stems (broad — a miss is the expensive direction) ───────────────
#
# Matched at a word start (see :func:`_mentions_stem`), NOT as a bare
# substring — DRF-973. Multi-word entries («тяжесть в») anchor on their
# first token and are otherwise unchanged.

_PAIN_STEMS: frozenset[str] = frozenset(
    {
        "бол",  # болит / больно / болью
        "ноет",
        "ноют",
        "ноющ",
        "тянет",
        "тянущ",
        "хрустит",
        "хруст",
        "ломит",
        "ломота",
        "колет",
        "стрел",  # стреляет / стреляющ
        "дёргает",
        "дергает",
        "пульсир",
        "защемил",
        "защемля",
        # «зажим» и «напряж» здесь больше не стоят (DRF-2001, S-3c): пакет 3
        # владельца п. 6b — «хочу снять напряжение / зажимы» без боли, онемения,
        # слабости, травмы — обычная потребность, не S2. С болью реплика даёт
        # SOFT по стему «бол», с онемением — RED_FLAG.
        "спазм",
        "судорог",
        # Body part / state combos that mean pain without «бол»
        "тяжесть в",
        "усталость в",
        "не могу повернуть",
        "не могу нагнуться",
    }
)


# ─── DRF-973 — phrases that carry a pain stem and are not pain ────────────
#
# Every entry below was OBSERVED to misfire on the pre-patch classifier
# (2026-08-24 run, `docs/REPORT_DRF973.md`). They are BLANKED before any
# pain test, so the rest of the message is still read in full.
#
# The list is a CLOSED set of phrases, deliberately not an open «word
# that happens to start with a stem» rule: the guarded direction here is
# the false NEGATIVE (a real complaint answered with «вот наши услуги»),
# so a word may only stop counting as pain if it is named here.
#
# ADD to this list when a false positive is observed. Do NOT add a
# phrase that could also be a complaint — «болит» in any form belongs to
# the screening, whatever else the sentence does.
_NOT_PAIN: tuple[re.Pattern[str], ...] = (
    # «больш*» — the whole «большой» family starts with the «бол» stem.
    # «спасибо большое» (the ticket's headline case), «я больше не
    # приду» (a cancellation, DRF-1060), «большая чистка лица».
    re.compile(r"больш\w*", re.IGNORECASE),
    # small talk: «болтать» / «болтливый» / «болтун».
    re.compile(r"болт(?:а|л|у)\w*", re.IGNORECASE),
    # «болото» — appears in place names and idioms.
    re.compile(r"болот\w*", re.IGNORECASE),
    # «болею за» — supporting a team, not an illness. Anchored on the
    # preposition so «болею уже неделю» stays a complaint.
    re.compile(r"бол(?:ею|еешь|еет|еем|еете|еют|ел|ела|ело|ели)\s+за\b", re.IGNORECASE),
    # «стрелки» — eyeliner. A SERVICE WE SELL, and the «стрел» stem
    # («стреляет в шею») swallowed every request for one.
    re.compile(r"стрелк\w*", re.IGNORECASE),
    # «хрустальный» — a nail-design finish, not «хруст в шее».
    re.compile(r"хрустал\w*", re.IGNORECASE),
    # «зажим для волос» — a hair clip. Since DRF-2001 «зажим» is not a pain
    # stem at all (п. 6b), so this mask is belt and braces for the purchase
    # phrasing; kept so a future stem cannot revive the false friend.
    re.compile(r"зажим\w*\s+для\b", re.IGNORECASE),
    # «напряжённая неделя / график / работа» — the reason a person books
    # a relaxing massage, not a symptom. Since DRF-2001 «напряж» is not a
    # pain stem either (п. 6b: «напряжение в спине» without pain is a need),
    # so this mask is belt and braces, kept for the same reason as above.
    re.compile(
        r"напряж[её]нн\w*\s+(?:график\w*|недел\w*|день|дня|дн[ий]\w*"
        r"|месяц\w*|период\w*|работ\w*|разговор\w*)",
        re.IGNORECASE,
    ),
    # «больно ли?» / «а это больно?» / «не колет ли лазер?» — a question
    # about a procedure that has NOT happened, not a report of pain that
    # has. Answering it with «где именно болит?» is the same defect this
    # ticket is about, one axis over: the FORM carries a pain word, the
    # MEANING is a price-list question.
    #
    # ONLY the hypothetical frames are masked, and each needs an
    # interrogative particle or the future tense to qualify — a bare
    # «больно» is untouched. «больно ли делать массаж, если болит
    # спина» therefore still reaches the screening on its «болит».
    re.compile(r"больно\s+ли\b", re.IGNORECASE),
    re.compile(r"\b(?:будет\s+больно|больно\s+будет)\b", re.IGNORECASE),
    re.compile(r"\b(?:это|а\s+это)\s+больно\s*\?", re.IGNORECASE),
    re.compile(r"\b(?:не\s+)?колет\s+ли\b", re.IGNORECASE),
    # DRF-973 follow-up — both measured on the PATCHED classifier
    # (2026-08-25) and both belonging to the food tier of this bot:
    #
    # «хрустящие хлебцы» / «хрустящая корочка» — the «хруст» stem read a
    # meal log as «хруст в шее». The adjective «хрустящий» is never a
    # symptom; the symptom forms «хруст» and «хрустит» are untouched.
    re.compile(r"хрустящ\w*", re.IGNORECASE),
    # «меня тянет на сладкое» — a craving, and about the most likely
    # sentence in a nutrition dialogue, read as «тянет поясницу».
    # Anchored on the animate accusative pronoun, which is exactly what
    # separates the craving from the symptom: «спину тянет на работе»
    # has a BODY PART in that slot and is left alone.
    re.compile(
        r"\b(?:меня|тебя|его|е[её]|нас|вас|их)\s+тянет\s+на\b",
        re.IGNORECASE,
    ),
)


def _mask_not_pain(text: str) -> str:
    """Blank every :data:`_NOT_PAIN` phrase, preserving offsets' meaning.

    Replaced by a SPACE rather than removed: deletion could weld two
    halves of the message into a stem that neither half contained.
    """

    for pattern in _NOT_PAIN:
        text = pattern.sub(" ", text)
    return text


# Verbal prefixes a pain stem may carry. CLOSED — this is what lets
# «прострел» and «заболела» match while «футбол» / «баскетбол» do not.
#
# Multi-letter only, on purpose. A one-letter prefix («о», «у», «с»)
# buys nothing — no phrasing in the corpus needs one — and each of them
# re-opens the middle of a word to the stem: «о» + «бол» would make
# «оболочка» a pain report, which is the very rule this fix replaces.
_STEM_PREFIXES: tuple[str, ...] = (
    "про",
    "за",
    "раз",
    "рас",
    "при",
    "на",
    "под",
    "по",
    "пере",
    "об",
    "вы",
    "из",
)

_STEM_PREFIX_ALT = "|".join(sorted(_STEM_PREFIXES, key=len, reverse=True))


# ─── DRF-973 follow-up — a word START is not enough for «бол» ─────────────
#
# The word-start rule answers «фут*бол*». It does NOT answer the other
# half of the family: words that genuinely BEGIN with «бол» and mean
# nothing like pain. Measured on the patched classifier (2026-08-25),
# these still reached the screening:
#
#     болгарский перец · болонка · болеро · болван · болт · Болгария
#
# «болгарский перец» is the one that matters. This bot has a food tier
# (``food_scanner`` / ``nutrition_anketa``) and
# :class:`~apps.skills.health_screening.skill.HealthScreeningSkill`
# ANSWERS on a soft signal — so a person logging lunch was asked «где
# именно болит?».
#
# One :data:`_NOT_PAIN` phrase per offender does not close this: the set
# of Russian words starting «бол» that are NOT pain is open (болид,
# Боливия, Болонья, болтанка, Болдино…), while the set of forms that
# ARE pain is closed and short. So «бол» — and only «бол» — is matched
# as a stem PLUS a required ending, i.e. by its real paradigm:
#
#     боль/боли/болью/болям/болях · болит/болят · болел/болею/болеет/
#     болеть · болезнь/болезненный · болячка · больно/больной/больнее
#
# The single carve-out inside that paradigm is «больш» («больше»,
# «большое») — the one everyday word that shares the «боль» opening,
# and this ticket's headline case.
#
# Verified both directions before shipping: every form listed above
# still matches, and the 32-phrase ``PAIN_PHRASES`` corpus is unchanged.
# Positive enumeration is allowed HERE because the paradigm is closed —
# for every other stem an ending list would risk the false negative
# this module exists to prevent, so they keep the plain stem rule.
_STEM_TAILS: dict[str, str] = {
    "бол": r"(?:ь(?!ш)|и\b|ит|ят|я[мхч]|е[люетмйшязн])",
}


def _stem_pattern(stem: str) -> re.Pattern[str]:
    """Word-start matcher for one stem, modulo :data:`_STEM_PREFIXES`.

    A stem listed in :data:`_STEM_TAILS` additionally requires one of
    its real inflectional endings — see the note above.
    """

    return re.compile(
        r"(?<![^\W\d_])(?:"
        + _STEM_PREFIX_ALT
        + r")?"
        + re.escape(stem)
        + _STEM_TAILS.get(stem, ""),
        re.IGNORECASE,
    )


_PAIN_STEM_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    _stem_pattern(stem) for stem in sorted(_PAIN_STEMS)
)


# ─── red-flag patterns — "see a doctor" cases ─────────────────────────────


# These patterns indicate something massage / nutrition cannot help and
# may worsen. The rule of thumb: anything that looks like a neurological,
# vascular, or acute systemic symptom.
_RED_FLAG_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Numbness / nerve-root signs.
    #
    # DRF-973 — the list held ``\bонемен`` and nothing else, which covers
    # the noun «онемение» and the masculine «онемел». «онемела рука» and
    # «немеет рука» — the two phrasings a person actually types — matched
    # NEITHER this list NOR any soft-pain stem and classified as NONE: a
    # red flag that never fired. Both verbs are spelled out now, in
    # EXACT forms (never «неме\w*») so «немецкий» cannot qualify.
    re.compile(r"\bонемен", re.IGNORECASE),
    re.compile(r"\b(?:о)?неме(?:ет|ют|л|ла|ло|ли|ть|вш\w*)\b", re.IGNORECASE),
    re.compile(r"потерял[аио]? чувствит", re.IGNORECASE),
    re.compile(r"отнима(?:ет|ется|ются)", re.IGNORECASE),
    re.compile(r"отдаёт в (?:руку|ногу|пальц)", re.IGNORECASE),
    re.compile(r"отдает в (?:руку|ногу|пальц)", re.IGNORECASE),
    # Acute systemic
    re.compile(r"температур[аы]\s*\d", re.IGNORECASE),
    re.compile(r"температур[аы]\s*(?:высок|поднял)", re.IGNORECASE),
    re.compile(r"тошн(?:ит|ота)", re.IGNORECASE),
    re.compile(r"рвот", re.IGNORECASE),
    # Vascular / cardiac warning
    re.compile(r"давит в груди", re.IGNORECASE),
    re.compile(r"одышк", re.IGNORECASE),
    re.compile(r"учащ[её]нный пульс", re.IGNORECASE),
    # Functional collapse
    re.compile(r"не могу встать", re.IGNORECASE),
    re.compile(r"не могу ходить", re.IGNORECASE),
    re.compile(r"теря(?:ю|ет) сознание", re.IGNORECASE),
    # Breathing — S1 group G1 (DRF-1997, S-1b). «трудно дышать» used to sit in
    # _PAIN_STEMS: a person short of breath got two questions about where it
    # hurts instead of the protective answer (CLINICAL-F01).
    #
    # The only exception is an emotional idiom («задыхаюсь от смеха») — not a
    # clinical ruling, for the clinical expert to check (VQ1). There is NO
    # exception for a stuffy room («в зале душно, не хватает воздуха»): an
    # asthma or panic attack indoors is real, and a false «лучше к врачу» is
    # cheaper than a missed breathing red flag. Known false positive, named:
    # irony («не могу дышать без этого крема, шучу»).
    re.compile(r"\b(?:трудно|тяжело)\s+дышать", re.IGNORECASE),
    re.compile(r"\bне\s+могу\s+(?:вдохнуть|дышать|отдышаться)", re.IGNORECASE),
    re.compile(r"\bзадыха\w*+(?!\s+от\s+(?:смеха|хохота|восторга|счастья|радости))", re.IGNORECASE),
    re.compile(r"\bудушь\w*", re.IGNORECASE),
    re.compile(r"(?:не\s+хватает\s+воздуха|воздуха\s+не\s+хватает)", re.IGNORECASE),
    # Pregnancy + back pain is a soft red-flag — surface but don't block;
    # caller emits the warning. We keep this OUT of the regex list for
    # now; future versions can add tiered red-flags.
)

# -- S1 groups G2–G7 (DRF-2004, S-1d) ----------------------------------------
# Only obvious red flags. Each group is narrowed by its own text in
# ``docs/safety/F0-C3-safety-matrix.md`` (:197-203), the matrix's illustrative
# phrases (:205), T-S1-11 (:286) and the G4 boundary in clinical-review-001
# (:400-413). No numeric thresholds, no diagnoses. The fidelity of every pattern
# and every exception awaits the clinical expert (VQ1).

#: Emotional idioms («от смеха / восторга …») — the same exception as breathing (S-1b).
_S1_EMOTIONAL_IDIOM = r"(?!\s+от\s+(?:смеха|хохота|восторга|счастья|радости))"

#: G2 — «потеря сознания или выраженное нарушение сознания» (:198); «теряю сознание» (:205).
_S1_G2_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bпотерял\w*\s+сознани\w*+" + _S1_EMOTIONAL_IDIOM, re.IGNORECASE),
    re.compile(r"\bбез\s+сознания\b", re.IGNORECASE),
    re.compile(r"\bобморок\w*", re.IGNORECASE),
    re.compile(r"\bв\s+глазах\s+(?:темнеет|потемнело|темно)", re.IGNORECASE),
)

#: G3 — «внезапная сильная боль / давление в груди» (:199); «резко давит в груди и плохо» (:205).
_S1_G3_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"\b(?:боль|болит|колет|жж[её]т|давит|сдавливает|сжимает)\s+в\s+груди", re.IGNORECASE
    ),
    re.compile(r"\bв\s+груди\s+(?:болит|колет|жж[её]т|давит|сдавливает|сжимает)", re.IGNORECASE),
)

#: G4 — «слабость одной стороны тела, нарушение речи, … внезапная потеря движения /
#: чувствительности» (:200); «не чувствую половину лица, речь заплетается» (:205).
#:
#: Owner ruling 18.09 — [OD-BOT §164] G4 boundary (``docs/OPEN_DECISIONS.md``;
#: immutable record ``docs/safety/reviews/OWNER_RULINGS_S1_AI_CLINICAL_PRE_REVIEW_2026-09-18.md``):
#: внезапная односторонняя слабость / онемение, перекос лица, нарушение речи,
#: внезапное нарушение зрения, внезапное нарушение равновесия / координации — явный
#: G4 → STOP. [OD-BOT §161]: recent-resolved G4 («прошло», «стало лучше», «сейчас
#: нормально») остаётся STOP — исчезновение признаков не снимает срочность, so the
#: resolution words are deliberately NOT in the negation set below.
#:
#: The detector lives in :func:`detect_g4` (the same isolation as G6, its own
#: semantics): a sign is read only next to its context — a SIDE («правая», «с одной
#: стороны», «половина лица / тела») for weakness / numbness / loss of movement, a
#: SUDDEN marker («внезапно», «резко», «вдруг») for vision, balance and coordination
#: — never a single word («рука», «лицо», «зрение», «речь», «равновесие»,
#: «координация», «язык»). Negation is a closed set of shapes attached to the sign
#: word («лицо не перекосило», «речь не нарушена», «равновесие не нарушено»); a
#: future-tense sign next to a hypothetical marker («что делать, если когда-нибудь
#: перекосит лицо?») is not a current sign.
#:
#: Named limits, NOT compensated here: the pre-S1 numbness rule (DRF-973,
#: ``_RED_FLAG_PATTERNS``: «онемен…», «немеет») still fires BEFORE this detector, so
#: «немеет рука иногда» and «онемения и слабости нет» are a red flag by that older
#: rule — fail-closed, not attributed to G4 (``tests/test_g4_detector.py``, strict
#: xfail). Third-party and quoted phrases are caught (fail-closed) — attribution /
#: quotation context is a gap of the whole S1 tract. The §164 routing question is not
#: asked on this path (architectural blocker, see the PR). Fidelity of every pattern
#: awaits the licensed physician (VQ1).

#: Sudden onset — the §164 discriminator for vision / balance / coordination.
_G4_SUDDEN = (
    r"(?:внезапн\w*|резко|вдруг|неожиданно|в\s+один\s+момент|ни\s+с\s+того\s+ни\s+с\s+сего)"
)
#: One side — the §164 discriminator for weakness / numbness / loss of movement.
_G4_SIDE = (
    r"(?:прав(?:ая|ую|ой|ые|ой)|лев(?:ая|ую|ой|ые)|справа|слева"
    r"|(?:с\s+)?одн(?:ой|а|у)\s+сторон\w*|половин\w+\s+(?:тела|лица))"
)
#: Weakness / numbness / loss of movement or sensation — the verb or noun forms.
_G4_MOTOR = (
    r"(?:онемел\w*|онемени\w*|немеет|не\s+чувству\w*|ослаб\w*|слабост\w*|отнял\w*"
    r"|не\s+двига\w*|не\s+слушает\w*|повисл\w*|парализ\w*|обвисл\w*"
    r"|потерял\w*\s+чувствит\w*|перестал\w*\s+(?:двигаться|чувствовать|слушаться))"
)
#: A. one-sided motor / sensory sign — either order, same sentence.
_G4_SIDE_MOTOR = re.compile(
    _G4_SIDE + r"[^.!?;]{0,40}?" + _G4_MOTOR + r"|" + _G4_MOTOR + r"[^.!?;]{0,40}?" + _G4_SIDE,
    re.IGNORECASE,
)
_G4_MOTOR_TOKEN = re.compile(_G4_MOTOR, re.IGNORECASE)
#: A'. sudden weakness / numbness without a named side (kept from the flat list).
_G4_SUDDEN_MOTOR = re.compile(
    r"\bвнезапн\w+\s+(?:слабость|онемени\w*)|\bотнял(?:ась|ся|ись)\s+(?:рука|нога|руки|ноги|лицо|язык)"
    r"|\b(?:рука|нога|руки|ноги)\s+отнял\w+",
    re.IGNORECASE,
)
#: B. face droop — unconditional (an acute sign by wording).
_G4_FACE = r"(?:перекосил\w*|перекошен\w*|скосил\w*|опустил\w*\s+(?:уголок|угол)|асимметри\w+)"
_G4_FACE_SIGN = re.compile(
    _G4_FACE + r"[^.!?;]{0,25}?\b(?:лиц\w*|рот|рта|губ\w*|половин\w+\s+лица)\b"
    r"|\b(?:лицо|рот|половин\w+\s+лица|уголок\s+рта|угол\s+рта)\b[^.!?;]{0,25}?" + _G4_FACE,
    re.IGNORECASE,
)
_G4_FACE_TOKEN = re.compile(_G4_FACE, re.IGNORECASE)
#: C. speech — unconditional (§164 «нарушение речи»); «речь идёт о …» is not a sign.
_G4_SPEECH = (
    r"(?:реч\w*\s+(?:(?!не\b|ни\b|нет\b)[\w-]+\s+){0,2}(?:невнятн\w*|нарушил\w*|нарушен\w*|заплета\w*|пропал\w*|не\s+получ\w*)"
    r"|(?:невнятн\w+|заплетающ\w+|нарушен\w+)\s+реч\w*|нарушени\w*\s+речи"
    r"|язык\s+заплета\w*|не\s+могу\s+(?:говорить|выговорить|произнести)"
    r"|не\s+выговарива\w*|путаю\s+слова|слова\s+не\s+выговарива\w*)"
)
_G4_SPEECH_SIGN = re.compile(_G4_SPEECH, re.IGNORECASE)
#: D. vision — needs a SUDDEN marker in the sentence, except the monocular / total
#: loss forms that are acute by wording.
_G4_VISION = (
    r"(?:пропал\w*\s+зрени\w*|зрени\w*\s+(?:пропал\w*|исчезл\w*|упал\w*|потерял\w*)"
    r"|потерял\w*\s+зрени\w*|перестал\w*\s+видеть|не\s+вижу|двоится|двоение|двоиться"
    r"|потемнел\w*\s+в\s+глазах|расплыва\w*|размыт\w*|туман\s+(?:в\s+глазах|перед\s+глазами)"
    r"|пелена\s+(?:в\s+глазах|перед\s+глазами)|нарушени\w*\s+зрения|зрени\w*\s+нарушил\w*)"
)
_G4_VISION_SIGN = re.compile(_G4_VISION, re.IGNORECASE)
_G4_VISION_ACUTE = re.compile(
    r"(?:пропал\w*\s+зрени\w*|зрени\w*\s+(?:пропал\w*|исчезл\w*)|потерял\w*\s+зрени\w*"
    r"|перестал\w*\s+видеть|не\s+вижу\s+(?:одним|левым|правым)\s+глазом)",
    re.IGNORECASE,
)
#: E. balance / coordination — needs a SUDDEN marker in the sentence.
_G4_BALANCE = (
    r"(?:(?:потерял\w*|нарушил\w*|пропал\w*|теря\w*)\s+(?:равновеси\w*|координаци\w*)"
    r"|(?:равновеси\w*|координаци\w*)\s+(?:нарушил\w*|нарушен\w*|пропал\w*|потерял\w*)"
    r"|нарушени\w*\s+(?:равновеси\w*|координаци\w*)|повело\s+в\s+сторону|заносит\s+в\s+сторону"
    r"|не\s+могу\s+(?:нормально\s+)?(?:стоять|идти|ходить|удержать\s+равновесие)"
    r"|шатает|падаю\s+на\s+(?:одну\s+)?сторону)"
)
_G4_BALANCE_SIGN = re.compile(_G4_BALANCE, re.IGNORECASE)
_G4_SUDDEN_RE = re.compile(_G4_SUDDEN, re.IGNORECASE)
#: Negation attached to the sign word: up to two words between the particle and the
#: word. «прошло», «стало лучше», «сейчас нормально» are NOT here ([OD-BOT §161]).
_G4_NEG_BEFORE = re.compile(
    r"(?:\bне|\bни|\bнет|\bнету|\bбез|\bне\s+было|\bне\s+бывает)\s+(?:[\w-]+\s+){0,2}$",
    re.IGNORECASE,
)
_G4_NEG_AFTER = re.compile(
    r"^\s*(?:нет\b|нету\b|не\s+было\b|отсутству\w*|не\s+нарушен\w*)",
    re.IGNORECASE,
)
_G4_FUTURE = re.compile(
    r"\b(?:перекосит|онемеет|ослабнет|отнимется|пропад[её]т|потеря(?:ю|ешь|ет)|начн[её]т"
    r"|станет|нарушится|перестан(?:у|ешь|ет)|повед[её]т|исчезнет)\b",
    re.IGNORECASE,
)
_G4_HYPOTHETICAL = re.compile(
    r"(?:что\s+делать,?\s+если|а\s+если|если\s+вдруг|если\s+когда-нибудь|когда-нибудь"
    r"|бывает\s+ли|может\s+ли|а\s+вдруг)",
    re.IGNORECASE,
)


def _g4_sentence(lower: str, pos: int) -> str:
    start = max(lower.rfind(ch, 0, pos) for ch in ".!?;") + 1
    end_candidates = [i for i in (lower.find(ch, pos) for ch in ".!?;") if i != -1]
    end = min(end_candidates) if end_candidates else len(lower)
    return lower[start:end]


def _g4_negated(lower: str, token_start: int, token_end: int) -> bool:
    before = lower[max(0, token_start - 40) : token_start]
    if _G4_NEG_BEFORE.search(before):
        return True
    after = lower[token_end : token_end + 20]
    return bool(_G4_NEG_AFTER.search(after))


def _g4_hypothetical(lower: str, match: re.Match[str]) -> bool:
    sentence = _g4_sentence(lower, match.start())
    return bool(_G4_HYPOTHETICAL.search(sentence)) and bool(_G4_FUTURE.search(sentence))


def _g4_live(
    lower: str,
    sign: re.Pattern[str],
    token: re.Pattern[str] | None = None,
    *,
    needs_sudden: bool = False,
    acute: re.Pattern[str] | None = None,
) -> bool:
    """A sign match that is current: not negated, not hypothetical, and — where §164
    asks for it — accompanied by a sudden-onset marker in the same sentence (or an
    acute-by-wording form)."""

    for match in sign.finditer(lower):
        if _g4_hypothetical(lower, match):
            continue
        tok = (token or sign).search(lower, match.start(), match.end())
        if tok is None:
            continue
        if _g4_negated(lower, tok.start(), tok.end()):
            continue
        if needs_sudden:
            sentence = _g4_sentence(lower, match.start())
            if not _G4_SUDDEN_RE.search(sentence) and not (
                acute is not None and acute.search(match.group(0))
            ):
                continue
        return True
    return False


def detect_g4(text: str) -> bool:
    """S1 group G4 — explicit, current or recent-resolved, non-negated neurological
    sign: one-sided weakness / numbness / loss of movement, face droop, speech
    disturbance, sudden vision loss, sudden loss of balance / coordination.

    Pure regex, no LLM. Reads the same masked lower-cased text as :func:`classify`.
    Does NOT decide the ambiguous case («немеет рука иногда») — that is the §164
    question contract, not implemented here.
    """
    if not isinstance(text, str) or not text.strip():
        return False
    lower = _mask_not_pain(text.strip().lower())
    return (
        _g4_live(lower, _G4_SIDE_MOTOR, _G4_MOTOR_TOKEN)
        or _g4_live(lower, _G4_SUDDEN_MOTOR)
        or _g4_live(lower, _G4_FACE_SIGN, _G4_FACE_TOKEN)
        or _g4_live(lower, _G4_SPEECH_SIGN)
        or _g4_live(lower, _G4_VISION_SIGN, needs_sudden=True, acute=_G4_VISION_ACUTE)
        or _g4_live(lower, _G4_BALANCE_SIGN, needs_sudden=True)
    )


#: G5 — «значительное или неконтролируемое кровотечение» (:201); «кровь не останавливается»
#: (:205). «кровит» is caught too: «после эпиляции немного кровит — это нормально?» is a
#: legitimate ambiguous G5 under the fail-closed expectation — a named cost.
_S1_G5_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bкровь\s+не\s+останавлива\w*", re.IGNORECASE),
    re.compile(r"\b(?:сильн\w+|обильн\w+)\s+кровотечени\w*", re.IGNORECASE),
    re.compile(r"\bкров(?:ит|оточит|оточат)\b", re.IGNORECASE),
)

#: G6 — «признаки тяжёлой аллергической реакции с дыхательными / системными проявлениями»
#: (:202); «после укола отекло горло, тяжело дышать» (:205); T-S1-11 (:286).
#:
#: Owner ruling 18.09 — [OD-BOT §159] (``docs/OPEN_DECISIONS.md``; immutable record
#: ``docs/safety/reviews/OWNER_RULINGS_S1_AI_CLINICAL_PRE_REVIEW_2026-09-18.md``):
#: внезапный отёк губ / рта / языка / горла после возможного контакта с аллергеном —
#: явный G6 → STOP, дыхательных симптомов ждать не нужно; также затруднение дыхания /
#: глотания, сдавление горла, внезапная осиплость, выраженное головокружение,
#: спутанность, обморок — в контексте возможной острой аллергической реакции.
#: Изолированная локальная сыпь / зуд без этих признаков — НЕ автоматический S1.
#:
#: The detector is negation-aware (the §159 boundary): «губы не опухли», «нет отёка
#: языка», «горло не отекает», «отёка нет» must not fire on the keywords alone.
#: Negation is read only in a closed set of shapes attached to the swelling word —
#: never anywhere in the sentence, so «…губы опухают, не знаю, что делать» keeps its
#: red flag. A future-tense swelling verb next to a hypothetical marker («что делать,
#: если когда-нибудь опухнут губы?») is not a current sign.
#:
#: Named limits, NOT compensated by a wider regex: third-party («у мамы опухли губы»),
#: a quoted phrase and a hypothetical in the present tense are still caught — the
#: fail-closed direction, documented as strict xfail in ``tests/test_g6_detector.py``
#: (attribution / quotation context is a runtime gap of the whole S1 tract, not of
#: G6). The local-rash question contract ([OD-BOT §164]) is not implemented here:
#: «сыпь и зуд после крема» is not G6 and gets no question on this path. The
#: fidelity of every pattern awaits the licensed physician (VQ1).

#: Sites named by the ruling — lips, mouth, tongue, throat (+ larynx). Word forms are
#: enumerated, not ``\w*``: «губка» (a sponge) and «языковой» must not qualify.
_G6_SITE = (
    r"(?:губ(?:а|ы|у|е|ой|ами|ах)?|рот|рта|рту|ртом|во\s+рту"
    r"|язык(?:а|у|ом|е)?|горл(?:о|а|у|е|ом)|гортан(?:ь|и|ью))"
)
#: Swelling — noun and verb forms. «опухол…» (a tumour) is excluded: it is not an acute
#: sign. «отеч…» is limited to conjugations of «отечь» so «отечественный крем» is not a
#: swelling.
_G6_SWELL = (
    r"(?:от[её]к\w*|отеч(?:ь|[её]т|ешь|[её]м|[её]те|ут)\b|опух(?!ол)\w*|распух\w*"
    r"|припух\w*|раздул\w*|вздул\w*)"
)
#: Same sentence, either order, within a short window. Commas are allowed inside the
#: window («губы, язык и горло не отекали») — the negation guard reads the verb.
_G6_SWELLING_SITE = re.compile(
    _G6_SWELL + r"[^.!?;]{0,30}?\b" + _G6_SITE + r"\b"
    r"|\b" + _G6_SITE + r"\b[^.!?;]{0,30}?" + _G6_SWELL,
    re.IGNORECASE,
)
_G6_SWELL_TOKEN = re.compile(_G6_SWELL, re.IGNORECASE)
#: Negation attached to the swelling word: up to two words may stand between the
#: particle and the word («не сильно опухли»), nothing more.
_G6_NEG_BEFORE = re.compile(
    r"(?:\bне|\bни|\bнет|\bнету|\bбез|\bне\s+было|\bне\s+бывает)\s+(?:[\w-]+\s+){0,2}$",
    re.IGNORECASE,
)
#: «отёка нет», «отёка не было», «отёк отсутствует» — negation after the word.
_G6_NEG_AFTER = re.compile(r"^\s*(?:нет\b|нету\b|не\s+было\b|отсутству\w*)", re.IGNORECASE)
#: Hypothetical: a future / infinitive swelling verb next to an «if / ever» marker.
_G6_FUTURE = re.compile(
    r"\b(?:опухн\w*|распухн\w*|отекут|отеч[её]т|отекать|опухать|распухать)\b", re.IGNORECASE
)
_G6_HYPOTHETICAL = re.compile(
    r"(?:что\s+делать,?\s+если|а\s+если|если\s+вдруг|если\s+когда-нибудь|когда-нибудь"
    r"|бывает\s+ли|может\s+ли|а\s+вдруг)",
    re.IGNORECASE,
)
#: Possible contact with an allergen / trigger — the context the ruling names for the
#: secondary signs (the swelling itself needs no context).
_G6_EXPOSURE = (
    r"(?:после\s+(?:крем\w*|маз\w*|маск\w*|косметик\w*|лекарств\w*|таблет\w*|антибиотик\w*"
    r"|препарат\w*|укол\w*|инъекци\w*|прививк\w*|еды|пищи|орех\w*|морепродукт\w*|укус\w*"
    r"|пчел\w*|ос[ыа]\b|пилинг\w*|процедур\w*|сеанс\w*|нанес\w*)"
    r"|аллерг\w*|анафилакт\w*|укусил\w*|ужалил\w*)"
)
#: Secondary G6 signs (the ruling's list). Breathing is already G1; «трудно глотать»,
#: throat tightness, sudden hoarseness, marked dizziness, confusion, fainting count as
#: G6 only next to a possible exposure. «больно глотать» alone is a sore throat, not
#: listed, and is left out.
_G6_SIGN = (
    r"(?:(?:трудно|тяжело|не\s+могу|не\s+получается)\s+глотать|глотать\s+(?:трудно|тяжело)"
    r"|(?:сдавливает|сдавило|сжимает|сжало|перехватило|стеснени\w*|сдавлен\w*)\s+(?:в\s+)?горл\w*"
    r"|горло\s+(?:сдавливает|сдавило|сжимает|сжало|перехватило)"
    r"|внезапн\w+\s+осипл\w*|(?:резко|внезапно)\s+осип\w*|голос\s+(?:резко\s+|внезапно\s+)?(?:осип|сел|пропал)"
    r"|сильно\s+кружится\s+голова|выраженн\w+\s+головокружени\w*|спутанн\w*|обморок\w*"
    r"|(?:трудно|тяжело)\s+дышать|не\s+могу\s+(?:дышать|вдохнуть)|задыха\w*|удушь\w*)"
)
_G6_EXPOSURE_SIGN = re.compile(
    _G6_EXPOSURE
    + r"[^.!?;]{0,60}?"
    + _G6_SIGN
    + r"|"
    + _G6_SIGN
    + r"[^.!?;]{0,60}?"
    + _G6_EXPOSURE,
    re.IGNORECASE,
)
_G6_SIGN_TOKEN = re.compile(_G6_SIGN, re.IGNORECASE)
#: Unconditional: the reaction is named.
_S1_G6_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bанафилакт\w*", re.IGNORECASE),
    re.compile(r"\bот[её]к\w*\s+квинке", re.IGNORECASE),
)


def _g6_negated(lower: str, token_start: int, token_end: int) -> bool:
    """True when the swelling / sign word at ``lower[token_start:token_end]`` is negated.

    Only the closed shapes above count; a «не» three words away is not a negation of
    this word.
    """
    before = lower[max(0, token_start - 40) : token_start]
    if _G6_NEG_BEFORE.search(before):
        return True
    after = lower[token_end : token_end + 20]
    return bool(_G6_NEG_AFTER.search(after))


def _g6_hypothetical(lower: str, match: re.Match[str]) -> bool:
    sentence_start = max(lower.rfind(ch, 0, match.start()) for ch in ".!?") + 1
    sentence = lower[sentence_start : match.end() + 1]
    return bool(_G6_HYPOTHETICAL.search(sentence)) and bool(_G6_FUTURE.search(match.group(0)))


def _g6_live_match(lower: str, matches: list[re.Match[str]], token: re.Pattern[str]) -> bool:
    for match in matches:
        if _g6_hypothetical(lower, match):
            continue
        tok = token.search(lower, match.start(), match.end())
        if tok is None:
            continue
        if not _g6_negated(lower, tok.start(), tok.end()):
            return True
    return False


def detect_g6(text: str) -> bool:
    """S1 group G6 — explicit, current, non-negated swelling of lips / mouth / tongue /
    throat, or a named reaction, or a secondary sign next to a possible exposure.

    Pure regex, no LLM. Reads the same masked lower-cased text as :func:`classify`.
    """
    if not isinstance(text, str) or not text.strip():
        return False
    lower = _mask_not_pain(text.strip().lower())
    for pattern in _S1_G6_PATTERNS:
        if pattern.search(lower):
            return True
    if _g6_live_match(lower, list(_G6_SWELLING_SITE.finditer(lower)), _G6_SWELL_TOKEN):
        return True
    return _g6_live_match(lower, list(_G6_EXPOSURE_SIGN.finditer(lower)), _G6_SIGN_TOKEN)


#: G7 — «иное внезапное тяжёлое системное ухудшение» (:203). The weakest source: the
#: matrix gives no illustrative phrase for G7, and «плохо» is ambiguous in Russian. Rules
#: (main window, 15.09):
#:
#: * a bare «плохо» needs an intensifier («очень / резко / совсем»); «резко стало
#:   плохо», «сейчас упаду»;
#: * exceptions only right next to «плохо»: «плохо сделал(а) / видно / подходит /
#:   спал(а) / сплю / с деньгами» — never anywhere in the phrase;
#: * «мне / стало плохо» next to a bodily sign or a procedure («после укола», «сделали
#:   инъекцию») is caught regardless of any exception.
#:
#: Known miss by decision of the main window: «плохо себя чувствую» without context — a
#: reschedule for feeling unwell must not turn into «лучше к врачу».
_S1_G7_ADJACENT_EXCEPTION = r"(?!\s+(?:сделал\w*|видно|подход\w*|спал\w*|сплю|с\s+деньгами))"
_S1_BODY_SIGN = (
    r"(?:кружится\s+голова|голова\s+кружится|головокружени\w*|холодный\s+пот|в\s+пот\s+бросает"
    r"|темнеет\s+в\s+глазах|в\s+глазах\s+темнеет|падаю|сердце\w*|тряс[её]т|знобит)"
)
_S1_PROCEDURE = (
    r"(?:после\s+(?:процедур\w*|укол\w*|инъекци\w*|сеанс\w*)"
    r"|сделали\s+(?:укол|инъекци\w*|процедур\w*)|сделал\w*\s+(?:укол|инъекци\w*))"
)
_S1_G7_CO_SIGNAL = "(?:" + _S1_BODY_SIGN + "|" + _S1_PROCEDURE + ")"
_S1_BARE_PLOHO = r"\b(?:мне|стало|становится)\s+(?:(?:очень|резко|совсем|как-то)\s+)?плохо\b"
_S1_G7_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"\b(?:мне|стало|становится)\s+(?:очень|резко|совсем)\s+плохо\b"
        + _S1_G7_ADJACENT_EXCEPTION,
        re.IGNORECASE,
    ),
    re.compile(
        r"\bрезко\s+(?:стало\s+)?(?:очень\s+)?плохо\b" + _S1_G7_ADJACENT_EXCEPTION, re.IGNORECASE
    ),
    re.compile(r"\bсейчас\s+упаду\b", re.IGNORECASE),
    re.compile(
        _S1_BARE_PLOHO
        + r"[^.!?]{0,40}"
        + _S1_G7_CO_SIGNAL
        + "|"
        + _S1_G7_CO_SIGNAL
        + r"[^.!?]{0,40}"
        + _S1_BARE_PLOHO,
        re.IGNORECASE,
    ),
)

# One group per line, so a probe can take a whole group out with a one-line edit.
# G4 and G6 are not in this tuple: they are negation-aware and live in
# :func:`detect_g4` / :func:`detect_g6`.
_RED_FLAG_PATTERNS = (
    _RED_FLAG_PATTERNS + _S1_G2_PATTERNS + _S1_G3_PATTERNS + _S1_G5_PATTERNS + _S1_G7_PATTERNS
)


def classify(text: str) -> PainSignal:
    """Return the strongest pain signal in ``text``.

    Type-tolerant: non-``str`` returns :data:`PainSignal.NONE`. We never
    raise — bad upstream data is a router bug, not a classifier bug.

    No length cap (DRF-1996, S-1a). A 200-character cap used to return
    ``NONE`` before the red-flag scan, so a person who described an
    emergency in more words got no red flag at all (CLINICAL-F01). The S1
    detector validation report requires «отсутствие length cap / иных
    silent-miss механизмов» (clinical-review-001 F01 п. 2). The patterns are
    plain linear scans; message size is bounded by the channel.
    """
    if not isinstance(text, str):
        return PainSignal.NONE
    stripped = text.strip()
    if not stripped:
        return PainSignal.NONE

    # DRF-973 — the false-friend phrases are blanked ONCE and both tiers
    # read the masked text. Nothing in :data:`_NOT_PAIN` is a red flag,
    # so masking cannot hide one; scanning the same string with both
    # tiers is what keeps them from disagreeing about what was said.
    lower = _mask_not_pain(stripped.lower())

    # Red flags take priority — if the message contains a red-flag
    # pattern, the soft-pain path is shadowed.
    for pattern in _RED_FLAG_PATTERNS:
        if pattern.search(lower):
            return PainSignal.RED_FLAG
    # G6 ([OD-BOT §159]) and G4 ([OD-BOT §164]) — negation-aware, so functions, not
    # patterns. Order between them does not matter: both return RED_FLAG.
    if detect_g6(stripped):
        return PainSignal.RED_FLAG
    if detect_g4(stripped):
        return PainSignal.RED_FLAG

    for pattern in _PAIN_STEM_PATTERNS:
        if pattern.search(lower):
            return PainSignal.SOFT

    return PainSignal.NONE
