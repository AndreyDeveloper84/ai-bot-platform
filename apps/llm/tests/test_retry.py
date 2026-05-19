"""Tests for :mod:`apps.llm.retry` (Phase 1 / PI7 / DRF-858).

All tests inject fake ``_sleep`` and ``_random`` so no wall-clock
time is consumed and the backoff math is deterministic.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from apps.llm.retry import (
    DEFAULT_POLICY,
    RetriableLLMError,
    RetryPolicy,
    _compute_backoff_delay,
    is_retriable_anthropic,
    is_retriable_openai,
    run_with_retry,
)


# ---------------------------------------------------------------------------
# Helpers — fake SDK exception classes
# ---------------------------------------------------------------------------


# OpenAI-style retriable exceptions (matched by class NAME — same trick
# the provider uses to avoid pinning to a specific SDK version).
class _FakeRateLimitError(Exception):
    pass


_FakeRateLimitError.__name__ = "RateLimitError"


class _FakeAPIConnectionError(Exception):
    pass


_FakeAPIConnectionError.__name__ = "APIConnectionError"


class _FakeAPITimeoutError(Exception):
    pass


_FakeAPITimeoutError.__name__ = "APITimeoutError"


class _FakeInternalServerError(Exception):
    pass


_FakeInternalServerError.__name__ = "InternalServerError"


# OpenAI-style non-retriable (4xx, business errors).
class _FakeAuthenticationError(Exception):
    pass


_FakeAuthenticationError.__name__ = "AuthenticationError"


class _FakeBadRequestError(Exception):
    pass


_FakeBadRequestError.__name__ = "BadRequestError"


class _FakePermissionDeniedError(Exception):
    pass


_FakePermissionDeniedError.__name__ = "PermissionDeniedError"


class _FakeNotFoundError(Exception):
    pass


_FakeNotFoundError.__name__ = "NotFoundError"


# Unified APIStatusError shape — status carried as attribute, not class.
class _FakeStatusError(Exception):
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        super().__init__(f"status_code={status_code}")


_FakeStatusError.__name__ = "APIStatusError"


# Helper: a fake _sleep that records every call without sleeping.
class _SleepRecorder:
    def __init__(self) -> None:
        self.calls: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.calls.append(delay)


# Helper: a deterministic fake _random that always returns 0.5
# (midpoint → no jitter applied).
async def _no_jitter_random() -> float:  # noqa: D401
    return 0.5


def _fixed_rand(value: float):
    def _rand() -> float:
        return value

    return _rand


# Policy with jitter disabled — exact arithmetic predictability.
NO_JITTER_POLICY = RetryPolicy(max_attempts=3, base_delay_s=1.0, max_delay_s=30.0, jitter=0.0)


# ---------------------------------------------------------------------------
# Happy path + retry-then-succeed
# ---------------------------------------------------------------------------


class TestRetrySuccess:
    @pytest.mark.asyncio
    async def test_returns_on_first_try_no_sleep(self) -> None:
        sleeper = _SleepRecorder()
        fn = AsyncMock(return_value="ok")

        result = await run_with_retry(
            fn,
            policy=NO_JITTER_POLICY,
            is_retriable=is_retriable_openai,
            _sleep=sleeper,
            _random=_fixed_rand(0.5),
        )

        assert result == "ok"
        assert fn.await_count == 1
        assert sleeper.calls == []

    @pytest.mark.asyncio
    async def test_succeeds_on_attempt_2(self) -> None:
        sleeper = _SleepRecorder()
        # Fail once, then succeed.
        fn = AsyncMock(side_effect=[_FakeRateLimitError("rate limited"), "ok"])

        result = await run_with_retry(
            fn,
            policy=NO_JITTER_POLICY,
            is_retriable=is_retriable_openai,
            _sleep=sleeper,
            _random=_fixed_rand(0.5),
        )

        assert result == "ok"
        assert fn.await_count == 2
        # One sleep call (between attempt 1 and 2).
        assert len(sleeper.calls) == 1
        # base * 2^0 = 1s, no jitter → exactly 1.0.
        assert sleeper.calls[0] == pytest.approx(1.0)

    @pytest.mark.asyncio
    async def test_succeeds_on_attempt_3(self) -> None:
        sleeper = _SleepRecorder()
        fn = AsyncMock(
            side_effect=[
                _FakeRateLimitError("first"),
                _FakeInternalServerError("second"),
                "third-success",
            ]
        )

        result = await run_with_retry(
            fn,
            policy=NO_JITTER_POLICY,
            is_retriable=is_retriable_openai,
            _sleep=sleeper,
            _random=_fixed_rand(0.5),
        )

        assert result == "third-success"
        assert fn.await_count == 3
        # Two sleeps: between attempts 1→2 and 2→3.
        assert sleeper.calls == pytest.approx([1.0, 2.0])


# ---------------------------------------------------------------------------
# Exhaustion + non-retriable
# ---------------------------------------------------------------------------


class TestRetryExhaustion:
    @pytest.mark.asyncio
    async def test_exhausted_raises_retriable_llm_error(self) -> None:
        sleeper = _SleepRecorder()
        exc_to_raise = _FakeRateLimitError("persistent rate limit")
        fn = AsyncMock(side_effect=[exc_to_raise] * 5)

        with pytest.raises(RetriableLLMError) as exc_info:
            await run_with_retry(
                fn,
                policy=NO_JITTER_POLICY,
                is_retriable=is_retriable_openai,
                _sleep=sleeper,
                _random=_fixed_rand(0.5),
            )

        # max_attempts=3 means exactly 3 attempts before giving up.
        assert exc_info.value.attempts == 3
        assert exc_info.value.last_error is exc_to_raise
        assert fn.await_count == 3
        # Sleep is called between attempts 1→2 and 2→3, NOT after
        # the final failed attempt.
        assert len(sleeper.calls) == 2

    @pytest.mark.asyncio
    async def test_retriable_llm_error_chains_via_from(self) -> None:
        """Exception chaining: the last error is the __cause__ too."""
        sleeper = _SleepRecorder()
        last = _FakeInternalServerError("final 503")
        fn = AsyncMock(
            side_effect=[
                _FakeRateLimitError("first"),
                _FakeRateLimitError("second"),
                last,
            ]
        )

        with pytest.raises(RetriableLLMError) as exc_info:
            await run_with_retry(
                fn,
                policy=NO_JITTER_POLICY,
                is_retriable=is_retriable_openai,
                _sleep=sleeper,
                _random=_fixed_rand(0.5),
            )

        assert exc_info.value.last_error is last
        assert exc_info.value.__cause__ is last


class TestNonRetriable:
    @pytest.mark.asyncio
    async def test_non_retriable_raises_immediately(self) -> None:
        sleeper = _SleepRecorder()
        exc = _FakeBadRequestError("bad arg")
        fn = AsyncMock(side_effect=exc)

        with pytest.raises(_FakeBadRequestError):
            await run_with_retry(
                fn,
                policy=NO_JITTER_POLICY,
                is_retriable=is_retriable_openai,
                _sleep=sleeper,
                _random=_fixed_rand(0.5),
            )

        # Exactly one call — no retry.
        assert fn.await_count == 1
        assert sleeper.calls == []

    @pytest.mark.asyncio
    async def test_arbitrary_value_error_propagates(self) -> None:
        sleeper = _SleepRecorder()
        fn = AsyncMock(side_effect=ValueError("weird"))

        with pytest.raises(ValueError):
            await run_with_retry(
                fn,
                policy=NO_JITTER_POLICY,
                is_retriable=is_retriable_openai,
                _sleep=sleeper,
                _random=_fixed_rand(0.5),
            )

        assert fn.await_count == 1

    @pytest.mark.asyncio
    async def test_mixed_transient_then_4xx_fails_fast(self) -> None:
        """First call: retriable; second call: non-retriable.

        Expected: one retry (the transient one), then immediate
        propagation of the 4xx.
        """
        sleeper = _SleepRecorder()
        fn = AsyncMock(
            side_effect=[
                _FakeRateLimitError("transient"),
                _FakeBadRequestError("now 400"),
            ]
        )

        with pytest.raises(_FakeBadRequestError):
            await run_with_retry(
                fn,
                policy=NO_JITTER_POLICY,
                is_retriable=is_retriable_openai,
                _sleep=sleeper,
                _random=_fixed_rand(0.5),
            )

        # Two attempts total — first retried (1 sleep), second propagated.
        assert fn.await_count == 2
        assert len(sleeper.calls) == 1


# ---------------------------------------------------------------------------
# Backoff math
# ---------------------------------------------------------------------------


class TestBackoffMath:
    def test_no_jitter_exponential_progression(self) -> None:
        policy = RetryPolicy(max_attempts=5, base_delay_s=1.0, max_delay_s=100.0, jitter=0.0)
        rand = _fixed_rand(0.5)
        # attempt=1 → base*1 = 1; attempt=2 → 2; attempt=3 → 4; attempt=4 → 8.
        assert _compute_backoff_delay(attempt=1, policy=policy, rand=rand) == pytest.approx(1.0)
        assert _compute_backoff_delay(attempt=2, policy=policy, rand=rand) == pytest.approx(2.0)
        assert _compute_backoff_delay(attempt=3, policy=policy, rand=rand) == pytest.approx(4.0)
        assert _compute_backoff_delay(attempt=4, policy=policy, rand=rand) == pytest.approx(8.0)

    def test_capped_at_max_delay(self) -> None:
        policy = RetryPolicy(max_attempts=10, base_delay_s=1.0, max_delay_s=5.0, jitter=0.0)
        rand = _fixed_rand(0.5)
        # attempt=3 → raw=4, OK; attempt=4 → raw=8, CAPPED at 5.
        assert _compute_backoff_delay(attempt=3, policy=policy, rand=rand) == pytest.approx(4.0)
        assert _compute_backoff_delay(attempt=4, policy=policy, rand=rand) == pytest.approx(5.0)
        assert _compute_backoff_delay(attempt=10, policy=policy, rand=rand) == pytest.approx(5.0)

    def test_jitter_within_range(self) -> None:
        """Many samples from the jitter formula must fall within
        ``[base * (1 - jitter), base * (1 + jitter)]``."""
        import random

        policy = RetryPolicy(max_attempts=3, base_delay_s=2.0, max_delay_s=100.0, jitter=0.25)
        rng = random.Random(12345)
        rand = rng.random

        observed: list[float] = []
        for _ in range(200):
            observed.append(_compute_backoff_delay(attempt=1, policy=policy, rand=rand))

        # base=2.0, jitter=0.25 → expected range [1.5, 2.5].
        assert min(observed) >= 1.5 - 1e-9
        assert max(observed) <= 2.5 + 1e-9
        # Sanity: with 200 samples we should see real variation, not
        # all-equal values.
        assert len(set(observed)) > 10

    def test_zero_jitter_is_deterministic(self) -> None:
        policy = RetryPolicy(max_attempts=3, base_delay_s=2.0, max_delay_s=100.0, jitter=0.0)
        # Rand return shouldn't matter when jitter=0.
        for rv in (0.0, 0.3, 0.999):
            assert _compute_backoff_delay(
                attempt=1, policy=policy, rand=_fixed_rand(rv)
            ) == pytest.approx(2.0)


# ---------------------------------------------------------------------------
# on_attempt_failed callback
# ---------------------------------------------------------------------------


class TestOnAttemptFailedCallback:
    @pytest.mark.asyncio
    async def test_called_per_failed_attempt(self) -> None:
        sleeper = _SleepRecorder()
        seen: list[tuple[int, str]] = []

        async def _hook(attempt: int, exc: BaseException) -> None:
            seen.append((attempt, type(exc).__name__))

        e1 = _FakeRateLimitError("first")
        e2 = _FakeInternalServerError("second")
        fn = AsyncMock(side_effect=[e1, e2, "ok"])

        await run_with_retry(
            fn,
            policy=NO_JITTER_POLICY,
            is_retriable=is_retriable_openai,
            on_attempt_failed=_hook,
            _sleep=sleeper,
            _random=_fixed_rand(0.5),
        )

        # Hook fires AFTER each retriable failure (attempts 1 and 2),
        # but not after the successful attempt 3.
        assert seen == [(1, "RateLimitError"), (2, "InternalServerError")]

    @pytest.mark.asyncio
    async def test_not_called_for_non_retriable(self) -> None:
        sleeper = _SleepRecorder()
        seen: list[Any] = []

        async def _hook(attempt: int, exc: BaseException) -> None:
            seen.append((attempt, type(exc).__name__))

        fn = AsyncMock(side_effect=_FakeBadRequestError("400"))
        with pytest.raises(_FakeBadRequestError):
            await run_with_retry(
                fn,
                policy=NO_JITTER_POLICY,
                is_retriable=is_retriable_openai,
                on_attempt_failed=_hook,
                _sleep=sleeper,
                _random=_fixed_rand(0.5),
            )

        # Non-retriable → hook does NOT fire (the audit row would be
        # misleading; the error is a business failure, not a transient).
        assert seen == []

    @pytest.mark.asyncio
    async def test_hook_failure_does_not_break_retry(self) -> None:
        sleeper = _SleepRecorder()

        async def _broken_hook(attempt: int, exc: BaseException) -> None:
            raise RuntimeError("hook is broken")

        fn = AsyncMock(side_effect=[_FakeRateLimitError("transient"), "ok"])
        # Despite the hook raising, retry must succeed on attempt 2.
        result = await run_with_retry(
            fn,
            policy=NO_JITTER_POLICY,
            is_retriable=is_retriable_openai,
            on_attempt_failed=_broken_hook,
            _sleep=sleeper,
            _random=_fixed_rand(0.5),
        )
        assert result == "ok"


# ---------------------------------------------------------------------------
# Predicate coverage — OpenAI
# ---------------------------------------------------------------------------


class TestIsRetriableOpenAI:
    @pytest.mark.parametrize(
        "exc_factory",
        [
            lambda: _FakeRateLimitError("429"),
            lambda: _FakeAPIConnectionError("connection lost"),
            lambda: _FakeAPITimeoutError("timeout"),
            lambda: _FakeInternalServerError("500"),
        ],
    )
    def test_true_for_retriable_classes(self, exc_factory) -> None:
        assert is_retriable_openai(exc_factory()) is True

    @pytest.mark.parametrize(
        "exc_factory",
        [
            lambda: _FakeAuthenticationError("401"),
            lambda: _FakeBadRequestError("400"),
            lambda: _FakePermissionDeniedError("403"),
            lambda: _FakeNotFoundError("404"),
            lambda: ValueError("random"),
            lambda: RuntimeError("not an SDK error"),
        ],
    )
    def test_false_for_non_retriable(self, exc_factory) -> None:
        assert is_retriable_openai(exc_factory()) is False

    @pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
    def test_status_code_fallback_retriable(self, status: int) -> None:
        # APIStatusError-shaped exception — class name doesn't match
        # the retriable set, but status_code attribute does.
        assert is_retriable_openai(_FakeStatusError(status)) is True

    @pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
    def test_status_code_fallback_non_retriable(self, status: int) -> None:
        assert is_retriable_openai(_FakeStatusError(status)) is False


# ---------------------------------------------------------------------------
# Predicate coverage — Anthropic
# ---------------------------------------------------------------------------


class TestIsRetriableAnthropic:
    @pytest.mark.parametrize(
        "exc_factory",
        [
            lambda: _FakeRateLimitError("429"),
            lambda: _FakeAPIConnectionError("connection lost"),
            lambda: _FakeAPITimeoutError("timeout"),
            lambda: _FakeInternalServerError("500"),
        ],
    )
    def test_true_for_retriable_classes(self, exc_factory) -> None:
        assert is_retriable_anthropic(exc_factory()) is True

    @pytest.mark.parametrize(
        "exc_factory",
        [
            lambda: _FakeAuthenticationError("401"),
            lambda: _FakeBadRequestError("400"),
            lambda: _FakePermissionDeniedError("403"),
            lambda: _FakeNotFoundError("404"),
            lambda: ValueError("random"),
        ],
    )
    def test_false_for_non_retriable(self, exc_factory) -> None:
        assert is_retriable_anthropic(exc_factory()) is False

    @pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
    def test_status_code_fallback_retriable(self, status: int) -> None:
        assert is_retriable_anthropic(_FakeStatusError(status)) is True


# ---------------------------------------------------------------------------
# Default policy + settings integration
# ---------------------------------------------------------------------------


class TestRetryPolicyDefaults:
    def test_default_policy_matches_acceptance_criteria(self) -> None:
        # Per DRF-858 brief: 3 attempts, 1s base, 30s cap, ±25% jitter.
        assert DEFAULT_POLICY.max_attempts == 3
        assert DEFAULT_POLICY.base_delay_s == 1.0
        assert DEFAULT_POLICY.max_delay_s == 30.0
        assert DEFAULT_POLICY.jitter == 0.25

    def test_policy_from_settings(self) -> None:
        from apps.llm.retry import policy_from_settings

        policy = policy_from_settings()
        # The defaults configured in config/settings/base.py match the
        # acceptance criteria. Test settings inherit from base.
        assert policy.max_attempts == 3
        assert policy.base_delay_s == 1.0
        assert policy.max_delay_s == 30.0
