"""douyin_batch.retry 的测试 —— 迁入自 tests/test_coverage_gaps.py 与 tests/test_coverage_gaps_2.py(按被测模块归位)。"""

from __future__ import annotations

from unittest import mock

import pytest

###########################################################################
# 迁入自 tests/test_coverage_gaps.py —— TestRetryExtras(douyin_batch.retry)
###########################################################################


class TestRetryExtras:
    """Cover backoff / on_retry / max_retries=0 paths."""

    def test_retry_max_retries_zero_raises_first_exception(self):
        from douyin_batch.retry import retry

        calls = {"n": 0}

        def boom():
            calls["n"] += 1
            raise ValueError("first")

        with pytest.raises(ValueError):
            retry(boom, max_retries=0, delay=0)
        assert calls["n"] == 1

    def test_retry_backoff_increases_delay(self):
        from douyin_batch.retry import retry

        # Patch time.sleep to record the delays without actually sleeping.
        delays: list[float] = []
        with mock.patch("douyin_batch.retry.time.sleep", side_effect=lambda d: delays.append(d)):

            def boom():
                raise ValueError()

            with pytest.raises(ValueError):
                retry(boom, max_retries=3, delay=0.1, backoff=2.0)

        # 4 attempts fail (0..3); sleeps fire after attempts 0, 1, 2 with
        # growing delays (0.1 → 0.2 → 0.4); the final break skips the
        # sleep so we get 3 entries, not 4.
        assert delays == [0.1, 0.2, 0.4]

    def test_retry_on_retry_callback_receives_attempt_and_exc(self):
        from douyin_batch.retry import retry

        attempts: list[tuple[int, Exception]] = []
        calls = {"n": 0}

        def boom():
            calls["n"] += 1
            raise RuntimeError(calls["n"])

        def on_retry(attempt, exc):
            attempts.append((attempt, exc))

        with pytest.raises(RuntimeError) as excinfo:
            retry(boom, max_retries=2, delay=0, exceptions=(RuntimeError,), on_retry=on_retry)
        # on_retry is called *before* the sleep on attempts 0 and 1, so
        # we get two callbacks with attempt numbers 1 and 2 and exc args
        # 1 and 2.  The third attempt breaks out without calling on_retry
        # and re-raises RuntimeError(3).
        assert [a for a, _ in attempts] == [1, 2]
        assert attempts[-1][1].args == (2,)
        assert excinfo.value.args == (3,)

    def test_retry_does_not_retry_on_unrelated_exception(self):
        from douyin_batch.retry import retry

        calls = {"n": 0}

        def boom():
            calls["n"] += 1
            raise KeyError("nope")

        with pytest.raises(KeyError):
            retry(boom, max_retries=3, delay=0, exceptions=(ValueError,))
        assert calls["n"] == 1


###########################################################################
# 迁入自 tests/test_coverage_gaps_2.py —— TestRetryBackoffStrategy(douyin_batch.retry)
###########################################################################


class TestRetryBackoffStrategy:
    def test_retry_does_not_call_func_with_kwarg(self):
        """Make sure retry forwards *args / **kwargs correctly and does
        not pass any internal config to the user function (the bug
        used to leak ``jitter=`` to the wrapped callable)."""
        from douyin_batch.retry import retry

        seen: list = []

        def func(*args, **kwargs):
            seen.append((args, kwargs))
            raise ValueError("fail")

        with pytest.raises(ValueError):
            retry(func, max_retries=1, delay=0, backoff=2.0)
        # Each call received no extra kwargs.
        for _, kw in seen:
            assert "jitter" not in kw
            assert "delay" not in kw
            assert "backoff" not in kw
