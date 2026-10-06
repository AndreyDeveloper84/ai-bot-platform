"""Разговор с Ayla внутри клиентского Mini App (DRF-2799).

Решение владельца 06.10.2026 (замещает Д2 §172 / DRF-2266 для клиентской
поверхности): «диалог продолжается там, где начат». Человек в Mini App —
разговор с Ayla идёт в Mini App, а не закрывает его и не уводит в чат бота.

    GET  /customer/assistant/history  → {messages: [...]}
    POST /customer/assistant/ask      → {answer, buttons, pending_action, cards}

### Ход — тот же глобальный ход бота, а не его копия

``ask`` не заводит своего мозга и своей безопасности. Он строит событие
того же вида, что вебхук MAX, и прогоняет его через
:func:`apps.channels.max.handler._handle_global_max_event_inner` — ту же
функцию, что отвечает человеку в чате бота. Поэтому:

* **безопасность та же** — ``evaluate_inbound`` (S1), глушение при передаче
  оператору, согласия, команды памяти, охрана исходящего: развилки
  «клиентская безопасность для Mini App» нет, она буквально одна;
* **нить та же** — событие несёт MAX-id этого человека, ход разрешает ту же
  глобальную оболочку и ту же ``Conversation``, которую читают чат бота и
  «Последняя тема» (``views_last_topic``, по ``person_channel_shells``).
  Второй истории не появляется.

Меняется только доставка: на время хода
:func:`apps.channels.max.delivery_capture.capturing` перехватывает исходящее
этому человеку в трёх функциях ``outbound``, и ответ уходит телом ответа
ручки, а не в MAX.

### Личность — только из проверенного принципала

MAX-id берётся из ``request.bot_user`` — его разрешил ``require_init_data``
по подписанному ``initData``. Тело запроса личности не несёт и не может её
подменить: поле с чужим id в теле игнорируется. Иначе ход можно было бы
вписать в чужой разговор, а историю — прочитать чужую.

### Цена хода

Каждый ``ask`` — полный ход модели. Квота на человека
(:mod:`apps.miniapp_api.per_person_quota`) стоит до хода.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from django.db.models import F, Q
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from apps.identity.models import BotUser
from apps.miniapp_api.per_person_quota import over_quota, rate_limited
from apps.miniapp_api.views import _error, require_init_data

logger = logging.getLogger(__name__)

#: Длина вопроса — та же граница, что у поля ввода ``AylaChat``.
MAX_QUESTION_CHARS = 1000

#: Ходов модели в минуту на человека. Человек пишет медленнее; цикл — нет.
ASK_PER_MINUTE = 10

#: Сколько реплик отдаёт ``history``.
HISTORY_LIMIT = 50

_SURFACE = "miniapp"


def _person_max_id(bot_user: BotUser) -> str | None:
    """MAX-id человека из проверенного принципала, или ``None`` — не MAX."""
    if (bot_user.channel or "") != "max":
        return None
    raw = (bot_user.channel_user_id or "").strip()
    return raw or None


def _dialog_chat_id(person_id: str) -> str:
    """Адрес диалога этого человека с ботом — сохранённый у глобальной оболочки.

    Только читается: выдуманный адрес здесь нельзя, ход дописывает пустой
    ``chat_id`` оболочки тем, что пришёл в событии, и чужое значение сломало
    бы ей будущие сообщения от бота. Нет сохранённого — пустая строка.
    """
    from apps.identity.services.global_tenant import get_global_bot_tenant

    row = (
        BotUser.all_tenants.filter(
            tenant=get_global_bot_tenant(), channel="max", channel_user_id=person_id
        )
        .values_list("chat_id", flat=True)
        .first()
    )
    return str(row or "")


def _buttons(attachments: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Кнопки клавиатуры MAX → быстрые ответы Mini App.

    ``callback`` — подпись и payload: тап вернётся тем же ``ask``, и ход
    обработает его, как обрабатывает тап в MAX. ``link`` — подпись и адрес.
    Остальные виды (``open_app`` и т.п.) в Mini App смысла не имеют.
    """
    out: list[dict[str, str]] = []
    for attachment in attachments:
        if not isinstance(attachment, dict) or attachment.get("type") != "inline_keyboard":
            continue
        rows = (attachment.get("payload") or {}).get("buttons") or []
        for row in rows:
            for button in row if isinstance(row, list) else []:
                if not isinstance(button, dict):
                    continue
                label = str(button.get("text") or "")
                if button.get("type") == "callback" and button.get("payload"):
                    out.append({"label": label, "payload": str(button["payload"])})
                elif button.get("type") == "link" and button.get("url"):
                    out.append({"label": label, "url": str(button["url"])})
    return out


def _answer_from(replies: list[dict[str, Any]]) -> tuple[str, list[dict[str, str]]]:
    """Что сказано человеку за ход: тексты по порядку, кнопки последнего ответа.

    Правка сообщения (``edit``) заменяет предыдущую реплику — так её увидел бы
    человек в MAX.
    """
    shown: list[dict[str, Any]] = []
    for reply in replies:
        if reply.get("edit") and shown:
            shown[-1] = reply
        else:
            shown.append(reply)
    texts = [str(r.get("text") or "").strip() for r in shown]
    answer = "\n\n".join(t for t in texts if t)
    buttons = _buttons(shown[-1].get("attachments") or []) if shown else []
    return answer, buttons


