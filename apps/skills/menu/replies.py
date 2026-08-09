"""Shared reply copy + builders for the menu skills (DRF-963).

Lives apart from the skill modules on purpose: ``@register`` fires at
module import time, and :class:`~apps.skills.menu.help_skill.HelpSkill`
and :class:`~apps.skills.menu.skill.MenuSkill` must land at DIFFERENT
positions in the registry (help before faq, menu last before echo). If
they shared a module, importing either one would register both
side-by-side and collapse the ordering the design depends on. Keeping the
copy here lets each skill module be imported independently.
"""

from __future__ import annotations

from apps.skills.base import SkillResult
from apps.skills.menu.matching import main_menu_action_data

# U-5 — the honest fallback. Says plainly that the bot did not understand,
# then shows what it CAN do. Never echoes the user's text back.
FALLBACK_TEXT = (
    "Я пока не понял 🤔\n\n"
    "Вот что я умею:\n"
    "• записать к мастеру\n"
    "• показать ваши записи\n"
    "• перенести или отменить запись\n"
    "• ответить на вопросы об услугах, ценах и адресе\n\n"
    "Выберите действие или напишите своими словами — например, «хочу массаж»."
)

# «Помощь» / «что ты умеешь» — same menu, but framed as an answer rather
# than as a miss, so the customer isn't told they were misunderstood when
# they weren't.
HELP_TEXT = (
    "Я бот салона «Формула тела». Вот что я умею:\n\n"
    "• 📅 Записаться — подберу мастера и время\n"
    "• 📋 Мои записи — покажу ваши ближайшие визиты\n"
    "• 🔄 Перенести запись — поменяю дату или время\n"
    "• ❌ Отменить запись\n"
    "• ❓ Вопросы об услугах, ценах, адресе и режиме работы\n\n"
    "Можно нажать кнопку или написать своими словами — например, "
    "«хочу массаж спины» или «когда у меня запись?».\n"
    "Если что-то срочное — напишите «оператор», и я передам вас администратору."
)


def fallback_result() -> SkillResult:
    return SkillResult(
        reply_text=FALLBACK_TEXT,
        action_type="menu_fallback",
        action_data=main_menu_action_data(),
        meta={"reply_kind": "menu_fallback"},
        confidence=None,
    )


def help_result() -> SkillResult:
    return SkillResult(
        reply_text=HELP_TEXT,
        action_type="menu_help",
        action_data=main_menu_action_data(),
        meta={"reply_kind": "menu_help"},
        confidence=None,
    )
