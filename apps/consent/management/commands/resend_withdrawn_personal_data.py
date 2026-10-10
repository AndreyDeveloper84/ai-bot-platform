"""``manage.py resend_withdrawn_personal_data [--apply]`` — дослать каталогу отзывы согласия на хранение.

DRF-2967. Каталог раньше принимал события согласия ``personal_data`` и не
хранил их; об отзывах до выкладки его правки он не знает. Команда досылает
текущее состояние «отозвано» по каждому такому человеку — см.
:mod:`apps.consent.catalog_resend`.

По умолчанию — сухой прогон: печатает, скольким людям ушло бы событие,
ничего не отправляя. С ``--apply`` отправляет по одному событию на человека
и печатает число по исходам каталога. Повторный прогон безопасен: каталог
узнаёт событие по ``event_id`` и отвечает ``duplicate``.

Печатает только числа — без идентификаторов людей.

Запускать ПОСЛЕ выкладки каталожной правки, которая хранит ``personal_data``:
до неё каталог ответит ``ignored`` и ничего не запомнит. На пилоте
``--apply`` выполняет главное окно как шаг выката.
"""

from __future__ import annotations

import time
from collections import Counter
from typing import Any

from django.core.management.base import BaseCommand, CommandParser

from apps.consent.catalog_resend import withdrawn_people
from apps.integrations.ayla.consent_events_client import (
    ConsentEventError,
    post_consent_event,
)

#: Ведро ручки каталога — 300 событий в минуту; с запасом.
DEFAULT_PAUSE_S = 0.25


class Command(BaseCommand):
    help = "Дослать каталогу отзывы согласия на хранение (сухой прогон без --apply)."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Отправить события. Без флага — только посчитать.",
        )
        parser.add_argument(
            "--pause",
            type=float,
            default=DEFAULT_PAUSE_S,
            help="Пауза между событиями, секунды (ведро каталога — 300 в минуту).",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        apply: bool = options["apply"]
        pause: float = max(0.0, options["pause"])
        outcomes: Counter[str] = Counter()
        total = 0
        for person in withdrawn_people():
            total += 1
            if not apply:
                continue
            try:
                receipt = post_consent_event(
                    external_user_id=person.external_user_id, body=person.body()
                )
            except ConsentEventError as exc:
                # Имя класса отказа — не данные человека. Прогон идёт дальше:
                # повтор команды дошлёт оставшихся, уже доставленным каталог
                # ответит ``duplicate``.
                outcomes[f"failed:{type(exc).__name__}"] += 1
            else:
                outcomes[receipt.outcome] += 1
            if pause:
                time.sleep(pause)

        mode = "apply" if apply else "dry-run"
        self.stdout.write(f"resend_withdrawn_personal_data mode={mode} people={total}")
        for outcome in sorted(outcomes):
            self.stdout.write(f"  {outcome}={outcomes[outcome]}")
        if not apply:
            self.stdout.write("  ничего не отправлено; для отправки — --apply")
