"""The full image-to-cash flow: Order-first, master data created lazily.

Matches docs/design.md's intended shape: the Order opens first and stays
open for the run; the Debtor and every Product are resolved through the
Order's own selectors, and a piece of master data is only ever created on a
genuine miss - never speculatively upfront. The default Shipping method is
the one exception handled differently: opening a New Order on a wiped
workspace shows a modal Error dialog rather than failing to open at all
(see ``OrderEditor.open_new_order()``), so it isn't created until the
Order's own Shipping field is actually being filled in, later in this
function, rather than up front.

The risk this design has to manage, documented at length in
docs/challenges.md, is the stale-combo-box bug: a combo already rendered in
an already-open editor does not see data created elsewhere while it stays
open. Every lazy branch below is written to avoid ever hitting that:

- The address selector and product selector are dialogs opened fresh each
  time (open_address_selector()/open_product_selector()), so retrying them
  after creating the missing Debtor/Product elsewhere queries live, current
  data - no staleness.
- A Product's own VAT rate is the one dependency handled the brief's way
  rather than lazily from inside the Product editor: checked against the
  VATs list and created there first, before ``create_product()`` is ever
  called, so its VAT combo simply never renders before the rate exists -
  nothing to refresh in place.
- The Debtor's own Payment combo and the Order's own Shipping combo don't
  have that luxury - both live inside an editor that has already been open
  for a while by the time the missing record is discovered. Both are
  handled the same confirmed-live way: a bare Ctrl+S sent to that same
  still-open editor, after the record now exists, refreshes the combo in
  place and saves cleanly, with no need to abandon the editor or fill a
  second one from scratch. See ``create_debtor()`` for the full story,
  including why this doesn't contradict the earlier finding that saving a
  Debtor with literally no payment term *anywhere in the workspace* fails
  outright.
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Decimal
from typing import Iterator

from ..app.contact_editor import ContactEditor, open_new_debtor
from ..app.documents_view import open_documents
from ..app.order_editor import OrderEditor, open_new_order
from ..app.payment_term_editor import create_payment_method
from ..app.product_editor import create_product
from ..app.shipping_editor import create_default_shipping_method
from ..app.vat_editor import create_vat_rate
from ..errors import ManualReviewRequired
from ..models import OrderDoc, Party
from ..runlog import RunLog
from ..uia.session import FakturamaSession


class _NoopStep:
    """Stands in for ``runlog.step()``'s yielded ``Step`` when no RunLog is attached."""

    def note(self, *args: object, **kwargs: object) -> None:
        pass

    def capture(self, *args: object, **kwargs: object) -> None:
        pass


@contextmanager
def _step(runlog: RunLog | None, title: str, **context: object) -> Iterator[object]:
    """``runlog.step(title)`` when a RunLog is attached, otherwise a no-op.

    Every meaningful phase of the flow is wrapped in one of these - each
    capture a before/failure screenshot and a run.jsonl record as a
    by-product of actually running, rather than screenshots taken by hand
    after the fact that can drift from what the code did (see runlog.py).
    ``runlog`` stays optional so the flow is still directly callable
    without one, e.g. from a test.
    """
    if runlog is None:
        yield _NoopStep()
        return
    with runlog.step(title, **context) as step:
        yield step


@dataclass(frozen=True)
class FlowResult:
    order_number: str
    invoice_number: str
    net_total: str
    vat_total: str
    gross_total: str
    invoice_paid: bool


