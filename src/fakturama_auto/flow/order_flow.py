"""The full image-to-cash flow: master data, Order, Invoice, payment.

Order of operations matters and is deliberate: every piece of master data a
later step depends on (the payment method, each VAT rate, each product) is
created *before* the editor that consumes it is opened. That specifically
avoids the stale-combo-box bug in docs/challenges.md - a combo already open
when its backing list changes elsewhere keeps showing the old options until
its editor is closed and reopened, so the fix is to never let that ordering
happen in the first place.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from decimal import Decimal

from ..app.contact_editor import ContactEditor, open_new_debtor
from ..app.order_editor import OrderEditor, open_new_order
from ..app.payment_term_editor import create_payment_method
from ..app.product_editor import create_product
from ..app.shipping_editor import create_default_shipping_method
from ..app.vat_editor import create_vat_rate
from ..errors import ManualReviewRequired
from ..models import OrderDoc, Party
from ..uia.session import FakturamaSession


@dataclass(frozen=True)
class FlowResult:
    order_number: str
    invoice_number: str
    net_total: str
    vat_total: str
    gross_total: str
    invoice_paid: bool


def run_order_flow(session: FakturamaSession, doc: OrderDoc) -> FlowResult:
    """Drive the whole brief end to end against an already-open Fakturama."""
    window = session.focus()
    # The Items grid's Discount column sits far enough right that a
    # non-maximised window scrolls it out of view - see OrderEditor's
    # cell-editing notes for why that matters. Confirmed live: this call can
    # silently no-op on a just-launched Fakturama (same cold-start quirk
    # documented on click_and_await_pane) - it raises nothing, but
    # is_maximized() comes back False - so it is verified and retried a
    # few times rather than trusted on the first call.
    for _ in range(5):
        if window.is_maximized():
            break
        window.maximize()
        time.sleep(1.0)

    # A brand-new/wiped workspace has no Shipping record at all, and opening
    # a New Order hard-fails with a modal Error dialog ("No default value
    # found for Shippings") until one is marked standard - not something to
    # dismiss and work around, since it blocks the Order editor from
    # opening at all. Must exist before the first open_new_order() call.
    create_default_shipping_method(session)

    # Created before the Debtor editor ever opens: a fresh editor enumerates
    # its Payment combo from whatever exists at that moment, so there is no
    # staleness to work around as long as the term already exists first.
    create_payment_method(session, doc.payment.method)
    for vat_percent in doc.distinct_vat_rates:
        create_vat_rate(session, _vat_rate_name(vat_percent), f"{_decimal_text(vat_percent)}%")
    for item in doc.items:
        create_product(
            session,
            sku=item.sku,
            name=item.description,
            gross_price=str(item.product_master_gross_price),
            vat_name=item.vat_rate_name,
        )

    create_debtor(session, doc.customer, payment_method=doc.payment.method)

    order_editor = build_order(session, doc)
    order_editor.save()
    order_number = order_editor.order_number()
    # Read while the Order tab is still the active one - its content pane
    # is torn from the UIA tree the moment another tab (the Invoice, next)
    # takes focus, the same tab-teardown behaviour documented elsewhere in
    # this project for every multi-tab workflow.
    totals = order_editor.read_totals()

    invoice_editor = order_editor.create_followup_invoice()
    if doc.payment.is_paid:
        invoice_editor.mark_paid(doc.payment.payment_date or doc.order_date)
    invoice_editor.save()
    invoice_number = _read_invoice_number(session)

    return FlowResult(
        order_number=order_number,
        invoice_number=invoice_number,
        net_total=totals["net_or_gross"],
        vat_total=totals["vat"],
        gross_total=totals["total"],
        invoice_paid=invoice_editor.is_paid(),
    )


def create_debtor(session: FakturamaSession, customer: Party, *, payment_method: str) -> ContactEditor:
    """Steps 2.5-2.10: a new Debtor with both addresses, alias, and payment method."""
    editor = open_new_debtor(session)

    if customer.company:
        editor.set_company(customer.company)
    editor.set_name(customer.first_name, customer.last_name)

    editor.open_main_address_tab()
    editor.open_address_subtab("Main address")
    editor.set_main_address(
        street=customer.billing.street,
        zip_code=customer.billing.zip_code,
        city=customer.billing.city,
        country=customer.billing.country,
        email=customer.email,
        phone=customer.phone,
    )
    editor.set_address_role(invoice=True, delivery=customer.delivery_matches_billing)

    if not customer.delivery_matches_billing and customer.delivery is not None:
        editor.add_address_tab()
        editor.open_address_subtab("additional address #1")
        editor.set_main_address(
            street=customer.delivery.street,
            zip_code=customer.delivery.zip_code,
            city=customer.delivery.city,
            country=customer.delivery.country,
            email=None,
            phone=None,
        )
        editor.set_address_role(invoice=False, delivery=True)

    editor.open_miscellaneous_tab()
    if customer.alias:
        editor.set_alias(customer.alias)
    editor.set_discount_zero()
    editor.set_net_or_gross("Net")

    if not editor.has_payment_method(payment_method):
        raise ManualReviewRequired(
            "payment method not available on the Debtor even after creating it standalone",
            method=payment_method,
        )
    editor.set_payment_method(payment_method)

    editor.save()
    return editor


def build_order(session: FakturamaSession, doc: OrderDoc) -> OrderEditor:
    """Steps 1.3-3.x: header, the saved Debtor's address, and every line item."""
    editor = open_new_order(session)
    editor.set_date(doc.order_date)
    editor.set_cust_ref(doc.external_reference)
    editor.set_price_mode("Net")
    editor.set_vat_mode("With VAT")

    # Confirmed live: the address selector's search matches against the
    # visible columns (No./First Name/Name/Company/ZIP/City) but not the
    # Alias field - "NORTHSTAR-BERLIN" (the alias) matched nothing, while
    # "Northstar" (the company) did. Company/last name first, alias last.
    search_text = doc.customer.company or doc.customer.last_name or doc.customer.alias or ""
    editor.select_address(search_text)

    for index, item in enumerate(doc.items):
        editor.add_item(item.sku)
        editor.set_item_quantity(index, _decimal_text(item.quantity))
        if item.discount_percent:
            editor.set_item_discount_percent(index, _decimal_text(item.discount_percent))

    return editor


def _read_invoice_number(session: FakturamaSession) -> str:
    window = session.focus()
    for tab in window.descendants(control_type="TabItem"):
        name = tab.element_info.name or ""
        if name.lstrip("*").startswith("INV"):
            return name.lstrip("*")
    return ""


def _vat_rate_name(percent: Decimal) -> str:
    return f"VAT {_decimal_text(percent)}%"


def _decimal_text(value: Decimal) -> str:
    """19 -> '19', 7.5 -> '7.5' - matches LineItem.vat_rate_name's own formatting."""
    return format(value.normalize(), "f")
