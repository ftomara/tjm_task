"""Arithmetic reconciliation of an extracted order.

The source document prints both the line detail *and* the totals, which makes
the extraction self-checking: if the numbers a vision model read back do not
reconcile, at least one of them is wrong and we must not start typing them
into an accounting system.

This is the cheapest reliability win available in the whole pipeline - it
catches transposed digits, dropped rows and misread discounts before any UI
is touched.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from ..models import OrderDoc

#: Per-check tolerance. One cent absorbs legitimate half-up rounding
#: differences without letting a real misread through.
TOLERANCE = Decimal("0.01")


@dataclass
class ValidationReport:
    """Outcome of reconciling an :class:`OrderDoc` against its own totals."""

    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def render(self) -> str:
        lines = []
        for message in self.errors:
            lines.append(f"  ERROR   {message}")
        for message in self.warnings:
            lines.append(f"  WARNING {message}")
        return "\n".join(lines) or "  all checks passed"


def validate(doc: OrderDoc) -> ValidationReport:
    """Reconcile line maths and document totals.

    Errors block automation. Warnings are surfaced to the operator but do not
    stop the run.
    """
    report = ValidationReport()

    if not doc.items:
        report.errors.append("no line items were extracted")
        return report

    # --- per line: qty x unit x (1 - disc) must equal the printed line total
    for item in doc.items:
        delta = abs(item.line_total_net - item.computed_line_total_net)
        if delta > TOLERANCE:
            report.errors.append(
                f"line {item.position} ({item.sku}): printed total "
                f"{item.line_total_net} but {item.quantity} x {item.unit_net_price} "
                f"less {item.discount_percent}% = {item.computed_line_total_net}"
            )
        if item.quantity <= 0:
            report.errors.append(f"line {item.position} ({item.sku}): quantity is {item.quantity}")
        if item.unit_net_price < 0:
            report.errors.append(f"line {item.position} ({item.sku}): negative unit price")
        if not (Decimal(0) <= item.discount_percent <= Decimal(100)):
            report.errors.append(
                f"line {item.position} ({item.sku}): discount {item.discount_percent}% out of range"
            )
        if not (Decimal(0) <= item.vat_percent <= Decimal(100)):
            report.errors.append(
                f"line {item.position} ({item.sku}): VAT {item.vat_percent}% out of range"
            )

    # --- document totals
    _compare(report, "net total", doc.totals.net_total, doc.computed_net_total)
    _compare(report, "VAT total", doc.totals.vat_total, doc.computed_vat_total)
    _compare(report, "gross total", doc.totals.gross_total, doc.computed_gross_total)

    # --- internal consistency of the printed totals themselves
    printed_sum = doc.totals.net_total + doc.totals.vat_total
    if abs(doc.totals.gross_total - printed_sum) > TOLERANCE:
        report.errors.append(
            f"printed totals disagree: net {doc.totals.net_total} + VAT "
            f"{doc.totals.vat_total} = {printed_sum}, but gross reads "
            f"{doc.totals.gross_total}"
        )

    # --- non-blocking signals
    if doc.low_confidence_fields:
        report.warnings.append(
            "model flagged as hard to read: " + ", ".join(doc.low_confidence_fields)
        )
    if doc.payment.is_paid and doc.payment.payment_date is None:
        report.warnings.append("status is PAID but no payment date was extracted")
    if not doc.payment.is_paid and doc.payment.payment_date is not None:
        report.warnings.append("payment date present although status is not PAID")

    duplicates = _duplicate_skus(doc)
    if duplicates:
        report.warnings.append(f"repeated SKUs across lines: {', '.join(duplicates)}")

    return report


def _compare(report: ValidationReport, label: str, printed: Decimal, computed: Decimal) -> None:
    if abs(printed - computed) > TOLERANCE:
        report.errors.append(
            f"{label}: document says {printed}, line items compute to {computed}"
        )


def _duplicate_skus(doc: OrderDoc) -> list[str]:
    seen: set[str] = set()
    repeated: list[str] = []
    for item in doc.items:
        key = item.sku.casefold()
        if key in seen and item.sku not in repeated:
            repeated.append(item.sku)
        seen.add(key)
    return repeated
