"""Tests for the extraction layer: normalisation, arithmetic, reconciliation.

The numbers below mirror the sample order (2 x 250.00 less 10%, 3 x 40.00,
19% VAT throughout -> net 570.00, VAT 108.30, gross 678.30). SKUs and street
lines are stand-ins; only the maths is load-bearing here.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from fakturama_auto.models import (
    NormalisationError,
    RawAddress,
    RawLineItem,
    RawOrderExtraction,
    RawParty,
    RawPayment,
    RawTotals,
    normalise,
)
from fakturama_auto.extract.validate import validate


def build_raw(**overrides) -> RawOrderExtraction:
    payload = {
        "order_date": "2026-07-14",
        "external_reference": "WEB-2026-0714-A17",
        "currency": "EUR",
        "customer": RawParty(
            company="Northstar Office GmbH",
            first_name="Maria",
            last_name="Klein",
            alias="NORTHSTAR-BERLIN",
            customer_id="CUST-1007",
            email="maria.klein@example.test",
            phone="+49 30 5550 1420",
            billing=RawAddress(
                company="Northstar Office GmbH",
                street="Beispielstrasse 42",
                zip_code="10117",
                city="Berlin",
                country="Germany",
            ),
            delivery=RawAddress(
                company="Northstar Office Warehouse",
                street="Beispielstrasse 42",
                zip_code="10117",
                city="Berlin",
                country="Germany",
            ),
        ),
        "payment": RawPayment(method="Bank Transfer", status="PAID", payment_date="2026-07-18"),
        "items": [
            RawLineItem(
                position=1,
                sku="CHR-ERG-01",
                description="Ergonomic Office Chair",
                quantity="2",
                unit="PCS",
                unit_net_price="250.00",
                discount_percent="10",
                vat_percent="19",
                line_total_net="450.00",
            ),
            RawLineItem(
                position=2,
                sku="MAT-DESK-32",
                description="Anti-Fatigue Desk Mat",
                quantity="3",
                unit="PCS",
                unit_net_price="40.00",
                discount_percent="0",
                vat_percent="19",
                line_total_net="120.00",
            ),
        ],
        "totals": RawTotals(net_total="570.00", vat_total="108.30", gross_total="678.30"),
        "low_confidence_fields": [],
    }
    payload.update(overrides)
    return RawOrderExtraction(**payload)


# --------------------------------------------------------------------------
# Reconciliation
# --------------------------------------------------------------------------


def test_sample_order_reconciles_cleanly():
    report = validate(normalise(build_raw()))
    assert report.ok, report.render()
    assert report.warnings == []


def test_line_total_that_disagrees_with_its_own_maths_is_an_error():
    raw = build_raw()
    raw.items[0].line_total_net = "500.00"  # forgot the 10% discount
    report = validate(normalise(raw))
    assert not report.ok
    assert any("line 1" in message for message in report.errors)


def test_document_total_that_disagrees_with_the_lines_is_an_error():
    raw = build_raw()
    raw.totals.net_total = "580.00"  # transposed digit
    report = validate(normalise(raw))
    assert not report.ok
    assert any("net total" in message for message in report.errors)


def test_printed_totals_that_disagree_with_each_other_are_an_error():
    raw = build_raw()
    raw.totals.gross_total = "700.00"
    report = validate(normalise(raw))
    assert not report.ok
    assert any("printed totals disagree" in message for message in report.errors)


def test_a_dropped_line_is_caught_by_the_totals():
    raw = build_raw()
    raw.items.pop()  # vision model missed the second row
    report = validate(normalise(raw))
    assert not report.ok


def test_one_cent_of_rounding_is_tolerated():
    raw = build_raw()
    raw.totals.vat_total = "108.29"
    raw.totals.gross_total = "678.29"
    assert validate(normalise(raw)).ok


def test_low_confidence_fields_warn_but_do_not_block():
    report = validate(normalise(build_raw(low_confidence_fields=["items[0].sku"])))
    assert report.ok
    assert any("hard to read" in message for message in report.warnings)


def test_paid_without_a_payment_date_warns():
    raw = build_raw()
    raw.payment.payment_date = None
    report = validate(normalise(raw))
    assert report.ok
    assert any("no payment date" in message for message in report.warnings)


# --------------------------------------------------------------------------
# Normalisation
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("250.00", Decimal("250.00")),
        ("EUR 250.00", Decimal("250.00")),
        ("19%", Decimal("19")),
        ("1.234,56", Decimal("1234.56")),  # German
        ("1,234.56", Decimal("1234.56")),  # English
        ("0", Decimal("0")),
    ],
)
def test_money_parsing_tolerates_ocr_punctuation(text, expected):
    raw = build_raw()
    raw.items[0].unit_net_price = text
    assert normalise(raw).items[0].unit_net_price == expected


def test_unparseable_number_raises_rather_than_defaulting():
    raw = build_raw()
    raw.items[0].unit_net_price = "two hundred"
    with pytest.raises(NormalisationError, match="unit_net_price"):
        normalise(raw)


def test_bad_date_raises():
    with pytest.raises(NormalisationError, match="order_date"):
        normalise(build_raw(order_date="14/07/2026"))


# --------------------------------------------------------------------------
# Derived values the UI automation depends on
# --------------------------------------------------------------------------


def test_product_master_price_is_gross_and_ignores_the_line_discount():
    """Step 3.9: unit net x (1 + VAT/100), discount deliberately excluded."""
    doc = normalise(build_raw())
    assert doc.items[0].product_master_gross_price == Decimal("297.50")
    assert doc.items[1].product_master_gross_price == Decimal("47.60")


def test_vat_rate_names_match_fakturama_convention():
    doc = normalise(build_raw())
    assert doc.items[0].vat_rate_name == "VAT 19%"
    assert doc.distinct_vat_rates == [Decimal("19")]


def test_delivery_address_at_the_same_place_is_recognised_despite_a_different_company_line():
    """Step 2.8 hinges on this: same place -> one address with both roles."""
    doc = normalise(build_raw())
    assert doc.customer.delivery_matches_billing


def test_a_genuinely_different_delivery_address_is_not_collapsed():
    raw = build_raw()
    raw.customer.delivery.city = "Hamburg"
    raw.customer.delivery.zip_code = "20095"
    assert not normalise(raw).customer.delivery_matches_billing
