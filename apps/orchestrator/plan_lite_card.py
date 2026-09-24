"""«Мой план» в чате — предложение Ayla, подтверждение кнопкой, карточка «N из M»
(DRF-2101 → DRF-2125, §49 / §51).

### Что человек видит

* **Плана нет, цель есть** — предложение из шаблона цели
  (``GET /internal/me/plan-lite/proposal/``, DRF-2123): «Для цели «…» Ayla
  предлагает: … Почему: …» + кнопки **«Подтвердить план»**
  (``cb:plan:accept:<template_version>``) / **«Изменить»** (``open_app`` на
  «Мой план») / **«Не сейчас»** (``cb:plan:later``). Предложение ничего не
  создаёт; план создаётся ТОЛЬКО тапом «Подтвердить план» — текст «да» его
  не создаёт (сторож ``TestConfirmOnlyByButton``).
* **Цели нет** — «Сначала выберем цель» + ``open_goal_select``.
* **Цель есть, шаблона нет** — прежний текст «составить можно в приложении»
  + «Изменить» (конструктор).
* **План есть** — карточка: слова человека, ниже «На этой неделе: …» (только «N из
  M», В-5: ни процента, ни «достигнута»; у дневника при подтверждённом
  ориентире — ещё «в ориентире N», DRF-2124: второй факт, не оценка) +
  кнопки **«Записаться»**
  (``cb:plan:book`` → подбор услуг по КУРИРУЕМОМУ КЛЮЧУ цели, не
  рекомендательный движок — модуль 4 отдельно), **«В дневник»**
  (``cb:food:diary`` — структурный ход текста DRF-1837, свой код не нужен),
  **«Изменить план»** (``open_app``).

### Подтверждение

``cb:plan:accept:<v>``: свежее предложение читается заново, и ``v`` из payload
сверяется с его ``template_version``. Разошлась (шаблон обновили, пока
карточка лежала в чате) — «Предложение обновилось» и НОВАЯ карточка (старую
не редактируем — поправка главного окна 20.09), план не создаётся. Совпала —
``POST /internal/me/plan-lite/`` с действиями предложения и
``template_version`` (провенанс в каталоге). 409 — «План уже есть» и карточка
плана; нет цели — «Сначала выберем цель»; сеть — «сервис не отвечает».

### «Не сейчас»

Маркер ``plan_proposal_declined_at`` в ``skill_state["plan_lite"]`` — для
недельного возврата A4 (DRF-2126): в тот же день не дёргать. Повторный «мой
план» в тот же день предложение показывает — это явный запрос.

### Матчер и маршрутизация

Текстовый матчер прежний (DRF-2101): фразы со словом «план», под флагом
``PLAN_LITE_ENABLED``, иначе текст — модели. Тапы ``cb:plan:*`` — семейство в
``_STRUCTURED_CALLBACK_PREFIXES``, разбираются здесь до навыков; без флага
тап — «не наше» (старая кнопка в истории после выключения).

Логи — без идентификатора канала (DRF-2009): ``bot_user.pk``, класс отказа,
версия шаблона; тел нет.

Каденс ``per_2_weeks`` — «Эти 2 недели: …» (хвост #1872 закрыт здесь).
Имя цели — метка из зеркала (``_known_goals``), без неё — ключ.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from django.conf import settings

from apps.integrations.ayla import external_user_id_for
from apps.integrations.ayla.wellness_context_client import (
    PlanLite,
    PlanLiteAction,
    PlanLiteAlreadyActiveError,
    PlanLiteGoalNotFoundError,
    PlanLiteNoTemplateError,
    PlanLiteProposal,
    PlanLiteProposalAction,
    WellnessContextError,
    WellnessContextHttpClient,
)
from apps.skills.base import SkillResult

logger = logging.getLogger(__name__)

#: Фразы, которые забираются детерминированно. Нижняя граница в тесте:
#: каждая содержит «план» и не равна ему.
MY_PLAN_TRIGGERS: tuple[str, ...] = (
    "мой план",
    "покажи план",
    "какой у меня план",
    "план на неделю",
)

# ─── callbacks ────────────────────────────────────────────────────────────

CB_PREFIX = "cb:plan:"
CB_ACCEPT_PREFIX = "cb:plan:accept:"
CB_LATER = "cb:plan:later"
CB_BOOK = "cb:plan:book"
#: «В дневник» — существующий тап food_clarify (структурный ход текста).
CB_DIARY = "cb:food:diary"

#: Строгая форма payload'а (правило C01): набранное руками «cb:plan: …» — не тап.
PLAN_CALLBACK_RE = re.compile(r"^cb:plan:(accept:[1-9][0-9]{0,5}|later|book)$")

#: Слаг экрана «Мой план» — в ``MINIAPP_ROUTES`` и ``_ROUTE_MAP`` (паритет —
#: ``test_miniapp_routes``).
OPEN_PLAN_SLUG = "open_plan"
OPEN_GOAL_SLUG = "open_goal_select"
OPEN_CATALOG_SLUG = "open_catalog"

#: Ключ в ``conversation.skill_state`` — маркер «Не сейчас» для A4 (DRF-2126).
STATE_KEY = "plan_lite"
DECLINED_AT = "plan_proposal_declined_at"


@dataclass(frozen=True)
class _Copy:
    #: DRF-2283 — над списком действий стоят СЛОВА ЧЕЛОВЕКА, и ничего
    #: нашего: решение владельца 24.09 (реестр §77) — «пусть будет слова
    #: собственные клиента». Ни ярлыка, ни подписи, ни «Цель:» — строки,
    #: которую можно было бы «улучшить», здесь больше нет.
    #:
    #: Решение — про ЭТУ карточку. На другие места, где цель упоминается,
    #: оно без отдельного слова владельца не распространяется.
    title: str = "{goal}"
    week: str = "На этой неделе: {items}."
    two_weeks: str = "Эти 2 недели: {items}."
    today_suffix: str = " (сегодня)"
    #: DRF-2124 — дни ведра с суммой ≤ ориентира; печатается только когда
    #: ориентир подтверждён (``within_target_count`` не ``None``). Факт
    #: рядом с фактом: без «отлично», без ✓, без процента (В-5).
    within_target: str = ", в ориентире {n}"
    no_plan: str = (
        "Плана пока нет. Составить его можно в приложении: выбери 1–3 шага под свою цель — "
        "и я буду показывать, сколько из них сделано."
    )
    no_goal: str = "Сначала выберем цель — от неё Ayla и предложит план."
    unavailable: str = "Не могу прочитать план: сервис сейчас не отвечает. Попробуй чуть позже."
    proposal_head: str = "Для цели «{goal}» Ayla предлагает:"
    proposal_why: str = "Почему: {why}"
    proposal_tail: str = "Подтвердить план можно кнопкой ниже — или изменить его в приложении."
    accepted: str = "План составлен."
    already_active: str = "План уже есть — вот он."
    already_active_no_card: str = "План уже есть — посмотри его в приложении."
    proposal_changed: str = "Предложение обновилось — посмотри свежее:"
    later: str = "Хорошо, вернёмся к плану, когда скажешь. Напиши «мой план» — покажу снова."
    stale: str = "Эта кнопка уже не действует — напиши «мой план», покажу свежее."
    no_services_for_goal: str = "Под эту цель пока нет услуг — посмотри каталог в приложении."
    action_book: str = "записаться на услугу"
    action_food: str = "дневник"
    action_water: str = "вода"
    #: Форма обязательства в предложении (без фактов — плана ещё нет).
    propose_book: str = "записаться на услугу под цель"
    propose_food: str = "вести дневник еды {n} {days} в неделю"
    propose_food_daily: str = "вести дневник еды каждый день"
    propose_water: str = "пить воду {n} {times} в день"
    propose_water_weekly: str = "пить воду {n} {times} в неделю"
    button_accept: str = "Подтвердить план"
    button_edit: str = "Изменить"
    button_edit_plan: str = "Изменить план"
    button_later: str = "Не сейчас"
    button_book: str = "Записаться"
    button_diary: str = "В дневник"
    button_goal: str = "🎯 Выбрать цель"
    button_catalog: str = "📅 Найти услугу"


PLAN_LITE_COPY = _Copy()

_ACTION_LABELS = {
    "book_service": PLAN_LITE_COPY.action_book,
    "log_food": PLAN_LITE_COPY.action_food,
    "log_water": PLAN_LITE_COPY.action_water,
}

#: Метки кнопок по payload — для истории (тап = высказывание человека).
TAP_LABELS: dict[str, str] = {
    CB_LATER: PLAN_LITE_COPY.button_later,
    CB_BOOK: PLAN_LITE_COPY.button_book,
}


def plan_lite_enabled() -> bool:
    return bool(getattr(settings, "PLAN_LITE_ENABLED", False))


def _normalise(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower().replace("ё", "е"))


def looks_like_my_plan_request(text: str) -> bool:
    """``True`` — фраза про свой план. ``False`` — «не моё, дальше по лестнице»."""
    norm = _normalise(text)
    if not norm or len(norm) > 60:
        return False
    return any(trigger in norm for trigger in MY_PLAN_TRIGGERS)


def is_plan_callback(text: str) -> bool:
    return bool(PLAN_CALLBACK_RE.match((text or "").strip()))


def tap_history_text(text: str) -> str | None:
    """Фраза тапа для истории: метка кнопки; «Подтвердить план» — с версией нет."""
    stripped = (text or "").strip()
    if stripped.startswith(CB_ACCEPT_PREFIX):
        return PLAN_LITE_COPY.button_accept
    return TAP_LABELS.get(stripped)


# ─── склонения ────────────────────────────────────────────────────────────


def _plural(n: int, one: str, few: str, many: str) -> str:
    n_abs = abs(n)
    if n_abs % 10 == 1 and n_abs % 100 != 11:
        return one
    if 2 <= n_abs % 10 <= 4 and not 12 <= n_abs % 100 <= 14:
        return few
    return many


# ─── имя цели ─────────────────────────────────────────────────────────────


def goal_label(goal_key: str) -> str:
    """Метка цели из зеркала (то слово, что видит человек), без неё — ключ."""
    try:
        from apps.marketplace.discovery import _known_goals

        return _known_goals().get(goal_key) or goal_key or "—"
    except Exception:  # noqa: BLE001 — зеркало не должно ронять карточку
        return goal_key or "—"


def goal_words(external_user_id: str) -> str | None:
    """Цель словами человека, как он её написал (DRF-2283, CD §73).

    Источник — decision-context: документ, обращённый к человеку, из
    которого те же слова читает экран цели в Mini App. Один источник на
    две поверхности.

    **Почему не из plan_lite.** `plan_lite` едет внутри документа
    wellness-context, у которого контракт объявлен прямым текстом
    (`wellness/context_read.py`, DRF-1344): только коды состояний, **ни
    одного текста**, потому что это вход решающего слоя, а не экран.
    Дословная речь человека туда не кладётся — то же свойство, ради
    которого событие воронки несёт `has_text`, а не сам текст.

    ``None`` — слов нет ИЛИ чтение не удалось. Оба случая читаются
    одинаково нарочно: у карточки есть прежний выход — курируемая
    подпись по ключу, и единое состояние ошибки не должно съедать его.

    **Ничего не кэшируем.** Взяли на показ — показали. Кэш пережил бы
    «забудь всё» в каталоге, и стирание перестало бы быть стиранием.
    """
    try:
        from apps.integrations.ayla.goals_client import fetch_decision_context

        doc = fetch_decision_context(external_user_id=external_user_id)
        goal = (doc or {}).get("known", {}).get("goal") or {}
        words = (goal.get("goal_text") or "").strip()
    except Exception:  # noqa: BLE001 — карточка переживает отказ чтения
        logger.info("orchestrator.plan_lite.goal_words_unavailable")
        return None
    return words or None


# ─── карточка плана ───────────────────────────────────────────────────────


def _action_line(action: PlanLiteAction) -> str:
    label = _ACTION_LABELS.get(action.action_type, action.action_type)
    if action.action_type == "book_service" and action.target_count == 1:
        return f"{label} {'✓' if action.done_count >= 1 else '—'}"
    suffix = PLAN_LITE_COPY.today_suffix if action.cadence == "per_day" else ""
    line = f"{label} {action.done_count} из {action.target_count}{suffix}"
    # DRF-2124: только дневник еды и только при подтверждённом ориентире —
    # ``None`` значит «ориентира нет», и строка остаётся прежней (§103).
    # Ноль — тоже факт и печатается; per_day даёт 0/1 («сегодня в ориентире»).
    if action.action_type == "log_food" and action.within_target_count is not None:
        line += PLAN_LITE_COPY.within_target.format(n=action.within_target_count)
    return line


def render_plan_lite_card(plan: PlanLite, *, words: str | None = None) -> str:
    """Карточка — только форма обязательств и факты «N из M» (и «в ориентире N»
    у дневника, когда ориентир подтверждён — DRF-2124).

    ``per_2_weeks`` — своей строкой «Эти 2 недели: …», остальное — «На этой неделе».
    """
    # DRF-2283 — цель зовётся словами человека, когда они есть. Курируемая
    # подпись по ключу остаётся живым путём: она и ответ на «слов нет», и
    # ответ на «прочитать не удалось».
    lines = [PLAN_LITE_COPY.title.format(goal=words or goal_label(plan.goal_key))]
    weekly = [a for a in plan.actions if a.cadence != "per_2_weeks"]
    biweekly = [a for a in plan.actions if a.cadence == "per_2_weeks"]
    if weekly:
        lines.append(PLAN_LITE_COPY.week.format(items=", ".join(_action_line(a) for a in weekly)))
    if biweekly:
        lines.append(
            PLAN_LITE_COPY.two_weeks.format(items=", ".join(_action_line(a) for a in biweekly))
        )
    return "\n".join(lines)


# ─── карточка предложения ─────────────────────────────────────────────────


def _propose_line(action: PlanLiteProposalAction) -> str:
    n = action.target_count
    if action.action_type == "book_service":
        line = PLAN_LITE_COPY.propose_book
        if n > 1:
            line = f"{line} ({n} {_plural(n, 'раз', 'раза', 'раз')})"
    elif action.action_type == "log_food":
        if action.cadence == "per_day":
            line = PLAN_LITE_COPY.propose_food_daily
        else:
            line = PLAN_LITE_COPY.propose_food.format(n=n, days=_plural(n, "день", "дня", "дней"))
    elif action.action_type == "log_water":
        if action.cadence == "per_day":
            line = PLAN_LITE_COPY.propose_water.format(n=n, times=_plural(n, "раз", "раза", "раз"))
        else:
            line = PLAN_LITE_COPY.propose_water_weekly.format(
                n=n, times=_plural(n, "раз", "раза", "раз")
            )
    else:
        line = f"{action.action_type} {n}"
    if action.cadence == "per_2_weeks":
        line = f"{line} — эти 2 недели"
    return line


def render_proposal_card(proposal: PlanLiteProposal) -> str:
    lines = [PLAN_LITE_COPY.proposal_head.format(goal=goal_label(proposal.goal_key))]
    lines.extend(f"• {_propose_line(a)}" for a in proposal.actions)
    if (proposal.why or "").strip():
        lines.append(PLAN_LITE_COPY.proposal_why.format(why=proposal.why.strip()))
    lines.append(PLAN_LITE_COPY.proposal_tail)
    return "\n".join(lines)


# ─── кнопки ───────────────────────────────────────────────────────────────


def _app_button(label: str, slug: str) -> dict[str, str] | None:
    """``open_app`` на экран Mini App, без ``web_app`` — ссылка, без обоих — ничего."""
    from apps.channels.miniapp_config import miniapp_target
    from apps.skills.welcome.skill import MINIAPP_ROUTES, _miniapp_url

    if slug not in MINIAPP_ROUTES:
        return None
    target = miniapp_target()
    if target.web_app:
        return {"label": label, "callback": slug, "web_app": target.web_app}
    if target.miniapp_url:
        return {"label": label, "url": _miniapp_url(target.miniapp_url, slug)}
    return None


def _buttons(*items: dict[str, str] | None) -> dict[str, Any] | None:
    from apps.orchestrator.discovery import keyboard_envelope

    return keyboard_envelope([b for b in items if b])


def proposal_buttons(proposal: PlanLiteProposal) -> dict[str, Any] | None:
    return _buttons(
        {
            "label": PLAN_LITE_COPY.button_accept,
            "callback": f"{CB_ACCEPT_PREFIX}{proposal.template_version}",
        },
        _app_button(PLAN_LITE_COPY.button_edit, OPEN_PLAN_SLUG),
        {"label": PLAN_LITE_COPY.button_later, "callback": CB_LATER},
    )


def plan_buttons() -> dict[str, Any] | None:
    return _buttons(
        {"label": PLAN_LITE_COPY.button_book, "callback": CB_BOOK},
        {"label": PLAN_LITE_COPY.button_diary, "callback": CB_DIARY},
        _app_button(PLAN_LITE_COPY.button_edit_plan, OPEN_PLAN_SLUG),
    )


# ─── состояние «Не сейчас» ────────────────────────────────────────────────


def _mark_declined(conversation: Any) -> None:
    """Маркер для A4 (DRF-2126): в этот день предложение не дёргать.

    Read-merge-write внутри ведра ``plan_lite`` — соседние ключи (их добавит
    A4) не затираются. Глобальный путь идёт при ``current_tenant()=None`` по
    замыслу, а ``write_skill_state`` требует область — она входится на время
    одной записи и берётся у самого разговора (тот же приём, что
    ``open_question._write``; ветка «внутри навыка» её уже держит).
    """
    raw = getattr(conversation, "skill_state", None)
    bucket = dict(raw.get(STATE_KEY) or {}) if isinstance(raw, dict) else {}
    bucket[DECLINED_AT] = datetime.now(UTC).isoformat()
    try:
        from apps.conversations.models import Conversation

        if isinstance(conversation, Conversation):
            from apps.conversations.services import write_skill_state
            from apps.tenancy.context import current_tenant, tenant_scope

            if current_tenant() is not None:
                write_skill_state(conversation, STATE_KEY, bucket)
            else:
                with tenant_scope(conversation.tenant):
                    write_skill_state(conversation, STATE_KEY, bucket)
            return
    except Exception:  # noqa: BLE001 — потеря маркера стоит одного лишнего напоминания A4
        logger.warning(
            "plan_lite.state_write_failed conversation=%s",
            getattr(conversation, "id", None),
            exc_info=True,
        )
        return
    if isinstance(raw, dict):
        raw[STATE_KEY] = bucket


def declined_at(conversation: Any) -> datetime | None:
    """Когда человек нажал «Не сейчас»; ``None`` — не нажимал / маркер стёрт."""
    raw = getattr(conversation, "skill_state", None)
    bucket = raw.get(STATE_KEY) if isinstance(raw, dict) else None
    stamped = bucket.get(DECLINED_AT) if isinstance(bucket, dict) else None
    if not isinstance(stamped, str):
        return None
    try:
        at = datetime.fromisoformat(stamped)
    except ValueError:
        return None
    return at if at.tzinfo else at.replace(tzinfo=UTC)


# ─── результаты ───────────────────────────────────────────────────────────


def _result(text: str, kind: str, *, buttons: dict[str, Any] | None = None) -> SkillResult:
    return SkillResult(
        reply_text=text,
        action_type=kind,
        action_data=buttons,
        meta={"reply_kind": kind},
    )


def _plan_result(plan: PlanLite, *, prefix: str = "", words: str | None = None) -> SkillResult:
    text = render_plan_lite_card(plan, words=words)
    if prefix:
        text = f"{prefix}\n{text}"
    return _result(text, "plan_lite_card", buttons=plan_buttons())


def _proposal_result(proposal: PlanLiteProposal, *, prefix: str = "") -> SkillResult:
    text = render_proposal_card(proposal)
    if prefix:
        text = f"{prefix}\n{text}"
    return _result(text, "plan_lite_proposal", buttons=proposal_buttons(proposal))


def _no_goal_result() -> SkillResult:
    return _result(
        PLAN_LITE_COPY.no_goal,
        "plan_lite_no_goal",
        buttons=_buttons(_app_button(PLAN_LITE_COPY.button_goal, OPEN_GOAL_SLUG)),
    )


def _no_template_result() -> SkillResult:
    return _result(
        PLAN_LITE_COPY.no_plan,
        "plan_lite_none",
        buttons=_buttons(_app_button(PLAN_LITE_COPY.button_edit, OPEN_PLAN_SLUG)),
    )


def _unavailable(bot_user: Any, exc: Exception, *, step: str, trace_id: str) -> SkillResult:
    logger.warning(
        "orchestrator.plan_lite.unavailable step=%s bot_user=%s class=%s trace=%s",
        step,
        getattr(bot_user, "pk", None),
        type(exc).__name__,
        trace_id,
    )
    return _result(PLAN_LITE_COPY.unavailable, "plan_lite_unavailable")


def _proposal_or_refusal(
    client: WellnessContextHttpClient, *, external_id: str, bot_user: Any, trace_id: str
) -> SkillResult:
    """Без плана: предложение, «сначала цель», «составить в приложении» или отказ."""
    try:
        proposal = client.get_plan_lite_proposal(external_user_id=external_id)
    except PlanLiteGoalNotFoundError:
        return _no_goal_result()
    except PlanLiteNoTemplateError:
        return _no_template_result()
    except WellnessContextError as exc:
        return _unavailable(bot_user, exc, step="proposal", trace_id=trace_id)
    logger.info(
        "orchestrator.plan_lite.proposal bot_user=%s template_version=%s actions=%d trace=%s",
        getattr(bot_user, "pk", None),
        proposal.template_version,
        len(proposal.actions),
        trace_id,
    )
    return _proposal_result(proposal)


# ─── вход: текст «мой план» ───────────────────────────────────────────────


def try_handle_my_plan(
    *, text: str, bot_user: Any, trace_id: str, conversation: Any = None
) -> SkillResult | None:
    """«мой план» → карточка плана или предложение; ``None`` — не наше (флаг/текст)."""
    if not plan_lite_enabled() or not looks_like_my_plan_request(text):
        return None
    external_id = external_user_id_for(bot_user)
    client = WellnessContextHttpClient()
    try:
        ctx = client.get_wellness_context(external_user_id=external_id)
    except WellnessContextError as exc:
        return _unavailable(bot_user, exc, step="context", trace_id=trace_id)
    if ctx.plan_lite is None:
        return _proposal_or_refusal(
            client, external_id=external_id, bot_user=bot_user, trace_id=trace_id
        )
    logger.info(
        "orchestrator.plan_lite.card bot_user=%s actions=%d trace=%s",
        getattr(bot_user, "pk", None),
        len(ctx.plan_lite.actions),
        trace_id,
    )
    # DRF-2283 — один лишний REST-вызов на ПОКАЗ карточки (событие редкое),
    # а не на каждый ход. Отказ чтения оставляет карточку прежней.
    return _plan_result(ctx.plan_lite, words=goal_words(external_id))


# ─── вход: тапы cb:plan:* ─────────────────────────────────────────────────


def _accept(
    client: WellnessContextHttpClient,
    *,
    version: int,
    external_id: str,
    bot_user: Any,
    trace_id: str,
) -> SkillResult:
    """«Подтвердить план»: свежее предложение → сверка версии → POST."""
    try:
        proposal = client.get_plan_lite_proposal(external_user_id=external_id)
    except PlanLiteGoalNotFoundError:
        return _no_goal_result()
    except PlanLiteNoTemplateError:
        return _no_template_result()
    except WellnessContextError as exc:
        return _unavailable(bot_user, exc, step="accept_proposal", trace_id=trace_id)
    if proposal.template_version != version:
        # Шаблон обновили, пока карточка лежала в чате: старую не редактируем,
        # шлём новую; план по устаревшей версии не создаётся.
        logger.info(
            "orchestrator.plan_lite.proposal_changed bot_user=%s tapped=%s current=%s trace=%s",
            getattr(bot_user, "pk", None),
            version,
            proposal.template_version,
            trace_id,
        )
        return _proposal_result(proposal, prefix=PLAN_LITE_COPY.proposal_changed)
    actions = [
        {"action_type": a.action_type, "cadence": a.cadence, "target_count": a.target_count}
        for a in proposal.actions
    ]
    try:
        plan = client.create_plan_lite(
            external_user_id=external_id, actions=actions, template_version=version
        )
    except PlanLiteAlreadyActiveError:
        try:
            ctx = client.get_wellness_context(external_user_id=external_id)
        except WellnessContextError as exc:
            return _unavailable(bot_user, exc, step="accept_existing", trace_id=trace_id)
        if ctx.plan_lite is None:
            # Каталог сказал «план есть», документ его не отдал (гейт /
            # запаздывание): карточку не обещаем — дверь в приложение.
            return _result(
                PLAN_LITE_COPY.already_active_no_card,
                "plan_lite_already_active",
                buttons=_buttons(_app_button(PLAN_LITE_COPY.button_edit_plan, OPEN_PLAN_SLUG)),
            )
        return _plan_result(
            ctx.plan_lite, prefix=PLAN_LITE_COPY.already_active, words=goal_words(external_id)
        )
    except PlanLiteGoalNotFoundError:
        return _no_goal_result()
    except WellnessContextError as exc:
        return _unavailable(bot_user, exc, step="accept_create", trace_id=trace_id)
    logger.info(
        "orchestrator.plan_lite.accepted bot_user=%s template_version=%s actions=%d trace=%s",
        getattr(bot_user, "pk", None),
        version,
        len(plan.actions),
        trace_id,
    )
    return _plan_result(plan, prefix=PLAN_LITE_COPY.accepted, words=goal_words(external_id))


def _book(
    client: WellnessContextHttpClient,
    *,
    external_id: str,
    bot_user: Any,
    trace_id: str,
    conversation: Any,
) -> SkillResult:
    """«Записаться»: услуги по курируемому ключу цели плана — не рекомендательный движок.

    Выбор — ``discover_services(goal_key=…)`` по ключу напрямую (не через
    разбор метки как текста), рендер — тот же, что у инструмента
    ``show_services`` консьержа; пусто — названо, а не «ничего не нашла».
    """
    from apps.marketplace.discovery import _known_goals, discover_services
    from apps.orchestrator.discovery import (
        MAX_SERVICE_CARDS,
        render_service_cards,
        rotation_seed,
    )

    try:
        ctx = client.get_wellness_context(external_user_id=external_id)
    except WellnessContextError as exc:
        return _unavailable(bot_user, exc, step="book", trace_id=trace_id)
    if ctx.plan_lite is None:
        return _proposal_or_refusal(
            client, external_id=external_id, bot_user=bot_user, trace_id=trace_id
        )
    goal_key = ctx.plan_lite.goal_key
    services = discover_services(
        goal_key=goal_key, limit=MAX_SERVICE_CARDS + 1, rotation_seed=rotation_seed(conversation)
    )
    if not services:
        logger.info(
            "orchestrator.plan_lite.book_no_services bot_user=%s trace=%s",
            getattr(bot_user, "pk", None),
            trace_id,
        )
        return _result(
            PLAN_LITE_COPY.no_services_for_goal,
            "plan_lite_book_none",
            buttons=_buttons(_app_button(PLAN_LITE_COPY.button_catalog, OPEN_CATALOG_SLUG)),
        )
    reply = render_service_cards(
        services, shown=MAX_SERVICE_CARDS, query=_known_goals().get(goal_key) or goal_key
    )
    logger.info(
        "orchestrator.plan_lite.book bot_user=%s services=%d trace=%s",
        getattr(bot_user, "pk", None),
        len(services),
        trace_id,
    )
    return SkillResult(
        reply_text=reply.text,
        action_type="plan_lite_book",
        action_data=reply.action_data,
        meta={"reply_kind": "plan_lite_book"},
    )


def try_handle_plan_callback(
    *, text: str, bot_user: Any, trace_id: str, conversation: Any = None
) -> SkillResult | None:
    """Тап ``cb:plan:*``; ``None`` — не наше (форма / флаг)."""
    stripped = (text or "").strip()
    if not is_plan_callback(stripped):
        return None
    if not plan_lite_enabled():
        # Кнопка из истории после выключения флага: честный отказ, не модель.
        return _result(PLAN_LITE_COPY.stale, "plan_lite_stale")
    external_id = external_user_id_for(bot_user)
    client = WellnessContextHttpClient()
    if stripped == CB_LATER:
        _mark_declined(conversation)
        logger.info(
            "orchestrator.plan_lite.declined bot_user=%s trace=%s",
            getattr(bot_user, "pk", None),
            trace_id,
        )
        return _result(PLAN_LITE_COPY.later, "plan_lite_later")
    if stripped == CB_BOOK:
        return _book(
            client,
            external_id=external_id,
            bot_user=bot_user,
            trace_id=trace_id,
            conversation=conversation,
        )
    version = int(stripped[len(CB_ACCEPT_PREFIX) :])
    return _accept(
        client, version=version, external_id=external_id, bot_user=bot_user, trace_id=trace_id
    )


__all__ = [
    "CB_ACCEPT_PREFIX",
    "CB_BOOK",
    "CB_DIARY",
    "CB_LATER",
    "CB_PREFIX",
    "DECLINED_AT",
    "MY_PLAN_TRIGGERS",
    "OPEN_PLAN_SLUG",
    "PLAN_CALLBACK_RE",
    "PLAN_LITE_COPY",
    "STATE_KEY",
    "declined_at",
    "goal_label",
    "is_plan_callback",
    "looks_like_my_plan_request",
    "plan_buttons",
    "plan_lite_enabled",
    "proposal_buttons",
    "render_plan_lite_card",
    "render_proposal_card",
    "tap_history_text",
    "try_handle_my_plan",
    "try_handle_plan_callback",
]
