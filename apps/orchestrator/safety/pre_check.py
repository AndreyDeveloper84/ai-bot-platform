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


# ── DRF-2920: pieces of the direct-crisis HANDOFF entries below ──────────────
#
# A word with a hyphen inside («наконец-то», «как-то») — ``\w+`` breaks on it.
_W = r"[\w-]+"
_DIE = r"(умереть|сдохнуть|подохнуть|помереть)"
# After the perfective «не проснуться / не проснусь» only an explicit wake-up
# time is everyday speech («не проснуться вовремя», «к восьми»). «завтра /
# утром» are NOT excluded: «уснуть и не проснуться завтра» is the crisis.
_NOT_WAKE_PERF = r"(?!\s+((вовремя|к|ко|до|по|рано|раньше|от|из-за|без)\b|в\s+\d|во\s+сколько))"
# The same after «не хочу проснуться», plus «с прыщами / с отёками».
_NOT_WAKE_PERF_WITH = (
    r"(?!\s+((вовремя|с|со|к|ко|до|по|рано|раньше|от|из-за|без)\b|в\s+\d|во\s+сколько))"
)
# After the imperfective «не просыпаться»: sleep goals of the Mini App and
# everyday complaints («с отёками», «уставшей», «ночью», «ради работы»).
_NOT_WAKE_IMPF = (
    r"(?!\s+((вовремя|к|ко|в|во|до|по|рано|раньше|завтра|сегодня|утром|так|без|с|со|от"
    r"|ради|ни|ночью|среди|после|из-за|уставш\w*|разбит\w*|невыспав\w*|с\s+утра)\b|в\s+\d))"
)
# After «чтобы меня не было» only a preposition with an everyday place («в
# (вашей) рассылке», «на смене», «на фото»). A bare preposition or «дома» is
# not enough: in the punctuation-free copy of a voice transcript it may start
# the NEXT sentence («…не было. Дома никто не ждёт»).
_NOT_PLACE = (
    r"(?!\s+((в|во|на|из|у)\s+([\w-]+\s+)?(списк|график|смен|работ|запис|сайт|ватсап"
    r"|whatsapp|телеграм|выходн|рассылк|баз|чат|групп|салон|кадр|фото|видео|отзыв"
    r"|расписан|журнал|систем|приложени|профил|контакт|очеред|сети|соцсет|сторис|истори"
    r"|телефон|звонк)\w*|видно|слышно|онлайн)\b)"
)


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
    # («умираю как хочу…», «убьюсь если…», «cut myself shaving») are in the PR
    # #1081 review table.
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
        # ── DRF-2920: direct statements the entries above did not know ───
        # «хочу умереть» above takes the two words side by side only;
        # «хочу просто умереть», «уйти из жизни», «не проснуться», «выпить
        # все таблетки» passed. Accepted over-triggers of these three entries
        # are listed in the DRF-2920 PR (pills on a schedule, «уйти из жизни
        # соцсетей», «хочу на тот свет… шучу»). Typed text with a mark inside
        # the phrase («хочу, просто умереть») is DRF-2922.
        #
        # RU — wanting death, not wanting to live, cannot live like this.
        r"(?i)("
        r"(\bхоч\w*|\bохота|\bхотел\w*\s+бы|\bмечта\w*|\bсобира\w*|\bрешил\w*|\bпора"
        r"|\b(по)?скор(ее|ей)\s+бы|\bмне\s+бы|\b(вот|хоть|лишь)\s+бы)"
        rf"\s+({_W}\s+){{0,2}}?(взять\s+и\s+)?"
        rf"({_DIE}|исчезнуть\b(?!\s+(из|с|со|на|в|во|до|от)\b))\b"
        rf"|({_DIE}|исчезнуть)\s+({_W}\s+)?(хоч|охота)|{_DIE}\s+бы\b"
        r"|лучше\s+(бы?\s+)?(мне\s+|я\s+)?"
        r"(сдохнуть|подохнуть|помереть|сдох\w*|умерл\w*|не\s+рожда\w*)"
        r"|\bхоч\w*\s+чтоб(ы)?\s+я\s+(умер|сдох)\w*"
        rf"|(\bхоч\w*|хочется)\s+({_W}\s+)?не\s+(жить|существовать)\b"
        r"(?![\s,]+((на|в|во|с|у|без|по|от|для|ради|как|так)\b"
        r"|а\s+(жить|отдыха|наслажда|радова|кайфова)))"
        r"|перестать\s+существовать\b"
        r"|\bне\s*охот[аы]\s+(больше\s+)?жить\b(?!\s+(без|в|во|на|с|по)\b)"
        r"|жить\s+(больше\s+)?не\s*охот"
        r"|\bнехоч\w*\s+(больше\s+)?(жить|существовать)\b"
        r"|\bне\s*хоч\w*\s+((больше|так|уже|вообще|дальше)\s+)*ж[иы]ть\b"
        # «…жить в этом мире» is the crisis, «жить в этом городе» is not.
        r"(?!\s+(без|в|во|на|с|по)\b(?!\s+(этом|том)\s+(свете|мире)))"
        r"|ж[иы]ть\s+((больше|уже|так|вообще)\s+)+не\s*хоч"
        r"|\bне\s*хоч\w*\s+(больше\s+)?быть\s+жив\w*"
        r"|нет\s+сил\s+(больше\s+)?жить\b(?!\s+(в|во|на|без|с|по|от)\b)"
        r"|не\s+могу\s+(больше\s+|так\s+)+жить\b(?!\s+без\b)"
        r"|(больше|так)\s+((больше|так)\s+)?не\s+могу\s+((больше|так)\s+)?жить\b(?!\s+без\b)"
        # ``{1,2}``, not ``+``: «так так так …» made it backtrack quadratically.
        r"|жить\s+((так|больше)\s+)+не\s+могу|((так|больше)\s+){1,2}жить\s+не\s+могу"
        r"|жить\s+(мне\s+)?незачем|(не)?зачем\s+(вообще\s+|больше\s+|дальше\s+)жить\b"
        r"|не\s+(для|ради)\s+(чего|кого)\s+(мне\s+)?жить"
        r"|(жить|жизнь)\s+(мне\s+)?(надоел[оа]|осточертел[оа]|опостылел[оа])"
        r"|(легче|проще|спокойнее)\s+без\s+меня"
        r"|без\s+меня\s+(всем\s+|вам\s+)?(будет\s+)?(легче|проще)"
        # «совсем покончила с вредной едой» is the nutritionist's, not a crisis.
        r"|\bсо\s*всем\s+(этим\s+)?поконч\w*\b(?!\s+(с|со)\b)"
        r"|\bпоконч\w*\s+(совсем\b(?!\s+(с|со)\b)|с\s+жизнью\b)"
        # «(я) больше не могу (так)» — only as the WHOLE message: at the tail
        # of a complaint («мастер опаздывает, больше не могу») it is a sigh.
        r"|\A\W*((вс[её]|ну)\W+)?(я\s+)?(так\s+|уже\s+|просто\s+|правда\s+|реально\s+)?"
        r"(больше\s+(так\s+)?не\s+могу(\s+так)?|не\s+могу\s+(так\s+)?больше)\W*\Z"
        r")",
        # RU — leaving life, sleeping without waking, «чтобы меня не было».
        r"(?i)("
        rf"(\bуйти|\bуйду|\bухожу|\bуходить)\s+({_W}\s+)?из\s*(этой\s+)?жизни"
        r"|покин(уть|у)\s+(этот\s+)?(мир|свет)\b"
        r"|(\bхоч\w*|\bпора|\bотправ\w*|\bуйти|\bуйду|\bсобира\w*|\bмне|\bменя)"
        rf"\s+({_W}\s+)?на\s+тот\s+свет\b"
        rf"|(уснуть|заснуть|\bусну|\bзасну|\bлечь|\bлягу)\s+({_W}\s+){{0,2}}?(и|бы|чтобы|чтоб)\s+"
        rf"((больше|уже|никогда|просто|вообще)\s+)*не\s+(проснуться|проснусь)\b{_NOT_WAKE_PERF}"
        rf"|(уснуть|заснуть|\bусну|\bзасну|\bлечь|\bлягу)\s+({_W}\s+){{0,2}}?(и|бы|чтобы|чтоб)\s+"
        r"((больше|уже)\s+)*"
        r"(никогда\s+не\s+(просыпаться|вставать)|больше\s+не\s+(вставать|встану))\b"
        rf"|(уснуть|заснуть|\bусну|\bзасну|\bлечь|\bлягу)\s+({_W}\s+){{0,2}}?(и|бы|чтобы|чтоб)\s+"
        rf"(больше|уже)\s+не\s+просыпаться\b{_NOT_WAKE_IMPF}"
        r"|(уснуть|заснуть|\bусну|\bзасну)\s+(бы\s+)?(навсегда|навечно|вечным\s+сном)"
        r"|вечным\s+сном"
        r"|(\bхоч\w*|хочется|\bхотел\w*\s+бы|\bмечта\w*|\b(вот|хоть|лишь)\s+бы)"
        rf"\s+({_W}\s+)?не\s+проснуться\b{_NOT_WAKE_PERF}"
        rf"|(\bхоч\w*|хочется)\s+({_W}\s+)?не\s+просыпаться\b{_NOT_WAKE_IMPF}"
        rf"|\bне\s*хоч\w*\s+(больше\s+|вообще\s+|никогда\s+)?просыпаться\b{_NOT_WAKE_IMPF}"
        r"|\bне\s*хоч\w*\s+((больше|вообще|никогда|завтра|утром)\s+)?проснуться\b"
        rf"{_NOT_WAKE_PERF_WITH}"
        r"|чтоб(ы)?\s+меня\s+(больше\s+|просто\s+|никогда\s+|вообще\s+|совсем\s+)*"
        rf"не\s+(было|стало|существовало)\b{_NOT_PLACE}"
        # «меня не станет» — not «у меня не станет денег», not «мастер меня
        # не станет ждать» (an infinitive follows).
        rf"|(?<!\bу\s)\bменя\s+({_W}\s+)?не\s+станет\b"
        r"(?!\s+((никто|уже|даже|больше|просто|вообще)\s+)?"
        r"[\w-]+(ать|ять|ить|еть|уть|ыть|оть|ться|тись)\b)"
        r")",
        # RU — a method: with intent, in the first person, jumping, pills.
        # Every stem starts at ``\b``: inside another word («знаете» → «нае»)
        # it both misfired and backtracked quadratically on a long text.
        r"(?i)("
        rf"(\bхоч\w*|\bсобира\w*|\bдумаю|\bрешил\w*|\bпойду)\s+({_W}\s+)?"
        r"(застрелиться|отравиться|утопиться|удавиться|повеситься|зарезаться|выпилиться"
        r"|вздёрнуться|вздернуться|сброситься|выброситься|выкинуться)\b"
        r"|\b(застрелюсь|утоплюсь|удавлюсь|вздёрнусь|вздернусь|выпилюсь|сброшусь|выброшусь)\b"
        r"|\bсамоуби\w*|\bсамовыпил\w*|\bсамо\s+убийств\w*|\bсу[иеі]ц[иы]д\w*"
        r"|\bпоконч\w*\s+собой|\bубить\s+себе\b"
        r"|\b(прыгн|спрыгн|сигану|сиганут|брош|брос|шагн|выйти|выйду|выпрыгн|кинусь|кинуться"
        rf"|выкинусь|выкинуться|лечь|лягу)\w*\s+({_W}\s+)?"
        r"(под\s+(поезд|машин|электричк|кол[её]с|автобус|трамва|метро|фур|грузовик)\w*"
        # «окно» in a salon is also a free slot: «выйти в окно в 16:00».
        r"|в\s+окно\b(?!\s+(в|во|на|к|у|до|после|между)\b|\s+\d)"
        r"|из\s+окна\b(?!\s+(записи|записей|при[её]ма)\b)"
        r"|с\s+(крыши|моста|балкона|окна|высотки|многоэтажки)|с\s+\w+\s+этажа)"
        rf"|(под\s+(поезд|электричк|машин)\w*|в\s+окно|с\s+(крыши|моста|балкона))\s+({_W}\s+)?"
        r"\b(прыгн|брош|брос|шагн|кинусь|лягу|выйду|сигану)\w*"
        r"|\b(выпить|выпью|съесть|съем|проглотить|проглочу|принять|приму|сожрать|сожру)\s+"
        r"((сразу|разом|залпом)\s+)?"
        rf"(вс[её]|всю\s+пачку|всю\s+упаковку|пачку|упаковку|горсть|кучу)\s+({_W}\s+)?"
        r"(таблет|снотворн|колёс|колес|лекарств|феназепам|транквилиз|антидепрессант"
        r"|успокоительн)\w*"
        r"|\b(наглота\w*|нажр\w*|нае\w*|напьюсь|напиться|напилась|напился|обожр\w*)\s+"
        r"(таблет|снотворн|колёс|колес|лекарств)\w*"
        r"|\bтаблетк\w*\s+горст\w*"
        r"|\bпредсмертн\w*|\bпрощальн\w*\s+(записк|письм)\w*"
        r"|\bв[её]р[её]вк\w*\s+и\s+мыл\w*|\bмыл\w*\s+и\s+в[её]р[её]вк\w*"
        r"|(\bлезть|\bполез\w*|\bзалез\w*|\bхоч\w*|\bпора)\s+в\s+петлю"
        # «порежу / перережу» — future tense has «ж».
        r"|\b(вскр|вскрыв|поре[зж]|перере[зж])\w*\s+себе\s+вен\w*"
        r"|\bвен\w*\s+себе\s+(вскр|поре[зж]|перере[зж])\w*"
        r")",
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
        # «умираю (как) хочу…» (common on a beauty bot) is excluded by the
        # negative lookahead; the physical «умираю от …» moved to MEDICAL
        # (DRF-2000) together with the rest of the acute-emergency group.
        r"(?i)(\bумираю\b(?!\s+(как\s+)?хочу)(?!\s+от\b)|\bdying\b)",
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
        r"|\bскор(ая|ую|ой|ые)\b(\s+помощ\w*)?|\bemergency\b|\bумираю\s+от\b)",
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
        #
        # ``\A`` (DRF-2921) changes no verdict: both lookaheads read to the
        # end of the text, so if they hold anywhere they hold at the start.
        # Without it ``re.search`` retried them from every position — the
        # cost grew as the square of the length: seconds on a 4 000-character
        # message, in the one consumer everybody shares.
        r"(?is)\A(?=.*\b(подбер\w*|подобра\w*|посовет\w*|порекоменд\w*|назнач\w*"
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
