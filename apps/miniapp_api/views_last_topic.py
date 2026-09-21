"""«Продолжить разговор с Ayla» — последняя тема для главного экрана (DRF-2144).

    GET /customer/last-topic/ → {"last_topic": {"text": "…", "at": "<iso>"} | null}

Фриз H01 25.08 п.4: превью показывает только РЕАЛЬНЫЙ последний контекст, при
его отсутствии экран говорит нейтрально «Продолжить разговор». Поэтому тема
здесь — не пересказ и не генерация, а первые :data:`TOPIC_MAX_CHARS` знаков
последнего хода ассистента (то, что человек прочитал — ``rendered_text``,
иначе ``content``), обрезанные по слову с «…».

### Что темой не становится

* **Канонные строки safety** — ход с ``action_type`` ``safety_pre_check``
  (предпроверка) или ``safety_outbound`` (замена §128), а также текст,
  дословно равный :data:`REPLACEMENT_TEXT` §128 без маркера (второй путь
  передачи человеку пишет ту же строку). «Пока у меня недостаточно
  подтверждённых данных…» — не тема разговора, а наш отказ; берётся ход до
  него.
* **Служебные строки памяти** (DRF-1292 «Запомнила: ты …», вопрос памяти
  «Кстати, чтобы подбирать точнее — …»). Обе дописываются к ответу отдельным
  абзацем после пустой строки (``weave_service_line`` / ``memory_ask``), и
  ответ — это первый абзац; служебный абзац в тему не попадает, а ход,
  состоящий из одной служебной строки, пропускается.
* **Обезличенное** — ходы не позже ``Conversation.anonymized_through``
  (после «забудь всё» тело пустое, и пустая строка тоже не тема).
* **Тень и удалённое** — ``is_shadow`` / ``deleted_at`` разговоры не читаются.
* **Чужое** — subject всегда ``request.bot_user`` и его оболочки; чужой
  разговор недостижим по построению.
* **Испорченное при записи** (DRF-2266) — ход с символами замены U+FFFD.
  Транспорт их внести не может (``JsonResponse`` — ``ensure_ascii``, срез —
  по символам), значит они лежат в самом ходе; такой ход пропускается.

### Чей разговор (DRF-2266)

У человека в пилоте две оболочки ``BotUser``: Mini App — под
``MAX_BOT_TENANT_SLUG``, чат глобального бота — под сентинелом ``global_bot``.
Живой разговор с Ayla идёт на строке чата, и читать только строку Mini App
значило показывать старый ход салонной эпохи («6 авг.»), а не последний
разговор. Ходы читаются по всем оболочкам человека —
:func:`apps.consent.services.person_channel_shells`, то же определение, что
у чтения согласия (DRF-2230).

### Куда ведут кнопки блока (DRF-2266)

``chat_link`` — публичная ссылка на диалог бота, из которого открыт Mini App
(``MAX_BOT_<S>_LINK`` записи реестра, бот — ``VerifiedInitData.bot_slug``);
``null``, если ссылки нет. Экран зовёт её, только когда мост MAX не умеет
``close()`` (web.max.ru), и показывает подсказку, если нет и её.

Салонная / мастерская / админская поверхности маршрут не видят: он объявлен
только в ``apps/miniapp_api/urls.py`` (тот же сторож, что у памяти DRF-2133).
"""

from __future__ import annotations

import logging

from django.db.models import F, Q
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from apps.conversations.models import Conversation, Message
from apps.identity.models import BotUser
from apps.miniapp_api.views import require_init_data

logger = logging.getLogger(__name__)

#: Первые N знаков ответа Ayla — короткая строка под подписью «Последняя тема».
TOPIC_MAX_CHARS = 80
#: Сколько последних ходов ассистента просмотреть, прежде чем сказать «темы нет»:
#: подряд идущие safety-строки и служебные абзацы — редкость, а не норма.
_LOOKBACK = 5

#: Маркеры ходов, которые темой не бывают (см. модульную документацию).
_SAFETY_PRE_CHECK = "safety_pre_check"


def _skipped_action_types() -> frozenset[str]:
    from apps.orchestrator.safety.gate import OUTBOUND_ACTION_TYPE

    return frozenset({_SAFETY_PRE_CHECK, OUTBOUND_ACTION_TYPE})


