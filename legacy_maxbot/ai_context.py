"""Master context builder для system_prompt'а AI Concierge — Phase 2.3 §T04.

Адаптация Ayla/djangoproject/ai/application/services/specialist_context_builder.py.
Упрощения vs Ayla: single-salon (нет геолокации), нет min_rating filter,
нет distance ordering. Просто Top-N активных мастеров с услугами.

build_master_context() → MasterContext, который содержит:
- candidates: list[MasterCandidate] — id, name, specialization, services
- candidate_ids: frozenset[int] — для O(1) anti-hallucination check в handlers
- candidate_service_ids: frozenset[int] — service IDs для cross-validation
  (например handle_show_slots проверяет что service_id связан с master_id)
- summary_text: str — рендер для system_prompt'а с явными ID

system_prompt должен ВСЕГДА содержать список реальных ID — иначе LLM
галлюцинирует.
"""
from __future__ import annotations

from dataclasses import dataclass

from django.db.models import Prefetch

from services_app.models import Master, Service


DEFAULT_LIMIT = 20
APPROACH_PREVIEW_CHARS = 200  # Обрезаем approach в context — длинные HTML-блобы снижают LLM perf


def _strip_html(text: str) -> str:
    """Простая очистка HTML тегов для prompt-context (approach содержит HTML)."""
    import re
    return re.sub(r"<[^>]+>", " ", text)


@dataclass(frozen=True)
class MasterCandidate:
    """Один мастер для контекста LLM."""

    id: int
    name: str
    specialization: str
    # (service_id, name, category_name, requires_health_check)
    services: list[tuple[int, str, str, bool]]
    # Phase 2.4 T06: данные для LLM-side фильтрации по criteria клиента
    # ("опытный" / "мягкий" / "сильный").
    experience: str = ""
    approach: str = ""


@dataclass(frozen=True)
class MasterContext:
    """Bundle мастеров + service IDs + summary для system_prompt'а."""

    candidates: list[MasterCandidate]
    candidate_ids: frozenset[int]
    candidate_service_ids: frozenset[int]
    summary_text: str


def build_master_context(*, limit: int = DEFAULT_LIMIT) -> MasterContext:
    """Top-N активных мастеров с их услугами.

    Дешёвый вызов — single ORM-query + prefetch. Безопасно вызывать на каждом
    chat-turn'е. Если подключённые услуги > 5, в summary показываем top-5
    (по имени), но в candidate_service_ids — все.
    """
    qs = (
        Master.objects.filter(is_active=True)
        .prefetch_related(
            Prefetch(
                "services",
                queryset=(
                    Service.objects.filter(is_active=True)
                    .select_related("category")
                    .order_by("category__name", "name")
                ),
            )
        )
        .order_by("order", "name")[:limit]
    )

    candidates: list[MasterCandidate] = []
    all_service_ids: set[int] = set()

    for master in qs:
        services = list(master.services.all())
        all_service_ids.update(s.id for s in services)
        candidates.append(MasterCandidate(
            id=master.id,
            name=master.name,
            specialization=(master.specialization or "").strip(),
            services=[
                (
                    s.id,
                    s.name,
                    s.category.name if s.category else "Прочее",
                    bool(getattr(s, "requires_health_check", False)),
                )
                for s in services
            ],
            experience=(master.experience or "").strip(),
            approach=_strip_html(master.approach or "").strip(),
        ))

    return MasterContext(
        candidates=candidates,
        candidate_ids=frozenset(c.id for c in candidates),
        candidate_service_ids=frozenset(all_service_ids),
        summary_text=_render_summary(candidates),
    )


def _render_summary(candidates: list[MasterCandidate]) -> str:
    """Markdown-список мастеров с услугами, СГРУППИРОВАННЫМИ по категориям.

    Группировка по категории критична для anti-hallucination матчинга:
    клиент пишет «лазер» → LLM видит секцию [Лазерная эпиляция] → выбирает
    правильные service_id, не путает с RF-лифтингом.

    Формат:
        - master_id=42 Анна Иванова (массаж):
            [Ручные массажи] service_id=10 Спина; service_id=11 Лимфодренаж
            [Лазерная эпиляция (жен)] service_id=72 Голени; service_id=74 Бикини

    Без услуг → строка-пометка. LLM использует service_id из этого списка
    в show_slots/confirm_booking. Cross-validation в handle_show_slots
    проверяет что master.services содержит выбранный service_id.
    """
    if not candidates:
        return "(нет активных мастеров — записаться можно только через менеджера)"

    lines: list[str] = []
    for c in candidates:
        spec = f" ({c.specialization})" if c.specialization else ""
        lines.append(f"- master_id={c.id} {c.name}{spec}:")

        # Phase 2.4 T06: данные для LLM-side фильтрации по criteria
        meta_parts = []
        if c.experience:
            meta_parts.append(f"опыт: {c.experience}")
        if c.approach:
            preview = c.approach[:APPROACH_PREVIEW_CHARS]
            if len(c.approach) > APPROACH_PREVIEW_CHARS:
                preview += "…"
            meta_parts.append(f"подход: {preview}")
        if meta_parts:
            lines.append(f"    {' | '.join(meta_parts)}")

        if not c.services:
            lines.append("    (нет активных услуг)")
            continue

        # Группируем услуги по категории, сохраняя порядок появления.
        # Опасные услуги маркируем ⚠health чтобы LLM знал — нужен health-check
        # перед confirm_booking (T03).
        by_cat: dict[str, list[tuple[int, str, bool]]] = {}
        for sid, sname, cat, needs_health in c.services:
            by_cat.setdefault(cat, []).append((sid, sname, needs_health))

        for cat_name, items in by_cat.items():
            parts = []
            for sid, name, needs_health in items:
                marker = " ⚠health" if needs_health else ""
                parts.append(f"service_id={sid} {name}{marker}")
            lines.append(f"    [{cat_name}] {'; '.join(parts)}")
    return "\n".join(lines)
