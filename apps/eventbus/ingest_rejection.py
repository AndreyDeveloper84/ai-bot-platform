"""Постоянный отказ потребителя события (DRF-2302).

Outbox каталога на 5xx повторяет доставку 9 раз за ~4,5 ч и только потом
кладёт событие в dead; на 4xx (кроме 429) — dead сразу, повтор вручную
(``replay_dead_outbox_events``). Поэтому отказ, который повтор не исправит
никогда (тенанта нет, событие вне пилотного allowlist, битый payload),
должен быть 422, а не 500: иначе 4,5 ч повторов — шум, который прячет отказ.

Потребитель поднимает наследника :class:`IngestRejection` со slug
``reason``; диспетчер пишет DLQ с этим slug, дедуп НЕ пишет (повтор после
починки должен пройти), а view отвечает 422. Всё остальное — временный
отказ (гонка порядка, сбой БД) и настоящий сбой обработчика — остаётся
500 и повторяется, как было (``event-contract.md`` §8.1, §8.12).

Отдельный модуль, а не ``ingest_dispatcher``: ``ingest_tenancy`` и
потребители импортируют его, не затягивая диспетчер с моделями.
"""

from __future__ import annotations


class IngestRejection(Exception):
    """Отказ, который повтор не исправит. ``reason`` — slug для DLQ и ответа."""

    reason: str = "rejected"

    def __init__(self, *args: object, reason: str | None = None) -> None:
        super().__init__(*args)
        if reason is not None:
            self.reason = reason
