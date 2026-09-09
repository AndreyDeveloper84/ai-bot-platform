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
  a reason to contain (DRF-1039).

Anything subtler stays with the prompt. A greedy filter that mangles decent
replies would get itself turned off within a week, and then there would be
no filter at all.

### What happens on a hit

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

_CATEGORIES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("medical", _MEDICAL),
    ("promise", _PROMISES),
    ("contact", _CONTACTS),
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
