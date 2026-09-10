"""LLM path availability probe + cold-connection warm-up.

DRF-1054 (monitor the LLM path, signal on failure) and DRF-1056 (keep
the connection from going cold) are two readings of one periodic act:
*make one cheap real call down the production LLM path and look at what
comes back*. This module owns that act; :mod:`apps.llm.tasks` is the
beat-scheduled shell around it.

### The incident this exists for (2026-08-13)

The proxy through which the bot reaches OpenAI stopped establishing
HTTPS tunnels. The TCP port stayed open, so nothing that merely pings
the host would have noticed — ``CONNECT`` hung and was cut at the
client timeout. Direct access to ``api.openai.com`` from a Russian
address is closed (403 in ~0.2 s), so the proxy is the *only* path and
its failure takes the whole product down: the pilot runs through an
LLM concierge. The pilot was dead for hours and we learned about it
from the owner, not from the system. The DRF-989 emergency reply did
its job — it is the only reason anybody noticed at all.

### One call, two tickets

DRF-1054 wants a periodic probe that alerts on failure. DRF-1056 wants
periodic traffic so the first real user message of the day does not pay
the cold-start price (measured: 20.7 s cold vs 0.8–1.4 s warm,
reproduced twice). Both are satisfied by the same request, so this
module makes ONE request per tick rather than two:

* the request itself is the warm-up (DRF-1056);
* its outcome drives the state machine and the alert (DRF-1054).

Running two separate periodic LLM calls would double the cost and the
traffic to buy nothing. If the two ever need different cadences, split
the beat entry — the logic below is already parameterless per concern.

### WHICH provider the probe builds (DRF-1631)

The vendor is **resolved, never named here**. The probe asks
:func:`apps.llm.router.resolve_provider_tier` — the same function
:meth:`apps.llm.router.LLMRouter.get_provider` asks on a live turn —
and builds the answer with
:func:`apps.llm.router.build_provider`, the same constructor the
serving path uses. Not a rule that agrees with the router: the router's
own code.

Until DRF-1631 this module imported ``OpenAIProvider`` by name. The
pilot has run ``LLM_PROVIDER=anthropic`` since DRF-1443, so the probe
was measuring a vendor the product does not call. Both halves of that
cost us:

* **false alarm** — on 10.09.2026 the owner's panel showed
  "🔴 LLM недоступна … LLMVendorCreditsExhausted … openai.complete:
  vendor credits exhausted" for the **2018th** consecutive tick. The
  OpenAI wallet really was empty; nothing was routed to it. Two hours
  of bot logs held zero Anthropic errors and zero emergency replies to
  customers. An alarm that has been red for 2018 ticks is furniture;
  nobody walks over to it any more.
* **blindness, which is worse** — had Anthropic failed, the probe would
  have gone on reporting green, because it was asking OpenAI.

The probe's context has no tenant and no skill, so the tier that
answers it is tier 3, ``LLM_PROVIDER``. That is the honest scope of one
cheap call, and it is stated rather than assumed: when
``SKILL_LLM_PROVIDER`` routes some skill to another vendor, that vendor
is NOT covered, and every tick logs ``llm.health.uncovered_vendors``
naming it. Widening the probe to one call per configured vendor is a
real change in cost and cadence and wants its own ticket; going quiet
about the gap is not an option.

### Why a FRESH client on every probe

Each provider's ``_get_client`` caches its ``httpx`` client, and a
pooled, already-established tunnel would sail straight past exactly the
failure we are trying to detect: the proxy refusing to *establish* new
tunnels. So each probe constructs its own provider, and closes it in a
``finally`` (see :meth:`OpenAIProvider.aclose` and its DRF-1631 twin
:meth:`AnthropicProvider.aclose` — otherwise every tick leaks a
connection pool into the Celery worker).

This also makes the warm-up meaningful. The 13.08 measurements were
taken from separate short-lived processes, and the second one was still
fast — so the warmth being preserved lives in the *proxy*, not in our
client's connection pool (INFERRED, and the assumption DRF-1056 rests
on). If that turns out to be wrong, warming from the Celery worker will
not help the consumer process and the latency logged here will say so:
probe latency will keep showing cold-start numbers.

### Why the probe does NOT retry

The production call path retries twice (``LLM_RETRY_MAX_ATTEMPTS=2``).
The probe deliberately runs with ``max_attempts=1``:

* retries are exactly what *masks* a degrading path — a probe that
  retries reports "fine" right up until it reports nothing;
* debouncing belongs in the state machine, where it is explicit and
  tunable (``LLM_HEALTH_FAILURE_THRESHOLD``), not smeared into the
  request layer;
* a retrying probe takes up to ~61 s to conclude, which pushes a beat
  task toward its time limit for no information gain.

Everything else about the probe — client construction, proxy, timeout,
SDK settings — comes from the production provider unchanged, so the
probe measures the path users actually travel.

### Signal on transition, never per tick

A monitor that shouts every iteration trains its reader to ignore it,
and then it is worth less than no monitor. State lives in Redis
(``llm:health:*``); MAX gets a message only when the state *changes*:

* UP → DOWN after ``LLM_HEALTH_FAILURE_THRESHOLD`` consecutive
  failures (default 2 — one blip does not page anyone);
* DOWN → UP on the first success, immediately. Asymmetric on purpose:
  slow to alarm, fast to clear.

Ticks that do not change the state log and do nothing else. Recovery is
logged and audited under its own slug (``llm.health.recovered``) so
"how long was it down" is answerable after the fact.

Losing the Redis state (flush, restart with a cold cache) costs at most
one duplicate alert on the next transition. Acceptable — the alternative
is a table and a migration for two strings.

### Transport

The alert reuses :func:`apps.handoff.notify.send_max_notification` and
the recipient list from ``HANDOFF_NOTIFY_MAX_CHAT_IDS`` — the primitive
DRF-1029 already put in production for escalations. No second transport,
no second address book.

### Secrets

The proxy credentials live in an environment variable in the clear, and
SDK/httpx connection errors are entirely capable of quoting the proxy
URL — userinfo included — back at us. Every error string that reaches a
log line, an audit row, or a messenger goes through
:func:`redact_secrets` first. See its docstring; do not weaken it.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

STATE_UP = "up"
STATE_DOWN = "down"

#: Returned by :func:`evaluate_probe` — what (if anything) changed.
TRANSITION_NONE = "none"
TRANSITION_DOWN = "down"
TRANSITION_UP = "up"

CACHE_KEY_STATE = "llm:health:state"
CACHE_KEY_FAILURES = "llm:health:consecutive_failures"
CACHE_KEY_DOWN_SINCE = "llm:health:down_since"

AUDIT_HEALTH_DOWN = "llm.health.down"
AUDIT_HEALTH_RECOVERED = "llm.health.recovered"

#: Skip reasons returned by :func:`check_llm_availability` without probing.
SKIP_DISABLED = "disabled"
SKIP_NO_API_KEY = "no_api_key"  # pragma: allowlist secret — a skip reason, not a key

# The cheapest completion that still exercises the whole path: one
# token in, one token out. At gpt-4o-mini prices a tick costs on the
# order of 2e-6 USD, i.e. ~0.0006 USD/day at a 5-minute cadence.
_PROBE_PROMPT = "ping"
_PROBE_MAX_TOKENS = 1

# Error text is truncated before it reaches a messenger — a pathological
# provider error must not push an unbounded blob into an operator chat.
_MAX_ERROR_CHARS = 160

# Provider exception wrappers whose ``__cause__`` carries the SDK error
# we actually want to name in the alert.
_LLM_WRAPPER_NAMES = frozenset({"LLMError", "LLMTransportError", "LLMQuotaError"})


# ---------------------------------------------------------------------------
# Secret redaction
# ---------------------------------------------------------------------------

# The ``scheme://userinfo@host`` shape an httpx/SDK connection error
# quotes back when it names the proxy it failed to reach. (The literal
# form is spelled out only in the regex below — writing it in prose
# trips the repo's own secret scanner, which is the behaviour we want.)
_SCHEME_USERINFO_RE = re.compile(r"(?P<scheme>[A-Za-z][A-Za-z0-9+.\-]*://)[^/\s@]+@")

# Bare userinfo (no scheme) left behind by whatever formatted the
# message. Deliberately narrow: both halves must be non-empty and free
# of whitespace, ``/``, ``@`` and ``:``.
_BARE_USERINFO_RE = re.compile(r"(?<![\w.\-/])[^\s:@/]+:[^\s:@/]+@")


def redact_secrets(text: str) -> str:
    """Strip proxy credentials / API keys out of ``text``.

    Three layers, in order:

    1. a URL carrying userinfo → the userinfo replaced with ``***``,
       scheme and host kept (``http://***@proxy.example:3128``);
    2. the same shape with the scheme already stripped;
    3. verbatim occurrences of ``OPENAI_PROXY`` / ``OPENAI_API_KEY`` /
       ``ANTHROPIC_PROXY`` / ``ANTHROPIC_API_KEY`` replaced with
       ``***``.

    Layer 3 is the belt to layers 1–2's braces: it also covers a proxy
    URL that carries no userinfo but is itself not for publication.

    Applied to every error string before it reaches a log line, an audit
    payload, or a MAX message.
    """

    if not text:
        return ""
    out = _SCHEME_USERINFO_RE.sub(lambda m: f"{m.group('scheme')}***@", text)
    out = _BARE_USERINFO_RE.sub("***@", out)
    for secret in (
        getattr(settings, "OPENAI_PROXY", "") or "",
        getattr(settings, "OPENAI_API_KEY", "") or "",
        # DRF-1631 — the probe now follows ``LLM_PROVIDER``, so an
        # Anthropic connection error can quote Anthropic's proxy and key
        # back at us just as readily.
        getattr(settings, "ANTHROPIC_PROXY", "") or "",
        getattr(settings, "ANTHROPIC_API_KEY", "") or "",
    ):
        if secret and secret in out:
            out = out.replace(secret, "***")
    return out


# ---------------------------------------------------------------------------
# Probe
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProbeResult:
    """Outcome of one probe.

    Attributes:
      ok: the LLM path answered.
      provider: the vendor that was actually asked — resolved from
        ``LLM_PROVIDER`` through the router's own tier walk, never
        assumed. Carried into the alert text so the panel names its
        subject next to its verdict; DRF-1631 exists because for 2018
        ticks it did not.
      latency_s: wall-clock seconds the attempt took — on success this
        is the DRF-1056 warm/cold signal, on failure it is how long the
        path took to fail (a 30 s failure is a hung tunnel, a 0.2 s
        failure is an active refusal — worth telling apart).
      error_class: SDK exception class name, unwrapped past our own
        wrappers. Empty on success.
      error_message: redacted + truncated error text. Empty on success.
    """

    ok: bool
    latency_s: float
    provider: str = ""
    error_class: str = ""
    error_message: str = ""


def _unwrap_error(exc: BaseException) -> BaseException:
    """Dig out the SDK exception behind our own wrapper classes.

    ``RetriableLLMError`` carries ``last_error``; ``LLMTransportError``
    and friends carry ``__cause__``. Naming ``APITimeoutError`` in the
    alert is worth the two lines — it is the difference between "the
    tunnel hung" and "the proxy said no".
    """

    from apps.llm.retry import RetriableLLMError

    if isinstance(exc, RetriableLLMError):
        return exc.last_error
    cause = exc.__cause__
    if cause is not None and type(exc).__name__ in _LLM_WRAPPER_NAMES:
        return cause
    return exc


def probe_target() -> tuple[str, str]:
    """Which vendor this probe will ask, and which tier said so.

    A one-line delegation to :func:`apps.llm.router.resolve_provider_tier`
    with no tenant and no skill — the probe's real context — and it must
    stay a delegation. The moment this function grows a rule of its own,
    "the probe measures what the product uses" goes back to being a
    coincidence that holds until somebody edits one of the two copies.
    ``apps/llm/tests/test_health_probe_parity.py`` is the guard that
    notices if it does.
    """

    from apps.llm.router import resolve_provider_tier

    return resolve_provider_tier()


def build_probe_provider(name: str) -> Any:
    """A FRESH, unwrapped instance of vendor ``name`` for one probe.

    Construction goes through :func:`apps.llm.router.build_provider`,
    the same name→class step the serving path takes, so a vendor added
    to the registry is probeable the day it lands.

    What the probe deliberately does NOT inherit from
    ``get_provider``:

    * the router's per-process provider **cache** — a pooled tunnel
      would hide the failure the probe exists to find (see the module
      docstring);
    * :class:`~apps.llm.router.QuotaFallbackProvider` — a probe that
      hops on quota exhaustion reports the *other* vendor's health
      under this one's name, which is the exact confusion DRF-1631 is
      undoing;
    * :class:`~apps.llm.pii_protected_provider.PIITokenizingProvider` —
      the payload is the literal string ``ping``.

    ``retry_policy`` is pinned to one attempt: see the module docstring,
    "Why the probe does NOT retry".

    Typed ``Any`` rather than :class:`~apps.llm.protocol.LLMProvider`
    because the probe uses ``aclose``, which is deliberately NOT part of
    that protocol — it is the short-lived-caller hook and this module is
    the only short-lived caller.
    """

    from apps.llm.retry import RetryPolicy
    from apps.llm.router import build_provider

    return build_provider(name, retry_policy=RetryPolicy(max_attempts=1))


def _log_uncovered_vendors(probed: str) -> list[str]:
    """Name, every tick, the configured vendors this probe does not cover.

    One cheap call answers for one vendor. When ``SKILL_LLM_PROVIDER``
    points a skill at a second one, that second vendor is serving real
    turns with nobody watching it — the same blindness DRF-1631 fixes
    for tier 3, one tier up. This does not fix it; a probe per vendor
    changes the cost and the cadence and wants its own ticket. It
    refuses to let the gap be silent, which is the part that can be done
    for free.

    Returns the uncovered names so tests can assert on the count rather
    than on a log string.
    """

    from apps.llm.router import configured_vendor_names

    uncovered = [name for name in configured_vendor_names() if name != probed]
    if uncovered:
        logger.warning(
            "llm.health.uncovered_vendors probed=%s uncovered=%s count=%d "
            "(SKILL_LLM_PROVIDER routes live turns to a vendor this probe does not measure)",
            probed,
            ",".join(uncovered),
            len(uncovered),
        )
    return uncovered


async def probe_llm(*, model: str | None = None) -> ProbeResult:
    """Make one cheap real completion down the production LLM path.

    Never raises: every failure mode is folded into ``ok=False``. A
    monitor that can throw is a monitor that can take the scheduler with
    it.

    The SDK-level timeout is deliberately NOT overridden — the provider
    reads ``LLM_REQUEST_TIMEOUT_S`` exactly as it does for user traffic,
    so the probe measures the path users travel.
    ``LLM_HEALTH_PROBE_TIMEOUT_S`` is a separate *outer* ceiling: httpx
    applies its scalar timeout per phase (connect, read, write, pool),
    so a pathological request can outlive any single phase budget, and a
    beat task must have a hard stop.

    WHICH vendor is asked comes from the router, not from this module —
    see the docstring section "WHICH provider the probe builds".
    """

    chosen_model = model or getattr(settings, "LLM_HEALTH_PROBE_MODEL", "") or None
    ceiling = float(getattr(settings, "LLM_HEALTH_PROBE_TIMEOUT_S", 60.0))

    provider_name, source = probe_target()
    _log_uncovered_vendors(provider_name)

    started = time.monotonic()
    try:
        provider = build_probe_provider(provider_name)
    except Exception as exc:  # noqa: BLE001 — the probe reports, never raises
        # A vendor that cannot even be CONSTRUCTED (missing SDK, unset
        # key, malformed settings) is a dead path, not a skipped check.
        # Pre-DRF-1631 this construction sat outside the try and could
        # take the beat task with it, contradicting "never raises".
        underlying = _unwrap_error(exc)
        logger.error(
            "llm.health.provider_build_failed provider=%s source=%s error=%s",
            provider_name,
            source,
            type(underlying).__name__,
        )
        return ProbeResult(
            ok=False,
            latency_s=time.monotonic() - started,
            provider=provider_name,
            error_class=type(underlying).__name__,
            error_message=redact_secrets(str(underlying))[:_MAX_ERROR_CHARS],
        )

    logger.info(
        "llm.health.probe_target provider=%s source=%s model=%s",
        provider_name,
        source,
        chosen_model or "<vendor default>",
    )
    try:
        await asyncio.wait_for(
            provider.complete(
                [{"role": "user", "content": _PROBE_PROMPT}],
                model=chosen_model,
                temperature=0.0,
                max_tokens=_PROBE_MAX_TOKENS,
            ),
            timeout=ceiling,
        )
    except Exception as exc:  # noqa: BLE001 — the probe reports, never raises
        underlying = _unwrap_error(exc)
        return ProbeResult(
            ok=False,
            latency_s=time.monotonic() - started,
            provider=provider_name,
            error_class=type(underlying).__name__,
            error_message=redact_secrets(str(underlying))[:_MAX_ERROR_CHARS],
        )
    finally:
        # Fresh client per probe by design — close it or leak a pool per tick.
        await _aclose(provider, provider_name)

    return ProbeResult(ok=True, latency_s=time.monotonic() - started, provider=provider_name)


async def _aclose(provider: object, name: str) -> None:
    """Close the probe's own client. Loud when a provider cannot.

    ``aclose`` is not part of :class:`apps.llm.protocol.LLMProvider` —
    it is the short-lived-caller hook, and only this module is a
    short-lived caller. A vendor added without it leaks an httpx pool
    per tick into the Celery worker, silently, which is precisely how
    ``AnthropicProvider`` reached DRF-1631 with no ``aclose`` at all:
    the probe could not reach it, so nothing complained. The getattr is
    tolerant so one missing hook cannot break the monitor; the WARNING
    is what stops it being tolerant *and quiet*.
    """

    close = getattr(provider, "aclose", None)
    if close is None:
        logger.warning("llm.health.provider_has_no_aclose provider=%s (pool leaked)", name)
        return
    await close()


def run_probe_sync(*, model: str | None = None) -> ProbeResult:
    """Sync wrapper for the prefork Celery worker."""

    return asyncio.run(probe_llm(model=model))


# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------


def _state_ttl() -> int:
    return int(getattr(settings, "LLM_HEALTH_STATE_TTL_S", 7 * 24 * 3600))


def _failure_threshold() -> int:
    return max(1, int(getattr(settings, "LLM_HEALTH_FAILURE_THRESHOLD", 2)))


def evaluate_probe(result: ProbeResult) -> str:
    """Fold ``result`` into the persisted state; notify only on change.

    Returns one of :data:`TRANSITION_NONE` / :data:`TRANSITION_DOWN` /
    :data:`TRANSITION_UP`.

    The initial state is assumed UP: a fresh deploy with an empty cache
    must not announce a recovery that nobody was waiting for. The first
    real outage still fires normally after ``threshold`` failures.
    """

    ttl = _state_ttl()
    threshold = _failure_threshold()
    previous = cache.get(CACHE_KEY_STATE) or STATE_UP

    if result.ok:
        cache.set(CACHE_KEY_FAILURES, 0, ttl)
        if previous != STATE_DOWN:
            logger.info(
                "llm.health.ok latency_s=%.2f state=%s",
                result.latency_s,
                STATE_UP,
            )
            return TRANSITION_NONE

        down_since = cache.get(CACHE_KEY_DOWN_SINCE)
        cache.set(CACHE_KEY_STATE, STATE_UP, ttl)
        cache.delete(CACHE_KEY_DOWN_SINCE)
        # Recovery gets its own log slug + audit action (brief: log it
        # separately) so downtime windows are reconstructable later.
        logger.warning(
            "llm.health.recovered latency_s=%.2f down_since=%s",
            result.latency_s,
            down_since,
        )
        _write_audit(
            AUDIT_HEALTH_RECOVERED,
            {
                "latency_s": round(result.latency_s, 3),
                "down_since": down_since,
                "downtime": _format_downtime(down_since),
            },
        )
        _notify(build_recovered_message(result, down_since=down_since))
        return TRANSITION_UP

    failures = int(cache.get(CACHE_KEY_FAILURES) or 0) + 1
    cache.set(CACHE_KEY_FAILURES, failures, ttl)

    if previous == STATE_DOWN:
        # Already announced. Keep the log trail, stay off the channel —
        # a repeating alert is a muted alert.
        logger.warning(
            "llm.health.still_down failures=%d error=%s",
            failures,
            result.error_class,
        )
        return TRANSITION_NONE

    if failures < threshold:
        logger.warning(
            "llm.health.probe_failed failures=%d/%d error=%s latency_s=%.2f msg=%s",
            failures,
            threshold,
            result.error_class,
            result.latency_s,
            result.error_message,
        )
        return TRANSITION_NONE

    now_iso = timezone.now().isoformat()
    cache.set(CACHE_KEY_STATE, STATE_DOWN, ttl)
    cache.set(CACHE_KEY_DOWN_SINCE, now_iso, ttl)
    logger.error(
        "llm.health.down failures=%d error=%s latency_s=%.2f msg=%s",
        failures,
        result.error_class,
        result.latency_s,
        result.error_message,
    )
    _write_audit(
        AUDIT_HEALTH_DOWN,
        {
            "failures": failures,
            "provider": result.provider,
            "error_class": result.error_class,
            "error_message": result.error_message,
            "latency_s": round(result.latency_s, 3),
        },
    )
    _notify(build_down_message(result, failures=failures))
    return TRANSITION_DOWN


def reset_state() -> None:
    """Drop the persisted health state. Test + operator escape hatch."""

    cache.delete_many([CACHE_KEY_STATE, CACHE_KEY_FAILURES, CACHE_KEY_DOWN_SINCE])


# ---------------------------------------------------------------------------
# Operator-facing messages
# ---------------------------------------------------------------------------


def _now_label() -> str:
    return timezone.localtime().strftime("%d.%m.%Y %H:%M")


def _format_downtime(down_since: object) -> str:
    """Human "≈ 25 мин" / "≈ 3 ч 10 мин" from a stored ISO timestamp."""

    if not isinstance(down_since, str) or not down_since:
        return ""
    try:
        started = datetime.fromisoformat(down_since)
    except ValueError:
        return ""
    if timezone.is_naive(started):
        started = timezone.make_aware(started, timezone.get_default_timezone())
    seconds = int((timezone.now() - started).total_seconds())
    if seconds < 0:
        return ""
    minutes, hours = (seconds // 60) % 60, seconds // 3600
    if hours:
        return f"{hours} ч {minutes} мин"
    return f"{minutes} мин"


def build_down_message(result: ProbeResult, *, failures: int) -> str:
    """Operator-facing text for the UP → DOWN transition.

    No credentials, no endpoint URLs — ``result.error_message`` has
    already been through :func:`redact_secrets`.
    """

    lines = [
        "🔴 LLM недоступна",
        "Проверка пути к языковой модели не проходит.",
        f"Неудачных проверок подряд: {failures}",
    ]
    # DRF-1631 — the panel names its SUBJECT next to its verdict. The
    # 10.09 alarm said "openai.complete: vendor credits exhausted" while
    # the product ran on Anthropic, and nothing in the message let the
    # reader see that mismatch.
    if result.provider:
        lines.append(f"Провайдер: {result.provider}")
    lines.append(f"Ошибка: {result.error_class or 'unknown'}")
    if result.error_message:
        lines.append(f"Детали: {result.error_message}")
    lines.append(f"Проверка длилась: {result.latency_s:.1f} с")
    lines.append(f"Время: {_now_label()}")
    lines.append("Бот сейчас отвечает клиентам аварийным текстом.")
    return "\n".join(lines)


def build_recovered_message(result: ProbeResult, *, down_since: object = None) -> str:
    """Operator-facing text for the DOWN → UP transition."""

    lines = [
        "🟢 LLM снова доступна",
        f"Ответ получен за {result.latency_s:.1f} с",
    ]
    if result.provider:
        lines.append(f"Провайдер: {result.provider}")
    downtime = _format_downtime(down_since)
    if downtime:
        lines.append(f"Недоступность длилась ≈ {downtime}")
    lines.append(f"Время: {_now_label()}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Side channels — notification + audit
# ---------------------------------------------------------------------------


def _notify(text: str) -> int:
    """Fan the text out to the DRF-1029 MAX recipients. Never raises.

    Empty recipient list = mechanism off (the CI / local-dev default),
    exactly as for escalations.
    """

    try:
        from apps.handoff.notify import get_notify_addresses, send_max_notification

        recipients = get_notify_addresses()
        if not recipients:
            logger.info("llm.health.notify_skipped reason=no_recipients")
            return 0
        # DRF-1559 — тот же список операторов, но выбор ключа адресации
        # сделан за нас: людей предпочитаем диалогам.
        failures = send_max_notification(text=text, addresses=recipients)
        logger.info(
            "llm.health.notify_sent recipients=%d failures=%d addressed_by=%s",
            len(recipients),
            failures,
            recipients[0].key,
        )
        return failures
    except Exception:  # noqa: BLE001 — alerting must never break the probe
        logger.exception("llm.health.notify_unexpected")
        return 0


def _write_audit(action: str, payload: dict) -> None:
    """Audit row for the transition. Best-effort, like every audit call."""

    try:
        from apps.audit.services import write_audit

        write_audit(action, target="llm.health", payload=payload)
    except Exception:  # noqa: BLE001 — audit is observational
        logger.exception("llm.health.audit_failed action=%s", action)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def check_llm_availability(*, model: str | None = None) -> dict[str, object]:
    """Probe once, fold the result into the state machine, alert on change.

    This is the whole tick: the call is the DRF-1056 warm-up, the verdict
    is the DRF-1054 monitor. Never raises.

    Returns a small dict for Celery result visibility and for tests:
    ``{"skipped": ..., "provider": ...}`` when no probe was made,
    otherwise ``{"ok", "provider", "latency_s", "transition",
    "error_class"}``.
    """

    if not getattr(settings, "LLM_HEALTH_PROBE_ENABLED", True):
        return {"skipped": SKIP_DISABLED}

    from apps.llm.router import provider_is_configured

    provider_name, _source = probe_target()

    # DRF-1631: the key that matters is the RESOLVED vendor's, read
    # through the router's own ``key_setting_name``. This used to test
    # ``OPENAI_API_KEY`` unconditionally, which on an Anthropic pilot
    # asks about a wallet nobody spends from: with the pilot's keys it
    # waved a dead path through, and on a deployment that (rightly) sets
    # only ``ANTHROPIC_API_KEY`` it would have skipped every tick and
    # called the silence health.
    if not provider_is_configured(provider_name):
        # No key: on a dev box this is normal, on the pilot it is a
        # deploy fault. We cannot tell the two apart from here, so we
        # refuse to page anyone and leave a WARNING that says why.
        logger.warning(
            "llm.health.skipped reason=%s provider=%s",
            SKIP_NO_API_KEY,
            provider_name,
        )
        return {"skipped": SKIP_NO_API_KEY, "provider": provider_name}

    result = run_probe_sync(model=model)
    transition = evaluate_probe(result)
    return {
        "ok": result.ok,
        "provider": result.provider,
        "latency_s": round(result.latency_s, 3),
        "transition": transition,
        "error_class": result.error_class,
    }
