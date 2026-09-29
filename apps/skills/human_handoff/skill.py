"""HumanHandoffSkill (DRF-470 / Sprint 3 / D3).

Trigger: user asks for a human operator. The skill creates an
``AdminTask`` via :func:`apps.handoff.services.create_admin_task`
(which atomically flips ``Conversation.state`` → HUMAN_HANDOFF and
emits the canonical ``handoff_initiated`` event) and confirms to the
user that an operator is on the way.

### Dispatcher guard

D1's :func:`apps.skills.registry.dispatch` is upgraded in this task
(D3) with a state guard: when the conversation is currently in
HUMAN_HANDOFF, ``dispatch()`` returns a "silent" SkillResult
(``should_send=False``) instead of running any skill. The bot stays
quiet until the operator resolves the handoff via
``resolve_admin_task`` (C2) which flips state back to IDLE.

This is enforced at the dispatcher layer rather than per-skill so
every Sprint 4+ skill inherits the guard for free.
"""

from __future__ import annotations

import logging
import re
from typing import ClassVar

from apps.handoff.models import AdminTask
from apps.handoff.services import create_admin_task
from apps.skills.base import SkillContext, SkillResult
from apps.skills.registry import register

logger = logging.getLogger(__name__)

_HANDOFF_KEYWORDS: tuple[str, ...] = (
    "оператор",
    "связь с человеком",
    "поддержка живой",
    "живая поддержка",
    "менеджер",
    "администратор",
    "админ",
    "human",
    "agent",
    "manager",
    # DRF-2545: «let me talk to an operator» / «operator please» не ловились —
    # по-английски слова «operator» в словаре не было.
    "operator",
)


# ─── Правило «просят человека?» — одно на оба пути (DRF-2545) ───────────────
#
# До DRF-2545 на один вопрос отвечали два матчера по-разному: навык салона —
# голой подстрокой (``kw in text``), глобальный путь — той же подстрокой плюс
# окно отрицания в 15 символов перед словом. Замер на собранных корпусах:
# ложных срабатываний 24/24 и 21/24 — правило отвечало на «встречается ли
# слово», а не на «просят ли человека», и будило салон на любое упоминание
# сотрудника («спасибо администратору», «админка не открывается»,
# «оператор мне не нужен»). Корпуса и числа до/после —
# ``apps/skills/human_handoff/tests/handoff_request_corpus.py``.
#
# Что правило требует, по фразе (фраза = кусок между . , ; ! ? и «но»/«а»):
#
# 1. слово словаря ЦЕЛИКОМ, с окончанием (``менеджера``), но не внутри другого
#    слова: «админ» не ловит «админку» и «администрацию»;
# 2. сотрудник — АДРЕСАТ просьбы: не после «от», не в дательном без «к»
#    («напишу администратору»), не подлежащее чужого глагола («менеджер хочет»);
# 3. в фразе нет отрицания — ни до слова, ни после («оператор мне не нужен»),
#    и нет прошедшего времени («нужен был вчера»);
# 4. в фразе есть конструкция просьбы («позовите», «дайте», «хочу», «нужен»,
#    «можно», «бы», «где», «есть …» …) — ИЛИ фраза состоит из одного обращения
#    («оператор!», «менеджера, пожалуйста»).
#
# «человек» в словарь по-прежнему не входит — решение DRF-972, закреплено
# узлом ``test_human_word_not_added_no_false_escalation``.

_SHORT_FORMS = {"админ": r"(?:а|у|ом|е|ы|ов|ам|ами|ах)?"}


def _word_pattern(keyword: str) -> str:
    if keyword in _SHORT_FORMS:
        return keyword + _SHORT_FORMS[keyword]
    if keyword.isascii():
        return keyword + "s?"
    return keyword + r"\w*"


def _phrase_pattern(phrase: str) -> str:
    stems = [w if len(w) <= 2 else w[: max(len(w) - 2, 3)] + r"\w*" for w in phrase.split()]
    return r"\s+".join(stems)


