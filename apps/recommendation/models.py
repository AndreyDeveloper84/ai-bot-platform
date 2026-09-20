"""Запись Recommendation — что Ayla показала человеку как направление (К-3, DRF-1772).

Почему в боте
-------------
Решение владельца B13 (12.09): Recommendation — immutable record с
собственной retention для attribution/audit («иначе через два часа нельзя
доказать, какая Recommendation привела к Booking»). Это AI-вывод и его
аудит — не транзакционный домен (ADR-0009: транзакционные домены — Ayla;
AI/observability — здесь). Персистентная запись в КАТАЛОГЕ (D13) — отдельный
вопрос владельцу; эта таблица — то, на что сядут `recommendation_id` в
интенте записи и реакции N7 (DRF-1773).

Что здесь есть и чего нет
-------------------------
Есть: что показано (WHAT, WHY, факты, из которых WHY собран), кому, под
какую цель, и реакция человека (B8: ENGAGED доказывает взаимодействие —
`accepted` снят). Нет: услуги, мастера, цены, слота — карточка C04 их не
содержит по построению (B2/B3), и хранить здесь нечего.

`fingerprint` — идемпотентность: один и тот же собранный контекст под ту же
цель даёт одну запись и одну карточку, сколько бы раз прокси ни увидел
`return_to_chat` (повторный GET, перезагрузка мини-аппа).
"""

from __future__ import annotations

import uuid

from django.db import models


class Recommendation(models.Model):
    class Reaction(models.TextChoices):
        NONE = "", "—"
        WHY_REQUESTED = "why_requested", "Почему"
        ALTERNATIVE_REQUESTED = "alternative_requested", "Другой вариант"
        REJECTED = "rejected", "Не сейчас"

    class Kind(models.TextChoices):
        DIRECTION = "direction", "Направление (C04.1)"
        ABSENCE = "absence", "Нет рекомендации (C04.4)"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    bot_user = models.ForeignKey(
        "identity.BotUser",
        on_delete=models.CASCADE,
        related_name="recommendations",
    )
    #: Цель каталога (`known.goal.id`) — строкой: своей таблицы целей у бота
    #: нет и не будет (ADR-0009: цель — Ayla).
    goal_id = models.CharField(max_length=64, blank=True, default="")
    kind = models.CharField(max_length=16, choices=Kind.choices, default=Kind.DIRECTION)
    what = models.CharField(max_length=200, blank=True, default="")
    subline = models.CharField(max_length=300, blank=True, default="")
    #: Причины дословно, как показаны (≤3), — чтобы аудит читал то же, что человек.
    why = models.JSONField(default=list, blank=True)
    #: Факты, из которых собраны причины (подписи цели/ответов) — provenance.
    facts = models.JSONField(default=dict, blank=True)
    fingerprint = models.CharField(max_length=64)
    reaction = models.CharField(
        max_length=32, choices=Reaction.choices, blank=True, default=Reaction.NONE
    )
    reacted_at = models.DateTimeField(null=True, blank=True)
    #: К какой брони привела эта карточка (DRF-1773). Строкой, а не FK:
    #: бронь — собственность Ayla (ADR-0009), её ключ у нас чужой. Пусто —
    #: «показана, но записи не было»: `shown ≠ engaged ≠ booked` (R17).
    booking_id = models.CharField(max_length=64, blank=True, default="")
    booked_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["bot_user", "fingerprint"], name="recommendation_user_fingerprint_uniq"
            ),
        ]
        indexes = [models.Index(fields=["bot_user", "created_at"], name="reco_user_created_idx")]

    def __str__(self) -> str:
        return f"{self.kind}:{self.id} ({self.bot_user_id})"