@csrf_exempt
@require_http_methods(["POST"])
@require_init_data
def customer_assistant_ask(request: HttpRequest) -> HttpResponse:
    """Вопрос Ayla из Mini App — тем же ходом, что в чате бота."""
    from apps.channels.max.delivery_capture import capturing
    from apps.channels.max.handler import _handle_global_max_event_inner
    from apps.channels.max.parser import CanonicalEvent
    from apps.identity.services.memory_origin import global_surface_scope
    from apps.tools.idempotency import AlreadyClaimed, with_idempotency

    bot_user: BotUser = request.bot_user  # type: ignore[attr-defined]
    person_id = _person_max_id(bot_user)
    if person_id is None:
        return _error("not_supported", "Разговор в приложении доступен из MAX.", 400)

    try:
        body = json.loads(request.body or b"{}")
    except ValueError:
        return _error("malformed", "body is not valid JSON", 400)
    if not isinstance(body, dict):
        return _error("malformed", "body must be a JSON object", 400)
    text = str(body.get("text") or "").strip()
    if not text:
        return _error("empty_question", "Нужен текст вопроса.", 400)
    if len(text) > MAX_QUESTION_CHARS:
        return _error("question_too_long", "Вопрос слишком длинный.", 400)

    if over_quota("assistant_ask", person_id, per_minute=ASK_PER_MINUTE):
        logger.warning("miniapp_api.assistant.ask_rate_limited")
        return rate_limited("ask_rate_limited", "too many questions, retry in a minute")

    # Повтор того же запроса (сеть, двойной тап) — не второй ход модели.
    request_id = str(body.get("request_id") or "") or uuid.uuid4().hex
    try:
        request_key = uuid.UUID(request_id).hex
    except ValueError:
        return _error("malformed", "request_id must be a UUID", 400)

    chat_id = _dialog_chat_id(person_id)
    event = CanonicalEvent(
        channel="max",
        channel_user_id=person_id,
        channel_message_id=f"{_SURFACE}-{request_key}",
        chat_id=chat_id,
        text=text,
        timestamp=timezone.now(),
        raw={"source": _SURFACE},
    )
    trace_id = uuid.uuid4()
    try:
        with (
            capturing(chat_id=chat_id, user_id=person_id) as delivery,
            with_idempotency(f"webhook:max_global:{_SURFACE}:{request_key}", ttl_seconds=86_400),
            global_surface_scope(),
        ):
            _handle_global_max_event_inner(event, trace_id)
    except AlreadyClaimed:
        return _error("duplicate_request", "Этот вопрос уже обработан.", 409)
    except Exception:
        logger.exception("miniapp_api.assistant.ask_failed trace=%s", trace_id)
        return _error("turn_failed", "Не получилось ответить. Попробуй ещё раз.", 502)

    answer, buttons = _answer_from(delivery.replies)
    logger.info(
        "miniapp_api.assistant.ask replies=%d buttons=%d trace=%s",
        len(delivery.replies),
        len(buttons),
        trace_id,
    )
    return JsonResponse({"answer": answer, "buttons": buttons, "pending_action": None, "cards": []})


@csrf_exempt
@require_http_methods(["GET"])
@require_init_data
def customer_assistant_history(request: HttpRequest) -> HttpResponse:
    """Что уже сказано — та же нить, что у чата бота и «Последней темы».

    Те же правила видимости, что у ``last-topic``: только разговоры
    оболочек человека, ходы после отметки обезличивания. Плюс отсечка отзыва
    согласия (DRF-2700): реплики, сказанные до последнего отзыва
    ``personal_data``, не показываются, даже если обезличивание не дошло.
    """
    from apps.consent.services import last_personal_data_withdrawal, person_channel_shells
    from apps.conversations.models import Conversation, Message

    bot_user: BotUser = request.bot_user  # type: ignore[attr-defined]
    threads = Conversation.all_tenants.filter(
        bot_user__in=person_channel_shells(bot_user),
        is_shadow=False,
        deleted_at__isnull=True,
    )
    rows = Message.all_tenants.filter(
        conversation__in=threads,
        role__in=(Message.Role.USER, Message.Role.ASSISTANT),
    ).filter(
        Q(conversation__anonymized_through__isnull=True)
        | Q(created_at__gt=F("conversation__anonymized_through"))
    )
    cutoff = last_personal_data_withdrawal(bot_user)
    if cutoff is not None:
        rows = rows.filter(created_at__gt=cutoff)
    recent = list(rows.order_by("-created_at")[:HISTORY_LIMIT])
    recent.reverse()
    messages = [
        {
            "id": str(m.pk),
            "role": m.role,
            "content": (m.rendered_text or m.content or "")
            if m.role == Message.Role.ASSISTANT
            else (m.content or ""),
            "tool": "",
            "created_at": m.created_at.isoformat(),
        }
        for m in recent
    ]
    return JsonResponse({"messages": messages})
