"""Убрать из накопленной истории сырые ``cb:`` строки с ролью ``user`` (DRF-990).

### Зачем это существует

Гейт персистенса на глобальном пути починен вперёд: #1325 (``cb:anketa:*``),
#1329 (``cb:welcome:*`` / ``cb:food:*`` фразой, ``cb:visit:*`` /
``cb:discover:book:*`` молчанием) и этот PR (всё семейство ``cb:discover:*``).
Но замер боевого пилота 30.08 — 68 сырых строк нажатия среди 355 реплик
``role=user``, 55 из них семейства ``cb:discover`` — это уже ЛЕЖАЩИЕ данные.
Консьерж читает историю на каждом ходе и будет читать их, пока их не уберут.

### Что команда делает и чего НЕ делает

Ровно то же решение, что принято в коде, применённое задним числом. И принято
оно не заново: команда зовёт ТЕ ЖЕ резолверы, что и хендлер
(``resolve_anketa_tap``, ``resolve_welcome_tap``, ``resolve_food_tap``,
``resolve_discover_tap``), поэтому «что делать с формой» не может разъехаться
с живым поведением — переименуют кнопку, изменится и уборка.

Три исхода, и третий важнее первых двух:

* **ФРАЗА** (``cb:anketa:choice:*``, ``cb:welcome:*``, ``cb:food:*``) — строка
  ПЕРЕПИСЫВАЕТСЯ меткой кнопки, которую человек нажал. Не удаляется: тап по
  «Женский» — это высказывание человека о себе, и удалить его значило бы
  оставить запись, где «30» есть, а пола нет. Метка берётся из живой
  клавиатуры, и если кнопку с тех пор сняли — метки нет, и такая строка
  попадает в третий исход, а не удаляется наугад;
* **МОЛЧАНИЕ** (``cb:discover:*``, ``cb:visit:*``, ``cb:book:*``,
  ``cb:catalog:*``, ``cb:clarify:*``) — строка УДАЛЯЕТСЯ. Ретроспективный
  эквивалент молчания — отсутствие строки; переписать её нечем, потому что
  payload несёт id карточки, а не слова;
* **НЕ ТРОГАЕМ** — всё остальное (``cb:menu:*``, ``cb:nutrition:*``,
  ``cb:water:*``, ``cb:qa:*``, ``cb:retry:last``, снятые кнопки известных
  семейств и любой незнакомый префикс). По этим формам решения «фраза или
  молчание» ВЛАДЕЛЬЦЕМ не принято, и принимать его молча в скрипте уборки
  нельзя. Они перечисляются в отчёте — это и есть список к следующему
  решению.

### Чем это опасно — прочитать до ``--apply``

**Удаление меняет ЧИСЛО СТРОК в диалоге, а его считают два стража первого
контакта**: ``_conversation_already_under_way``
(``apps/channels/max/global_onboarding.py``) и
``WelcomeSkill._flow_already_established``, оба с порогом
``_FIRST_CONTACT_MESSAGE_ROWS``. Диалог, у которого после уборки строк
осталось меньше порога, снова выглядит как первый контакт — человеку на
пилоте прилетит приветствие, которое он уже проходил. Отчёт называет каждый
такой диалог поимённо ДО того, как что-либо удалено; без ``--force-below-
threshold`` такие диалоги пропускаются целиком.

Переписывание строк этой опасности не несёт: число строк не меняется.

### Договор безопасности

* **Сухой прогон по умолчанию.** Без ``--apply`` не пишется ничего.
* **``--apply`` требует ``--dump PATH``.** Дамп (JSONL: id, conversation_id,
  role, content, created_at, действие) пишется, синхронизируется на диск и
  закрывается ДО первой правки, и правки идут по ЗАХВАЧЕННЫМ id, а не по
  предикату — «всё изменённое есть в дампе» становится инвариантом, а не
  надеждой. Существующий путь дампа не перезаписывается, а отвергается.
* **Только ``role="user"``.** Ответы бота — это то, что человек прочитал;
  их не трогает ничто.
* **Всё в одной транзакции**, и по ходу проверяется, что число ЗАТРОНУТЫХ
  строк совпало с числом строк в дампе.

Использование::

    # отчёт, ничего не меняет — с этого начинать всегда
    python manage.py sweep_callback_history
    python manage.py sweep_callback_history --conversation <uuid>

    # применить (только по явному решению владельца)
    python manage.py sweep_callback_history --apply --dump /tmp/cb-sweep.jsonl
"""

from __future__ import annotations

import json
import os
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.conversations.models import Message

#: Семейства, чьё решение — МОЛЧАНИЕ. Литералы зеркалят гейт персистенса
#: (``apps/channels/max/handler.py``) и живут ровно для того, чтобы уборка
#: не удалила больше, чем гейт пропускает.
_SILENT_PREFIXES = (
    "cb:discover:",
    "cb:visit:",
    "cb:book:",
    "cb:catalog:",
    "cb:clarify:",
)


