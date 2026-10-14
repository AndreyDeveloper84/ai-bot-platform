"""Что из тела вебхука НЕ ложится в ``WebhookJournal`` (DRF-1943).

Решение владельца 06.10.2026: «голос не храним, только текст». Байты
голосового и так живут только в памяти воркера (``channels.max.audio`` →
``speech``), но вебхук MAX несёт во вложении ссылку на файл::

    {"type": "audio", "payload": {"id": …, "url": "https://…?sig=…", "token": "…"}}

``url`` отдаёт запись 24 часа без авторизации, ``token`` — ручка повторной
отправки вложения. Тело вебхука журнал держит 72 часа и показывает в админке,
то есть сутки после голосового запись мог скачать любой, кто открыл журнал.

Поэтому в журнал тело ложится без содержимого аудио-вложений. Вложение
заменяется ЦЕЛИКОМ (а не «у payload убрать url»): ссылка, лежащая рядом с
``payload`` или в нестандартном поле, тоже уходит, а ``type: "audio"``
остаётся — по журналу видно, что пришло голосовое.

Воркеру ссылка по-прежнему нужна, и он её получает: тело для обработки едет
потоком Redis (``views.enqueue``), запись потока удаляется сразу после
успешной обработки (``workers.consumer``, DRF-2220). Журнал программно не
читает никто, кроме стирания по просьбе человека (``retention``), а оно ищет
строки по отправителю — его здесь не трогаем.

Кружочки и аудио файлом (решение владельца 08.10.2026): голос человека бывает
и там, поэтому то же правило действует на ``video`` — всегда — и на ``file``,
если по имени это аудио или видео либо имя неизвестно. Бот такие вложения не
распознаёт, ссылка в журнале ему не нужна вовсе.

Упавшие записи потока (DRF-1943, хвост): если обработчик воркера упал,
запись не удаляется, а с ней — ссылка. Запись с голосовым воркер сразу
переносит в ``<stream>:dlq`` копией без ссылки (``workers.consumer``), а
разборщик зависших пишет в DLQ тело через то же правило
(``workers.reaper``). Помощник для поля ``data`` записи —
:func:`stream_data_without_voice`.

Чего это НЕ покрывает (названо, а не скрыто): ``file`` с именем документа
(``.pdf``, ``.docx`` …) и фото еды (``image``) — решения владельца о них не
было; записи, которые воркер прочитал, но не успел обработать (убит посреди
пачки), — до разборщика или до обрезки через ``INGRESS_RAW_RETENTION_HOURS``.
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

#: Что остаётся в журнале на месте аудио-вложения.
REDACTED_VOICE: dict[str, str] = {"type": "audio", "redacted": "voice"}

#: Расширения файла, в котором может быть голос: аудио и видео.
_VOICE_FILE_SUFFIXES: frozenset[str] = frozenset(
    {
        "aac", "amr", "flac", "m4a", "mp3", "oga", "ogg", "opus", "wav", "weba", "wma",
        "3gp", "avi", "m4v", "mkv", "mov", "mp4", "mpeg", "mpg", "webm",
    }
)  # fmt: skip


def _carries_voice(attachment: dict[str, Any]) -> bool:
    """Вложение, в котором может быть голос человека.

    ``audio`` и ``video`` (кружочки) — всегда. ``file`` — по расширению имени;
    файл без имени или с именем не строкой считается голосовым: неизвестное
    здесь вырезается, а не хранится.
    """
    kind = attachment.get("type")
    if kind in ("audio", "video"):
        return True
    if kind != "file":
        return False
    name = attachment.get("filename")
    if not isinstance(name, str) or "." not in name:
        return True
    return name.rsplit(".", 1)[1].strip().lower() in _VOICE_FILE_SUFFIXES


#: Тело, которое пишется, если вырезать не удалось: сырое тело в журнал не идёт.
REDACTION_FAILED: dict[str, bool] = {"redaction_failed": True}


def without_voice_links(payload: Any) -> Any:
    """Глубокая копия тела вебхука без содержимого аудио-вложений.

    Исходный объект не меняется: он же уходит в поток воркеру. Обход
    рекурсивный — пересланное и процитированное сообщение
    (``message.link.message.attachments``) покрывается тем же правилом.
    """
    if isinstance(payload, dict):
        if _carries_voice(payload):
            return {"type": payload["type"], "redacted": "voice"}
        return {key: without_voice_links(value) for key, value in payload.items()}
    if isinstance(payload, list):
        return [without_voice_links(item) for item in payload]
    return payload


def journal_body(payload: dict[str, Any]) -> dict[str, Any]:
    """Тело для ``WebhookJournal.raw_payload``. Никогда не бросает.

    Сбой вырезания не должен стоить приёма вебхука (500 → MAX повторяет и
    в конце концов отписывает бота) и не должен привести сырое тело в журнал:
    тогда пишется заглушка, а событие обрабатывается как обычно.
    """
    try:
        body = without_voice_links(payload)
    except Exception as exc:  # noqa: BLE001 — журнал не стоит приёма вебхука
        logger.warning("ingress.journal.redaction_failed err=%s", type(exc).__name__)
        return dict(REDACTION_FAILED)
    if not isinstance(body, dict):
        return dict(REDACTION_FAILED)
    return body


def stream_data_without_voice(data: Any) -> tuple[Any, bool]:
    """Поле ``data`` записи потока Redis без голосовых ссылок. Никогда не бросает.

    Возвращает ``(значение, вырезано_ли)``. ``data`` — JSON тела вебхука,
    его кладёт ``streams.enqueue``. Значение меняется, только если вырезание
    что-то убрало: признак «голос» — это «правило сработало», а не «нашлось
    вложение ``audio``», иначе уже вырезанная пометка ``{"redacted": "voice"}``
    снова читалась бы голосом.

    Не строка, пустое или не JSON — возвращается как есть: такое поле кладёт
    не ``enqueue`` (учения, повреждённая запись), и судить, голос ли там, не
    по чему. Сбой самого вырезания на разобранном теле — заглушка, как у
    журнала: в сторону «не хранить».
    """
    if not isinstance(data, str) or not data:
        return data, False
    try:
        parsed = json.loads(data)
    except ValueError:
        return data, False
    try:
        redacted = without_voice_links(parsed)
    except Exception as exc:  # noqa: BLE001 — копия без ссылки важнее копии целиком
        logger.warning("ingress.stream.redaction_failed err=%s", type(exc).__name__)
        return json.dumps(REDACTION_FAILED), True
    if redacted == parsed:
        return data, False
    return json.dumps(redacted, ensure_ascii=False, default=str), True
