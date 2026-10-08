"""Safety pre-check (DRF-537 / Sprint 6 / O3).

Step 7 of the orchestrator pipeline. Inspects user text + intent decision
BEFORE invoking the skill; returns a :class:`SafetyVerdict` ∈
{allow, clarify, block, handoff}. Regex keyword guard — no LLM, <10ms p95.

### Verdicts

- **allow** — proceed to skill dispatch (step 10).
- **clarify** — message is ambiguous; reply with a generic clarification
  prompt, do NOT engage the skill (low-risk medical question, vague
  pronoun reference, etc.).
- **block** — refuse outright with a disclaimer (acute medical symptom,
  legal advice request, forbidden topic). Pipeline skips skill + tool
  layers, jumps to composer with a canned safety response.
- **handoff** — escalate to a human operator via AdminTask. Used when
  the regex hits a high-risk pattern (suicidal ideation, abuse, etc.)
  OR when intent_decision.risk_level=='high'.

### Pattern source

- Default patterns ship in :const:`_DEFAULT_PATTERNS` — Russian + English
  for medical / drug / acute / legal / suicidal / forbidden.
- Tenant overrides via ``settings.SAFETY_PATTERNS`` (merged on top of
  defaults — partial override doesn't wipe defaults).
- Per-tenant ``BrandVoiceConfig.forbidden_phrases`` is NOT consulted here
  (DRF-2608). Those are words Ayla must not SAY — they are checked on the
  reply by ``post_check`` → ``voice_check.validate_voice``. Blocking the
  PERSON's message for a word forbidden to Ayla broke the side of the
  conversation. The earlier design also used the list as an operator
  filter of rude client requests; that filter is not cancelled by us — it
  is put to the owner as a separate question (a separate list, if wanted).

### intent_decision integration

The router (O2) returns `risk_level` ∈ {low, medium, high}. Pre-check
elevates verdicts by risk:
- intent_decision.risk_level=='high' → at least 'handoff' regardless of regex
- intent_decision.risk_level=='medium' → blocked patterns become 'handoff'
- intent_decision.risk_level=='low' → normal regex verdicts

This lets the LLM context-aware risk assessment elevate borderline
cases that the regex alone might miss.

### Latency budget

PHASE0_DESIGN §5.2 budget: <10ms. Per-pattern compilation is cached at
module load. Test: 100 calls in <50ms.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from django.conf import settings

logger = logging.getLogger(__name__)


class SafetyVerdict(str, Enum):
    ALLOW = "allow"
    CLARIFY = "clarify"
    BLOCK = "block"
    #: DRF-2000 (S-2): медицинская неотложка — 103 / 112. Отдельно от
    #: ``HANDOFF`` (психологический кризис, телефон доверия): это два
    #: разных ответа человеку, и первой строкой у них стоят разные номера.
    MEDICAL = "medical"
    HANDOFF = "handoff"


@dataclass(frozen=True)
class SafetyResult:
    """Verdict + provenance: which pattern fired (forensic / observability)."""

    verdict: SafetyVerdict
    matched_patterns: list[str] = field(default_factory=list)
    reason: str = ""


# ── «умираю (как) хочу …»: hyperbole vs crisis (#1081 narrowing, DRF-2684) ──
#
# Bare «умираю» is a crisis signal; «Умираю, хочу кофе» is a figure of speech,
# common on a beauty bot — typed with a comma, and always punctuated when it
# comes from speech recognition. Until DRF-2684 the exemption was «хочу +
# anything» across whitespace only: «Умираю, хочу кофе.» got the crisis reply,
# and «умираю хочу просто умереть» passed.
#
# The exemption is now narrow on purpose. «умираю» is NOT a crisis only when
# all of this holds:
#
# * «(как) хочу» is followed by an everyday object of desire
#   (``_BENIGN_WANT``: food, sleep, rest, a service, «к вам»), with at most
#   three words from a closed list in between (``_WANT_FILLER``) — any other
#   word there, and the wish is not an everyday one;
# * no word of death, ending, farewell or hopelessness (``_DISTRESS_NEAR``)
#   stands within ``_UMIRAYU_WINDOW`` characters before «умираю» or after
#   «хочу»;
# * the message has the shape of a figure of speech: one «умираю»; nothing but
#   a greeting in the sentences before it; after its own sentence — only
#   questions («Умираю, хочу пиццу. Сколько в ней калорий?»).
#
# What this does and does not promise. A word missing from ``_BENIGN_WANT`` or
# ``_WANT_FILLER`` costs a false crisis reply — those two fail towards the
# stop. ``_DISTRESS_NEAR`` cannot: distress told in words it does not know,
# inside ONE sentence with an everyday wish («Умираю, хочу записаться на
# <unknown word>»), passes. The shape rule is what keeps the rest: a second
# statement or an earlier sentence takes the exemption away whatever words it
# uses. It reads sentence ends, so it works on typed text only — the
# punctuation-free copy of a voice transcript (``voice_turn.strip_for_gate``)
# has none and is judged by the two lists alone. That is where voice was before
# this change, not a new gap (DRF-2922 is the way to close it).
#
# ``_GAP`` is whitespace plus every mark ``strip_for_gate`` removes: marks
# BETWEEN the words of the phrase itself never change the verdict. ``_GAP_IN``
# leaves the sentence enders out for «умираю от …»: «умираю, от боли» is the
# body (MEDICAL) like the unpunctuated form, while typed «Умираю. От меня все
# отвернулись.» stays with this rule.
_GAP = r"[\s,.!?;:…\"'’«»“”„()\[\]{}<>—–-]+"
_GAP_IN = r"[\s,;:\"'’«»“”„()\[\]{}<>—–-]+"
_SENTENCE_END = r".!?…\n"
_UMIRAYU_WINDOW = 120

_BENIGN_WANT = (
    # Food and drink.
    r"(кофе\w*|ча[йюя]|чайку|есть|поесть|кушать|покушать|жрать|пожрать|пить|попить|воды|водички"
    r"|сладк\w+|шоколад\w*|пицц\w+|суши|ролл\w+|морожен\w+|торт\w*|пирожн\w+|конфет\w*|булочк\w+"
    r"|бургер\w*|мяс[оа]|картошк\w+|пельмен\w+|шаурм\w+|вин[оа]|пив[оа]"
    # Sleep, rest, everyday.
    r"|спать|поспать|выспаться|отдохнуть|отдыхать|отпуск\w*|море|моря|домой|бан[юи]|сауну|душ|ванну"
    r"|туалет|курить|покурить|похудеть|каникул\w*|полежать|поваляться"
    # Services and booking. «записать завещание» is not a booking.
    r"|маникюр\w*|педикюр\w*|массаж\w*|стрижк\w+|подстричь\w*|постричь\w*|покрас\w+|окрас\w+"
    r"|перекрас\w+|подкрас\w+|корни"
    r"|окрашиван\w+|бров\w+|ресниц\w+|реснич\w+|ногт\w+|ноготочк\w+|волос\w*|кончик\w+|уклад\w+"
    r"|прич[её]ск\w+|макияж\w*|эпиляц\w+|депиляц\w+|шугаринг\w*|спа|чистк\w+|пилинг\w*|гель-?лак\w*"
    r"|покрыти\w+|наращиван\w+|нараст\w+|ламинирован\w+|кератин\w*|загар\w*|солярий\w*"
    rf"|записаться|запись|запиш\w+|к{_GAP}вам|мастер\w*)"
)

# Words that may stand between «хочу» and the everyday object. A closed list:
# with any two words allowed here, «хочу сброситься, есть крыша» read as «хочу
# есть».
_WANT_FILLER = (
    r"(?:в|во|на|к|ко|с|со|у|за|по|до|и|а|же|бы|уже|ещё|еще|очень|так|просто|прямо|срочно|снова"
    r"|опять|наконец|поскорее|скорее|сейчас|сегодня|завтра|этот|эту|это|эти|этого|тот|ту"
    r"|так(?:ой|ую|ое|ие|ого)|ваш\w*|твой|твою|нов\w+|свеж\w+|горяч\w+|холодн\w+|больш\w+|вкусн\w+"
    r"|красив\w+|нормальн\w+|хорош\w+|фирменн\w+|чашк\w+|чашечк\w+|кус(?:ок|очек)|чего|что|нибудь"
    r"|то|сделать|обновить|снять|поправить|покрыть|попробовать|сходить|пойти|поехать|попасть"
    r"|прийти|приехать|лечь|убрать|подровнять|эти|этих|отросш\w+|секущ\w+|немного|бокал\w*)"
)

# «не проснуться до обеда» and «не могу больше ждать» are everyday speech.
_NOT_ABOUT_TIME = rf"(?!{_GAP}(до|к|в|на|по|вовремя|утром|рано)\b)"
_NOT_WAITING = rf"(?!{_GAP}(ждать|терпеть|без)\b)"
_DISTRESS_WORDS = (
    # Death and non-being.
    r"(умер(?!ен)\w*|умира(?!ю\b)\w*|умр\w+|сдох\w*|подох\w*|помер(еть|ла|ли)?\b|помр\w+|погиб\w*"
    r"|исчез\w*|пропа(сть|ду)\b|смерт(?!ельн)\w*|\bуби(ть|ться|л\w*|йств\w*|ва\w*)|убь\w+"
    r"|похорон\w*|эвтаназ\w*"
    rf"|не{_GAP}(жить|существовать|дышать|чувствовать|родит\w+|рожда\w+|встав\w+)\b"
    rf"|не{_GAP}(вернусь|наступ\w+|будите|выдерж\w+)"
    rf"|не{_GAP}(проснуться|проснусь|просыпаться)\b{_NOT_ABOUT_TIME}"
    rf"|не{_GAP}хоч\w*{_GAP}(просыпаться|проснуться)\b{_NOT_ABOUT_TIME}"
    rf"|меня{_GAP}(\w+{_GAP}){{0,2}}"
    rf"не{_GAP}(было|стало|будет|станет|существ\w+|наш\w+|найд\w+|спас\w+|откач\w+)"
    rf"|не{_GAP}(было|стало|будет|станет){_GAP}меня|без{_GAP}меня|забер\w+{_GAP}меня"
    # Life and its end.
    rf"|(из|от|с){_GAP}(\w+{_GAP})?жизн\w+"
    rf"|жизн\w+{_GAP}(\w+{_GAP})?((за|о)?конч|надоел|оборв|бессмысл|не{_GAP}(имеет|нужн|мил))"
    r"|(?<!в\s)смысл\w*|незачем"
    rf"|(вс[её]|это|этим|всем){_GAP}(\w+{_GAP})?(за|о|по)?конч\w+"
    rf"|(за|о|по)?конч\w+{_GAP}(вс[её]|это\b|с{_GAP}эт|со{_GAP}вс)"
    r"|\bкончено\b|приконч\w*"
    rf"|(?<!под\s)\bконец\b(?!{_GAP}(дня|недел|месяц|год|смен|рабоч))"
    rf"|прекрат\w*{_GAP}(вс[её]|это\b|жить|существ\w+|мучен\w+|страдан\w+)"
    rf"|перест\w+{_GAP}(жить|быть|существ\w+|дыш\w+|мучи\w+|страда\w+|чувств\w+)|отмуч\w*"
    # Leaving, sleep «for good», the other world.
    r"|(уйти|уйду|уснуть|заснуть|усну|засну|спать|поспать|отдохнуть|лечь)"
    rf"{_GAP}(навсегда|насовсем|вечн\w+)"
    rf"|от{_GAP}(всего|всех|себя)\b"
    rf"|(этот|этого|тот){_GAP}(мир|свет)|на{_GAP}неб|в{_GAP}(могил|гроб|петл|ра[йю]\b)"
    rf"|под{_GAP}земл\w+|к{_GAP}богу"
    # Farewell.
    r"|попрощ|прощан\w+|прост(ить|ит)ся|прощай|напоследок|записк\w*|завещан\w*"
    rf"|прости(те)?{_GAP}меня"
    rf"|(мо[йяеё]|мои){_GAP}последн\w+"
    rf"|последн\w+{_GAP}(шаг|день|вечер|ноч\w+|вздох|сообщени\w+|слов\w+)"
    # Methods.
    rf"|с{_GAP}собой{_GAP}(что|сдел)|(что|чего)-?(то|нибудь){_GAP}с{_GAP}собой"
    rf"|под{_GAP}(поезд|машин|кол[её]с)|из{_GAP}окна"
    rf"|(выйти|шагн\w*|прыгн\w*|выпрыгн\w*|выброс\w*){_GAP}(\w+{_GAP})?(в|из){_GAP}окн"
    rf"|с{_GAP}(крыш|мост|балкон)"
    r"|застрел|отрав|утоп|повес(ит|ил|ь)\w*|повеш\w*|задуш|удав(ит|л|к)\w*|таблет|снотворн"
    r"|вен[ыу]\b|вер[её]вк\w*|лезви\w*|\bяд[ау]?\b"
    # Hopelessness.
    rf"|больше{_GAP}не{_GAP}могу\b{_NOT_WAITING}|не{_GAP}могу{_GAP}(больше|так)\b{_NOT_WAITING}"
    rf"|(нет|нету){_GAP}(больше{_GAP})?сил|сил{_GAP}(больше{_GAP}|уже{_GAP})?(нет|нету)"
    rf"|никому{_GAP}(я{_GAP})?не{_GAP}нуж|не{_GAP}вижу{_GAP}выхода|выхода{_GAP}нет|нет{_GAP}выхода"
    rf"|вс[её]{_GAP}решил|обуз\w*|никч[её]мн\w*|ненавижу{_GAP}себя"
    rf"|(очень|так){_GAP}плохо|плохо{_GAP}мне|мне{_GAP}плохо)"
)
# The list above is written with plain ``\w*`` / ``\w+`` to stay readable. An
# unbounded tail backtracks letter by letter under the window that follows it —
# seconds on a hostile 4 000 characters — so the tails are made bounded and
# possessive here; no word form needs more than 30 letters.
_DISTRESS_NEAR = _DISTRESS_WORDS.replace(r"\w*", r"\w{0,30}+").replace(r"\w+", r"\w{1,30}+")

# The rest of the message after the everyday object: its own sentence, then
# nothing but questions. A text without sentence ends satisfies it trivially.
_ONLY_QUESTIONS_FOLLOW = (
    rf"[^{_SENTENCE_END}]*+[{_SENTENCE_END}\s]*+"
    rf"(?:[^{_SENTENCE_END}]++\?[{_SENTENCE_END}\s]*+)*+\Z"
)
_UMIRAYU_HYPERBOLE = (
    rf"{_GAP}(?:(?:как(?:{_GAP}же)?|так){_GAP})?хочу\b"
    rf"(?!.{{0,{_UMIRAYU_WINDOW}}}{_DISTRESS_NEAR})"
    rf"{_GAP}(?:{_WANT_FILLER}{_GAP}){{0,3}}{_BENIGN_WANT}\b{_ONLY_QUESTIONS_FOLLOW}"
)
_UMIRAYU_NOT_OF = rf"(?!{_GAP_IN}от\b)"
_UMIRAYU = rf"\bумираю\b{_UMIRAYU_NOT_OF}"
# A sentence before the first «умираю» that is more than a greeting.
_GREETING = (
    r"[\W_]*+(?:(?:привет\w*|здравствуй\w*|добр\w+\s+(?:день|вечер|утро)|девочки|девчонки"
    r"|ой|ох|ай|блин|слушайте|я)[\W_]*+)*+"
)
_SENTENCE_BEFORE_UMIRAYU = rf"(?!{_GREETING}\bумираю\b)(?:(?!\bумираю\b)[^{_SENTENCE_END}])*+[{_SENTENCE_END}].*?{_UMIRAYU}"
# ``\A(?=.*…)`` is a gate, not a condition: a text with neither word leaves the
# rule after one pass instead of trying ``_DISTRESS_NEAR`` at every position.
# ``s`` lets the windows cross a line break. A second «умираю» is tried first:
# a repeated word is not a figure of speech, and a hostile text made of it must
# not be weighed occurrence by occurrence.
_UMIRAYU_CRISIS = (
    r"(?is)\A(?=.*(?:умираю|dying))(?:"
    rf".*?{_UMIRAYU}.*?{_UMIRAYU}"
    rf"|{_SENTENCE_BEFORE_UMIRAYU}"
    r"|.*?(?:"
    rf"\bумираю\b(?!{_UMIRAYU_HYPERBOLE}){_UMIRAYU_NOT_OF}"
    rf"|{_DISTRESS_NEAR}.{{0,{_UMIRAYU_WINDOW}}}{_UMIRAYU}"
    r"|\bdying\b))"
)


# Pinned default safety patterns. Tenant overrides MERGE on top via settings.
# Verdicts encode the action — patterns map to specific verdicts.
_DEFAULT_PATTERNS: dict[str, list[str]] = {
    # ── Suicidal ideation / self-harm (#1081) ─────────────────────────────
    # Coverage-first (founder 2026-07-03): a MISS is a safety failure; a
    # false-positive crisis reply is only a UX cost on a beauty marketplace.
    # This is the LIVE, SOLE net (no LLM fallback), so patterns lean toward
    # recall. Russian morphology via \w* stems; reflexive `себя`/`себе` matched
    # in BOTH word orders (Russian reorders freely — «убью себя» AND «себя
    # убью»). EN verb stems keep a leading \b so «send it» / «change myself» /
    # «haircut myself» don't false-trigger. Known accepted over-triggers
    # («убьюсь если…», «cut myself shaving») are in the PR #1081 review table;
    # «умираю как хочу…» has its own block above (DRF-2684).
    SafetyVerdict.HANDOFF.value: [
        # RU — base stems + idioms «наложить на себя руки» / «счёты с жизнью».
        r"(?i)(\bсамоубийств|\bсуицид|налож\w*\s+на\s+себя\s+руки|сч[её]ты\s+с\s+жизнью)",
        # RU — kill-self, either word order.
        r"(?i)(убить\s+себя|себя\s+убить|убь[юё]\s+себя|себя\s+убь\w*|\bубьюсь\b"
        r"|поконч\w*\s+(с\s+собой|со\s+всем))",
        # RU — want-to-die / not-want-to-live / tired-of-life.
        r"(?i)(хоч\w*\s+(умереть|умирать|сдохнуть|исчезнуть)|лучше\s+(мне\s+)?умереть"
        r"|не\s+хоч\w*\s+(больше\s+)?(жить|существовать)|жить\s+(больше\s+)?не\s+хоч"
        r"|надоело\s+жить|устал\w*\s+(жить|от\s+жизни))",
        # RU — no-meaning-in-life (both «нет смысла жить» and «смысла в жизни нет»).
        r"(?i)((нет|не\s+вижу|какой)\s+смысла?\s+(в\s+)?жи(ть|зни)|зачем\s+(мне\s+)?жить"
        r"|жизнь\s+не\s+имеет\s+смысла|смысла?\s+в\s+жизни\s+нет|в\s+жизни\s+нет\s+смысла)",
        # RU — burden / better-off-gone ideation.
        r"(?i)(лучше\s+бы\s+(я\s+(не\s+жил|не\s+родил|умер)|меня\s+не\s+было)"
        r"|(всем\s+)?(будет\s+)?лучше\s+без\s+меня|без\s+меня\s+(всем\s+)?лучше)",
        # RU — self-harm acts, either word order.
        r"(?i)((реж|рез|порез)\w*\s+себя|себя\s+(реж|рез|порез)\w*"
        r"|причин\w*\s+себе\s+(вред|боль)|себе\s+(вред|боль|больно|причин\w*|навред\w*)"
        r"|навред\w*\s+себе)",
        # RU — method statements. Reflexive endings vary (вскроюСЬ / вскрытьСЯ).
        r"(?i)(вскр\w*\s+вены|(реж|рез)\w*\s+вены|вскр(о|ы)\w*с[ья]|повеш\w*сь|повеси\w*ся"
        r"|наглота\w*\s+таблеток|горсть\s+таблеток|таблеток\s+и\s+усну"
        r"|(прыгн|спрыгн|шагн)\w*\s+с\s+(крыши|моста|балкона|окна)|исчезнуть\s+навсегда)",
        # EN — want-to-die / not-want-to-live / no-point.
        r"(?i)(wan(t\w*|na)\s+(to\s+)?die|(don'?t|do\s+not)\s+want\s+to\s+(live|be\s+alive)"
        r"|no\s+reason\s+to\s+live|no\s+point\s+(in\s+)?living|nothing\s+to\s+live\s+for"
        r"|better\s+off\s+dead)",
        # EN — method / self-harm verbs (leading \b guards against send/change/haircut).
        r"(?i)(\bkill\w*\s+my\s?self|\bhang\w*\s+my\s?self|\b(hurt|harm|cut)\w*\s+my\s?self"
        r"|\bslit\w*\s+my\s+wrist|take\s+my\s+own\s+life|\bend\s+(my\s+life|it(\s+all)?)\b"
        r"|\bsuicid|self[\s-]?harm|\boverdos)",
        # Bare «умираю» stays here (crisis): without «от боли / скорую /
        # сердце» it reads as despair as often as as a body. The hyperbole
        # «умираю (как) хочу <everyday thing>» is exempt — see the block
        # above ``_GAP`` (DRF-2684); the physical «умираю от …» moved to
        # MEDICAL (DRF-2000) together with the rest of the acute-emergency
        # group.
        _UMIRAYU_CRISIS,
        # Abuse / domestic violence — stems
        r"(?i)(\bизбива|\bнасили|\babuse\b|\bbattered\b)",
    ],
    # DRF-2000 (S-2) — the «неотложка» group, owner ruling 20.09 (ticket
    # comment) on top of [OD-BOT §163]: a heart attack, a call for an
    # ambulance, «не могу дышать / теряю сознание / давит в груди». The
    # reply is the medical emergency text (103 / 112 first line), never the
    # psychological helpline. Until DRF-2000 the cardiac phrases sat in the
    # HANDOFF list and got the crisis text; «скорая» matched only the
    # nominative, so «вызовите скорую» went to the model.
    SafetyVerdict.MEDICAL.value: [
        # Cardiac / ambulance / dying of pain — all inflections of «скорая».
        r"(?i)(сердечн\w*\s+приступ|heart\s+attack|\bинфаркт|\bинсульт"
        rf"|\bскор(ая|ую|ой|ые)\b(\s+помощ\w*)?|\bemergency\b|\bумираю{_GAP_IN}от\b)",
        # Breathing / consciousness / chest — with the same emotional-idiom
        # exception the health_screening classifier keeps («задыхаюсь от
        # смеха», «потеряла сознание от восторга» are not emergencies; S-1b).
        r"(?i)(не\s+могу\s+(в)?дышать|нечем\s+дышать|\bудушь\w*"
        r"|\bзадыха\w*+(?!\s+от\s+(смеха|хохота|восторга|счастья|радости)))",
        r"(?i)((теря|потеря)\w*\s+сознани\w*+(?!\s+от\s+(смеха|хохота|восторга|счастья|радости))"
        r"|без\s+сознания|\bобморок\w*)",
        r"(?i)((давит|сдавил\w*|жж[её]т|боль)\s+(в\s+)?груд\w*|груд\w*\s+давит)",
    ],
    SafetyVerdict.BLOCK.value: [
        # Owner decision 11.09 §3, verbatim: «Простое упоминание лекарства не
        # является автоматическим STOP; запрос подобрать препарат, дозировку
        # или схему — STOP». Until 11.09 the bare word was enough — «вчера
        # выпила ибупрофен, можно на массаж?» was refused as if it asked for
        # a prescription. Two lookaheads, order-free: the message must ASK
        # (pick / advise / dose / how to take / what to take) AND name a
        # drug or a drug noun. «подберите обезболивающее» is STOP with no
        # brand named; «принимаю парацетамол, это помешает?» is not. Bare
        # mention is NORMAL, not CAUTION: CAUTION has 0 rules by §126 and
        # giving it its first one is the owner's act, not this patch's.
        r"(?is)(?=.*\b(подбер\w*|подобра\w*|посовет\w*|порекоменд\w*|назнач\w*"
        r"|дай(те)?|выпиш\w*|пропиш\w*|дозир\w*|доз[ауы]"
        r"|сколько\s+(таблет\w*|мг|миллиграм\w*|раз\s+в\s+день)"
        r"|схем\w*\s+(при[её]ма|лечения)|как\s+(принимать|пить|колоть)"
        r"|что\s+(принять|выпить|попить|поколоть)"
        r"|как[ойуюие]+\s+(препарат\w*|лекарств\w*|таблет\w*|обезболивающ\w*)"
        r"|recommend|prescribe|dosage|how\s+much|what\s+to\s+take|should\s+i\s+take)\b)"
        r"(?=.*\b(ибупрофен\w*|анальгин\w*|парацетамол\w*|кеторол\w*|tramadol|opioid\w*"
        r"|препарат\w*|лекарств\w*|таблет\w*|обезболивающ\w*|антибиотик\w*"
        r"|painkiller\w*|medication\w*|pills?)\b)",
        # Diagnosis requests with definitive words
        # DRF-2728 — «поставь» рядом с «поставьте» и слово-два между глаголом и
        # «диагноз» («поставь мне диагноз», «поставьте мне, пожалуйста, диагноз»):
        # ловилась одна форма из тех, какими это на самом деле говорят.
        r"(?i)\b(постав(?:ь|ьте)\s+(?:мне\s*,?\s*)?(?:пожалуйста\s*,?\s*)?диагноз|diagnose me"
        r"|у меня (рак|онколог))",
        # Legal advice
        r"(?i)\b(подать в суд|sue them|lawsuit|юридическ.{1,3} совет)\b",
    ],
    SafetyVerdict.CLARIFY.value: [
        # Vague medical questions (need handoff suggestion, not block)
        r"(?i)\b(что у меня|what's wrong with me|почему болит)\b",
    ],
}


# Compiled patterns cache. Key: pattern source string. Value: compiled regex.
_COMPILED: dict[str, re.Pattern[str]] = {}


def _compile(pattern: str) -> re.Pattern[str] | None:
    """Return cached compiled regex or None on bad pattern."""

    cached = _COMPILED.get(pattern)
    if cached is not None:
        return cached
    try:
        compiled = re.compile(pattern)
    except re.error:
        logger.warning("safety.pre_check.bad_regex pattern=%r", pattern)
        return None
    _COMPILED[pattern] = compiled
    return compiled


def reset_cache() -> None:
    """Test hook — flush the compiled-regex cache."""

    _COMPILED.clear()


def _verdict_patterns() -> dict[str, list[str]]:
    """Merge settings overrides on top of defaults.

    Tenant adds custom patterns via ``settings.SAFETY_PATTERNS = {verdict: [patterns]}``.
    For each verdict, the override list is APPENDED to defaults — operators
    extend safety, never weaken it.
    """

    cfg = getattr(settings, "SAFETY_PATTERNS", None) or {}
    merged = {k: list(v) for k, v in _DEFAULT_PATTERNS.items()}
    for verdict, patterns in cfg.items():
        if not isinstance(patterns, list):
            continue
        merged.setdefault(verdict, []).extend(patterns)
    return merged


def pre_check(
    text: str,
    intent_decision: Any | None = None,
    *,
    brand_voice: dict | None = None,
) -> SafetyResult:
    """Inspect inbound text + intent decision → :class:`SafetyResult`.

    Args:
      text: user message.
      intent_decision: optional :class:`IntentDecision` (O2). If risk_level=='high',
        verdict is elevated to at least 'handoff'.
      brand_voice: accepted for call compatibility and NOT used on the
        input (DRF-2608): ``forbidden_phrases`` of the brand are words Ayla
        must not say and are checked on the reply (``post_check``). An
        input filter of client requests, if the owner wants one, is a
        separate list — not this one.

    Returns:
      :class:`SafetyResult`. Default verdict is ALLOW. Empty text → ALLOW.

    ### Verdict priority (highest wins)
    HANDOFF > MEDICAL > BLOCK > CLARIFY > ALLOW

    Multiple matches across verdicts: highest-priority verdict wins, but
    `matched_patterns` carries ALL matches (forensic). Cross-tenant
    operators see the full picture in the safety_triggered audit row
    Sprint 4 emits.
    """

    if not text:
        return SafetyResult(verdict=SafetyVerdict.ALLOW)

    matched: list[str] = []
    triggered_verdicts: set[str] = set()

    for verdict, patterns in _verdict_patterns().items():
        for pattern in patterns:
            compiled = _compile(pattern)
            if compiled is None:
                continue
            if compiled.search(text):
                matched.append(pattern)
                triggered_verdicts.add(verdict)

    # DRF-2608: brand ``forbidden_phrases`` are NOT applied to the person's
    # input — they belong to the reply side (``post_check``). ``brand_voice``
    # stays in the signature for callers; see the docstring.
    del brand_voice

    # Map intent_decision.risk_level into the verdict elevation.
    elevation = _risk_elevation(intent_decision)
    if elevation:
        triggered_verdicts.add(elevation)

    # Reduce to highest-priority verdict.
    final = _reduce_verdict(triggered_verdicts)
    return SafetyResult(
        verdict=final,
        matched_patterns=matched,
        reason=_reason(triggered_verdicts, matched),
    )


# Verdict priority — higher index = higher priority.
# HANDOFF > MEDICAL > BLOCK > CLARIFY > ALLOW. Self-harm outranks the medical
# group on purpose: the crisis text already names 112, and a person who
# writes both must not lose the helpline (DRF-2000).
_VERDICT_PRIORITY = [
    SafetyVerdict.ALLOW.value,
    SafetyVerdict.CLARIFY.value,
    SafetyVerdict.BLOCK.value,
    SafetyVerdict.MEDICAL.value,
    SafetyVerdict.HANDOFF.value,
]


def _reduce_verdict(verdicts: set[str]) -> SafetyVerdict:
    """Pick the highest-priority verdict that fired. Defaults to ALLOW."""

    if not verdicts:
        return SafetyVerdict.ALLOW
    highest = max(
        verdicts, key=lambda v: _VERDICT_PRIORITY.index(v) if v in _VERDICT_PRIORITY else -1
    )
    return SafetyVerdict(highest)


def _risk_elevation(intent_decision: Any | None) -> str | None:
    """Translate intent_decision.risk_level into a verdict elevation.

    high → HANDOFF (operator must see anything LLM flagged as high-risk).
    medium → BLOCK (do not let the bot proceed; canned disclaimer).
    low / None → no elevation.
    """

    if intent_decision is None:
        return None
    risk = getattr(intent_decision, "risk_level", None)
    if risk == "high":
        return SafetyVerdict.HANDOFF.value
    if risk == "medium":
        return SafetyVerdict.BLOCK.value
    return None


def _reason(verdicts: set[str], matched: list[str]) -> str:
    """Human-readable reason for the verdict, used in events / audit."""

    if not verdicts:
        return ""
    if matched:
        return f"matched={len(matched)} verdicts={sorted(verdicts)}"
    return f"risk_elevation verdicts={sorted(verdicts)}"
