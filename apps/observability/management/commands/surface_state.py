"""Состояние поверхности бота — одним прогоном, с названным предметом (DRF-1661).

Зачем команда, а не документ
----------------------------

Владелец: «ты опять меряешь, и так было много раз; почему текущее
состояние поверхности не хранить в документе». Он прав — но рукописный
документ протухает **молча**: 11.09.2026 запись «пилот — 194.87.99.126»,
верная в августе, стоила целого замера на брошенной копии. Команда
каждый раз спрашивает машину заново; ``--write`` кладёт ответ в файл, и
файл — это вывод, а не текст, который кто-то правит.

Парная команда в каталоге — ``beautygo_backend`` ``manage.py
surface_state`` (PR #337): та же шапка, те же три правила, свои числа.

Три правила вывода
------------------

1. **Шапка предмета первой** — хост, контейнер, БД по её собственному
   ответу, старт процесса БД, пульс по нескольким опорам с возрастом
   каждой (:mod:`apps.observability.measurement_subject`). Молчание всех
   опор — «свежесть НЕ подтверждена», не ноль.
2. **Рядом с числом — таблица и поле**, из которых оно снято. Подпись
   читатель проверить не может, ``catalog.CatalogMaster.linked_bot_user
   IS NOT NULL`` — может.
3. **Названный предел** — команда показывает данные и значения
   рубильников, а не поведение: «клиент видит мастера» отсюда не видно,
   видно только ``sale_block(row) IS NULL``.

Рубильники и §138
-----------------

§138 (11.09.2026) различает ИСПОЛНЕНО и ЗАПЕРТО ровно значением флага:
построено, но ``false`` — ЗАПЕРТО. Поэтому у каждого флага печатается
живое значение из ``settings`` и то, задан ли он в окружении: умолчание
в коде и явное ``false`` в ``.env`` — разные новости.

``GOAL_RESOLUTION_ENABLED`` в списке главного окна есть, а в настройках
бота — нет: это setting **каталога** (``djangoProject/settings/base.py``).
Команда не выдумывает ему значение и не молчит — печатает, где его
снимать.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import get_args

from django.apps import apps
from django.conf import settings
from django.core.management.base import BaseCommand
from django.db.models import Count
from django.utils import timezone

from apps.catalog.master_state import SaleBlock, sale_block
from apps.observability.measurement_subject import gather_pulse, subject_lines

#: Строка, которой открывается записанный файл. Названо последствие
#: правки руками, а не «сгенерировано автоматически».
FILE_HEADER = (
    "<!-- файл перезаписывается командой `manage.py surface_state --write`; "
    "правка руками превращает замер в мнение -->"
)

LIMIT_LINES = (
    "== ПРЕДЕЛ: что эта команда НЕ показывает ==",
    "Команда показывает ДАННЫЕ и ЗНАЧЕНИЯ РУБИЛЬНИКОВ, а не ПОВЕДЕНИЕ. «Клиент",
    "видит мастера» отсюда не видно — видно только sale_block(row) IS NULL.",
    "Открытый флаг ≠ ИСПОЛНЕНО (§138): исполнено — когда доступно человеку.",
    "Ноль в любой строке ниже — факт о таблице, а не вывод о готовности.",
)

#: Рубильники бота, чьё значение решает «ЗАПЕРТО или нет» по §138.
#: Имя setting == имя переменной окружения для каждого из них
#: (config/settings/base.py читает ``os.environ.get(<то же имя>)``).
FLAGS = (
    "BOOKING_VIA_AYLA_REST",
    "FOOD_PHOTO_SCAN_ENABLED",
    "NUTRITION_ENABLED",
    "WELLNESS_PROACTIVE_ENABLED",
    "NUTRITION_PROACTIVE_ENABLED",
    "NUTRITION_COACH_ENABLED",
    "POST_VISIT_FOLLOWUP_ENABLED",
    "MASTER_SCHEDULE_CONFIRMATION_REQUIRED",
)

#: Флаги, которые спрашивают у бота, а живут в каталоге. Печатаются
#: строкой «не setting бота», чтобы отсутствие не читалось как «выключен».
FOREIGN_FLAGS = {
    "GOAL_RESOLUTION_ENABLED": (
        "setting КАТАЛОГА (beautygo_backend djangoProject/settings/base.py) — "
        "снимать `manage.py surface_state` каталога"
    ),
}

_LABEL_W = 26
_VALUE_W = 8
#: Имена рубильников длиннее подписей чисел; своя ширина, чтобы столбец не плыл.
_FLAG_W = max(len(n) for n in (*FLAGS, *FOREIGN_FLAGS)) + 2


def _row(label: str, value, source: str, *, label_w: int = _LABEL_W) -> str:
    """Одна строка вывода: подпись · число · откуда снято."""
    return f"  {label:<{label_w}}: {str(value):>{_VALUE_W}}   {source}"


def _by_value(qs, field: str, choices) -> list[tuple[str, int]]:
    """Распределение по значениям поля — ВСЕ значения, что есть в базе.

    Сначала объявленные варианты (включая нули: ноль — тоже факт), затем
    всё, чего в объявлении нет: данные переживают код, и значение, снятое
    из choices, в таблице остаётся.
    """
    found = dict(qs.values_list(field).annotate(n=Count("pk")).values_list(field, "n"))
    declared = [c[0] for c in choices]
    rows = [(str(v), found.pop(v, 0)) for v in declared]
    rows += [(f"{v!r} (вне choices)", n) for v, n in sorted(found.items(), key=str)]
    return rows


class Command(BaseCommand):
    help = (
        "Печатает состояние поверхности бота: тенанты, пользователи, карточки "
        "мастеров, setup_state соло-мастеров, рубильники — с шапкой предмета "
        "и источником каждого числа."
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--write",
            metavar="PATH",
            default=None,
            help=(
                "Перезаписать файл выводом команды (обычно docs/SURFACE_STATE.md). "
                "Файл целиком заменяется; заголовок предупреждает о правке руками."
            ),
        )

    def handle(self, *args, **options) -> None:
        now = timezone.now()
        lines: list[str] = []

        # Предмет — первым и до всякого счёта. Пульс собирается один раз:
        # напечатанный возраст и есть тот, по которому судят.
        pulses = gather_pulse()
        lines.extend(subject_lines(pulses=pulses, now=now))
        lines.append(f"время снятия               : {now.isoformat(timespec='seconds')}")
        lines.append("")
        lines.extend(LIMIT_LINES)
        lines.append("")
        lines.extend(self._flags())
        lines.append("")
        lines.append("== СОСТОЯНИЕ ПОВЕРХНОСТИ ==")
        lines.append(
            f"  {'':<{_LABEL_W}}  {'число':>{_VALUE_W}}   откуда снято "
            "(таблица.поле; счёт через _base_manager, без фильтров арендатора)"
        )
        lines.extend(self._tenants())
        lines.extend(self._bot_users())
        lines.extend(self._masters())
        lines.extend(self._solo_setup_state())

        text = "\n".join(lines)
        self.stdout.write(text)

        if options["write"]:
            path = Path(options["write"])
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                FILE_HEADER + "\n\n# Состояние поверхности бота\n\n```\n" + text + "\n```\n",
                encoding="utf-8",
            )
            self.stdout.write("")
            self.stdout.write(f"записано: {path}")

    # -- рубильники ---------------------------------------------------------

    def _flags(self) -> list[str]:
        out = ["== РУБИЛЬНИКИ (§138: построено, но false — ЗАПЕРТО) =="]
        for name in FLAGS:
            if not hasattr(settings, name):
                # Флаг из списка исчез из настроек — это новость, а не
                # «выключен»: нет setting — нет и рубильника.
                out.append(
                    _row(name, "нет setting", f"settings.{name} отсутствует", label_w=_FLAG_W)
                )
                continue
            value = bool(getattr(settings, name))
            in_env = name in os.environ
            mark = "открыт" if value else "ЗАПЕРТО"
            origin = (
                f"env {name}={os.environ[name]!r}" if in_env else "env не задан → умолчание кода"
            )
            out.append(_row(name, mark, f"settings.{name} = {value}; {origin}", label_w=_FLAG_W))
        for name, where in FOREIGN_FLAGS.items():
            out.append(_row(name, "не setting бота", where, label_w=_FLAG_W))
        return out

    # -- разделы --------------------------------------------------------------

    def _tenants(self) -> list[str]:
        Tenant = apps.get_model("tenancy.Tenant")
        qs = Tenant._base_manager.all()
        out = ["тенанты бота"]
        out.append(
            _row("всего", qs.count(), "tenancy.Tenant (все строки, включая is_active=false)")
        )
        out.append(
            _row("активных", qs.filter(is_active=True).count(), "tenancy.Tenant.is_active = true")
        )
        out.append(
            _row("системных", qs.filter(is_system=True).count(), "tenancy.Tenant.is_system = true")
        )
        out.append(
            _row(
                "в shadow_mode",
                qs.filter(shadow_mode=True).count(),
                "tenancy.Tenant.shadow_mode = true",
            )
        )
        out.append(
            _row(
                "соло (slug solo-*)",
                qs.filter(slug__startswith="solo-").count(),
                "tenancy.Tenant.slug LIKE 'solo-%'",
            )
        )
        out.append(_row("с городом", qs.exclude(city="").count(), "tenancy.Tenant.city <> ''"))
        # У адреса три состояния, и модель их различает намеренно: NULL —
        # «источник об адресе не сказал ничего», "" — «источник сказал, что
        # адреса нет». Слить их в одно «без адреса» значило бы потерять
        # то, ради чего столбец сделан nullable.
        out.append(
            _row(
                "с адресом",
                qs.filter(address__isnull=False).exclude(address="").count(),
                "tenancy.Tenant.address IS NOT NULL AND <> ''",
            )
        )
        out.append(
            _row(
                "  адрес не известен",
                qs.filter(address__isnull=True).count(),
                "tenancy.Tenant.address IS NULL",
            )
        )
        out.append(
            _row("  адреса нет", qs.filter(address="").count(), "tenancy.Tenant.address = ''")
        )
        out.append(
            _row(
                "синхронизировались",
                qs.filter(last_catalog_sync_ok_at__isnull=False).count(),
                "tenancy.Tenant.last_catalog_sync_ok_at IS NOT NULL",
            )
        )
        return out

    def _bot_users(self) -> list[str]:
        BotUser = apps.get_model("identity.BotUser")
        qs = BotUser._base_manager.all()
        out = ["пользователи бота"]
        out.append(_row("всего", qs.count(), "identity.BotUser (все арендаторы)"))
        out.append(
            _row(
                "тенантов с людьми",
                qs.values("tenant_id").distinct().count(),
                "identity.BotUser.tenant_id (distinct)",
            )
        )
        return out

    def _masters(self) -> list[str]:
        Master = apps.get_model("catalog.CatalogMaster")
        qs = Master._base_manager.all()
        out = ["карточки мастеров"]
        out.append(_row("всего", qs.count(), "catalog.CatalogMaster (все арендаторы)"))
        out.append(
            _row(
                "связаны с BotUser",
                qs.filter(linked_bot_user__isnull=False).count(),
                "catalog.CatalogMaster.linked_bot_user IS NOT NULL",
            )
        )
        out.append(
            _row(
                "с ayla_user_id",
                qs.filter(ayla_user_id__isnull=False).count(),
                "catalog.CatalogMaster.ayla_user_id IS NOT NULL",
            )
        )
        out.append(
            _row(
                "активных",
                qs.filter(is_active=True).count(),
                "catalog.CatalogMaster.is_active = true",
            )
        )
        out.append(
            _row(
                "в архиве",
                qs.filter(archived_at__isnull=False).count(),
                "catalog.CatalogMaster.archived_at IS NOT NULL",
            )
        )
        for value, n in _by_value(qs, "invite_status", Master.InviteStatus.choices):
            out.append(
                _row(
                    f"  {value}", n, f"catalog.CatalogMaster.invite_status = {value.split(' ')[0]}"
                )
            )
        return out

    def _solo_setup_state(self) -> list[str]:
        """Готовность соло-мастеров — ТЕМ ЖЕ определением, что у витрины.

        ``sale_block`` — единственное место, где живёт ответ «почему не
        продаётся» (DRF-1506); ``SoloSetupState`` производен от него.
        Своя копия условий здесь разъехалась бы с витриной молча.
        """
        Master = apps.get_model("catalog.CatalogMaster")
        rows = list(Master._base_manager.filter(tenant__slug__startswith="solo-"))
        blocks: dict[str, int] = {}
        ready = 0
        for row in rows:
            block = sale_block(row)
            if block is None:
                ready += 1
            else:
                blocks[block] = blocks.get(block, 0) + 1

        out = ["соло-мастера: setup_state"]
        out.append(
            _row(
                "карточек в соло-тенантах",
                len(rows),
                "catalog.CatalogMaster JOIN tenancy.Tenant.slug LIKE 'solo-%'",
            )
        )
        out.append(_row("ready", ready, "sale_block(row) IS NULL (apps/catalog/master_state.py)"))
        out.append(_row("setup_pending", len(rows) - ready, "sale_block(row) IS NOT NULL"))
        # Все причины из SaleBlock печатаются, включая нулевые: ноль — тоже
        # факт, а пропущенная причина читается как «такой не бывает».
        for reason in get_args(SaleBlock):
            out.append(_row(f"  {reason}", blocks.pop(reason, 0), f"sale_block(row) = {reason!r}"))
        for reason, n in sorted(blocks.items()):
            out.append(_row(f"  {reason} (вне SaleBlock)", n, f"sale_block(row) = {reason!r}"))
        return out