#: Порог стражей первого контакта. Импортируется, а не переписывается: копия
#: разъехалась бы с живым порогом молча, и уборка вернула бы приветствие
#: человеку, который его уже прошёл.
def _first_contact_threshold() -> int:
    from apps.channels.max.global_onboarding import _FIRST_CONTACT_MESSAGE_ROWS

    return int(_FIRST_CONTACT_MESSAGE_ROWS)


@dataclass(frozen=True)
class _Verdict:
    """Что делать с одной строкой. ``action`` — ``rewrite`` / ``delete`` / ``skip``."""

    action: str
    new_content: str = ""
    reason: str = ""


def classify(content: str) -> _Verdict:
    """Решить судьбу одной строки истории теми же резолверами, что и хендлер.

    Порядок опроса значения не имеет: семейства различаются префиксом, и
    претендовать на payload может ровно один резолвер.
    """
    from apps.orchestrator.discovery import resolve_discover_tap
    from apps.orchestrator.nutrition_global import resolve_anketa_tap, resolve_food_tap
    from apps.channels.max.global_onboarding import resolve_welcome_tap

    stripped = (content or "").strip()
    if not stripped.startswith("cb:"):
        return _Verdict("skip", reason="не строка нажатия")

    for name, resolver in (
        ("anketa", resolve_anketa_tap),
        ("welcome", resolve_welcome_tap),
        ("food", resolve_food_tap),
        ("discover", resolve_discover_tap),
    ):
        tap = resolver(stripped)
        if tap is None:
            continue
        if tap.history_text:
            return _Verdict("rewrite", new_content=tap.history_text, reason=f"{name}: фраза")
        # Резолвер разобрал форму, но фразы нет. У ``discover`` это норма
        # (всё семейство молчит), у остальных — снятая кнопка: подставить
        # нечего, а удалять по догадке нельзя.
        if name == "discover":
            return _Verdict("delete", reason="discover: молчание")
        return _Verdict("skip", reason=f"{name}: форма верна, метки нет — решения нет")

    if stripped.startswith(_SILENT_PREFIXES):
        return _Verdict("delete", reason="карточка: молчание")
    return _Verdict("skip", reason="семейство без принятого решения")


