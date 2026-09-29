"""``GET /api/v1/admin/handoff-queue/`` — очередь handoff для «Сегодня» (DRF-2115).

Карточка «Диалоги — N ждут ответа» на экране «Сегодня» салонной админки и
экран очереди за ней. Источник — тот же, что у экрана очереди в
Django-админке (DRF-1499, :mod:`apps.adminconsole.handoff_queue`): открытые
``AdminTask`` (``open`` / ``in_progress``), самые старые сверху — но ТОЛЬКО
этого салона: ``AdminTask.objects`` под ``tenant_scope`` декоратора, а не
кросс-тенантный ``all_tenants`` админки.

Только чтение. «Взять» и «закрыть» остаются в Django-админке (DRF-1488 —
механика назначения и эскалации не дублируется на второй поверхности).

Что уходит наружу — задача и её возраст, не человек: ``task_id``, ``status``,
``age_minutes``, ``claimed`` / ``addressee`` (оператор или очередь — не
клиент), ``escalated``, ``created_at``. Текстов сообщений
(``transcript_snapshot``), причины (``reason``) и данных клиента (имя,
идентификатор канала) в ответе нет: очередь отвечает «сколько и как давно
ждут», а не «кто и что писал». Реестр ПДн — ``test_pii_route_registry_2094``.
"""

from __future__ import annotations

from typing import Any

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from apps.admin_api.auth import require_admin_role
from apps.adminconsole.handoff_queue import OPEN_STATUSES
from apps.handoff.models import AdminTask


def _row(task: AdminTask, *, now: Any) -> dict[str, Any]:
    age_minutes = max(0, int((now - task.created_at).total_seconds() // 60))
    return {
        "task_id": str(task.id),
        "status": task.status,
        "age_minutes": age_minutes,
        "created_at": task.created_at.isoformat(),
        "claimed": task.claimed_at is not None,
        "addressee": task.addressee,
        "escalated": task.pickup_escalated_at is not None,
    }


@csrf_exempt
@require_http_methods(["GET"])
@require_admin_role
def handoff_queue(request: HttpRequest) -> HttpResponse:
    """Открытые задачи handoff этого салона: сколько ждут и как давно."""
    now = timezone.now()
    tasks = (
        AdminTask.objects.filter(status__in=OPEN_STATUSES)
        .select_related("assigned_to")
        .order_by("created_at")
    )
    rows = [_row(task, now=now) for task in tasks]
    return JsonResponse({"waiting": len(rows), "rows": rows})
