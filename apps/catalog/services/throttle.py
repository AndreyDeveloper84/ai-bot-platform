"""Wait budget for Ayla's ``429 THROTTLED`` responses (DRF-1595).

### The defect this exists for

Ayla answers an over-quota catalog read with
``429 {"error":{"code":"THROTTLED","details":{"wait_seconds":54}}}``. Until
DRF-1595 :mod:`apps.catalog.services.http_client` treated that as any other
4xx — terminal, no retry — and threw the ``wait_seconds`` Ayla had just
handed us away. The beat then counted the tenant as *failed*, moved on, and
the same tenants lost every cycle (the fan-out walked an unordered
``Tenant.objects.all()``, so the tail of the database's order was the tail
every time). On the pilot that was ``formula-tela`` and ``fevralskiy-svet``:
three days with no catalog refresh while the bot told clients that services
the salon actively sells do not exist.

### Why waiting needs a budget, and why the budget is per beat run

The naive fix — sleep ``wait_seconds`` on every 429 — trades one defect for
a worse one. ``apps.catalog.tasks.sync_catalog_for_all_tenants`` runs under
``soft_time_limit=720`` (12 min, sized so the next 15-min tick starts
clean), and each tenant holds a Redis lock while it runs. Ten salons × three
catalog surfaces × a 54-second pause is far past that ceiling, so a run that
politely waited for everyone would be killed by
:class:`~celery.exceptions.SoftTimeLimitExceeded` mid-fan-out — and then
*nobody* would sync, not just the two on the tail.

So the waiting is capped, and the cap is shared across the whole run rather
than per request: one budget object is created by the beat and threaded
through :class:`~apps.catalog.services.sync.CatalogSyncService` into every
:class:`~apps.catalog.services.http_client.CatalogHttpClient` it builds.
Per-request budgets would multiply by the number of requests, which is the
same unbounded number this class exists to bound.

A healthy run never touches the budget: it is spent only on 429 sleeps.

### "Exhausted" means "we can no longer wait honestly"

Not ``remaining == 0``. If Ayla asks for 54 seconds and 10 remain, we cannot
honour it, and pretending otherwise (sleeping 10 and retrying into a still-
closed window) burns quota for nothing. :meth:`consume` therefore refuses
the whole request and latches :attr:`exhausted` — the beat reads that flag
to stop issuing work and mark the remaining salons *skipped*, which is a
different fact from *failed* and is logged as one.
"""

from __future__ import annotations

from django.conf import settings

# Default ceiling, in seconds, on time one beat run may spend asleep waiting
# out Ayla's rate limiter. One third of the task's 720s soft time limit:
# enough to let ~4 throttled salons through at the observed wait_seconds=54,
# and small enough that the ~480s left over comfortably covers the actual
# fetch/upsert work for the pilot's salon count.
DEFAULT_WAIT_BUDGET_SECONDS = 240


class ThrottleWaitBudget:
    """How much wall-clock one run may spend sleeping off ``429``s.

    Not thread-safe by design: the beat fan-out is sequential, and a budget
    shared across concurrent workers would need a distributed counter — a
    different problem than the one this solves.
    """

    __slots__ = ("_total", "_spent", "_refused")

    def __init__(self, total_seconds: float) -> None:
        self._total = max(0.0, float(total_seconds))
        self._spent = 0.0
        self._refused = False

    @classmethod
    def from_settings(cls) -> "ThrottleWaitBudget":
        """Budget sized by ``CATALOG_SYNC_THROTTLE_WAIT_BUDGET_SECONDS``.

        Used by callers that were handed no budget — the one-shot
        ``manage.py sync_catalog`` run, onboarding — so a lone client still
        has a ceiling instead of an unbounded one.
        """
        return cls(
            getattr(
                settings,
                "CATALOG_SYNC_THROTTLE_WAIT_BUDGET_SECONDS",
                DEFAULT_WAIT_BUDGET_SECONDS,
            )
        )

    @property
    def total_seconds(self) -> float:
        return self._total

    @property
    def spent_seconds(self) -> float:
        return self._spent

    @property
    def remaining_seconds(self) -> float:
        return max(0.0, self._total - self._spent)

    @property
    def exhausted(self) -> bool:
        """True once a wait was refused, or nothing is left to spend.

        Latched: a refusal stays visible after the fact, because the beat
        reads this *between* tenants to decide whether the rest of the
        fan-out is still worth attempting.
        """
        return self._refused or self.remaining_seconds <= 0.0

    def consume(self, seconds: float) -> bool:
        """Reserve ``seconds`` of sleep. ``False`` means: do not sleep.

        All-or-nothing on purpose (see the module docstring): a partial wait
        returns into a window that is still closed, so it costs a request
        and buys nothing.
        """
        want = float(seconds)
        if want <= 0.0:
            # Ayla said "no wait needed" (or said something unusable and the
            # caller normalised it to zero). Nothing to reserve, and this is
            # not a refusal — the caller may retry immediately.
            return True
        if want > self.remaining_seconds:
            self._refused = True
            return False
        self._spent += want
        return True


__all__ = ["DEFAULT_WAIT_BUDGET_SECONDS", "ThrottleWaitBudget"]
