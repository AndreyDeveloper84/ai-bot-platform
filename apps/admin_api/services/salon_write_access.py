"""Может ли человек отсюда менять время мастера (DRF-2936).

Один ответ на два вопроса:

* **рисовать ли правку** — чтения ``masters/<id>/exceptions/`` и
  ``masters/<id>/schedule/impact/`` отдают его полем ``writable``;
* **пустит ли запись** — ``views_salon_schedule_writes`` отказывает тем же
  :func:`signed_by_the_salon_bot` до обмена токена.

Два места с одним вопросом и двумя ответами разъехались бы на первой правке
— поэтому ответ живёт здесь, а не в каждом виде.

Чего заранее не узнать — выдаст ли каталог токен человеку (связь не доказана,
запуск устарел, лимит обмена): это экран встречает на самой записи
(``403 salon_write_refused``, ``401 stale``), и обязан встретить.
"""

from __future__ import annotations

from django.http import HttpRequest

from apps.channels.bot_registry import SALON_STREAM, effective_registry, resolve_by_slug


def signed_by_the_salon_bot(request: HttpRequest) -> bool:
    """The catalog accepts only the salon-master bot's signature. The bot's own
    gate accepts any registered bot — refuse here, before spending an exchange,
    when the person opened the Mini App from another bot."""
    verified = getattr(request, "verified_init_data", None)
    entry = resolve_by_slug(getattr(verified, "bot_slug", "") or "", effective_registry())
    return entry is not None and entry.stream == SALON_STREAM


def schedule_writable(request: HttpRequest) -> bool:
    """Владелец или администратор салона И Mini App из салонного бота.

    Ресепшн график только читает (§141): шлюз чтения её пускает, запись — нет
    (``require_admin_role``).
    """

    ctx = getattr(request, "role_context", None)
    if ctx is None or not (ctx.is_owner or ctx.is_admin):
        return False
    return signed_by_the_salon_bot(request)