def run_order_flow(
    session: FakturamaSession, doc: OrderDoc, runlog: RunLog | None = None
) -> FlowResult:
    """Drive the whole brief end to end against an already-open Fakturama.

    ``runlog``, when passed, turns every phase below into a recorded step -
    a before-screenshot, a failure-screenshot if that phase raises, and a
    line in ``run.jsonl`` - entirely as a by-product of the run itself. See
    ``runlog.py`` and this module's own docstring for why that matters more
    than screenshots taken by hand afterward.
    """
    if runlog is not None:
        runlog.bind(session)

    with _step(runlog, "Open the Order and set its header"):
        window = session.focus()

        # The Items grid's Discount column sits far enough right that a
        # non-maximised window scrolls it out of view - see OrderEditor's
        # cell-editing notes for why that matters. Confirmed live: this call
        # can silently no-op on a just-launched Fakturama (same cold-start
        # quirk documented on click_and_await_pane) - it raises nothing, but
        # is_maximized() comes back False - so it is verified and retried a
        # few times rather than trusted on the first call.
        for _ in range(5):
            if window.is_maximized():
                break
            window.maximize()
            time.sleep(1.0)

        order_editor = open_new_order(session)
        order_editor.set_date(doc.order_date)
        order_editor.set_cust_ref(doc.external_reference)
        order_editor.set_price_mode("Net")
        order_editor.set_vat_mode("With VAT")

    with _step(runlog, "Resolve the Debtor", customer=doc.customer.company):
        order_editor = _resolve_debtor(session, order_editor, doc.customer, doc.payment.method)

    created_vat_rates: set[str] = set()
    for index, item in enumerate(doc.items):
        with _step(runlog, f"Add item {item.sku}", sku=item.sku) as step:
            if not order_editor.add_item(item.sku):
                # Per the brief: check the VAT list and create the rate
                # there first if it's missing, before ever creating the
                # Product that references it - never inside an already-open
                # Product editor, which would leave that editor's own VAT
                # combo stale.
                if item.vat_rate_name not in created_vat_rates:
                    create_vat_rate(
                        session, item.vat_rate_name, f"{_decimal_text(item.vat_percent)}%"
                    )
                    created_vat_rates.add(item.vat_rate_name)
                    step.note(f"created VAT rate {item.vat_rate_name}")
                create_product(
                    session,
                    sku=item.sku,
                    name=item.description,
                    gross_price=str(item.product_master_gross_price),
                    vat_name=item.vat_rate_name,
                )
                step.note(f"created product {item.sku}")
                order_editor = OrderEditor(session, session.activate_tab("New Order"))
                if not order_editor.add_item(item.sku):
                    raise ManualReviewRequired(
                        "Product was created but still not found in the product selector",
                        sku=item.sku,
                    )

            order_editor.set_item_quantity(index, _decimal_text(item.quantity))
            if item.discount_percent:
                order_editor.set_item_discount_percent(
                    index, _decimal_text(item.discount_percent)
                )

    with _step(runlog, "Set the Shipping method and save the Order"):
        # Left blank on a wiped workspace (see open_new_order()'s docstring)
        # - filled in now, lazily, right before the field actually matters.
        # Same shape as the Debtor's Payment combo: if the method doesn't
        # exist yet, creating it while this same Order editor is open would
        # otherwise leave the Shipping combo stale, so a Ctrl+S on the
        # reactivated Order tab forces the same in-place refresh confirmed
        # live for Payment.
        shipping_name = "Free of shipping costs"
        if not order_editor.has_shipping_method(shipping_name):
            create_default_shipping_method(session, shipping_name)
            order_editor = OrderEditor(session, session.activate_tab("New Order"))
            order_editor.root.type_keys("^s", set_foreground=False)
            time.sleep(1.0)
            if not order_editor.has_shipping_method(shipping_name):
                raise ManualReviewRequired(
                    "shipping method still not available on the Order after a Ctrl+S refresh",
                    method=shipping_name,
                )
        order_editor.set_shipping_method(shipping_name)

        order_editor.save()
        order_number = order_editor.order_number()
        # Read while the Order tab is still the active one - its content
        # pane is torn from the UIA tree the moment another tab (the
        # Invoice, next) takes focus, the same tab-teardown behaviour
        # documented elsewhere in this project for every multi-tab workflow.
        totals = order_editor.read_totals()

    with _step(runlog, "Create the follow-up Invoice and apply payment"):
        invoice_editor = order_editor.create_followup_invoice()
        if doc.payment.is_paid:
            invoice_editor.mark_paid(doc.payment.payment_date or doc.order_date)
        invoice_editor.save()
        invoice_number = _read_invoice_number(session)
        invoice_paid = invoice_editor.is_paid()

    with _step(runlog, "Close all tabs and highlight the saved documents"):
        session.close_all_tabs()
        open_documents(session).highlight_last(2)

    return FlowResult(
        order_number=order_number,
        invoice_number=invoice_number,
        net_total=totals["net_or_gross"],
        vat_total=totals["vat"],
        gross_total=totals["total"],
        invoice_paid=invoice_paid,
    )


