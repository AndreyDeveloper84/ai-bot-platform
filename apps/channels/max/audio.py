"""MAX audio attachment extraction + streaming download (DRF-1942, этап 1, PR 1).

Sibling of :mod:`apps.channels.max.photo`. Same trust boundary, same
SSRF gate, same «hostname only» logging rule — the differences are
exactly what the live stage-0 measurement showed for voice notes
(``docs/REPORT_VOICE_INPUT_STAGE0_2026-09-15.md`` §1.4):

* the attachment ``type`` is ``"audio"`` and its ``payload`` carries
  ``{"id", "url", "token"}`` — ``id`` is an attachment identifier MAX
  does not document in the schema but ships on the wire; we keep it
  as the idempotency handle for the later download/recognition step;
* the file is Ogg/Opus, 48 kHz mono, ~4 KB per second of speech, so
  the byte cap is 5 MiB (≈ 20 minutes — far above any duration limit
  the owner may choose, yet small enough that a hostile 1 GiB payload
  never lands in the worker's memory);
* the download URL is a signed CDN link that is valid for 24 h and
  needs no bot token — ``GET`` as-is, no ``Authorization`` header;
* the download budget is one **wall-clock deadline for the whole
  transfer**, not httpx's per-operation timeout — the consumer is a
  single sequential process (``apps/workers/consumer.py``), and a CDN
  that trickles one byte every 7 s would otherwise hold it hostage
  for minutes while every per-read timeout is individually met.

Pure adapter: nothing here knows about handlers, feature flags,
recognition providers or the conversation model. The handler decides
what to do with the bytes (PR 3); ``apps/speech`` turns them into
text (PR 2).

The SSRF validator and the hostname helper are **reused from**
:mod:`apps.channels.max.photo` on purpose: that module's tests patch
``apps.channels.max.photo.socket`` / ``.httpx`` by name, so the
validator must stay where it is. We import the functions, we do not
move them.

### Logging note

Same rule as ``photo.py``: never the raw URL, never the querystring.
The signed query (``signatureToken``, ``expires``, ``userId``) is the
bearer for a person's voice for 24 h. Log lines carry the hostname
only.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Final

import httpx

from apps.channels.max.photo import PhotoDownloadError, _validate_cdn_url, safe_hostname

logger = logging.getLogger(__name__)

#: Cumulative byte cap. Stage-0 measurement: MAX voice ≈ 4 KB/s, so
#: 5 MiB ≈ 20 min of speech — well above the owner's duration limit
#: (60 s recommended) while still bounding worker memory.
MAX_AUDIO_BYTES: Final[int] = 5 * 1024 * 1024  # 5 MiB

#: Wall-clock budget for the entire download (connect + all reads).
#: Enforced with ``time.monotonic`` inside the chunk loop in addition
#: to httpx's per-operation timeout — see the module docstring.
AUDIO_DOWNLOAD_DEADLINE_S: Final[float] = 8.0

#: Ogg container capture pattern (RFC 3533 §6). MAX ships voice notes
#: as Ogg/Opus; anything else is refused before it can reach the
#: recognition provider, which would otherwise charge for garbage.
_OGG_MAGIC: Final[bytes] = b"OggS"


@dataclass(frozen=True, slots=True)
class AudioRef:
    """What we take from an ``audio`` attachment: where to fetch and how to name it.

    ``attachment_id`` is MAX's own identifier for the uploaded file
    (present on the wire, absent from the published schema). ``None``
    when MAX omits it — callers must not rely on it for correctness,
    only as an optional dedupe key.
    """

    url: str
    attachment_id: int | None


class AudioTooLargeError(Exception):
    """Payload exceeded :data:`MAX_AUDIO_BYTES` mid-stream → ``voice_too_large``."""


class AudioDownloadError(Exception):
    """Timeout, deadline, network error, 4xx/5xx or SSRF reject → ``voice_download_failed``."""


class AudioFormatError(Exception):
    """Body is not an Ogg container → ``voice_unsupported_format``."""


def extract_first_audio(attachments: list[dict[str, Any]] | None) -> AudioRef | None:
    """Return the first ``audio`` attachment as :class:`AudioRef`, or ``None``.

    Tolerant of mixed lists and malformed entries (missing ``payload``,
    non-string ``url``) — skips and continues. Does NOT validate the
    URL: the download path is the single SSRF chokepoint, the
    extractor stays a pure parser (same split as ``photo.py``).
    """
    if not attachments:
        return None
    for att in attachments:
        if not isinstance(att, dict) or att.get("type") != "audio":
            continue
        payload = att.get("payload")
        if not isinstance(payload, dict):
            continue
        url = payload.get("url")
        if not isinstance(url, str) or not url:
            continue
        raw_id = payload.get("id")
        # bool is an int subclass — `True` must not become attachment 1.
        attachment_id = raw_id if isinstance(raw_id, int) and not isinstance(raw_id, bool) else None
        return AudioRef(url=url, attachment_id=attachment_id)
    return None


def is_ogg(head: bytes) -> bool:
    """``True`` when ``head`` starts with the Ogg capture pattern."""
    return head[: len(_OGG_MAGIC)] == _OGG_MAGIC


def download_audio(url: str, *, deadline_s: float = AUDIO_DOWNLOAD_DEADLINE_S) -> bytes:
    """Stream-download a voice note with SSRF gate, byte cap, wall-clock deadline and format check.

    Order of checks, cheapest first:

    1. :func:`apps.channels.max.photo._validate_cdn_url` — no socket
       is opened for a rejected URL.
    2. HTTP status: 4xx/5xx → :class:`AudioDownloadError`.
    3. Per chunk: cumulative size vs :data:`MAX_AUDIO_BYTES`, elapsed
       vs ``deadline_s``. The first chunk is also checked for the Ogg
       magic so a wrong format is refused after a few bytes, not after
       5 MiB.

    ``deadline_s`` also bounds each httpx operation, so a stalled
    connect cannot outlive the budget either.

    Raises:
      :class:`AudioTooLargeError`: cumulative body > 5 MiB.
      :class:`AudioFormatError`: body does not start with ``OggS``
        (including an empty body).
      :class:`AudioDownloadError`: SSRF reject, deadline, timeout,
        network error, 4xx, 5xx, anything unexpected from httpx.

    Returns:
      Raw Ogg bytes, in memory only. The caller owns their lifetime;
      nothing is written to disk here.
    """
    try:
        _validate_cdn_url(url)
    except PhotoDownloadError as exc:
        # Same rejection classes as photos; re-typed so the handler's
        # audio branch has one exception family to map to a refusal.
        raise AudioDownloadError(str(exc)) from exc
    safe_host = safe_hostname(url)
    started = time.monotonic()

    try:
        with httpx.Client(timeout=deadline_s, follow_redirects=False) as http:
            with http.stream("GET", url) as resp:
                if resp.status_code >= 500:
                    logger.warning(
                        "max.audio.cdn_5xx host=%s status=%d",
                        safe_host,
                        resp.status_code,
                    )
                    raise AudioDownloadError(f"cdn 5xx: HTTP {resp.status_code}")
                if resp.status_code >= 400:
                    logger.warning(
                        "max.audio.cdn_4xx host=%s status=%d",
                        safe_host,
                        resp.status_code,
                    )
                    raise AudioDownloadError(f"cdn 4xx: HTTP {resp.status_code}")

                chunks: list[bytes] = []
                total = 0
                for chunk in resp.iter_bytes():
                    if time.monotonic() - started > deadline_s:
                        logger.warning(
                            "max.audio.deadline host=%s bytes=%d budget_s=%.1f",
                            safe_host,
                            total,
                            deadline_s,
                        )
                        raise AudioDownloadError("deadline: download exceeded budget")
                    if not chunks and chunk and not is_ogg(chunk):
                        logger.warning("max.audio.not_ogg host=%s", safe_host)
                        raise AudioFormatError("not an ogg container")
                    total += len(chunk)
                    if total > MAX_AUDIO_BYTES:
                        raise AudioTooLargeError(f"audio > {MAX_AUDIO_BYTES} bytes")
                    chunks.append(chunk)
                body = b"".join(chunks)
                if not is_ogg(body):
                    # Empty body, or the first chunk was empty and the
                    # magic never showed up.
                    logger.warning("max.audio.not_ogg host=%s", safe_host)
                    raise AudioFormatError("not an ogg container")
                return body
    except (httpx.TimeoutException, httpx.NetworkError) as exc:
        logger.warning("max.audio.network_failure host=%s exc=%s", safe_host, type(exc).__name__)
        raise AudioDownloadError(f"network: {type(exc).__name__}") from exc
    except (AudioTooLargeError, AudioFormatError, AudioDownloadError):
        raise
    except Exception as exc:  # noqa: BLE001 — defensive boundary, must not crash the dispatcher
        logger.warning("max.audio.unexpected_failure host=%s exc=%s", safe_host, type(exc).__name__)
        raise AudioDownloadError(f"unexpected: {type(exc).__name__}") from exc