class Command(BaseCommand):
    help = (
        "Отчёт и (по --apply) уборка сырых cb:-строк с ролью user из истории. "
        "Без --apply — сухой прогон: не пишется ничего."
    )

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Применить правки. Без него команда только отчитывается (по умолчанию).",
        )
        parser.add_argument(
            "--dump",
            type=str,
            default="",
            help="Путь для JSONL-дампа затронутых строк. Обязателен при --apply.",
        )
        parser.add_argument(
            "--conversation",
            type=str,
            default="",
            help="Ограничить одним диалогом (UUID).",
        )
        parser.add_argument(
            "--force-below-threshold",
            action="store_true",
            help=(
                "Удалять и в тех диалогах, где после уборки строк станет меньше порога "
                "первого контакта. По умолчанию такие диалоги пропускаются целиком."
            ),
        )

    def handle(self, *args: Any, **options: Any) -> None:
        apply_changes: bool = options["apply"]
        dump_path: str = options["dump"]
        conversation_id: str = options["conversation"]
        force: bool = options["force_below_threshold"]

        if apply_changes and not dump_path:
            raise CommandError("--apply требует --dump PATH: без дампа откатывать будет нечем.")
        dump_file: Path | None = None
        if dump_path:
            dump_file = Path(dump_path)
            if dump_file.exists():
                raise CommandError(f"{dump_file} уже существует — дамп не перезаписывается.")

        queryset = Message.all_tenants.filter(role="user", content__startswith="cb:")
        if conversation_id:
            queryset = queryset.filter(conversation_id=conversation_id)
        rows: list[Message] = list(
            queryset.order_by("created_at").only("id", "conversation_id", "content", "created_at")
        )

        if not rows:
            self.stdout.write("Сырых cb:-строк с ролью user не найдено.")
            return

        threshold = _first_contact_threshold()
        # Сколько строк ВСЕГО в каждом задетом диалоге — считаем ДО правок,
        # чтобы предупредить о пороге первого контакта, а не обнаружить его
        # последствия постфактум.
        touched_conversations = {r.conversation_id for r in rows}
        total_rows = {
            str(cid): Message.all_tenants.filter(conversation_id=cid).count()
            for cid in touched_conversations
        }

        planned: list[dict[str, Any]] = []
        families: Counter[str] = Counter()
        for row in rows:
            content = row.content or ""
            verdict = classify(content)
            families[content.split(":")[1] if content.count(":") >= 1 else "?"] += 1
            planned.append(
                {
                    "id": str(row.id),
                    "conversation_id": str(row.conversation_id),
                    "role": "user",
                    "created_at": row.created_at.isoformat(),
                    "content": content,
                    "action": verdict.action,
                    "new_content": verdict.new_content,
                    "reason": verdict.reason,
                }
            )

        deletions_per_conversation: Counter[str] = Counter(
            p["conversation_id"] for p in planned if p["action"] == "delete"
        )
        # ``_conversation_already_under_way`` считает диалог начавшимся при
        # ``rows > _FIRST_CONTACT_MESSAGE_ROWS``, поэтому опасна не только
        # пустота: ровно пороговое число строк уже читается как первый
        # контакт. Отсюда ``<=``, а не ``<``.
        at_risk = {
            cid
            for cid, deleted in deletions_per_conversation.items()
            if total_rows[cid] - deleted <= threshold
        }
        if at_risk and not force:
            for item in planned:
                if item["action"] == "delete" and item["conversation_id"] in at_risk:
                    item["action"] = "skip"
                    item["reason"] = "диалог упал бы ниже порога первого контакта"

        self._report(planned, families, at_risk, threshold, force)

        if not apply_changes:
            self.stdout.write(self.style.WARNING("\nСУХОЙ ПРОГОН — не изменено ничего."))
            self.stdout.write("Для применения: --apply --dump PATH (после решения владельца).")
            return

        actionable = [p for p in planned if p["action"] in ("rewrite", "delete")]
        if not actionable:
            self.stdout.write("Применять нечего.")
            return

        assert dump_file is not None  # noqa: S101 — проверено выше, --apply требует --dump
        with dump_file.open("w", encoding="utf-8") as fh:
            for item in actionable:
                fh.write(json.dumps(item, ensure_ascii=False) + "\n")
            fh.flush()
            os.fsync(fh.fileno())

        rewrite_ids = [p["id"] for p in actionable if p["action"] == "rewrite"]
        delete_ids = [p["id"] for p in actionable if p["action"] == "delete"]
        with transaction.atomic():
            changed = 0
            for item in actionable:
                if item["action"] != "rewrite":
                    continue
                changed += Message.all_tenants.filter(id=item["id"]).update(
                    content=item["new_content"]
                )
            _total, per_model = Message.all_tenants.filter(id__in=delete_ids).delete()
            # Считается ИМЕННО Message, а не сумма каскада: у строки истории
            # могут появиться зависимые записи, и тогда общий счётчик стал бы
            # больше числа строк в дампе — инвариант падал бы на здоровом
            # прогоне и уборка стала бы невыполнимой.
            deleted = per_model.get(Message._meta.label, 0)
            # Инвариант: тронуто ровно столько, сколько в дампе. Иначе откат.
            if changed != len(rewrite_ids) or deleted != len(delete_ids):
                raise CommandError(
                    "затронуто не то число строк "
                    f"(переписано {changed}/{len(rewrite_ids)}, "
                    f"удалено {deleted}/{len(delete_ids)}) — транзакция откачена"
                )

        self.stdout.write(
            self.style.SUCCESS(
                f"\nПрименено: переписано {len(rewrite_ids)}, удалено {len(delete_ids)}. "
                f"Дамп: {dump_file}"
            )
        )

    def _report(
        self,
        planned: list[dict[str, Any]],
        families: Counter[str],
        at_risk: set[str],
        threshold: int,
        force: bool,
    ) -> None:
        by_action = Counter(p["action"] for p in planned)
        self.stdout.write(f"Сырых cb:-строк с ролью user: {len(planned)}")
        self.stdout.write("\nПо семействам:")
        for family, count in families.most_common():
            self.stdout.write(f"  cb:{family:<12} {count}")
        self.stdout.write("\nПлан:")
        for action in ("rewrite", "delete", "skip"):
            self.stdout.write(f"  {action:<8} {by_action.get(action, 0)}")

        skipped = [p for p in planned if p["action"] == "skip"]
        if skipped:
            self.stdout.write("\nНЕ ТРОНУТО — решения по этим формам нет:")
            for reason, count in Counter(p["reason"] for p in skipped).most_common():
                self.stdout.write(f"  {count:>4}  {reason}")
            for content in sorted({p["content"] for p in skipped})[:20]:
                self.stdout.write(f"        {content}")

        if at_risk:
            verb = "УДАЛЯЮТСЯ ВСЁ РАВНО (--force-below-threshold)" if force else "ПРОПУЩЕНЫ"
            self.stdout.write(
                self.style.WARNING(
                    f"\nДиалогов, где после уборки строк стало бы меньше порога "
                    f"первого контакта ({threshold}): {len(at_risk)} — {verb}."
                )
            )
            for cid in sorted(at_risk):
                self.stdout.write(f"  {cid}")
