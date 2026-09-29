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

### The rule this module now obeys (owner decision В-14, 10.09.2026)

``LLM_PROVIDER`` is **authoritative** for the configured runtime
vendor. Under ``LLM_PROVIDER=anthropic``:

* production health checks MUST check Anthropic;
* **OpenAI's health does not determine Ayla's LLM health**;
* a silent runtime fall-back to OpenAI is **forbidden** — permitted only
  under a fallback policy that has been designed, approved and tested,
  and no such policy exists.

Which gives the two readings an operator has to hold at once, both
demonstrated this week:

* **a red panel is not a broken Ayla** — 10.09, 2018 ticks of a wallet
  nobody spends from;
* **a green panel is not a live Ayla** — what the same code would have
  shown had Anthropic died.

And the sentence that names the mechanism behind both:

    **Holding a secret is not permission to fall back.**

Nobody decided the probe should ask OpenAI. The key was present and the
class was imported, and the presence of a secret quietly became
behaviour. That is why this module now resolves its vendor and refuses
to inherit the router's quota-fallback wrapper: see
:func:`build_probe_provider`, and the DRF-1631 note on
:func:`apps.llm.router.provider_is_configured`, which arms the serving
path's hop from exactly the same evidence — a key being set.

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

Each transition goes to :func:`apps.observability.alerting.page`
(``critical`` down / ``warning`` up) — and only there. ``page`` fans out
to Telegram + Sentry + the operators' MAX chat (``HANDOFF_NOTIFY_MAX_CHAT_IDS``
/ ``_USER_IDS``, the DRF-1029 escalation recipients) and writes one
audit row ``observability.alert.paged`` with ``telegram_sent`` /
``sentry_sent`` / ``max_sent``, so an unconfigured channel is visible
there rather than silent.

History. Until DRF-1938 the probe sent to MAX directly through
:func:`apps.handoff.notify.send_max_notification`. 15.09 the probe went
DOWN at 07:40 and the message reached the one MAX chat configured — where
nobody saw it for 45 minutes — so DRF-1938 added ``page`` (Telegram +
Sentry) next to the direct MAX call, with the cause class in the text
(network/proxy, provider, or unclassified — never guessed). DRF-2158 made
MAX the third sink of ``page`` itself for every operator alert; the direct
call here was removed, otherwise «LLM недоступна» would land in the chat
twice. Invariant: **llm.health.down → exactly one message in MAX**
(guarded in ``tests/test_health_alert_delivery_1938.py``).

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

# DRF-1938 — класс причины в тексте алерта. Инцидент 15.09: прокси лёг, а
# алерт называл только класс исключения SDK; «сеть или провайдер» читающий
# должен был вывести сам. Словари закрытые: неизвестный класс — «причина не
# классифицирована», а не догадка.
CAUSE_NETWORK = "network"
CAUSE_PROVIDER = "provider"
CAUSE_UNCLASSIFIED = "unclassified"
#: Сеть или прокси: запрос не дошёл до провайдера или ответ не вернулся.
NETWORK_ERROR_CLASSES = frozenset({"APIConnectionError", "APITimeoutError", "ConnectError"})
#: Провайдер ответил ошибкой: HTTP-статусы SDK обоих вендоров и наши квоты.
PROVIDER_ERROR_CLASSES = frozenset(
    {
        "InternalServerError",
        "RateLimitError",
        "LLMVendorCreditsExhausted",
        "LLMQuotaError",
        "LLMProviderQuotaExceeded",
        "APIStatusError",
        "AuthenticationError",
        "PermissionDeniedError",
        "NotFoundError",
        "BadRequestError",
        "UnprocessableEntityError",
        "ConflictError",
        "OverloadedError",
    }
)
CAUSE_TEXT = {
    CAUSE_NETWORK: "сеть или прокси недоступны",
    CAUSE_PROVIDER: "провайдер отвечает ошибкой",
    CAUSE_UNCLASSIFIED: "причина не классифицирована",
}

# DRF-2065 — состояние ПУТИ к LLM, которое читает readyz. Инцидент 16–17.09:
# 9 ч аварийного текста при зелёном readyz. После DRF-2147 бот умеет уйти на
# резерв, и «основной лёг» больше не значит «клиентам отвечает заглушка» —
# состояний три, плюс честное «не знаю».
PATH_PRIMARY = "primary"
PATH_FALLBACK = "fallback"
PATH_DOWN = "down"
PATH_UNKNOWN = "unknown"
CACHE_KEY_PATH = "llm:health:path"
#: Три пропущенных тика (beat раз в 5 минут) — это уже не знание.
DEFAULT_PATH_STALE_S = 900

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