def _resolve_debtor(
    session: FakturamaSession, order_editor: OrderEditor, customer: Party, payment_method: str
) -> OrderEditor:
    """Select the customer's address on the Order, creating the Debtor on a genuine miss."""
    search_text = _debtor_search_text(customer)
    if order_editor.select_address(search_text):
        return order_editor

    create_debtor(session, customer, payment_method=payment_method)
    order_editor = OrderEditor(session, session.activate_tab("New Order"))
    if not order_editor.select_address(search_text):
        raise ManualReviewRequired(
            "Debtor was created but still not found in the address selector",
            search_text=search_text,
        )
    return order_editor


def create_debtor(session: FakturamaSession, customer: Party, *, payment_method: str) -> ContactEditor:
    """Steps 2.5-2.10: a new Debtor with both addresses, alias, and payment method.

    If the payment term doesn't exist yet, creating it while this same
    Debtor editor is open would normally leave its Payment combo stale
    (see docs/challenges.md). Confirmed live: a Ctrl+S on the still-open
    Debtor - after the term now exists, before Payment is ever set -
    refreshes that combo in place *and* saves cleanly (no error, despite
    Payment still being unset at that moment) - a real Fakturama
    refresh-on-save behaviour, not a guess. That save is what a second,
    completely separate attempt earlier in this project's history got
    wrong: saving *before* the term existed anywhere in the system failed
    outright ("Document number invalid" / "Failed to persist contents of
    part"). The two situations look identical from this function's own
    perspective (a Debtor being saved without Payment set) but are not:
    what actually matters is whether some payment term already exists in
    the workspace at all, not whether this specific record references one
    yet.
    """
    editor = _fill_new_debtor(session, customer)

    if not editor.has_payment_method(payment_method):
        create_payment_method(session, payment_method)
        # create_payment_method() switches tabs internally (list -> new term
        # editor -> save) - the Debtor tab is no longer active by the time it
        # returns, and its content is torn down along with every inactive
        # tab in this app. Reactivate it (and its Miscellaneous sub-tab,
        # torn down the same way) before touching it again.
        content = session.activate_tab("New Debtor")
        editor = ContactEditor(session, content)
        editor.open_miscellaneous_tab()
        editor.root.type_keys("^s", set_foreground=False)
        time.sleep(1.0)
        if not editor.has_payment_method(payment_method):
            raise ManualReviewRequired(
                "payment method still not available on the Debtor after a Ctrl+S refresh",
                method=payment_method,
            )

    editor.set_payment_method(payment_method)
    editor.save()
    return editor


def _fill_new_debtor(session: FakturamaSession, customer: Party) -> ContactEditor:
    """Everything on the Debtor except Payment - shared by both attempts in create_debtor()."""
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
    return editor


def _debtor_search_text(customer: Party) -> str:
    """Confirmed live: the address selector's search matches against the
    visible columns (No./First Name/Name/Company/ZIP/City) but not the
    Alias field - "NORTHSTAR-BERLIN" (the alias) matched nothing, while
    "Northstar" (the company) did. Company/last name first, alias last."""
    return customer.company or customer.last_name or customer.alias or ""


def _read_invoice_number(session: FakturamaSession) -> str:
    window = session.focus()
    for tab in window.descendants(control_type="TabItem"):
        name = tab.element_info.name or ""
        if name.lstrip("*").startswith("INV"):
            return name.lstrip("*")
    return ""


def _decimal_text(value: Decimal) -> str:
    """19 -> '19', 7.5 -> '7.5' - matches LineItem.vat_rate_name's own formatting."""
    return format(value.normalize(), "f")
