"""HMAC signature middleware for the internal events ingest channel.

Phase 0 / #432 scaffold. Skeleton only — the real verification (canonical
header name, HMAC-SHA256 over ``request.body`` with
``settings.EVENT_INGEST_HMAC_SECRET``, replay-window check) lands with
Beta #441 (``docs/architecture/event-contract.md``).

Until #441 publishes the wire-level contract, this middleware is a
pass-through. It MUST NOT be added to ``settings.MIDDLEWARE`` yet — a
stub that silently accepts is worse than a stub that's absent. The
class shape is in place so the follow-up PR can fill the body and
flip the settings hook without rewriting call sites or tests.
"""

from __future__ import annotations

from typing import Callable


class HMACSignatureMiddleware:
    """Django middleware skeleton for the events ingest HMAC gate.

    Standard Django middleware contract: ``__init__(get_response)``
    captures the downstream callable; ``__call__(request)`` passes
    the request through (or, post-#441, verifies the signature first
    and short-circuits with 401 / 403 on mismatch).
    """

    def __init__(self, get_response: Callable[..., object]) -> None:
        self.get_response = get_response

    def __call__(self, request: object) -> object:
        # TODO(#441): verify HMAC-SHA256 of request.body against the
        # canonical header (name TBD) using settings.EVENT_INGEST_HMAC_SECRET.
        # On mismatch: return JsonResponse({"status": "forbidden"}, status=403).
        # On replay (timestamp outside window): return 401.
        return self.get_response(request)
