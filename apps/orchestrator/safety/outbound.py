"""Checking what the assistant is about to say (DRF-1061 step 1).

`gate.py` held one function — `evaluate_inbound`. There was no outbound
counterpart anywhere in the project, which means every boundary the prompts
describe («я не врач», «не обещаю за салон») rested entirely on the model
choosing to obey. A prompt is a request, not a guarantee, and the failure
mode is silent: a person reads a confident medical claim and nothing in the
logs says anything happened.

### Deliberately small

This is not a content classifier and does not try to be. It looks for a
handful of shapes that are unambiguous in Russian and expensive when wrong:

* **medical claims** — a diagnosis, a prescription, a dosage;
* **promises made on the salon's behalf** — guarantees of a result, of a
  price, of a refund the assistant has no authority over;
* **contact details** — phone numbers and emails, which no answer here has
  a reason to contain (DRF-1039), including a phone's four-digit tail when
  the sentence itself calls it a number (DRF-1209: «номер 4567», «тел.
  1234» — a partial phone is a phone, OD-W2-2);
* **nagging / pressure** (DRF-1468, copy policy R2/R3) — «не забывайте про
  цель», «давно не работали», «вы пропустили», virtue streaks and counters
  («дней подряд», «серия»). Written for the proactive path, where an
  unsolicited reproach is the worst sentence there is; the shapes are
  banned in any reply;
* **planning claims** (P1-D1, `OPEN_DECISIONS.md` §50) — «между курсами
  нужно три-четыре недели», «курс из пяти процедур», «нельзя совмещать с
  пилингом», «восстановление занимает три дня». Правило «модель не
  придумывает тайминги» до этого существовало **только как намерение**:
  антигаллюцинационные списки во всех промптах перечисляли мастера, цену,
  адрес, длительность и ID — про интервалы, курсы, совместимость,
  восстановление и подготовку не было ни одного правила, и здесь не было
  категории. Инвентарь Трека D показал, что 11 из 13 типов ограничений в
  каноне — `UNKNOWN`, то есть утверждать их нам **нечем**.

Anything subtler stays with the prompt. A greedy filter that mangles decent
replies would get itself turned off within a week, and then there would be
no filter at all.

### What happens on a hit

The verdict model here is binary — allow or replace. A data leak is the
replace-class (BLOCK in `post_check.py`'s three-way vocabulary), never a
soften: there is no politely reworded way to hand out someone's number.

The reply is REPLACED, not edited. Cutting the offending sentence leaves
text that reads as though something is missing and, worse, can invert the
meaning of what remains. A short honest line plus a route to a human is
the better failure.

The hit is logged with the matched category — never the text, which by
definition is the part we do not want copied around.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

#: Diagnosis / prescription / dosage. Deliberately requires an assertive
#: verb: «противопоказания обсудите с врачом» must pass, «у вас аллергия»
#: must not.
_MEDICAL = (
    r"(?i)\bу\s+(вас|тебя|неё|него|клиент\w*)\s+(аллерг|дерматит|экзем|псориаз|грибок|"
    r"инфекц|воспалени|рак|диабет)",
    r"(?i)\b(примите|выпейте|принимайте|назначаю|пропейте)\b",
    r"(?i)\b(ибупрофен|анальгин|парацетамол|кеторол|антибиотик\w*)\b",
    r"(?i)\b(это\s+точно|у\s+вас\s+явно)\s+\w*(аллерг|инфекц|заболевани)",
    r"(?i)\bдиагноз\w*\s+(—|-|:)?\s*\w+",
)

#: Promises the assistant cannot keep on the salon's behalf.
_PROMISES = (
    r"(?i)\b(гарантиру\w+|обещаю|обещаем)\b",
    r"(?i)\b(вернём|вернем|возврат\w*)\s+(деньги|средства|полную\s+стоимость)",
    r"(?i)\b(результат\s+(гарантирован|100%)|точно\s+поможет|обязательно\s+поможет)\b",
    r"(?i)\b(бесплатно\s+переделаем|сделаем\s+скидку|дам\s+скидку|дадим\s+скидку)\b",
)

#: Contact details have no business in these replies.
_CONTACTS = (
    r"(?<!\d)(\+7|8)[\s\-(]*\d{3}[\s\-)]*\d{3}[\s\-]*\d{2}[\s\-]*\d{2}(?!\d)",
    r"(?i)[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}",
)

#: A four-digit tail, written the way a truncated phone actually comes out:
#: "4567", "45 67", "45-67". Never matched bare — only behind a marker below.
_TAIL = r"(?<!\d)\d{2}[\s\-]?\d{2}(?!\d)"

#: What may sit between the marker and the tail: at most one possessive-ish
#: word («номер телефона …», «телефон клиентки …») and a separator. The gap
#: is deliberately this narrow — every extra word of freedom is a legal
#: reply the guard can eat.
_FILLER = r"(?:\s+(?:клиентки|клиента|мастера|салона|телефона|ваш|вашего))?"
_SEP = r"\s*[:№\-–—]?\s*"

#: A partial phone is still a phone. The live leak behind this (DRF-1039 /
#: OD-W2-2, restated in DRF-1360) was a truncated excerpt leaving the last
#: four digits of a customer's number readable, and the owner decision
#: allows no "identifier" exception: «телефон клиента исполнителю не
#: передаётся ни в каком виде». So the tail must not go out — DRF-1209.
#:
#: But a bare four-digit group is also a price («1500 ₽»), a year
#: («в 2024 году»), a calorie count, and the middle group of a UUID — the
#: exact wound DRF-1382 measured in the replay redactor. Blocking those
#: would put false blocks on the live pilot, so the tail counts only when
#: the sentence itself says it is a phone:
#:
#: * «номер 4567» / «номер телефона 45-67» — «номер» with no noun defaults
#:   to a phone number in Russian; the nouns that make it something else
#:   (заказа, записи, карты, счёта, брони, талона, договора, паспорта) are
#:   excluded explicitly;
#: * «тел. 1234» / «телефон клиентки: 1234»;
#: * «…заканчивается на 4567» — how a partial number is usually described;
#: * «последние 4 цифры 4567».
#:
#: «код 4521» is deliberately NOT a marker: a one-time code is not a phone
#: tail, and the bot has legitimate reasons to read one back.
_PARTIAL_PHONES = (
    rf"(?i)\bтел(?:ефон\w*)?\.?{_FILLER}{_SEP}{_TAIL}",
    rf"(?i)\bномер(?!\s+(?:заказа|записи|карты|сч[её]та|брони|талона|договора|паспорта))"
    rf"{_FILLER}{_SEP}{_TAIL}",
    rf"(?i)\b(?:заканчивается|оканчивается)\s+на{_SEP}{_TAIL}",
    rf"(?i)\bпоследние\s+(?:\d|четыре)\s+цифр\w*{_SEP}{_TAIL}",
)

#: Nagging / pressure shapes (DRF-1468, copy policy R2/R3). A proactive
#: message must never scold, count absences, or score virtue: «не забывайте
#: про цель», «давно не работали», «вы пропустили», streaks and counters.
#: These read as reproach on a bad day, and an unsolicited reproach is the
#: exact failure the shared anti-nag mechanism exists to prevent.
#:
#: «серия» is excluded only before «процедур»: a course of salon procedures
#: is a legitimate service phrase, every other use here is a virtue counter.
_NAG = (
    r"(?i)\bне\s+забыва\w*\s+про\s+цель",
    r"(?i)\bдавно\s+не\s+(работа\w*|писа\w*|записыва\w*|заходил\w*)",
    r"(?i)\b(?:вы|ты)\s+пропустил\w*",
    r"(?i)\bдн(?:ей|я|ень)\s+без\s+(?:срыв\w*|пропуск\w*)",
    r"(?i)\bдн(?:ей|я|ень)\s+подряд",
    r"(?i)\bсери[яиюе]\b(?!\s+процедур)",
)

#: Числительные словами и цифрами. Планировочная выдумка почти никогда
#: не пишется цифрой: «три-четыре недели», «пара сеансов», «полтора
#: месяца». Список без цифр ловил бы ровно половину случаев.
_NUM = (
    r"(?:\d+|одн\w+|дв[еа]|двух|тр[иё]|трёх|трех|четыр\w+|пят\w+|шест\w+|"
    r"сем\w+|восьм\w+|восем\w+|девят\w+|десят\w+|полтора|полутора|пар[ауы])"
)

#: Единицы, которыми меряют план: время и штуки процедур.
_UNIT = r"(?:недел\w*|дн(?:я|ей|ь)|сут(?:ок|ки)|месяц\w*|сеанс\w*|процедур\w*)"

#: Модальность ДОЛЖЕНСТВОВАНИЯ — то, что отличает утверждение о норме от
#: приглашения. «Приходите через месяц, если понравится» никакой нормы не
#: утверждает и проходит; «между курсами нужно три-четыре недели» —
#: утверждает, и утверждать нам это нечем.
#:
#: Ровно на этом различии стоит вся категория. Без модальности пришлось бы
#: ловить любой интервал в тексте, и первым, что фильтр съел бы, стали бы
#: живые человеческие фразы вроде «загляните на следующей неделе».
_MODAL = (
    r"(?:нужн\w*|необходим\w*|следует|требуется|должн\w*|обязательн\w*|"
    r"рекоменд\w*|оптимальн\w*|минимум|минимальн\w*|положено|"
    r"не\s+раньше|не\s+ранее|не\s+чаще|не\s+менее)"
)

#: Внутри ОДНОГО предложения: `[^.!?\n]` не пускает совпадение через точку.
#: «Нужен мастер. Приходите через месяц» не должно читаться как норма
#: интервала только потому, что оба слова оказались в одном ответе.
_GAP = r"[^.!?\n]{0,60}"
_QTY = rf"{_NUM}\s*(?:[-–—]\s*{_NUM}\s*)?{_UNIT}"

#: Только ВРЕМЕННЫЕ единицы — без «процедур» и «сеансов». Нужно для формы
#: без числа («нужно приходить через месяц»), где счёта нет вовсе.
_TIME_UNIT = r"(?:недел\w*|дн(?:я|ей|ь)|сут(?:ок|ки)|месяц\w*|год|года|полгода)"

#: Предлог интервала. Он и делает форму без числа безопасной: «нужно
#: записаться на процедуру» модальность содержит, но интервала не
#: утверждает и проходит; «приходить нужно через месяц» — утверждает.
#: Без этого условия категория съела бы половину операционных фраз.
_EVERY = r"(?:через|спустя|раз\s+в|кажд\w+)"

_PLANNING = (
    # норма → количество и обратно, в пределах предложения
    rf"(?i)\b{_MODAL}\b{_GAP}\b{_QTY}",
    rf"(?i)\b{_QTY}{_GAP}\b{_MODAL}\b",
    # то же, но БЕЗ числа: «приходить нужно через месяц». Спасает предлог
    # интервала — см. _EVERY.
    rf"(?i)\b{_MODAL}\b{_GAP}\b{_EVERY}\s+(?:{_NUM}\s*)?{_TIME_UNIT}",
    rf"(?i)\b{_EVERY}\s+(?:{_NUM}\s*)?{_TIME_UNIT}{_GAP}\b{_MODAL}\b",
    # интервал и курс как утверждения сами по себе
    r"(?i)\b(?:интервал\w*|перерыв\w*)\s+(?:между|в)\b",
    rf"(?i)\bкурс\w*\s+из\s+{_NUM}",
    r"(?i)\b(?:повтор\w*|приходит[ья]|записыва\w*)\s+(?:кажд\w+|раз\s+в)\b",
    r"(?i)\bраз\s+в\s+(?:недел\w*|месяц\w*|полгода|год)\b",
    # совместимость — OD-CI-4/CI-5: реестра нет и не будет, значит сказать нечего
    r"(?i)\bнельзя\s+(?:совмещать|сочетать|делать\s+вместе)",
    r"(?i)\b(?:не\s+)?совместим\w*\s+(?:с|со)\b",
    r"(?i)\bнесовместим\w*",
    # восстановление и подготовка
    r"(?i)\b(?:восстановлени\w*|реабилитаци\w*|заживлени\w*)\s+"
    r"(?:занима\w*|длит\w*|составля\w*|проходит|идёт|идет)",
    # Подготовка: «за неделю до процедуры не загорайте». Числа может не
    # быть вовсе («за сутки до»), поэтому единица допускается голой.
    #
    # Но одного «за N до» мало: «за день до визита напомню» — это про НАС
    # и никакой нормы не утверждает. Поэтому требуется ещё и запрет либо
    # предписание человеку. Без этого условия фильтр съел бы полезное
    # напоминание, а такой фильтр выключают через неделю.
    rf"(?i)\bза\s+(?:{_QTY}|{_UNIT})\s+до\b{_GAP}"
    r"(?:нельзя|не\s+рекоменд\w*|не\s+стоит|воздержит\w*|откажит\w*|"
    r"нужно|необходимо|"
    r"не\s+(?:загора\w*|моч(?:и|ите)\w*|принима\w*|пейте|пить|ешьте|"
    r"есть|употребля\w*|наноси\w*|брейте|брить))",
    r"(?i)\bпосле\s+процедур\w*\s+(?:нельзя|не\s+рекомендуется|нужно|нельзя\s+будет)",
)

_CATEGORIES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("medical", _MEDICAL),
    ("promise", _PROMISES),
    ("contact", _CONTACTS + _PARTIAL_PHONES),
    ("nag", _NAG),
    ("planning", _PLANNING),
)

#: What the person reads instead. Says the shape of the problem without
#: pretending the assistant knows the answer.
REPLACEMENT_TEXT = (
    "Не могу это ответить — тут нужен человек, а не помощник. Спросите администратора салона."
)


@dataclass(frozen=True)
class OutboundVerdict:
    """Whether the drafted reply may be sent, and what to send instead."""

    allowed: bool
    text: str
    categories: tuple[str, ...] = field(default_factory=tuple)

    @property
    def blocked(self) -> bool:
        return not self.allowed


def evaluate_outbound(text: str) -> OutboundVerdict:
    """Check a drafted reply before it reaches a person.

    Returns the original text when clean, and :data:`REPLACEMENT_TEXT`
    with the matched categories when not. Never raises: a crash in a
    safety check must not be the thing that costs someone their answer.
    """

    body = text or ""
    if not body.strip():
        return OutboundVerdict(allowed=True, text=body)

    hits: list[str] = []
    try:
        for label, patterns in _CATEGORIES:
            if any(re.search(p, body) for p in patterns):
                hits.append(label)
    except Exception:  # noqa: BLE001 — a broken regex must not eat the turn
        logger.exception("safety.outbound.check_failed")
        return OutboundVerdict(allowed=True, text=body)

    if not hits:
        return OutboundVerdict(allowed=True, text=body)

    # Category only. Logging the sentence would copy the thing we just
    # decided not to show anyone.
    logger.warning("safety.outbound.blocked categories=%s len=%d", ",".join(hits), len(body))
    return OutboundVerdict(allowed=False, text=REPLACEMENT_TEXT, categories=tuple(hits))


__all__ = ["REPLACEMENT_TEXT", "OutboundVerdict", "evaluate_outbound"]
