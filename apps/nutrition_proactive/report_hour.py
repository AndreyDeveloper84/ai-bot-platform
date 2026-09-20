"""Час отчёта из чата (DRF-2141, решение владельца В3 от 20.09).

Три команды, ни одна не идёт через модель:

* «присылай итоги/отчёт в HH[:MM]» → ``daily_report_time = "HH:MM"``;
* «не присылай отчёт/итоги»        → ``daily_report_time = "off"``;
* «во сколько ты присылаешь итоги?» → читает, что стоит, и называет обе
  команды выше.

### Почему детерминированный матчер, а не модель

До этого листа фраза «присылай итоги в 21:00» уходила консьержу, получала
дружелюбный ответ и ничего не меняла: ``daily_report_time`` выставлялся
только в профиле Mini App. Настройка, о которой человек попросил словами,
обязана измениться от этих слов -- и обязана измениться *ровно так*,
как он сказал, без интерпретации моделью, которая могла бы «понять»
21:00 как 20:00 или как вежливую просьбу.

### Что здесь НЕ делается

* «Не присылай отчёт» гасит ОДНУ поверхность -- тем же писателем и тем же
  текстом, что и кнопка «Не присылать» под отчётом
  (:func:`apps.nutrition_proactive.optout.apply_surface_opt_out`). Вода
  и платформенное вето не трогаются: это не «не пиши мне» (:mod:`optout`),
  а «этот конкретный отчёт -- нет».
* Тихие часы (:data:`prefs.QUIET_START_HOUR` / :data:`prefs.QUIET_END_HOUR`)
  не меняются -- и допустимый диапазон ВЫВЕДЕН из них, а не задан рядом:
  принимается только час, в который планировщик реально пишет
  (``tasks.plan_daily_reports`` проверяет тихие часы ДО выбранного часа).
  Лист говорил «с 6 до 23»; подтвердить 23:00 и промолчать в 23:00 --
  ложь человеку, поэтому диапазон -- :data:`MIN_HOUR`..:data:`MAX_HOUR`
  (9..21 при сегодняшнем окне), и текст отказа называет его.
* Пояс человека -- :func:`prefs.resolve_timezone`, тот же, что читает
  планировщик: «21:00» значит 21:00 там, где человек, отсюда «(по твоему
  времени)» в ответе.

### Почему матч закрытый

Как у :mod:`optout`: целое сообщение, а не подстрока. Ложное срабатывание
здесь тихое -- человек «переставил» отчёт фразой, которая была про другое,
-- поэтому «21:00» без глагола, «итоги в 21:00» и «пришли итоги» -- не
команды. Глагол обязателен и обязан быть про *регулярную* присылку.

Зависимостей от ``apps.skills`` нет намеренно: ``apps/channels/max/handler.py``
импортирует этот модуль на уровне модуля, а тот файл несёт документированный
цикл загрузки с реестром навыков. Оба флага читаются лениво.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Literal

from apps.nutrition_proactive import optout, prefs

logger = logging.getLogger(__name__)

#: ``Message.action_type`` / ``skill_selected`` for a turn this module answered.
ACTION_TYPE = "nutrition_report_hour"

#: Допустимые часы отчёта, включительно -- ровно те, что вне тихого окна
#: (:data:`prefs.QUIET_END_HOUR` .. :data:`prefs.QUIET_START_HOUR` - 1).
#: Не литерал: сдвинется окно -- сдвинется и диапазон, и текст отказа.
MIN_HOUR = prefs.QUIET_END_HOUR
MAX_HOUR = prefs.QUIET_START_HOUR - 1

#: Тексты -- из листа DRF-2141. Слот ``{time}`` -- «HH:MM».
SET_CONFIRMATION = "Хорошо, итоги дня — в {time}."
NIGHT_REFUSAL = f"Ночью не пишу — выбери час с {MIN_HOUR} до {MAX_HOUR}."
ASK_REPLY = (
    "Итоги дня присылаю в {time} (по твоему времени). "
    "Скажи „присылай итоги в 20:00“, если хочешь иначе, или „не присылай отчёт“."
)
#: Ответ на вопрос, когда отчёт выключен. Лист текста для этого состояния
#: не даёт; это его же шаблон без слота времени и без «или не присылай»,
#: которое в выключенном состоянии звучало бы как насмешка.
ASK_REPLY_OFF = (
    "Итоги дня сейчас не присылаю. Скажи „присылай итоги в 20:00“, если хочешь получать."
)

#: Самая длинная команда -- «во сколько ты присылаешь итоги дня» + время;
#: потолок держит матчер подальше от абзацев, которые лишь цитируют её.
_MAX_LEN = 60

_REPORT = r"(?:итоги|итог|отч[её]т)(?:\s+дня)?"
_SET_RE = re.compile(
    r"^(?:присылай|присылайте|отправляй|отправляйте)\s+(?:мне\s+)?"
    + _REPORT
    + r"\s+в\s+(?P<hour>\d{1,2})(?:[:.](?P<minute>[0-5]\d))?(?:\s*(?:ч|час|часа|часов))?$"
)
_OFF_RE = re.compile(
    r"^(?:больше\s+)?не\s+присылай(?:те)?\s+(?:мне\s+)?(?:больше\s+)?"
    + _REPORT
    + r"(?:\s+больше)?$"
)
_ASK_RE = re.compile(
    r"^(?:во\s+сколько|когда)\s+(?:ты\s+|вы\s+)?(?:присылаешь|присылаете|шлешь|шлете)\s+(?:мне\s+)?"
    + _REPORT
    + r"$"
)

_TRAILING_PUNCT_RE = re.compile(r"[!?.,;:…]+$")
_WS_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class ReportHourCommand:
    kind: Literal["set", "off", "ask"]
    #: «HH:MM» for ``set``; None otherwise.
    time: str | None = None
    #: The parsed hour for ``set`` -- kept apart from ``time`` so an
    #: out-of-range hour is a parsed command that gets refused, not a
    #: non-command that falls through to the model.
    hour: int | None = None


def _normalise(text: str) -> str:
    """Lowercase, fold ё, drop trailing punctuation, collapse whitespace.

    Not :func:`optout.normalise`: that one strips every colon, and «21:00»
    is the payload here.
    """
    cleaned = _TRAILING_PUNCT_RE.sub("", text.strip().lower().replace("ё", "е"))
    return _WS_RE.sub(" ", cleaned).strip()


def parse_command(text: str) -> ReportHourCommand | None:
    """The command this whole message is, or None."""
    body = text or ""
    if not body.strip() or len(body) > _MAX_LEN:
        return None
    norm = _normalise(body)
    if not norm:
        return None
    if _OFF_RE.match(norm):
        return ReportHourCommand(kind="off")
    if _ASK_RE.match(norm):
        return ReportHourCommand(kind="ask")
    m = _SET_RE.match(norm)
    if m is None:
        return None
    hour = int(m.group("hour"))
    minute = int(m.group("minute") or 0)
    if hour > 23:
        # «в 25» is not a time; the reply still names the range rather
        # than sending «присылай итоги в 25» to the concierge.
        return ReportHourCommand(kind="set", hour=hour)
    return ReportHourCommand(kind="set", time=f"{hour:02d}:{minute:02d}", hour=hour)


def enabled() -> bool:
    """Both gates the neighbours read: the nutrition master switch and
    the proactive switch. Same readers, so «не задан» means the same."""
    from apps.nutrition_proactive.tasks import enabled as proactive_enabled
    from apps.skills.menu.marketplace import nutrition_enabled

    return nutrition_enabled() and proactive_enabled()


def apply_command(bot_user: Any, command: ReportHourCommand) -> str:
    """Execute ``command`` for ``bot_user`` and return the reply."""
    if command.kind == "off":
        # The «Не присылать» button under the report: same writer, same
        # text, same effect -- one surface, no platform veto.
        return optout.apply_surface_opt_out(bot_user, "report")

    if command.kind == "ask":
        current = prefs.report_time(prefs.get_prefs(bot_user))
        if current == prefs.REPORT_OFF:
            return ASK_REPLY_OFF
        return ASK_REPLY.format(time=current)

    if command.hour is None or command.time is None or not MIN_HOUR <= command.hour <= MAX_HOUR:
        return NIGHT_REFUSAL

    prefs.write_prefs(bot_user, {"daily_report_time": command.time})
    _, tz_source = prefs.resolve_timezone(bot_user)
    logger.info(
        "nutrition_proactive.report_hour.set bot_user=%s time=%s tz_source=%s",
        bot_user.pk,
        command.time,
        tz_source,
    )
    return SET_CONFIRMATION.format(time=command.time)


def try_handle_report_hour(*, text: str, bot_user: Any) -> str | None:
    """Global-surface entry point. The reply, or None to fall through.

    Same contract as :func:`optout.try_handle_opt_out`: the pilot IS the
    global bot, so the ladder in ``handler.py`` calls this directly. With
    either flag off the turn is not ours -- it goes where the other
    nutrition branches send it (the concierge), which is what the
    neighbours do. Never raises -- a failure must not cost the turn.
    """
    try:
        command = parse_command(text)
        if command is None:
            return None
        if not enabled():
            return None
        return apply_command(bot_user, command)
    except Exception:  # noqa: BLE001 -- must never break the turn
        logger.exception("nutrition_proactive.report_hour_failed")
        return None