_STAFF_WORD = re.compile(
    r"(?<!\w)(?:"
    + "|".join(_word_pattern(k) for k in _HANDOFF_KEYWORDS if " " not in k)
    + r")(?!\w)"
)
_PHRASE = re.compile("|".join(_phrase_pattern(k) for k in _HANDOFF_KEYWORDS if " " in k))
_CLAUSE_SPLIT = re.compile(r"[.,;!?\n]+|\s(?:но|а|but)\s")
_NEGATION = re.compile(
    r"(?<!\w)(?:не|нет|без|ни|никак\w*|никого|ничего|not|no|don't|dont|never)(?!\w)"
)
_PAST = re.compile(r"(?<!\w)(?:был|была|было|были|was|were)(?!\w)")
_REQUEST = re.compile(
    r"(?<!\w)(?:"
    r"позов\w*|позв\w*|зов\w*|пригла\w*|соедин\w*|свяж\w*|связа\w*|подключ\w*|переключ\w*"
    r"|дай|дайте|давай\w*|хочу|хотим|хотел\w*|нуж\w*|можно|желательно|требуется|пожалуйста"
    r"|бы|где"
    r"|speak|talk|connect|need|want|call|get|contact|give|transfer|please|can|could|let"
    r")(?!\w)"
)
_FILLER = frozenset(
    {
        "пожалуйста",
        "срочно",
        "скорее",
        "быстрее",
        "уже",
        "ну",
        "эй",
        "мне",
        "нам",
        "привет",
        "hello",
        "hi",
        "please",
        "a",
        "an",
        "the",
        "now",
        "asap",
    }
)
_DATIVE = re.compile(r"(?:ору|еру|ину|ам|у)$")
_THIRD_PERSON_VERB = re.compile(r"\w+(?:ет|ит|ут|ют|ат|ят)")


def _addressed_staff(clause: str) -> bool:
    """Есть ли в фразе сотрудник, к которому можно обращаться с просьбой."""
    for m in _STAFF_WORD.finditer(clause):
        before = clause[: m.start()].split()
        prev = before[-1] if before else ""
        word = m.group(0)
        if prev == "от":
            continue
        if not word.isascii() and _DATIVE.search(word) and prev != "к":
            continue
        after = clause[m.end() :].split()
        if not before and after and _THIRD_PERSON_VERB.fullmatch(after[0]):
            continue
        return True
    return False


def is_handoff_request(text: str) -> bool:
    """Просит ли человек живого сотрудника — единое правило обоих путей (DRF-2545)."""
    for clause in _CLAUSE_SPLIT.split(text.lower()):
        clause = clause.strip()
        if not (_addressed_staff(clause) or _PHRASE.search(clause)):
            continue
        if _NEGATION.search(clause) or _PAST.search(clause):
            continue
        if _REQUEST.search(clause) or clause.startswith("есть "):
            return True
        rest = _PHRASE.sub(" ", _STAFF_WORD.sub(" ", clause))
        if all(w in _FILLER for w in re.findall(r"[\w']+", rest)):
            return True
    return False


_HANDOFF_REPLY = "Передаю менеджеру — ответят в течение 30 минут."


@register
class HumanHandoffSkill:
    """Trigger: user explicitly asks for a human operator."""

    name: ClassVar[str] = "human_handoff"

    def matches(self, context: SkillContext) -> bool:
        return is_handoff_request(context.message_text)

    def handle(self, context: SkillContext) -> SkillResult:
        reason = f"Trigger phrase: {context.message_text[:80]}"
        # create_admin_task atomically:
        #   - inserts AdminTask with the transcript snapshot
        #   - flips Conversation.state IDLE → HUMAN_HANDOFF
        #   - emits the canonical `handoff_initiated` event
        #   - writes the `handoff.created` audit row
        task = create_admin_task(
            context.conversation,
            task_type=AdminTask.TaskType.HANDOFF,
            reason=reason,
        )
        logger.info(
            "human_handoff.created task=%s conversation=%s",
            task.id,
            context.conversation.id,
        )
        return SkillResult(
            reply_text=_HANDOFF_REPLY,
            new_state="human_handoff",
            meta={
                "skill": self.name,
                "task_id": str(task.id),
                "task_type": AdminTask.TaskType.HANDOFF,
            },
        )
