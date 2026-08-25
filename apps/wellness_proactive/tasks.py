"""Повод «внести текущий вес» — NBA семейства OBSERVE от Personal Plan (DRF-1344).

Personal Plan поставляет в решающий слой три вещи и ничего больше: код
Desired Outcome, факты о состоянии ряда наблюдений, семейство ``OBSERVE``.
Дальше по цепочке из тикета::

    Personal Plan -> повод OBSERVE -> решение (не LLM)
       -> consent_blocker(PERSONAL_DATA + HEALTH)
       -> vet_outbound
       -> отправка | ничего

**Отправки в этой задаче нет.** Формулировок текста в DRF-1344 нет — есть
код повода и границы — поэтому конвейер завершается решением и записью
следа, а не сообщением. ``Decision.send`` не существует: структурно
нечего ставить в ``True``. Терминальное зелёное состояние —
``reason="observe_due"``: повод порождён, оба гейта пройдены, наружу не
ушло ничего. ``no_action`` — тоже валидный зелёный результат: план,
который каждую неделю обязан что-то сказать, уже сломан.

### Порядок внутри цепочки

Гейт получателя вычисляется **до** обращения к Ayla, хотя в диаграмме
тикета повод стоит раньше. Две причины: ``consent_blocker`` — дешёвый
читаемый ответ из локальной БД, а документ wellness-context — это
health-class данные, и та же пара согласий (PERSONAL_DATA + HEALTH,
:mod:`apps.orchestrator.nutrition_context`) является основанием для их
*чтения*, не только для отправки. На след это не влияет: решение всё
равно называет сработавший гейт своим slug'ом.

### Гейт текста без текста

``vet_outbound`` остаётся в цепочке и проверяет единственное, что вообще
могло бы уйти наружу из этой задачи, — сериализацию кодов повода
(``OBSERVE:<target>:<progress_state>``). Срабатывание означает
«отправлено ничего», а не заглушку: unsolicited-замена текста здесь та
же бессмыслица, что и в DRF-1285/1307. Проверка кодов выглядит
избыточной — и пусть: это страховка на будущее редактирование, которое
однажды добавит в payload что-то, чему там не место.

### След

Каждое решение пишется в лог кодами повода (``Decision.as_log`` — без
значений ряда конструкционно: полей под них нет). В audit попадают три
отклонения от массового зелёного: закрытый гейт получателя, закрытый
гейт текста, ``observe_due``. Оператор по ``reason`` + ``gate`` видит,
какой именно гейт сработал. ``gated`` / ``no_plan`` / ``no_outcomes`` /
``no_action`` / ``ayla_unavailable`` — только лог: сегодня контур Ayla
закрыт (DRF-1333 не пройдён), и это массовые состояния, а не события.

### Что намеренно не заведено (границы тикета)

* **Свой тихий час, свой beat-планировщик, свой словарь аудита** — не
  заводятся; когда появятся тексты и отправка, они берутся у DRF-1285
  (:mod:`apps.nutrition_proactive`). Здесь ничего не отправляется, поэтому
  и тихий час нечего глушить. В ``CELERY_BEAT_SCHEDULE`` задача не
  встала по той же причине: регистрация тика — это решение про отправку.
* **Свой класс поводов** — не заводится; семейство одно, ``OBSERVE``.
* **Значения наблюдений** — не покидают документ: ни в payload решения,
  ни в логе, ни в аудите. Только коды. Это проверено grep-тестом.

### Выключатель

``WELLNESS_PROACTIVE_ENABLED`` (default ``False``) — задача возвращается
сразу. Второго выключателя (dry-run) нет и не нужно: модуль не
отправляет ничего при любом раскладе, вся задача структурно сухая.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from celery import shared_task  # type: ignore[import-untyped]
from django.conf import settings

from apps.audit.services import write_audit
from apps.consent.models import ConsentRecord
from apps.integrations.ayla import external_user_id_for
from apps.integrations.ayla.wellness_context_client import (
    OutcomeState,
    WellnessContext,
    WellnessContextError,
    WellnessContextHttpClient,
)
from apps.notifications.proactive import consent_blocker, vet_outbound

logger = logging.getLogger(__name__)

#: Семейство повода. Одно; своего класса поводов задача не заводит.
OCCASION_FAMILY = "OBSERVE"

#: Факты о состоянии ряда, которые порождают повод. Только коды.
TRIGGER_PROGRESS_STATES = frozenset({"no_observations", "baseline_only"})
TRIGGER_HORIZON_STATUS = "elapsed"

#: Оба согласия — стандарт чтения и отправки health-class данных
#: (DRF-1338; тот же набор, что у apps.orchestrator.nutrition_context).
REQUIRED_CONSENTS = (
    ConsentRecord.ConsentType.PERSONAL_DATA.value,
    ConsentRecord.ConsentType.HEALTH.value,
)

#: Cap на строки за тик — тот же смысл, что у nutrition_proactive:
#: пилотный список получателей однозначный, cap существует, чтобы будущий
#: импорт не превратил один тик в неограниченный fan-out.
BATCH_LIMIT = 500

#: Стабильный словарь причин — для dry-run-отчётов и тестов. Первые шесть
#: приходят из apps.notifications.proactive.BLOCK_REASONS (гейт получателя
#: с обоими согласиями), ``outbound_safety_*`` — из vet_outbound.
REASONS = (
    "opt_out",
    "deleted",
    "no_consent",
    "consent_withdrawn",
    "consent_unproven",
    "no_health_consent",
    "ayla_unavailable",
    "gated",
    "no_plan",
    "no_outcomes",
    "no_action",
    "observe_due",
)


def enabled() -> bool:
    return bool(getattr(settings, "WELLNESS_PROACTIVE_ENABLED", False))


@dataclass
class Decision:
    """Одно решение по одному получателю. ``send`` нет: отправки нет.

    ``occasion`` — порождён ли повод OBSERVE. ``gate`` — какой гейт
    закрыл конвейер (``"recipient"`` / ``"text"``), пусто, когда решение
    принял решающий слой документа. ``reason`` — slug из :data:`REASONS`
    либо ``outbound_safety_*``.
    """

    bot_user_id: Any
    external_user_id: str
    occasion: bool
    reason: str
    gate: str = ""
    detail: dict[str, Any] = field(default_factory=dict)

    def as_log(self) -> dict[str, Any]:
        """PII-free проекция для лога и аудита — только коды.

        Полей под значения ряда у :class:`Decision` нет, поэтому проекция
        не может их потечь конструкционно; положительная стража на то,
        что коды при этом присутствуют, — в тестах.
        """
        return {
            "bot_user_id": str(self.bot_user_id),
            "external_user_id": self.external_user_id,
            "occasion": self.occasion,
            "reason": self.reason,
            "gate": self.gate,
            **self.detail,
        }


# -- правила повода -----------------------------------------------------------


def observe_occasion_codes(outcome: OutcomeState) -> dict[str, str] | None:
    """Коды повода OBSERVE для одного outcome, либо None.

    Повод есть, когда ряд пуст (``no_observations`` / ``baseline_only``)
    или горизонт истёк (``elapsed``). В словаре — ТОЛЬКО коды: семейство,
    код результата, коды состояния ряда. Никогда значения наблюдений.
    """
    if (
        outcome.progress_state not in TRIGGER_PROGRESS_STATES
        and outcome.horizon_status != TRIGGER_HORIZON_STATUS
    ):
        return None
    return {
        "family": OCCASION_FAMILY,
        "outcome": outcome.target,
        "progress_state": outcome.progress_state,
        "horizon_status": outcome.horizon_status,
    }


def evaluate_document(context: WellnessContext) -> tuple[str, list[dict[str, str]]]:
    """Документ -> (reason, поводы). Чистая функция, без БД и сети.

    ``gated``-документ — это отказ контура, а не данные: повода нет.
    Нет плана или нет outcomes — повода нет. Иначе повод вычисляется из
    фактов о состоянии ряда по каждому outcome; пустой список поводов —
    ``no_action``, валидный зелёный результат.
    """
    if context.gated:
        return "gated", []
    if not context.has_plan:
        return "no_plan", []
    if not context.outcomes:
        return "no_outcomes", []
    occasions = [
        codes for outcome in context.outcomes if (codes := observe_occasion_codes(outcome))
    ]
    if not occasions:
        return "no_action", []
    return "observe_due", occasions


def _occasion_wire_text(occasions: list[dict[str, str]]) -> str:
    """Единственное, что могло бы уйти наружу из этой задачи: коды."""
    return ";".join(f"{c['family']}:{c['outcome']}:{c['progress_state']}" for c in occasions)


# -- планировщик --------------------------------------------------------------


def _base_queryset():
    """BotUser'ы, которым конвейер вообще мог бы понадобиться.

    Кросс-тенантно (тик системный). Opt-out и erasure отфильтрованы здесь
    и перепроверены построчно через ``consent_blocker`` — тот же
    belt-and-braces, что в DRF-1285: отказ этих двух условий — нарушение
    доверия, а не пропущенное сообщение. ``chat_id`` не фильтруется:
    отправки нет, адрес не нужен.
    """
    from apps.identity.models import BotUser

    return (
        BotUser.all_tenants.filter(proactive_messages_opt_out=False)
        .filter(deleted_at__isnull=True)
        .order_by("pk")[:BATCH_LIMIT]
    )


def plan_observe_occasions(
    *,
    fetch: Callable[[str], WellnessContext] | None = None,
) -> list[Decision]:
    """Оценить каждого кандидата на повод OBSERVE.

    Чиста относительно внешнего мира, кроме ``fetch`` — инжектируется,
    чтобы тесты гоняли ту же арифметику без живой Ayla.
    """
    fetch = fetch or _fetch_wellness_context
    decisions: list[Decision] = []

    for bot_user in _base_queryset():
        ext = external_user_id_for(bot_user)

        def decide(
            reason: str,
            *,
            occasion: bool = False,
            gate: str = "",
            detail: dict[str, Any] | None = None,
        ) -> Decision:
            """Привязать инвариантные поля строки, чтобы ни одна ветка их не забыла."""
            return Decision(
                bot_user_id=bot_user.pk,
                external_user_id=ext,
                occasion=occasion,
                reason=reason,
                gate=gate,
                detail=detail or {},
            )

        # Гейт получателя — до чтения health-class документа (см. модуль).
        blocked = consent_blocker(bot_user, required_consents=REQUIRED_CONSENTS)
        if blocked:
            decisions.append(decide(blocked, gate="recipient"))
            continue

        try:
            context = fetch(ext)
        except WellnessContextError as exc:
            logger.warning("wellness_proactive.observe.fetch_failed ext=%s err=%s", ext, exc)
            decisions.append(decide("ayla_unavailable"))
            continue

        reason, occasions = evaluate_document(context)
        if reason != "observe_due":
            decisions.append(decide(reason))
            continue

        # Гейт текста — над единственным outbound-содержимым, которое
        # существует в этой задаче: сериализацией кодов повода.
        _, blocked_by = vet_outbound(_occasion_wire_text(occasions))
        if blocked_by:
            # Отправлено ничего, а не заглушка.
            decisions.append(decide(blocked_by, occasion=True, gate="text"))
            continue

        decisions.append(decide("observe_due", occasion=True, detail={"occasions": occasions}))

    return decisions


def _fetch_wellness_context(ext: str) -> WellnessContext:
    return WellnessContextHttpClient().get_wellness_context(external_user_id=ext)


# -- исполнение: след, не сообщение -------------------------------------------

AUDIT_ACTION = "wellness_proactive.observe.decision"


@shared_task(name="wellness_proactive.evaluate_observe_occasions")
def evaluate_observe_occasions() -> dict[str, int]:
    """Прогнать конвейер до гейтов и записать след. Ничего не отправляет.

    В beat не зарегистрирована намеренно: регистрация тика — решение про
    отправку, а отправка — за пределами DRF-1344. Вызывается вручную /
    будущим тиком, переиспользующим инфраструктуру DRF-1285.
    """
    if not enabled():
        logger.info("wellness_proactive.observe.disabled")
        return {"planned": 0, "occasions": 0, "deliverable": 0, "blocked": 0, "enabled": 0}

    decisions = plan_observe_occasions()
    occasions = sum(1 for d in decisions if d.occasion)
    deliverable = sum(1 for d in decisions if d.reason == "observe_due")
    blocked = sum(1 for d in decisions if d.gate)

    for decision in decisions:
        logger.info("wellness_proactive.observe.decision %s", decision.as_log())
        if decision.occasion or decision.gate:
            _audit(decision)

    logger.info(
        "wellness_proactive.observe.summary planned=%d occasions=%d deliverable=%d blocked=%d",
        len(decisions),
        occasions,
        deliverable,
        blocked,
    )
    return {
        "planned": len(decisions),
        "occasions": occasions,
        "deliverable": deliverable,
        "blocked": blocked,
        "enabled": 1,
    }


def _audit(decision: Decision) -> None:
    """Audit-запись для отклонений от массового зелёного — коды, не значения."""
    try:
        write_audit(
            action=AUDIT_ACTION,
            target="BotUser",
            target_id=decision.bot_user_id,
            payload=decision.as_log(),
        )
    except Exception:  # noqa: BLE001
        logger.exception("wellness_proactive.observe.audit_failed")
