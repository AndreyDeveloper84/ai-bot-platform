"""Теневой режим DecisionReadiness в живом ходе (DRF-1882, бриф окна «Мозг» п.5).

### Что это

Движок (``apps.orchestrator.decision_readiness``) построен и не подключён: у
``shadow.observe()`` не было ни одного вызывающего. Решение владельца C1 (12.09,
пакет 2) — включить теневой режим: движок считает, что он решил бы, и **не
управляет** ходом, пока пороги не доказаны.

Здесь — единственный вызывающий: после того как ответ человеку уже отправлен,
из того, что у хода есть, собирается вход движка, и ``observe()`` пишет одну
строку лога — рядом с тем, что сделал текущий путь (ветка, инструменты, сколько
карточек мастеров показано). Одна строка на ход; кодами, не словами.

### Вход — честный, а не удобный

* **Состояние** — чтение DR-состояния без ``INCR``: теневой прогон не имеет
  права двигать нумерацию ревизий, которую потом будут читать настоящие
  писатели.
* **Безопасность** — то, что лежит в состоянии. С DRF-1885 ход открывает новую
  ревизию и пишет в неё вердикт ``pre_check`` (:func:`record_turn_safety`, при
  включённом флаге), поэтому тень читает вердикт ЭТОГО хода. До того на каждом
  ходу было ``not_evaluated`` → ``SAFETY_UNKNOWN`` (замер 14.09); следующий
  пробел — ``BLOCK_READINESS_INPUT_UNAVAILABLE`` (probe/ledger/кандидаты).
* **Кандидаты** — подпись из ``show_masters`` этого хода: сколько показано и в
  каком порядке. ``recommendation_eligible_count=None`` — в боте не видно,
  прошла ли услуга VERIFIED (решение C2); ``separation=None`` — **вид**
  ``separation``, не только порог τ, — управляемая политика владельца
  (``beautygo_backend recommendation/_pipeline.py``, DRF-1519/1533,
  OD-DR-SEPARATION-FORM). Число, придуманное здесь, было бы политикой,
  назначенной молча.
* **Ledger, probe, каталог вопросов, спецификация контекста** — недоступны /
  пусты, так и сказано во ``InputAvailability``. Политика — не откалибрована.

### Чего это не делает

Не влияет на ход — по типу, а не по обещанию: ``observe()`` возвращает запись,
в которой нет решения (``ShadowRecord.influenced_the_turn`` всегда False).
При выключенном ``DRE_SHADOW_ENABLED`` — ноль работы: ни Redis, ни движка.
Не бросает никогда: наблюдение не стоит хода.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from typing import Any

logger = logging.getLogger(__name__)

#: Имя строки лога — по нему считается расхождение «путь показал мастеров, а
#: движок не был вправе рекомендовать».
LIVE_LOG_EVENT = "decision_readiness.shadow.live_turn"

#: Дайджест подписи, когда на ходу мастеров не искали. Отдельное значение, а
#: не пустая строка: «не искали» и «искали и нашли ноль» — разные факты.
NOT_SEARCHED = "not_searched"


def _last_search(tool_trace: Any) -> dict[str, Any] | None:
    """Последний сработавший ``show_masters`` хода с числом результатов, или None."""

    found: dict[str, Any] | None = None
    for entry in tool_trace or ():
        if not isinstance(entry, dict) or entry.get("tool") != "show_masters":
            continue
        if str(entry.get("result") or "").startswith("declined"):
            continue
        if isinstance(entry.get("result_count"), int):
            found = entry
    return found


def candidate_signature(tool_trace: Any) -> tuple[Any, bool]:
    """Подпись кандидатов из трассы хода и признак «искали на этом ходу»."""

    from apps.orchestrator.decision_readiness.candidates import CandidateSetSignature

    entry = _last_search(tool_trace)
    if entry is None:
        return (
            CandidateSetSignature(
                digest=NOT_SEARCHED, visible_count=0, recommendation_eligible_count=None
            ),
            False,
        )
    ids = tuple(str(i) for i in (entry.get("ordered_ids") or ()))
    digest = hashlib.sha256("\x1f".join(ids).encode("utf-8")).hexdigest()[:16]
    return (
        CandidateSetSignature(
            digest=digest,
            visible_count=max(0, int(entry["result_count"])),
            recommendation_eligible_count=None,
            ordered_ids=ids,
            separation=None,
        ),
        True,
    )


def _conversation_state(conversation_id: str) -> Any:
    """DR-состояние разговора — только чтение, без выдачи новой ревизии."""

    from apps.orchestrator.decision_readiness import state as state_mod

    found = state_mod.load(conversation_id)
    if found.state is not None:
        return found.state
    revision = state_mod.peek_revision(conversation_id) or 1
    return state_mod.ConversationState(conversation_id=conversation_id, revision=max(1, revision))


def build_live_input(conversation_id: str, tool_trace: Any) -> Any:
    """``ReadinessInput`` из того, что есть у живого хода, — с названными пропусками."""

    from apps.orchestrator.decision_readiness import engine as eng
    from apps.orchestrator.decision_readiness.events import Surface
    from apps.orchestrator.decision_readiness.ledger import QuestionLedger
    from apps.orchestrator.decision_readiness.policy import UNCALIBRATED_POLICY
    from apps.orchestrator.decision_readiness.required_context import EMPTY_SPEC, Mode

    state = _conversation_state(conversation_id)
    signature, searched = candidate_signature(tool_trace)
    return eng.ReadinessInput(
        state_revision=state.revision,
        state=state,
        mode=Mode.DISCOVERY,
        safety=state.safety,
        candidates=signature,
        policy=UNCALIBRATED_POLICY,
        required_context_spec=EMPTY_SPEC,
        question_ledger=QuestionLedger(),
        availability=eng.InputAvailability(
            ledger_readable=False, probe_available=False, candidates_fresh=searched
        ),
        surface=Surface.MAX_CHAT,
    )


class LivePathSink:
    """Одна строка: что решил бы движок — и что сделал текущий путь."""

    def __init__(
        self,
        *,
        branch: str,
        tools: list[str],
        cards_shown: int,
        conversation: Any = None,
        dr_state: Any = None,
        message_text: str | None = None,
    ) -> None:
        self._branch = branch
        self._tools = tools
        self._cards_shown = cards_shown
        self._conversation = conversation
        self._dr_state = dr_state
        #: DRF-1932 — только для словарей выбора NBA; в строку лога не пишется.
        self._message_text = message_text

    def _snapshot_field(self, readiness_state: str | None) -> dict[str, Any] | None:
        """Версия и digest снимка контекста хода (DRF-1903) — без содержимого.

        Содержимое уходит только в каталог (DRF-1906); в лог — отпечаток, по
        которому запись каталога сверяется с ходом. Отказ сторожа снимка
        («не код») — код ``rejected``, не текст; любой другой сбой — имя типа.
        Наблюдение не бросает.
        """

        if self._conversation is None:
            return None
        from apps.orchestrator.context_snapshot import SnapshotRejected, build_turn_snapshot

        try:
            snapshot = build_turn_snapshot(
                bot_user=getattr(self._conversation, "bot_user", None),
                conversation=self._conversation,
                dr_state=self._dr_state,
                readiness_state=readiness_state,
            )
        except SnapshotRejected:
            return {"rejected": True}
        except Exception as exc:  # noqa: BLE001 — наблюдение не стоит хода
            return {"error": type(exc).__name__}
        return {
            "snapshot_version": snapshot.snapshot_version,
            "content_digest": snapshot.content_digest,
        }

    def _policy_field(self, evidence: Any) -> dict[str, Any]:
        """Что решила бы Decision Policy v0 (DRF-1904) — коды, на ход не влияет.

        ``catalog_writable`` — пропустила бы граница записи каталога этот
        статус (до таксономии — только ``SAFETY_BOUNDARY``). Наблюдение не
        бросает: сбой — имя типа.
        """

        from apps.orchestrator.decision_policy import decide
        from apps.orchestrator.nba_taxonomy import read_turn_needs

        try:
            safety = getattr(self._dr_state, "safety", None)
            # DRF-1932: реплика читается словарями и дальше этой строки не идёт —
            # в лог уходят только коды.
            needs = read_turn_needs(self._message_text) if self._message_text is not None else None
            verdict = decide(evidence, handoff=getattr(safety, "handoff", None), needs=needs)
        except Exception as exc:  # noqa: BLE001 — наблюдение не стоит хода
            return {"error": type(exc).__name__}
        return {
            "result_status": verdict.result_status.value,
            "reason_codes": list(verdict.reason_codes),
            "facts_used": list(verdict.facts_used),
            "decision_policy_version": verdict.decision_policy_version,
            "catalog_writable": verdict.catalog_writable,
            **verdict.nba_fields(),
        }

    def record(self, evidence: Any) -> None:
        question = evidence.question or {}
        logger.info(
            "%s %s",
            LIVE_LOG_EVENT,
            json.dumps(
                {
                    "evaluation_id": evidence.readiness_evaluation_id,
                    "readiness_key": evidence.readiness_key,
                    "state_revision": evidence.state_revision,
                    "readiness_state": evidence.readiness_state,
                    "allow_recommend": evidence.allow_recommend,
                    "reason_codes": list(evidence.reason_codes),
                    "measures": evidence.measures,
                    "candidates": evidence.candidates,
                    "safety_state": (evidence.safety or {}).get("state"),
                    "question_id": question.get("question_id"),
                    "spec_version": evidence.spec_version,
                    "policy_version": evidence.policy_version,
                    "current_path": {
                        "branch": self._branch,
                        "tools": self._tools,
                        "cards_shown": self._cards_shown,
                    },
                    # Путь показал мастеров там, где движок не был вправе рекомендовать.
                    "path_recommended_without_readiness": (
                        self._cards_shown > 0 and not evidence.allow_recommend
                    ),
                    "context_snapshot": self._snapshot_field(evidence.readiness_state),
                    "decision_policy": self._policy_field(evidence),
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
        )


def observe_live_turn(
    conversation: Any,
    *,
    tool_trace: Any,
    trace_id: Any,
    branch: str,
    message_text: str | None = None,
) -> Any:
    """Прогнать движок в тени для этого хода. Возвращает ``ShadowRecord`` или None.

    При выключенном флаге — None и ноль работы. Не бросает. ``message_text`` —
    реплика человека, только для словарей выбора NBA (DRF-1932); в лог не пишется.
    """

    from apps.orchestrator.decision_readiness.shadow import observe, shadow_flag

    try:
        if conversation is None or not shadow_flag().value:
            return None
        request = build_live_input(str(conversation.id), tool_trace)
        tools = [str(e.get("tool")) for e in tool_trace or () if isinstance(e, dict)]
        sink = LivePathSink(
            branch=branch or "",
            tools=tools,
            cards_shown=request.candidates.visible_count,
            conversation=conversation,
            dr_state=request.state,
            message_text=message_text,
        )
        evaluation_id = str(trace_id) if trace_id else f"no-trace:{uuid.uuid4()}"
        return observe(request, evaluation_id=evaluation_id, sink=sink)
    except Exception:  # noqa: BLE001 — наблюдение не стоит хода
        logger.exception("decision_readiness.shadow.live_turn_failed branch=%s", branch)
        return None


def _open_turn_revision(conversation_id: str) -> Any:
    """Новая ревизия на входящее сообщение (карта C03, D1-A).

    ``safety.record.record_verdict`` ревизию сам не двигает — по его докстрингу
    «какая ревизия текущая» решает производитель хода, до safety. Без этого
    вердикт о новом сообщении получил бы ревизию прошлого — устарелость, которую
    ловит P3. LIVE-состояние сохраняется со следующим номером (слоты и эпоха —
    как были), ABSENT/EXPIRED открывает эпоху выше последней выданной ревизии.
    """

    from apps.orchestrator.decision_readiness import state as state_mod

    found = state_mod.load(conversation_id)
    if found.state is not None:
        current = found.state
        state = state_mod.ConversationState(
            conversation_id=conversation_id,
            revision=state_mod.next_revision(conversation_id),
            slots=dict(current.slots),
            epoch_started_at_revision=current.epoch_started_at_revision,
            last_activity_at=current.last_activity_at,
            safety=current.safety,
        )
    else:
        state = state_mod.open_epoch(conversation_id, after=found)
    state_mod.save(state)
    return state


def record_turn_safety(conversation: Any, gate_outcome: Any) -> Any:
    """Открыть ревизию хода и записать в неё вердикт ``pre_check`` (DRF-1885).

    Пишет только при включённом теневом режиме: сегодня это единственный
    читатель DR-состояния, и запись без читателя — работа на каждом ходу ради
    ничего. ``record_verdict`` намеренно не глотает сбой записи — перехват здесь,
    у вызывающего: ход не падает, WARN называет разговор и вердикт.
    Возвращает ``Recorded`` или None.
    """

    from apps.orchestrator.decision_readiness.shadow import shadow_flag

    raw = getattr(gate_outcome, "result", None)
    try:
        if conversation is None or raw is None or not shadow_flag().value:
            return None
        from apps.orchestrator.safety.record import record_verdict

        conversation_id = str(conversation.id)
        _open_turn_revision(conversation_id)
        return record_verdict(conversation_id, raw, source="pre_check")
    except Exception:  # noqa: BLE001 — вердикт в тень не стоит хода
        logger.warning(
            "decision_readiness.safety_record_failed conversation=%s verdict=%s",
            getattr(conversation, "id", None),
            getattr(gate_outcome, "verdict", None),
            exc_info=True,
        )
        return None


__all__ = [
    "LIVE_LOG_EVENT",
    "NOT_SEARCHED",
    "LivePathSink",
    "build_live_input",
    "candidate_signature",
    "observe_live_turn",
    "record_turn_safety",
]
