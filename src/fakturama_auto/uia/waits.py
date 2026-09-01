"""Waiting primitives.

Two different kinds of waiting show up in this flow and conflating them is a
classic source of flakiness:

*Wait for an event* - "the dialog is open", "the editor tab exists".
    :func:`wait_until`.

*Wait for a process to settle* - "the search results have finished filtering".
    :func:`wait_for_stable`. There is no event to latch onto here; the list
    simply stops changing. The brief asks for exactly this in step 2.2 ("wait
    for the list to stabilize"), and polling for a non-empty list instead is
    the bug that makes such automations pick the wrong row.

Neither uses a bare ``sleep``. Fixed sleeps are both slower than they need to
be on a fast machine and too short on a slow one.
"""

from __future__ import annotations

import time
from typing import Any, Callable, TypeVar

from ..errors import AutomationError

T = TypeVar("T")

DEFAULT_TIMEOUT = 15.0
DEFAULT_INTERVAL = 0.25


class WaitTimeout(AutomationError):
    """A condition did not become true within its timeout."""


def wait_until(
    predicate: Callable[[], T],
    *,
    timeout: float = DEFAULT_TIMEOUT,
    interval: float = DEFAULT_INTERVAL,
    description: str = "condition",
) -> T:
    """Poll ``predicate`` until it returns something truthy.

    Returns that value, so a predicate can double as a lookup:

        row = wait_until(lambda: find_row(sku), description=f"row for {sku}")
    """
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None

    while True:
        try:
            value = predicate()
            if value:
                return value
        except Exception as exc:  # noqa: BLE001 - a mutating tree throws COM errors
            last_error = exc

        if time.monotonic() >= deadline:
            suffix = f" (last error: {last_error})" if last_error else ""
            raise WaitTimeout(f"timed out after {timeout:.0f}s waiting for {description}{suffix}")
        time.sleep(interval)


def wait_for_stable(
    sample: Callable[[], Any],
    *,
    timeout: float = DEFAULT_TIMEOUT,
    interval: float = DEFAULT_INTERVAL,
    consecutive: int = 3,
    description: str = "value",
) -> Any:
    """Wait until ``sample()`` returns the same value ``consecutive`` times running.

    Used after typing into a search box: the result list repaints several
    times as the query filters, and acting on the first non-empty state picks
    a row that is about to be replaced.

    Returns the settled value. Note this deliberately *succeeds* on a stable
    empty result - "no matches" is a real answer, and the caller decides what
    it means.
    """
    deadline = time.monotonic() + timeout
    previous: Any = object()  # sentinel that equals nothing
    streak = 0

    while True:
        try:
            current = sample()
        except Exception:  # noqa: BLE001 - treat a mid-repaint read as unsettled
            current = object()

        if current == previous:
            streak += 1
            if streak >= consecutive:
                return current
        else:
            streak = 1
            previous = current

        if time.monotonic() >= deadline:
            raise WaitTimeout(
                f"{description} never settled: still changing after {timeout:.0f}s"
            )
        time.sleep(interval)


def retry(
    action: Callable[[], T],
    *,
    attempts: int = 3,
    delay: float = 0.5,
    retry_on: tuple[type[BaseException], ...] = (Exception,),
    description: str = "action",
) -> T:
    """Run ``action``, retrying transient failures with a linear backoff.

    Deliberately linear, not exponential: these are sub-second UI hiccups
    (a control being re-created, a tree mutating mid-walk), not a loaded
    server that needs backing off.
    """
    last_error: BaseException | None = None
    for attempt in range(1, attempts + 1):
        try:
            return action()
        except retry_on as exc:
            last_error = exc
            if attempt < attempts:
                time.sleep(delay * attempt)
    raise AutomationError(f"{description} failed after {attempts} attempts: {last_error}")
