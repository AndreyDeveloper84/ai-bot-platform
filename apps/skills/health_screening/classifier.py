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
#: Boundary (clinical-review :400-413): numbness or weakness WITHOUT «внезапно / одна
#: сторона / речь» is S2, so «половина лица / тела» needs a neurological word next to
#: it, and «отнялась» needs a body part.
_S1_NEURO = r"(?:не\s+чувству\w*|онемел\w*|немеет|не\s+двига\w*|перекосил\w*|парализ\w*)"
_S1_G4_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        _S1_NEURO
        + r"[^.!?]{0,30}половин\w+\s+(?:лица|тела)|половин\w+\s+(?:лица|тела)[^.!?]{0,30}"
        + _S1_NEURO,
        re.IGNORECASE,
    ),
    re.compile(r"\bперекосил\w*\s+лиц\w*|\bлицо\s+перекосил\w*", re.IGNORECASE),
    re.compile(r"\b(?:речь|язык)\s+заплета\w*", re.IGNORECASE),
    re.compile(
        r"\bотнял(?:ась|ся|ись)\s+(?:рука|нога|руки|ноги|половин\w+|сторон\w+|лицо|язык)"
        r"|\b(?:рука|нога|руки|ноги)\s+отнял\w+",
        re.IGNORECASE,
    ),
    re.compile(r"\bвнезапн\w+\s+(?:слабость|онемени\w*)", re.IGNORECASE),
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
#: (:202); «после укола отекло горло, тяжело дышать» (:205); T-S1-11 (:286). A local rash
#: is not G6, so «сыпь» needs a swelling or breathing sign next to it.
_S1_SYSTEMIC = (
    r"(?:от[её]к\w*|отекл\w*|опух\w*|трудно\s+дышать|тяжело\s+дышать|задыха\w*|удушь\w*|горл\w*)"
)
_S1_G6_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bанафилакт\w*", re.IGNORECASE),
    re.compile(
        r"\b(?:отекл[оа]|отекает|опухл[оа]|опухает)\s+(?:горло|гортань|язык|губы)", re.IGNORECASE
    ),
    re.compile(r"\b(?:от[её]к|отек)\w*\s+(?:горла|гортани|языка|квинке)", re.IGNORECASE),
    re.compile(r"\bсыпь[^.!?]{0,40}" + _S1_SYSTEMIC, re.IGNORECASE),
)

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
_RED_FLAG_PATTERNS = (
    _RED_FLAG_PATTERNS
    + _S1_G2_PATTERNS
    + _S1_G3_PATTERNS
    + _S1_G4_PATTERNS
    + _S1_G5_PATTERNS
    + _S1_G6_PATTERNS
    + _S1_G7_PATTERNS
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

    for pattern in _PAIN_STEM_PATTERNS:
        if pattern.search(lower):
            return PainSignal.SOFT

    return PainSignal.NONE
