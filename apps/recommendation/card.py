"""Карточка C04 «направление + почему» — сборка из документа и рендер в DM (DRF-1772).

Макет DRF-1270, кадры C04.1 (лучшее направление) и C04.4 (нет рекомендации).
Решение владельца §60: OD-PILOT-9 в части C04 снят — «направление» строится
для пилота; «Ayla рекомендует услугу X» по-прежнему не звучит. B2/B3:
Recommendation = WHAT, услуга/мастер/цена/слот — только в C05.

Откуда что берётся — и чего здесь не сочиняется
------------------------------------------------
* **WHAT** — `known.goal.direction` документа: курируемая таблица владельца в
  каталоге (`services.GoalDirection`). Нет строки → направления нет →
  карточки нет → C04.4. Кодом фразы не придумываются (рамка главного окна).
* **WHY** — grounded-пересказ того, что человек сказал в этом пути
  (OD_C04 §1, форма владельца: «Ты сказала, что хочешь выглядеть свежее»):
  подпись цели, ответы шагов анкеты (`known.goal.answers`). «Не знаю» —
  ответ, но не факт; из него причины нет. **Ноль причин → карточки нет**
  (OD_C04 §2 — правило кода, не редактуры) → C04.4.
* **Сказал словами ≠ выбрал из предложенного** (DRF-1771, К-3 N5).
  Свободная цель (`goal_text`) и свободный ответ шага (`answer_text`) —
  слова ЧЕЛОВЕКА, и причина их цитирует; чип — подпись каталога, и причина
  остаётся пересказом. Происхождение каждой причины (`text` / `choice`)
  едет в факты записи: связь «причина ↔ факт» становится данными.
  Цитирование выключается одной константой :data:`QUOTE_THE_PERSON` —
  вопрос «цитата или всегда пересказ» у владельца открыт.

  Исходная формулировка листа (рендер `evidence_refs` решения) остаётся
  заблокированной D11: такой связи как данных нет ни в решении резолвера,
  ни в документе целей. Здесь построена та часть, для которой данные есть.
* **Другие подходы** (C04.2, DRF-1770, К-3 N4) — `known.goal.directions`:
  та же курируемая таблица, основной первым, других ≤2 (R05). Строк нет
  или она одна — кадра выбора нет, и кнопка «Показать больше подходов» не
  рисуется: больше двух не бывает по построению.
* **Сторож — по предмету, а не по слову** (:func:`boundary_violation`): в
  WHAT/подстроке/причинах нет услуги, мастера, салона, цены — той же
  проверкой, что стережёт опции C02 (`clarify_guard`); нет утверждения о
  приоритете (24.08 §1); нет словаря отладки — кодов, баллов,
  «уверенности» (R06); и нет «рекоменду… <услуга/мастер/цена>» (§60).
  Само слово не запрещено: заголовок макета C04.2 «Основной вариант
  (рекомендую):» — про НАПРАВЛЕНИЕ, и он в явном allow-list одной
  константой. Нарушение → карточки нет → C04.4.

Триггер — не здесь: `dispatch.maybe_send_card` зовёт `build_card` только на
серверном факте «контекст собран» (`next.id == return_to_chat`).
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from apps.orchestrator.clarify_guard import clarification_option_violation

logger = logging.getLogger(__name__)

# ─── тексты макета C04.1 (DRF-1270) дословно ─────────────────────────────

CARD_HEAD = "Моё лучшее направление для тебя:"
WHY_HEAD = "Почему это подходит тебе"
#: C04.3 — раскрытие: заголовок кадра дословно.
WHY_MORE_HEAD = "Почему это важно для тебя"
BUTTON_PICK = "Подобрать вариант"
BUTTON_WHY = "Почему"
BUTTON_ALT = "Другой вариант"
BUTTON_SKIP = "Не сейчас"

# ─── тексты макета C04.2 «другие подходы» (DRF-1270) дословно ────────────

ALT_HEAD = "Есть несколько подходящих направлений. Что для тебя важнее?"
#: «рекомендую» здесь — про НАПРАВЛЕНИЕ, и по §60 это законно: запрещено
#: «Ayla рекомендует услугу X». Строка стоит в allow-list сторожа явно —
#: иначе пришлось бы либо ослабить правило, либо переписать макет.
ALT_PRIMARY_HEAD = "Основной вариант (рекомендую):"
ALT_OTHERS_HEAD = "Другие подходы (тоже подойдут):"
#: Сколько ДРУГИХ подходов показываем (R05 — «не больше трёх всего»).
#: Поэтому кнопки «Показать больше подходов» на кадре нет: она обещала бы
#: продолжение, которого не бывает по построению.
MAX_ALTERNATIVES = 2
#: Длина подписи кнопки — как у соседних клавиатур (`discovery.py`).
MAX_BUTTON_LABEL_CHARS = 40

# ─── C04.4 — нет рекомендации: текст владельца 12.09 (общий словарь с
#     `apps/miniapp/src/lib/recommendation-absence.ts`, паритет — тест) ──

NO_VERIFIED_EVIDENCE_TEXT = (
    "Пока у меня недостаточно подтверждённых данных, чтобы уверенно посоветовать "
    "конкретный вариант. Могу показать доступные услуги или помочь уточнить, что тебе "
    "сейчас нужно."
)
ACTION_SHOW_SERVICES = "Посмотреть услуги"
ACTION_CLARIFY_REQUEST = "Уточнить запрос"

# ─── WHY — три шаблона пересказа (форма OD_C04 §1; тексты — отступление,
#     вынесены владельцу) ────────────────────────────────────────────────

WHY_GOAL = "Ты сказала, что хочешь {goal}"
WHY_AREA = "Ты выбрала: {area}"
WHY_FEELING = "Хочешь чувствовать себя: {feeling}"
#: Слова человека — в кавычках и дословно (DRF-1771). Ни пересказа, ни
#: модели: это его формулировка, и менять её мы не вправе.
WHY_GOAL_QUOTED = "Ты написала: «{quote}»"
WHY_STEP_QUOTED = "Ты написала: «{quote}»"
MAX_REASONS = 3

#: Цитировать ли человека дословно. Вопрос владельцу открыт («цитата или
#: всегда пересказ»); `False` возвращает прежний пересказ по подписи —
#: одна строка, а не правка веток.
QUOTE_THE_PERSON = True

#: Предел длины цитаты. Обрезка — по границе слова, с многоточием: кнопка
#: и пузырь MAX конечны, а обрывок посреди слова читается как сбой.
QUOTE_MAX_CHARS = 120

#: Происхождение факта, из которого собрана причина.
ORIGIN_TEXT = "text"
ORIGIN_CHOICE = "choice"

#: Запрещённая форма (OD_C04 §1, дословно): «Это сейчас для тебя самое
#: важное» — утверждение о приоритете, которого человек не подтверждал.
#: До этого среза правило держалось договорённостью: grep по репозиторию
#: давал ноль проверок.
PRIORITY_CLAIM_RE = re.compile(
    r"(сам\w*\s+важн)|(важнее\s+всего)|(наиболее\s+важн)|(главное\s+для\s+тебя)",
    re.IGNORECASE,
)

#: Управляющие символы и переносы в тексте человека: в одну строку, но не
#: выбросом — его слова остаются его словами.
_WHITESPACE_RE = re.compile(r"\s+")

#: §60: карточка — направление, а не «Ayla рекомендует услугу X».
#: Ловится не слово, а ПРЕДМЕТ: см. :func:`boundary_violation`.
RECOMMENDS_RE = re.compile(r"рекоменду\w*", re.IGNORECASE)

#: Строки, где «рекоменду…» законно, — по одной константе и с причиной.
#: Список явный и короткий: сторож §60 растворился бы от шаблона.
_RECOMMENDS_ALLOWED = frozenset({ALT_PRIMARY_HEAD})

#: Родовые имена бронируемого — то, о чём §60 говорит прямо: «Ayla
#: рекомендует УСЛУГУ X». Каталог знает конкретные имена («Массаж»), этот
#: список — само слово «услуга»; вместе они ловят обе формы фразы, и ни
#: одна не зависит от того, доступна ли база.
_BOOKABLE_NOUN_RE = re.compile(
    r"(услуг)|(мастер)|(специалист)|(салон)",
    re.IGNORECASE,
)
_BOOKABLE_NOUN_REASON = {"услуг": "service", "мастер": "master", "специалист": "master"}

#: Хвост после «рекоменду…»: что именно рекомендуют. Отрезаем пунктуацию —
#: «рекомендую: массаж» и «рекомендую массаж» — одно и то же.
_RECOMMENDS_TAIL_STRIP = " \t:,.;!?—-«»\"'"

#: Словарь отладки (R06 — «без reason codes / confidence / debug»): коды
#: пригодности и совпадения, баллы, «уверенность». Новая стража: до этого
#: среза правило держалось договорённостью — grep по репозиторию давал
#: ноль проверок, и первая же строка вида `ELIG_EXCLUDED_SAFETY` доехала
#: бы до человека.
DEBUG_VOCABULARY_RE = re.compile(
    r"(\bELIG_)|(\bMATCH_)|(\bEXEC_)|(\bCONTEXT_)|(\bscore\b)|(увереннос)|(\bбалл)",
    re.IGNORECASE,
)

# ─── callback-грамматика тапов ──────────────────────────────────────────

RECO_CALLBACK_PREFIX = "cb:reco:"
RECO_WHY_PREFIX = "cb:reco:why:"
RECO_ALT_PREFIX = "cb:reco:alt:"
RECO_SKIP_PREFIX = "cb:reco:skip:"
#: Выбор другого подхода на кадре C04.2: ``cb:reco:pick:<id>:<индекс>``.
RECO_PICK_PREFIX = "cb:reco:pick:"


@dataclass(frozen=True)
class CardDraft:
    """Что показать. `why` непуст по построению — иначе черновика нет."""

    goal_id: str
    what: str
    subline: str
    why: tuple[str, ...]
    facts: dict[str, Any] = field(default_factory=dict)
    #: Другие подходы (C04.2, DRF-1770): ≤2 строк той же формы, что и
    #: основной. Пусто — направление одно, и кадра выбора нет.
    alternatives: list[dict[str, Any]] = field(default_factory=list)

    @property
    def fingerprint(self) -> str:
        raw = json.dumps(
            {"goal": self.goal_id, "what": self.what, "why": list(self.why)},
            ensure_ascii=False,
            sort_keys=True,
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _lower_first(text: str) -> str:
    return text[:1].lower() + text[1:] if text else text


def normalize_quote(raw: Any) -> str:
    """Слова человека в одну строку и в пределах длины; ``""`` — цитировать нечего.

    Переносы и управляющие символы схлопываются в пробел: пузырь MAX —
    одна реплика, и вставленный перевод строки ломал бы кадр. Обрезка по
    границе слова с многоточием; текст из одних пробелов даёт пустую
    строку, а пустая цитата — не причина (её просто нет).
    """
    text = _WHITESPACE_RE.sub(" ", str(raw or "")).strip()
    if len(text) <= QUOTE_MAX_CHARS:
        return text
    cut = text[: QUOTE_MAX_CHARS - 1]
    head, _, _tail = cut.rpartition(" ")
    return f"{(head or cut).rstrip()}…"


def _said_in_own_words(answer: dict[str, Any]) -> str:
    """Слова человека на шаге — только `answer_text`, и только без выбора.

    Признак «набрал сам» — именно `answer_text`: `label` у шага без
    `option_key` бывает подписью варианта, и цитировать её значило бы
    выдать каталожную формулировку за слова человека.
    """
    if answer.get("option_key") or answer.get("option_keys"):
        return ""
    return normalize_quote(answer.get("answer_text"))


def grounded_reasons(goal: dict[str, Any]) -> tuple[tuple[str, ...], dict[str, Any]]:
    """Причины из фактов документа — и сами факты с происхождением.

    Порядок — порядок разговора: цель, затем ответы в порядке шагов.
    «Не знаю» (`unknown`) пропускается: это ответ, но не факт. Человек
    НАПИСАЛ — цитируем его (DRF-1771); ВЫБРАЛ — пересказываем подпись.
    """
    reasons: list[str] = []
    facts: dict[str, Any] = {}

    spoken_goal = normalize_quote(goal.get("goal_text"))
    label = normalize_quote(goal.get("label"))
    if spoken_goal:
        reasons.append(
            WHY_GOAL_QUOTED.format(quote=spoken_goal)
            if QUOTE_THE_PERSON
            else WHY_GOAL.format(goal=_lower_first(spoken_goal))
        )
        facts["goal"] = {"value": spoken_goal, "origin": ORIGIN_TEXT}
    elif label:
        reasons.append(WHY_GOAL.format(goal=_lower_first(label)))
        facts["goal"] = {"value": label, "origin": ORIGIN_CHOICE}

    for answer in goal.get("answers") or []:
        if not isinstance(answer, dict) or answer.get("unknown"):
            continue
        step = str(answer.get("step") or "")
        spoken = _said_in_own_words(answer)
        if spoken:
            # Набранный текст — либо цитата, либо ничего: формы пересказа
            # под произвольные слова у нас нет, а «Ты выбрала: болит спина»
            # врёт о способе — человек это написал, а не выбрал. Факт при
            # этом остаётся фактом и едет в запись.
            if QUOTE_THE_PERSON:
                reasons.append(WHY_STEP_QUOTED.format(quote=spoken))
            facts[step] = {"value": spoken, "origin": ORIGIN_TEXT}
            continue
        text = normalize_quote(answer.get("label"))
        if not text:
            continue
        if step == "area":
            reasons.append(WHY_AREA.format(area=_lower_first(text)))
        elif step == "feeling":
            reasons.append(WHY_FEELING.format(feeling=_lower_first(text)))
        else:
            # Шаг, которого шаблон не знает, причиной не становится:
            # сочинять форму под неизвестный факт нельзя.
            continue
        # Происхождение — правда о факте, а не о форме фразы: человек мог
        # набрать текст, а цитирование быть выключено.
        facts[step] = {"value": text, "origin": ORIGIN_CHOICE}
    return tuple(reasons[:MAX_REASONS]), facts


def _recommends_a_bookable(text: str) -> str | None:
    """«рекоменду… X», где X — услуга, мастер, салон или цена каталога.

    §60 запрещает не слово, а ПРЕДМЕТ: «Ayla рекомендует услугу X».
    Поэтому проверяется ровно хвост — то, ЧТО рекомендуют. Две проверки, и
    обе нужны: родовое имя («услугу», «мастера») ловится словарём и не
    зависит от базы; конкретное имя («массаж») знает только каталог — тем
    же сторожем, что стережёт опции C02.

    Названный предел: имя услуги каталог узнаёт равенством множеств
    стемов, а мастера и салон — дословно. Значит «рекомендую расслабляющий
    массаж спины» пройдёт, если такой строки в каталоге нет, и «рекомендую
    массаж» пройдёт там, где каталог недоступен. Это тот же предел, что
    назван в `clarify_guard`, а не новая дыра: ловить написание вместо
    предмета мы там отказались сознательно.
    """
    match = RECOMMENDS_RE.search(text)
    if match is None:
        return None
    tail = text[match.end() :].strip(_RECOMMENDS_TAIL_STRIP)
    if not tail:
        return None
    noun = _BOOKABLE_NOUN_RE.search(tail)
    if noun is not None:
        stem = noun.group(0).lower()
        return f"recommends_{_BOOKABLE_NOUN_REASON.get(stem, 'salon')}"
    reason = clarification_option_violation(tail)
    return f"recommends_{reason}" if reason is not None else None


def boundary_violation(*texts: str) -> str | None:
    """Чего в карточке быть не может — ``None``, если всё чисто.

    * ``priority_claim`` — «это сейчас для тебя самое важное» (24.08 §1):
      утверждение о приоритете, которого человек не подтверждал;
    * ``debug_vocabulary`` — коды причин, баллы, «уверенность» (R06);
    * ``price`` / ``service`` / ``master`` / ``salon`` — R11, той же
      проверкой, что стережёт опции C02;
    * ``recommends_*`` — «рекомендую» рядом с услугой/мастером/ценой (§60).

    Слово «рекомендую» само по себе не нарушение: заголовок кадра C04.2
    про НАПРАВЛЕНИЕ законен и стоит в allow-list одной константой. Сторож
    не ослаблен, а сужен по предмету — подсадь в ту же строку услугу, и
    она снова красная.
    """
    for text in texts:
        if not text or text in _RECOMMENDS_ALLOWED:
            continue
        if PRIORITY_CLAIM_RE.search(text):
            return "priority_claim"
        if DEBUG_VOCABULARY_RE.search(text):
            return "debug_vocabulary"
        reason = clarification_option_violation(text)
        if reason is not None:
            return f"recommends_{reason}" if RECOMMENDS_RE.search(text) else reason
        recommends = _recommends_a_bookable(text)
        if recommends is not None:
            return recommends
    return None


def build_card(doc: dict[str, Any]) -> CardDraft | None:
    """Черновик карточки из документа decision-context или ``None`` (→ C04.4).

    ``None`` — честное «нет рекомендации»: нет цели, нет направления
    (таблица владельца пуста или строки нет), нет ни одной причины, или
    текст нарушил границу. Каждый случай пишется в журнал своим именем.
    """
    known = doc.get("known") if isinstance(doc, dict) else None
    goal = (known or {}).get("goal") if isinstance(known, dict) else None
    if not isinstance(goal, dict):
        logger.info("recommendation.card.absent reason=no_goal")
        return None
    direction = goal.get("direction")
    if not isinstance(direction, dict) or not str(direction.get("what") or "").strip():
        logger.info("recommendation.card.absent reason=no_direction goal=%s", goal.get("id"))
        return None
    why, facts = grounded_reasons(goal)
    if not why:
        logger.info("recommendation.card.absent reason=no_grounded_why goal=%s", goal.get("id"))
        return None
    what = str(direction["what"]).strip()
    subline = str(direction.get("subline") or "").strip()
    violation = boundary_violation(what, subline, *why)
    if violation is not None:
        logger.warning(
            "recommendation.card.absent reason=boundary_%s goal=%s", violation, goal.get("id")
        )
        return None
    return CardDraft(
        goal_id=str(goal.get("id") or ""),
        what=what,
        subline=subline,
        why=why,
        facts=facts,
        alternatives=_alternatives(goal, primary=what),
    )


def _alternatives(goal: dict[str, Any], *, primary: str) -> list[dict[str, Any]]:
    """Другие подходы из документа: ≤2, без основного, через тот же сторож.

    Документ каталога до выкладки N4 поля `directions` не несёт — тогда
    других подходов нет, и кадр выбора не рисуется. Это не деградация:
    ровно так карточка и выглядела до этого среза.
    """
    out: list[dict[str, Any]] = []
    for row in goal.get("directions") or []:
        if not isinstance(row, dict):
            continue
        what = str(row.get("what") or "").strip()
        subline = str(row.get("subline") or "").strip()
        if not what or what == primary:
            continue
        if boundary_violation(what, subline) is not None:
            # Строка владельца, нарушившая границу, не показывается — ровно
            # как основное направление в том же случае.
            logger.warning(
                "recommendation.alternative.dropped goal=%s reason=boundary", goal.get("id")
            )
            continue
        # `area_key` сюда не едет: бот его не читает, а хранить поле,
        # которое некому прочесть, — значит однажды не суметь его
        # восстановить при выборе другого подхода. Область нужна экрану
        # (N3), и она остаётся в документе каталога.
        out.append({"what": what, "subline": subline})
        if len(out) == MAX_ALTERNATIVES:
            break
    return out


# ─── рендер ─────────────────────────────────────────────────────────────


def render_card_text(draft: CardDraft) -> str:
    lines = [CARD_HEAD, draft.what]
    if draft.subline:
        lines.append(draft.subline)
    lines.append("")
    lines.append(WHY_HEAD)
    lines.extend(f"✓ {reason}" for reason in draft.why)
    return "\n".join(lines)


def render_why_more_text(draft: CardDraft) -> str:
    """C04.3 — раскрытие. До N4/N5 дополнительных причин нет: те же,
    под заголовком кадра; «Показать больше причин» нечем — заглушка честная."""
    lines = [WHY_MORE_HEAD]
    lines.extend(f"✓ {reason}" for reason in draft.why)
    return "\n".join(lines)


def render_alternatives_text(draft: CardDraft) -> str:
    """Кадр C04.2 — заголовки макета дословно, направления как в таблице."""
    lines = [ALT_HEAD, "", ALT_PRIMARY_HEAD, draft.what]
    if draft.subline:
        lines.append(draft.subline)
    lines.extend(["", ALT_OTHERS_HEAD])
    for alt in draft.alternatives:
        lines.append(f"• {alt['what']}")
        if alt.get("subline"):
            lines.append(f"  {alt['subline']}")
    return "\n".join(lines)


def alternatives_keyboard(recommendation_id: str, draft: CardDraft) -> dict[str, Any] | None:
    """По кнопке на каждый другой подход — и ни одной сверх того.

    «Показать больше подходов» здесь нет: больше двух не бывает (R05), и
    кнопка обещала бы продолжение, которого не существует.
    """
    from apps.orchestrator.discovery import keyboard_envelope

    return keyboard_envelope(
        [
            {
                "label": alt["what"][:MAX_BUTTON_LABEL_CHARS],
                "callback": f"{RECO_PICK_PREFIX}{recommendation_id}:{index}",
            }
            for index, alt in enumerate(draft.alternatives)
        ]
    )


def _app_button(label: str, slug: str, *, payload: str | None = None) -> dict[str, str] | None:
    """``open_app`` на экран Mini App; ``payload`` — start-param поверх маршрута.

    Маршрут payload'а разбирает сам Mini App (`max-sdk.ts::parseStartRoute`):
    у `reco_<id>` назначение — тот же каталог, а сам id доезжает до
    провенанса интента. Без ``web_app`` — ссылка на экран, и тогда id не
    едет: у внешней ссылки start-param нет.
    """
    from apps.channels.miniapp_config import miniapp_target
    from apps.skills.welcome.skill import MINIAPP_ROUTES, _miniapp_url

    if slug not in MINIAPP_ROUTES:
        return None
    target = miniapp_target()
    if target.web_app:
        return {"label": label, "callback": payload or slug, "web_app": target.web_app}
    if target.miniapp_url:
        return {"label": label, "url": _miniapp_url(target.miniapp_url, slug)}
    return None


def card_keyboard(recommendation_id: str) -> dict[str, Any] | None:
    """Четыре кнопки C04.1 дословно. «Подобрать вариант» до К-4 (C05) ведёт в
    каталог Mini App — единственный сегодняшний вход в исполнение
    (отступление, названо в PR) — и несёт ССЫЛКУ НА ЭТУ КАРТОЧКУ
    (`reco_<id>`, DRF-1773): по ней бронь свяжется с рекомендацией, из
    которой выросла."""
    from apps.orchestrator.discovery import keyboard_envelope
    from apps.recommendation.provenance import RECO_PAYLOAD_PREFIX

    buttons = [
        _app_button(
            BUTTON_PICK, "open_catalog", payload=f"{RECO_PAYLOAD_PREFIX}{recommendation_id}"
        ),
        {"label": BUTTON_WHY, "callback": f"{RECO_WHY_PREFIX}{recommendation_id}"},
        {"label": BUTTON_ALT, "callback": f"{RECO_ALT_PREFIX}{recommendation_id}"},
        {"label": BUTTON_SKIP, "callback": f"{RECO_SKIP_PREFIX}{recommendation_id}"},
    ]
    return keyboard_envelope([b for b in buttons if b])


def absence_keyboard() -> dict[str, Any] | None:
    """C04.4 — два действия владельца 12.09, оба в Mini App."""
    from apps.orchestrator.discovery import keyboard_envelope

    buttons = [
        _app_button(ACTION_SHOW_SERVICES, "open_catalog"),
        _app_button(ACTION_CLARIFY_REQUEST, "open_goal_select"),
    ]
    return keyboard_envelope([b for b in buttons if b])


__all__ = [
    "ACTION_CLARIFY_REQUEST",
    "ALT_HEAD",
    "ALT_OTHERS_HEAD",
    "ALT_PRIMARY_HEAD",
    "DEBUG_VOCABULARY_RE",
    "MAX_ALTERNATIVES",
    "MAX_BUTTON_LABEL_CHARS",
    "RECO_PICK_PREFIX",
    "alternatives_keyboard",
    "render_alternatives_text",
    "ORIGIN_CHOICE",
    "ORIGIN_TEXT",
    "PRIORITY_CLAIM_RE",
    "QUOTE_MAX_CHARS",
    "QUOTE_THE_PERSON",
    "WHY_GOAL_QUOTED",
    "WHY_STEP_QUOTED",
    "normalize_quote",
    "ACTION_SHOW_SERVICES",
    "BUTTON_ALT",
    "BUTTON_PICK",
    "BUTTON_SKIP",
    "BUTTON_WHY",
    "CARD_HEAD",
    "CardDraft",
    "MAX_REASONS",
    "NO_VERIFIED_EVIDENCE_TEXT",
    "RECO_ALT_PREFIX",
    "RECO_CALLBACK_PREFIX",
    "RECO_SKIP_PREFIX",
    "RECO_WHY_PREFIX",
    "WHY_HEAD",
    "absence_keyboard",
    "boundary_violation",
    "build_card",
    "card_keyboard",
    "grounded_reasons",
    "render_card_text",
    "render_why_more_text",
]