def build_probe_provider(name: str, **overrides: Any) -> Any:
    """A FRESH, unwrapped instance of vendor ``name`` for one probe.

    Construction goes through :func:`apps.llm.router.build_provider`,
    the same name→class step the serving path takes, so a vendor added
    to the registry is probeable the day it lands.

    What the probe deliberately does NOT inherit from
    ``get_provider``:

    * the router's per-process provider **cache** — a pooled tunnel
      would hide the failure the probe exists to find (see the module
      docstring);
    * :class:`~apps.llm.router.FallbackProvider` — a probe that hops on
      quota exhaustion or unavailability (DRF-2147) reports the *other*
      vendor's health under this one's name, which is the exact
      confusion DRF-1631 is undoing;
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

    # ``overrides`` — только то, что проба меняет осознанно: ``proxy=""``
    # для замера прямого пути (DRF-2065). Всё остальное — как у продакшена.
    return build_provider(name, retry_policy=RetryPolicy(max_attempts=1), **overrides)


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


async def probe_llm(
    *,
    model: str | None = None,
    provider_name: str | None = None,
    proxy: str | None = None,
) -> ProbeResult:
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

    DRF-2065: ``provider_name`` и ``proxy`` задаёт только
    :func:`check_llm_availability` — для второго замера того же тика
    (резерв из ``fallback_candidates`` DRF-2147 или прямой путь без
    прокси). Без них — ровно прежняя проба основного вендора.
    """

    chosen_model = model or getattr(settings, "LLM_HEALTH_PROBE_MODEL", "") or None
    ceiling = float(getattr(settings, "LLM_HEALTH_PROBE_TIMEOUT_S", 60.0))

    if provider_name is None:
        provider_name, source = probe_target()
        _log_uncovered_vendors(provider_name)
    else:
        source = "drf2065_second_look"
    overrides = {} if proxy is None else {"proxy": proxy}

    started = time.monotonic()
    try:
        provider = build_probe_provider(provider_name, **overrides)
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


def evaluate_probe(
    result: ProbeResult,
    *,
    path: dict[str, Any] | None = None,
    previous_path_state: str | None = None,
) -> str:
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
        up_text = build_recovered_message(result, down_since=down_since)
        _page(
            "warning", "LLM снова доступна", up_text, dedup_key=f"llm.health.recovered:{down_since}"
        )
        return TRANSITION_UP

    failures = int(cache.get(CACHE_KEY_FAILURES) or 0) + 1
    cache.set(CACHE_KEY_FAILURES, failures, ttl)

    if previous == STATE_DOWN:
        # DRF-2065: основной лежит, а резерв умер или ожил — это новость,
        # а не повтор: объявленное «работаем на резерве» перестало быть
        # правдой (или стало ей). Один раз на смену, не на тик.
        new_path_state = path.get("state") if path else None
        watched = (PATH_FALLBACK, PATH_DOWN)
        if (
            previous_path_state in watched
            and new_path_state in watched
            and new_path_state != previous_path_state
        ):
            now_iso = timezone.now().isoformat()
            lost = new_path_state == PATH_DOWN
            _page(
                "critical" if lost else "warning",
                "LLM: резерв тоже недоступен" if lost else "LLM: работаем на резерве",
                build_down_message(result, failures=failures, path=path),
                dedup_key=f"llm.health.path:{new_path_state}:{now_iso}",
            )
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
    down_text = build_down_message(result, failures=failures, path=path)
    on_fallback = (path or {}).get("state") == PATH_FALLBACK
    # DRF-1938 / DRF-2158 — единственный путь: ``page`` = Telegram + Sentry +
    # MAX. Прямого вызова MAX здесь больше нет — иначе сообщение приходило
    # бы дважды. Ненастроенный канал виден в аудите
    # ``observability.alert.paged`` (telegram_sent / sentry_sent / max_sent).
    if on_fallback:
        # Клиентам отвечает резерв — для клиентов это не авария, но основной
        # путь всё равно чинить: warning, а не тишина.
        _page(
            "warning", "LLM: работаем на резерве", down_text, dedup_key=f"llm.health.down:{now_iso}"
        )
    else:
        _page("critical", "LLM недоступна", down_text, dedup_key=f"llm.health.down:{now_iso}")
    return TRANSITION_DOWN


def reset_state() -> None:
    """Drop the persisted health state. Test + operator escape hatch."""

    cache.delete_many([CACHE_KEY_STATE, CACHE_KEY_FAILURES, CACHE_KEY_DOWN_SINCE, CACHE_KEY_PATH])


