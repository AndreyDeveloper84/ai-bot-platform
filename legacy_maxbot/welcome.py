"""Welcome-картинка для bot_started (первый контакт нового user'а).

Идея: показать фотографию салона рядом с текстом приветствия — нативный
trust-builder. Включается только для НОВЫХ user'ов (is_new=True), чтобы
returning users не видели одну и ту же картинку при каждом /start.

Реализация: один upload в MAX → AttachmentUpload-токен в Django cache
(Redis на проде) на 30 дней. Следующие /start от новых user'ов берут
кэшированный токен — нет network round-trip к upload-серверу.

Best-effort: если файл отсутствует / upload упал / cache не работает —
return None, handler отправит приветствие без картинки.
"""
from __future__ import annotations

import logging
from pathlib import Path

from asgiref.sync import sync_to_async
from django.conf import settings
from django.core.cache import cache
from maxapi.enums.attachment import AttachmentType
from maxapi.types.attachments.attachment import Attachment
from maxapi.types.attachments.upload import AttachmentUpload
from maxapi.types.input_media import InputMedia

logger = logging.getLogger("maxbot.welcome")

CACHE_KEY = "maxbot:welcome:attachment_upload"
CACHE_TTL_SECONDS = 30 * 24 * 60 * 60  # 30 дней — токен MAX upload long-lived


async def get_welcome_attachment(bot) -> Attachment | None:
    """Кэшированный Image-attachment с приветственной картинкой или None.

    Порядок:
    1. Cache hit → собрать Attachment из dict, вернуть (~1ms)
    2. Файла нет → None (None всё равно безопасно для send_message)
    3. Upload в MAX → положить dict в cache → вернуть Attachment
    4. Upload упал → log warning + None (приветствие пойдёт без картинки)
    """
    cached = await sync_to_async(cache.get)(CACHE_KEY)
    if cached:
        try:
            upload = AttachmentUpload(**cached)
            return Attachment(type=AttachmentType.IMAGE, payload=upload)
        except Exception as exc:  # noqa: BLE001
            logger.warning("welcome cache deserialize failed (cleared): %s", exc)
            await sync_to_async(cache.delete)(CACHE_KEY)

    path = Path(settings.MAXBOT_WELCOME_IMAGE_PATH)
    if not path.exists():
        logger.debug("welcome image missing: %s", path)
        return None

    try:
        upload = await bot.upload_media(InputMedia(str(path)))
    except Exception as exc:  # noqa: BLE001
        logger.warning("welcome upload failed (no image): %s", exc)
        return None

    try:
        await sync_to_async(cache.set)(CACHE_KEY, upload.model_dump(), CACHE_TTL_SECONDS)
    except Exception as exc:  # noqa: BLE001
        # Upload удался, кэш недоступен — отдаём attachment без кэша
        logger.warning("welcome cache.set failed: %s", exc)

    return Attachment(type=AttachmentType.IMAGE, payload=upload)
