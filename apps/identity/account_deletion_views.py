"""``POST /api/v1/internal/privacy/account-deletion/`` — бот-половина удаления
аккаунта по просьбе исполнителя каталога (§7 D3, DRF-1725).

Сторож — тот же HMAC-SHA256 + метка времени, что у ingest событий
(``apps/eventbus/ingest_security.py``, секрет ``EVENT_INGEST_HMAC_SECRET``):
каталог подписывает сырое тело тем же ``AYLA_OUTBOUND_HMAC_SECRET``, и
нового секрета у контура не появляется. Отказ подписи — 401, тело не
логируется.

Ответ 200 — всегда с ``all_ok``: каталог ставит ``COMPLETED`` только при
``all_ok=true``. Исключение внутри каскада — 500, каталог повторит.
"""

from __future__ import annotations

import json
import logging
import uuid

from django.conf import settings
from django.http import HttpRequest, JsonResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from apps.audit.services import write_audit
from apps.eventbus.ingest_security import (
    signature_header_from,
    timestamp_header_from,
    verify_signature,
)
from apps.identity.services.account_deletion import execute_bot_half

logger = logging.getLogger(__name__)


@method_decorator(csrf_exempt, name="dispatch")
class InternalAccountDeletionView(View):
    http_method_names = ["post"]

    def post(self, request: HttpRequest) -> JsonResponse:
        body = request.body or b""
        sig = verify_signature(
            body=body,
            signature_header=signature_header_from(request),
            timestamp_header=timestamp_header_from(request),
            secret=getattr(settings, "EVENT_INGEST_HMAC_SECRET", "") or "",
        )
        if not sig.ok:
            logger.warning(
                "identity.account_deletion.signature_failed reason=%s body_bytes=%d",
                sig.reason,
                len(body),
            )
            return JsonResponse({"status": "unauthorized", "reason": sig.reason}, status=401)

        try:
            data = json.loads(body.decode("utf-8"))
            request_id = str(data["request_id"])
            ayla_user_id = uuid.UUID(str(data["ayla_user_id"]))
            external_user_ids = [str(x) for x in data.get("external_user_ids", [])]
        except (ValueError, KeyError, TypeError, AttributeError):
            return JsonResponse({"status": "bad_request", "reason": "malformed_body"}, status=400)

        outcome = execute_bot_half(
            ayla_user_id=ayla_user_id,
            external_user_ids=external_user_ids,
            request_id=request_id,
        )
        # Аудит: кто и что — без значений (C5 §6.2).
        write_audit(
            "privacy.account_deletion_bot_half",
            target="ayla_user",
            target_id=ayla_user_id,
            payload={
                "request_id": request_id,
                "shells": outcome.shells,
                "all_ok": outcome.all_ok,
                "failed_steps": outcome.failed_steps,
                "flag_cleared": outcome.flag_cleared,
            },
        )
        return JsonResponse({"data": outcome.as_payload()}, status=200)