# ---------------------------------------------------------------------------
# Path state (DRF-2065) — what readyz reads
# ---------------------------------------------------------------------------


def _empty_path_state() -> dict[str, Any]:
    return {
        "state": PATH_UNKNOWN,
        "primary": None,
        "fallback": None,
        "direct_path": None,
        "checked_at": None,
    }


def write_path_state(
    *,
    state: str,
    primary: str | None,
    fallback: str | None,
    direct_path: bool | None,
    checked_at: str | None = None,
) -> dict[str, Any]:
    """Итог тика для readyz. ``direct_path`` — None, если прямой путь не мерили."""

    record = {
        "state": state,
        "primary": primary,
        "fallback": fallback,
        "direct_path": direct_path,
        "checked_at": checked_at or timezone.now().isoformat(),
    }
    cache.set(CACHE_KEY_PATH, record, _state_ttl())
    return record


def read_path_state() -> dict[str, Any]:
    """Последнее измеренное состояние пути — или честное ``unknown``.

    Никакого вызова LLM: readyz опрашивается часто, и зависимость от
    внешнего API ему противопоказана (см. ``apps.orchestrator.health``).
    Неизмеренное — ``unknown``, а не «основной жив»; измеренное давнее
    ``LLM_HEALTH_PATH_STALE_S`` — тоже ``unknown`` с ``detail="stale"``:
    если beat умер, readyz не должен вечно показывать последний зелёный тик.
    """

    record = cache.get(CACHE_KEY_PATH)
    if not isinstance(record, dict):
        return _empty_path_state()
    out = {**_empty_path_state(), **record}
    try:
        checked = datetime.fromisoformat(str(out["checked_at"]))
    except ValueError:
        return {**out, "state": PATH_UNKNOWN, "detail": "unparseable"}
    if timezone.is_naive(checked):
        checked = timezone.make_aware(checked, timezone.get_default_timezone())
    stale_s = int(getattr(settings, "LLM_HEALTH_PATH_STALE_S", DEFAULT_PATH_STALE_S))
    if (timezone.now() - checked).total_seconds() > stale_s:
        return {**out, "state": PATH_UNKNOWN, "detail": "stale"}
    return out


def _direct_verdict(result: ProbeResult) -> bool | None:
    """Прямой путь «есть», если провайдер ответил хоть чем-то.

    403 региона — тоже ответ: сеть до провайдера жива, значит мёртв
    прокси (замер 17.09: 403 за 0.1 с при ConnectError через прокси).
    Сетевой отказ — «нет». Неклассифицированное — не угадываем.
    """

    if result.ok:
        return True
    cause = classify_cause(result.error_class)
    if cause == CAUSE_PROVIDER:
        return True
    if cause == CAUSE_NETWORK:
        return False
    return None


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


def classify_cause(error_class: str | None) -> str:
    """Класс причины по имени исключения (DRF-1938). Незнакомое не угадывается."""

    if error_class in NETWORK_ERROR_CLASSES:
        return CAUSE_NETWORK
    if error_class in PROVIDER_ERROR_CLASSES:
        return CAUSE_PROVIDER
    return CAUSE_UNCLASSIFIED


def build_down_message(
    result: ProbeResult, *, failures: int, path: dict[str, Any] | None = None
) -> str:
    """Operator-facing text for the UP → DOWN transition.

    No credentials — ``result.error_message`` has already been through
    :func:`redact_secrets`. The proxy HOST stays (decision on DRF-2065):
    operators need it to know which proxy to replace.

    DRF-2065: ``path`` — итог тика. После DRF-2147 основной путь может
    лежать, пока клиентам отвечает резерв; тогда строка «бот отвечает
    аварийным текстом» была бы неправдой, пережившей свою причину.
    """

    tick = path or {}
    on_fallback = tick.get("state") == PATH_FALLBACK
    lines = [
        "🟠 Основной путь к LLM недоступен — работаем на резерве"
        if on_fallback
        else "🔴 LLM недоступна",
        "Проверка пути к языковой модели не проходит.",
        f"Неудачных проверок подряд: {failures}",
    ]
    # DRF-1631 — the panel names its SUBJECT next to its verdict. The
    # 10.09 alarm said "openai.complete: vendor credits exhausted" while
    # the product ran on Anthropic, and nothing in the message let the
    # reader see that mismatch.
    if result.provider:
        lines.append(f"Провайдер: {result.provider}")
    # DRF-1938 — сеть/прокси или провайдер: первым делом, до имени исключения.
    lines.append(f"Причина: {CAUSE_TEXT[classify_cause(result.error_class)]}")
    direct = tick.get("direct_path")
    if direct is True:
        lines.append("Прямой путь к провайдеру: есть — сеть жива, менять нужно прокси.")
    elif direct is False:
        lines.append("Прямой путь к провайдеру: нет — провайдер недоступен и в обход прокси.")
    lines.append(f"Ошибка: {result.error_class or 'unknown'}")
    if result.error_message:
        lines.append(f"Детали: {result.error_message}")
    lines.append(f"Проверка длилась: {result.latency_s:.1f} с")
    lines.append(f"Время: {_now_label()}")
    if on_fallback:
        lines.append(
            f"Клиентам отвечает резервный провайдер: {tick.get('fallback')}. "
            "Основной путь нужно чинить."
        )
    else:
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


