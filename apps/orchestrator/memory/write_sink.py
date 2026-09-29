"""Что записали писатели зелёной памяти за ход — и уложились ли в бюджет (DRF-1292).

Писатели (``personal_context.record_explicit_green_facts``,
``said_memory.record_said_facts``) возвращают счётчики — этого хватало, пока
запись шла после отправки ответа. Анонс «Запомнила: …» строится из САМИХ
записанных строк (какая фраза, какой домен), поэтому писатели получают
``sink`` и складывают в него строки. Возврат-счётчик не меняется: прежние
вызывающие и тесты живут как жили.

``link_timed_out`` — связь с Ayla (``ensure_ayla_link``) не уложилась в бюджет
пре-отправки. Тогда факт не записан ЗДЕСЬ, и обработчик пишет его после
отправки, как раньше, без строки: строка обещает то, что уже сделано.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class WriteSink:
    entries: list[Any] = field(default_factory=list)
    link_timed_out: bool = False

    def add(self, entry: Any) -> None:
        if entry is not None:
            self.entries.append(entry)


def link_within_budget(
    sink: WriteSink | None, started: float, budget_s: float | None, resolved: Any
) -> None:
    """Note a link that came back empty after (roughly) the whole budget.

    ``ensure_ayla_link`` swallows the transport error and returns ``None``;
    the only honest signal left is time. ``None`` before the budget ran out
    is «Ayla down» — the post-send retry will not help, so nothing is noted.
    """
    if sink is None or budget_s is None or resolved is not None:
        return
    if time.monotonic() - started >= budget_s * 0.9:
        sink.link_timed_out = True
