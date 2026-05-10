"""Кэшированные фото мастеров для AI Concierge `show_masters` карточки (DRF-354).

Идея: top-3 master'а получают image attachment с фото из ImageField, чтобы
client видел лица, а не только список имён. Boost доверия в pre-booking flow.

Реализация: один upload файла в MAX → AttachmentUpload-токен в Django cache
(Redis на проде) на 30 дней. Следующие show_masters берут кэшированный токен —
никаких network round-trip к upload-серверу.

Best-effort: если файл отсутствует / upload упал / cache не работает —
return None. Caller рендерит карточку без фото — backwards-compatible.

Зеркало `maxbot/welcome.py` (welcome-картинка): тот же паттерн cache-key+ttl,
тот же try/except на upload, тот же AttachmentUpload(**dict) deserialize.
"""
from __future__ import annotations

import logging

from asgiref.sync import sync_to_async
from django.core.cache import cache
from maxapi.enums.attachment import AttachmentType
from maxapi.types.attachments.attachment import Attachment
from maxapi.types.attachments.upload import AttachmentUpload
from maxapi.types.input_media import InputMedia

logger = logging.getLogger("maxbot.master_image")

# 30 дней — токен MAX upload long-lived. Ребилд раз в месяц достаточно;
# если фото мастера обновится раньше — версионируем cache-key bump v1→v2.
CACHE_TTL_SECONDS = 30 * 24 * 60 * 60


def _cache_key(master_id: int) -> str:
    return f"master:{master_id}:photo:v1"


async def get_master_attachment(bot, master) -> Attachment | None:
    """Кэшированный Image-attachment с фото мастера, либо None.

    Порядок:
    1. Cache hit → собрать Attachment из dict, вернуть (~1ms)
    2. У master нет photo (или файла на диске нет) → None
    3. Upload в MAX → положить dict в cache → вернуть Attachment
    4. Upload упал → log warning + None (карточка пойдёт без фото)

    Args:
        bot: maxapi Bot — для bot.upload_media
        master: services_app.Master ORM-объект (читаем .photo, .id)

    Returns:
        Attachment(IMAGE) или None.
    """
    if master is None or master.id is None:
        return None

    key = _cache_key(master.id)

    cached = await sync_to_async(cache.get)(key)
    if cached:
        try:
            upload = AttachmentUpload(**cached)
            return Attachment(type=AttachmentType.IMAGE, payload=upload)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "master photo cache deserialize failed master=%s (cleared): %s",
                master.id, exc,
            )
            await sync_to_async(cache.delete)(key)

    # Django ImageField: .photo — falsy если файла нет; .photo.path может
    # бросить ValueError при отсутствии storage path.
    photo_field = getattr(master, "photo", None)
    if not photo_field:
        return None

    try:
        path = photo_field.path
    except (ValueError, NotImplementedError):
        # Storage backend без локального пути (S3 и т.п.) — текущий проект
        # использует FileSystemStorage, но защищаемся для будущего.
        logger.debug("master %s photo storage has no local path", master.id)
        return None

    try:
        upload = await bot.upload_media(InputMedia(str(path)))
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "master photo upload failed master=%s path=%s: %s",
            master.id, path, exc,
        )
        return None

    try:
        await sync_to_async(cache.set)(key, upload.model_dump(), CACHE_TTL_SECONDS)
    except Exception as exc:  # noqa: BLE001
        # Upload удался, кэш недоступен — отдаём attachment без кэша
        logger.warning("master photo cache.set failed master=%s: %s", master.id, exc)

    return Attachment(type=AttachmentType.IMAGE, payload=upload)