def _page(severity: str, title: str, body: str, *, dedup_key: str) -> bool:
    """Текст перехода в операционный канал (DRF-1938 / DRF-2158). Never raises.

    ``apps.observability.alerting.page`` — Telegram + Sentry + MAX
    (``HANDOFF_NOTIFY_MAX_CHAT_IDS`` / ``_USER_IDS``); он сам пишет аудит
    ``observability.alert.paged`` с ``telegram_sent`` / ``sentry_sent`` /
    ``max_sent`` — ненастроенный канал виден там, а не теряется. Текста
    реплик в ``body`` нет: это сообщение пробы.
    """

    try:
        from apps.observability import alerting

        sent = alerting.page(severity, title, body, dedup_key=dedup_key)  # type: ignore[arg-type]
        logger.info("llm.health.page_sent severity=%s sent=%s", severity, sent)
        return bool(sent)
    except Exception:  # noqa: BLE001 — alerting must never break the probe
        logger.exception("llm.health.page_unexpected")
        return False


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
    path = _measure_path(provider_name, result, model=model)
    previous_path = cache.get(CACHE_KEY_PATH)
    previous_path_state = previous_path.get("state") if isinstance(previous_path, dict) else None
    write_path_state(**path)
    transition = evaluate_probe(result, path=path, previous_path_state=previous_path_state)
    return {
        "ok": result.ok,
        "provider": result.provider,
        "latency_s": round(result.latency_s, 3),
        "transition": transition,
        "error_class": result.error_class,
        "path": path["state"],
    }


def _measure_path(primary: str, result: ProbeResult, *, model: str | None) -> dict[str, Any]:
    """Итог тика: основной / резерв / лежат — и прямой путь, если он что-то проясняет.

    В штатное время — ноль лишних вызовов: тик стоит одну проверку, как до
    DRF-2065. Второй взгляд — только когда основной не ответил:

    * резерв — тот кандидат, на которого ушёл бы живой ход
      (:func:`apps.llm.router.serving_fallback_candidates`, правило DRF-2147);
      модель — его собственная по умолчанию: ``LLM_HEALTH_PROBE_MODEL`` может
      называть модель основного вендора;
    * прямой путь — только при СЕТЕВОМ отказе и только если основной шёл
      через прокси: иначе упавший путь и был прямым, а ответ 500 ничего не
      говорит о сети.
    """

    from apps.llm.router import provider_class, serving_fallback_candidates

    if result.ok:
        return {"state": PATH_PRIMARY, "primary": primary, "fallback": None, "direct_path": None}

    candidates = serving_fallback_candidates(primary)
    fallback = candidates[0] if candidates else None
    fallback_ok = False
    if fallback is not None:
        fallback_ok = asyncio.run(probe_llm(provider_name=fallback)).ok

    direct: bool | None = None
    if classify_cause(result.error_class) == CAUSE_NETWORK:
        try:
            via_proxy = bool(provider_class(primary).configured_proxy())
        except Exception:  # noqa: BLE001 — the probe reports, never raises
            logger.warning("llm.health.proxy_unresolved provider=%s", primary, exc_info=True)
            via_proxy = False
        if via_proxy:
            direct = _direct_verdict(
                asyncio.run(probe_llm(model=model, provider_name=primary, proxy=""))
            )

    logger.warning(
        "llm.health.path primary=%s fallback=%s fallback_ok=%s direct_path=%s",
        primary,
        fallback,
        fallback_ok,
        direct,
    )
    return {
        "state": PATH_FALLBACK if fallback_ok else PATH_DOWN,
        "primary": primary,
        "fallback": fallback,
        "direct_path": direct,
    }