def _service_line_heads() -> tuple[str, ...]:
    from apps.orchestrator.memory_announce import ANNOUNCE_HEAD

    # Вопрос памяти — ``memory_ask._weave``: префикс дословно оттуда; сама
    # функция приватная, а строка — контракт того, что видит человек.
    return (ANNOUNCE_HEAD, "Кстати, чтобы подбирать точнее")


def _is_canned_safety(text: str) -> bool:
    from apps.orchestrator.safety.outbound import REPLACEMENT_TEXT

    return text.strip() == REPLACEMENT_TEXT.strip()


def _answer_paragraph(text: str) -> str:
    """Первый абзац ответа без служебных строк; пусто — темы в ходе нет.

    Служебная строка сегодня — отдельный абзац (пустая строка перед ней); на
    случай, если её когда-нибудь приклеят одним переводом строки, абзац ещё и
    обрезается по первому вхождению служебного начала — строка памяти в
    превью не попадёт ни в какой сборке.
    """
    heads = _service_line_heads()
    for paragraph in text.replace("\r\n", "\n").split("\n\n"):
        candidate = " ".join(paragraph.split())
        if not candidate:
            continue
        if candidate.startswith(heads):
            continue
        cut = min((i for i in (candidate.find(h) for h in heads) if i > 0), default=-1)
        if cut > 0:
            candidate = candidate[:cut].rstrip()
        if candidate:
            return candidate
    return ""


def _cut_by_word(text: str, limit: int = TOPIC_MAX_CHARS) -> str:
    if len(text) <= limit:
        return text
    head = text[: limit - 1]
    space = head.rfind(" ")
    if space > 0:
        head = head[:space]
    return head.rstrip(" ,;:—-") + "…"


#: Символ замены: так выглядит текст, испорченный при записи (DRF-2266).
_REPLACEMENT_CHAR = "\ufffd"


def _topic_of(message: Message) -> str | None:
    if message.action_type in _skipped_action_types():
        return None
    raw = message.rendered_text or message.content or ""
    if _REPLACEMENT_CHAR in raw:
        # Испорчено при записи — не превью. Длина и признак — в лог, текст — нет.
        logger.warning(
            "miniapp.last_topic.replacement_chars message=%s length=%d",
            message.pk,
            len(raw),
        )
        return None
    if _is_canned_safety(raw):
        return None
    answer = _answer_paragraph(raw)
    if not answer:
        return None
    return _cut_by_word(answer)


def _recent_assistant_turns(bot_user: BotUser) -> list[Message]:
    from apps.consent.services import person_channel_shells

    # Сначала — разговоры человека (их единицы), потом ходы по ним: индекс
    # ``(conversation, created_at)`` вместо обратного прохода по всему тенанту.
    # DRF-2266: по всем оболочкам человека — живой разговор идёт на строке чата.
    threads = Conversation.all_tenants.filter(
        bot_user__in=person_channel_shells(bot_user),
        is_shadow=False,
        deleted_at__isnull=True,
    )
    return list(
        Message.all_tenants.filter(
            conversation__in=threads,
            role=Message.Role.ASSISTANT,
        )
        .filter(
            Q(conversation__anonymized_through__isnull=True)
            | Q(created_at__gt=F("conversation__anonymized_through"))
        )
        .order_by("-created_at")[:_LOOKBACK]
    )


def _chat_link(request: HttpRequest) -> str | None:
    """Ссылка на диалог бота, из которого открыт Mini App, — или ``None``."""
    from apps.channels.bot_registry import effective_registry, resolve_by_slug

    verified = getattr(request, "verified_init_data", None)
    slug = getattr(verified, "bot_slug", "") or ""
    entry = resolve_by_slug(slug, effective_registry()) if slug else None
    link = (getattr(entry, "link", "") or "").strip() if entry is not None else ""
    return link or None


@csrf_exempt
@require_http_methods(["GET"])
@require_init_data
def customer_last_topic(request: HttpRequest) -> HttpResponse:
    """Последняя тема разговора звонящего с Ayla — или ``null``, не ошибка."""
    bot_user: BotUser = request.bot_user  # type: ignore[attr-defined]
    chat_link = _chat_link(request)
    for message in _recent_assistant_turns(bot_user):
        topic = _topic_of(message)
        if topic:
            return JsonResponse(
                {
                    "last_topic": {"text": topic, "at": message.created_at.isoformat()},
                    "chat_link": chat_link,
                }
            )
    return JsonResponse({"last_topic": None, "chat_link": chat_link})
