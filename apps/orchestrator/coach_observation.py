"""The dietologist's observation line when the diary is opened (DRF-1464, T6).

Owner decision Q-NUTRITION-05, variant Б with the window's amendment: when
the person opens their own diary, one observation line may ride along with
the render — a *solicited* coach message. It answers an action the person
just took, so it is NOT an interruption:

* it does **not** spend the weekly ``coach_hint`` budget and does **not**
  build the ignore streak — the outbox entry carries the ``solicited``
  marker (:mod:`apps.nutrition_proactive.prefs`), and the DRF-1468
  arithmetic skips such entries;
* but it has its **own** ceiling (the window's ruling): not more than once
  per recipient-local day, and never the same unchanged observation twice
  (the same content key → silence).

### Why this lives in orchestrator, not nutrition_coach

The architecture guard
(``apps/nutrition_coach/tests/test_no_frequency_counters.py``) forbids any
cadence machinery inside ``apps/nutrition_coach``: that package answers
«what pattern» (:mod:`~apps.nutrition_coach.triggers`) and «what words»
(:mod:`~apps.nutrition_coach.copy`), never «how often». The own-limit
state and the outbox journaling ARE cadence, so the gate ladder sits here,
next to its only caller — :func:`apps.orchestrator.personal_surface.
render_diary` — and the wording stays in ``copy.OBSERVATION_TEXTS`` where
the whitelist test can hold it to the same bar as the proactive hints.

### The ladder, in order

Cheap and absolute first, fetches last — the same discipline as the
proactive planner (:mod:`apps.nutrition_proactive.coach`):

1. **coach flags** — ``NUTRITION_COACH_ENABLED`` closed: no line.
2. **HEALTH** via ``has_global_consent``, fail-closed (Q-09). The diary
   render itself runs on PERSONAL_DATA (ADR-0011 §8, the subject reading
   their own record); the observation *reasons* about the week, and that
   is written on the special-category basis — the same split
   :mod:`apps.orchestrator.food_history` documents.
3. **sensitive perimeter** — ``render.remarks_suppressed`` on the profile
   the diary render already holds: pregnancy / eating disorder / an
   unreadable profile all mean silence (same as T5).
4. **explicit goal** (R7) → **trigger** on the live week picture — «нет
   триггера, нет строки»; an observation about nothing is banned by the
   same logic as the proactive hint.
5. **own limit** — once per local day + no unchanged repeat.
6. **outbound guard** — a blocked template is silence, nothing journaled.

No quiet-hours and no weekly-budget gates: the person is awake and reading
right now — they opened the diary. No ``coach_hints`` pref check either:
that pref unsubscribes from *unsolicited* pushes; this line is part of an
answer the person asked for.

### decide / persist, split on purpose

:func:`decide_observation` never writes: the caller attaches the line only
when the composed reply still fits its budget, and only a line that was
actually shown may be journaled — the journal records sends, not intents.
:func:`persist_observation` is the one write path, and it journals with
``solicited=True`` so the shared anti-nag arithmetic stays blind to it.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Any

from apps.orchestrator.safety.outbound import evaluate_outbound

if TYPE_CHECKING:
    from apps.integrations.ayla import ProfileResponse
    from apps.nutrition_coach.goals import Goal
    from apps.nutrition_coach.history import WeekPicture

logger = logging.getLogger(__name__)

#: The outbox surface the observation is journaled on. It IS a coach_hint
#: message — a solicited one — so it shares the surface name with the
#: proactive planner (``apps.nutrition_proactive.coach.SURFACE``) and lets
#: the marker, not a second name, carry the «не лимитируется» distinction.
#: A test pins the two literals equal.
SURFACE = "coach_hint"

#: The own-limit state inside the ``nutrition_proactive`` prefs sub-dict:
#: ``{"date": <recipient-local ISO day>, "key": "<trigger kind>:<days>"}``.
OBSERVATION_STATE_KEY = "coach_observation"


class Cadence(Enum):
    """Считается ли эта строка событием для системы лимитов (OPEN_DECISIONS §39).

    Владелец 07.09 решил про приветственное слово после выдачи согласия:
    «не считать это слотом диетолога, то есть приветственное слово не
    тратит лимиты на сообщения». Формулировка важна дословно: не «в этот
    раз пропускаем потолок», а ВНЕ СИСТЕМЫ ЛИМИТОВ — ни суточного потолка
    наблюдения, ни любого другого, который появится позже.

    Поэтому здесь категория, а не булев ``skip_limit`` у одного вызова.
    Разница видна не сегодня, когда лимит один, а в тот день, когда
    появится второй: он ляжет в тот же участок (:func:`_cadence_blocks`
    и :func:`_cadence_record`), и ``UNTRACKED`` окажется вне него БЕЗ
    ЕДИНОЙ ПРАВКИ здесь. Булев флаг такого не обещает — его пришлось бы
    протаскивать в каждый новый лимит руками, и первый же забытый вернул
    бы §39 обратно в поломанное состояние, причём молча.

    ``TRACKED``
        Обычный заход в дневник: человек пришёл сам. Лимиты читаются
        (:func:`_cadence_blocks`) и ставятся (:func:`_cadence_record`).

    ``UNTRACKED``
        Приветственное слово: строка едет прицепом к действию, которое
        человек не планировал как заход в дневник. Показывается, но
        участка лимитов не касается НИ НА ЧТЕНИЕ, НИ НА ЗАПИСЬ — суточный
        слот остаётся целым для захода, который человек сделает сам.
        Журнал при этом ведётся как обычно: §39 говорит про лимиты, а не
        про журнал, и «журналируется всё» остаётся в силе — иначе мы
        потеряли бы способ узнать, что приветствие вообще уходило.
    """

    TRACKED = "tracked"
    UNTRACKED = "untracked"


@dataclass(frozen=True)
class Observation:
    """A line that passed the whole ladder, not yet journaled.

    ``content_key`` is what «the same observation» means: the fired pair
    and how many days it fired across. Same key, another open → the week
    did not change in any way the line talks about, so there is nothing
    new to say — silence (the window's ruling).
    """

    text: str
    content_key: str
    local_date: str


def decide_observation(
    bot_user: Any,
    *,
    profile: ProfileResponse | None,
    now_utc: datetime | None = None,
    fetch_goal: Callable[[Any], Goal | None] | None = None,
    fetch_history: Callable[[Any], WeekPicture] | None = None,
    cadence: Cadence = Cadence.TRACKED,
) -> Observation | None:
    """The observation line due for this diary open, or ``None``. Writes nothing.

    Same seam contract as the proactive planner: ``fetch_goal`` /
    ``fetch_history`` replace the Ayla reads in tests; defaults are the
    real readers, breaker and all.

    ``cadence`` — считается ли эта строка событием для лимитов
    (:class:`Cadence`, §39). Умолчание ``TRACKED`` — обычный заход, то есть
    поведение до §39: сторона, которая ничего не знает про приветствие, не
    может случайно оказаться вне лимитов. ``UNTRACKED`` пропускает участок
    лимитов целиком, а ВСЕ ОСТАЛЬНЫЕ ворота лестницы (флаги, HEALTH,
    чувствительный периметр, цель, триггер, страж исходящего) проходятся
    одинаково: приветствие не даёт права сказать то, чего нельзя было бы
    сказать обычным заходом.
    """
    from apps.nutrition_coach import flags as coach_flags

    if not coach_flags.enabled():
        return None

    from django.utils import timezone as dj_timezone

    from apps.nutrition_coach import copy as coach_copy
    from apps.nutrition_coach import goals, history, triggers
    from apps.nutrition_proactive import prefs, render

    now_utc = now_utc or dj_timezone.now()
    fetch_goal = fetch_goal or goals.active_goal
    fetch_history = fetch_history or history.week_picture

    if not _health_open(bot_user):
        return None
    if render.remarks_suppressed(profile):
        # The sensitive perimeter gates the WHOLE surface; an unreadable
        # profile is «не знаем», and «не знаем» is silence.
        return None
    goal = fetch_goal(bot_user)
    if goal is None:
        return None
    tz, _tz_source = prefs.resolve_timezone(bot_user)
    local_date = now_utc.astimezone(tz).date().isoformat()
    trigger = triggers.any_trigger(goal.key, fetch_history(bot_user), tz=tz)
    if trigger is None:
        return None
    content_key = f"{trigger.kind}:{trigger.days}"
    if cadence is Cadence.TRACKED and _cadence_blocks(
        prefs.get_prefs(bot_user), content_key=content_key, local_date=local_date
    ):
        return None
    verdict = evaluate_outbound(coach_copy.render_observation(trigger.kind))
    if verdict.blocked:
        # Silence, and nothing journaled: the next open evaluates fresh.
        return None
    return Observation(text=verdict.text, content_key=content_key, local_date=local_date)


def persist_observation(
    bot_user: Any,
    observation: Observation,
    *,
    now_utc: datetime | None = None,
    cadence: Cadence = Cadence.TRACKED,
) -> None:
    """Journal a SHOWN observation and stamp the own-limit state.

    Called only after the line actually went into the reply. The outbox
    entry carries ``solicited=True`` (Q-NUTRITION-05): everything outgoing
    is journaled, but this send spends no weekly budget and builds no
    ignore streak.

    ``cadence`` (§39): ЖУРНАЛ ВЕДЁТСЯ ВСЕГДА — «журналируется всё» касается
    и приветствия, иначе исходящее перестало бы быть видимым. Не ставится
    только отметка потолка (:func:`_cadence_record`): ``UNTRACKED`` не
    тратит суточный слот, и следующий, осознанный заход в те же сутки
    получит настоящее наблюдение.
    """
    from django.utils import timezone as dj_timezone

    from apps.identity.models import BotUser
    from apps.nutrition_proactive import prefs

    now_utc = now_utc or dj_timezone.now()
    # Re-read the row: the caller's instance may be stale, and a stale read
    # would clobber outbox entries journaled since it was loaded.
    stored = BotUser.all_tenants.filter(pk=bot_user.pk).first()
    if stored is None:
        return
    updated = prefs.append_outbox(
        prefs.get_prefs(stored),
        surface=SURFACE,
        sent_at=now_utc,
        solicited=True,
    )
    if cadence is Cadence.TRACKED:
        _cadence_record(updated, observation)
    context = prefs.merge_prefs(stored, updated)
    BotUser.all_tenants.filter(pk=bot_user.pk).update(context=context)
    # Keep the caller's instance in sync: decide → persist → decide inside
    # one turn must see its own write (the once-a-day limit depends on it).
    bot_user.context = context


def _health_open(bot_user: Any) -> bool:
    """HEALTH granted, proven by a record — fail-closed on any error.

    ``has_global_consent`` because the personal surface runs tenant-less.
    PERSONAL_DATA is not re-checked here: the diary render above this call
    already gated on it, and a line that rides along cannot outrun the
    message it rides on.
    """
    try:
        from apps.consent.models import ConsentRecord
        from apps.consent.services import has_global_consent

        return has_global_consent(bot_user, ConsentRecord.ConsentType.HEALTH.value)
    except Exception:  # noqa: BLE001 — fail-closed: no proven basis, no line
        logger.exception("orchestrator.coach_observation.consent_check_failed")
        return False


def _cadence_blocks(
    user_prefs: dict[str, Any],
    *,
    content_key: str,
    local_date: str,
) -> bool:
    """УЧАСТОК ЛИМИТОВ, сторона чтения. Единственная на весь модуль.

    Сегодня здесь один лимит — свой потолок окна: раз в местные сутки и не
    повторять неизменившееся. Хранимое состояние нарочно крошечное (дата и
    ключ содержания), а не счётчик: «когда» и «что» — это всё правило, а
    счётчик стал бы вторым механизмом каданса рядом с DRF-1468.

    ЛЮБОЙ НОВЫЙ ЛИМИТ КЛАДЁТСЯ СЮДА, а не отдельной проверкой выше по
    лестнице. Это не стилистика: вызывающий решает вопрос «считается ли
    эта строка событием» один раз, категорией :class:`Cadence`, и лимит,
    положенный мимо этого участка, молча отменит §39 для приветственного
    слова. Страж на это стоит в тестах модуля.
    """
    state = user_prefs.get(OBSERVATION_STATE_KEY)
    if not isinstance(state, dict):
        return False
    if state.get("date") == local_date:
        return True
    return state.get("key") == content_key


def _cadence_record(updated: dict[str, Any], observation: Observation) -> None:
    """УЧАСТОК ЛИМИТОВ, сторона записи. Обратная сторона :func:`_cadence_blocks`.

    Вызывается только для :attr:`Cadence.TRACKED` и только после того, как
    строка действительно ушла в ответ: отметка потолка говорит о
    показанном, а не о задуманном. Новый лимит, которому нужно что-то
    запоминать, запоминает это здесь — по той же причине, что и в паре
    сверху.
    """
    updated[OBSERVATION_STATE_KEY] = {
        "date": observation.local_date,
        "key": observation.content_key,
    }


__all__ = [
    "OBSERVATION_STATE_KEY",
    "SURFACE",
    "Cadence",
    "Observation",
    "decide_observation",
    "persist_observation",
]
