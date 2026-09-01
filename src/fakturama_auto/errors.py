"""Exception hierarchy.

The important one is :class:`ManualReviewRequired`. The brief repeatedly says
"stop for manual review" when a result is ambiguous or conflicting. Making
that a *typed exception* rather than a log line means every such gate is
greppable, testable, and impossible to fall through by accident.
"""

from __future__ import annotations

from typing import Any


class AutomationError(Exception):
    """Base class for everything this package raises deliberately."""


class ExtractionError(AutomationError):
    """The order image could not be turned into a usable OrderDoc."""


class ManualReviewRequired(AutomationError):
    """A human has to look at this before automation may continue.

    Raised at every decision point the brief marks as ambiguous: conflicting
    search hits, a VAT record whose settings disagree with the source, a
    product that fails to reappear after saving, and so on.
    """

    def __init__(self, reason: str, **context: Any) -> None:
        self.reason = reason
        self.context = context
        detail = ", ".join(f"{k}={v!r}" for k, v in context.items())
        super().__init__(f"{reason}{' (' + detail + ')' if detail else ''}")


class ControlNotFound(AutomationError):
    """A UI element could not be grounded within the timeout."""


class VerificationFailed(AutomationError):
    """A saved record did not read back with the expected values."""
