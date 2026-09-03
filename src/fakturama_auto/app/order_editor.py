"""The 'New Order' / order-editing tab.

Grounded against a live Fakturama 2.x instance (Eclipse RCP / SWT). Two
things here are not what they first look like, and both were caught by
dumping the live tree rather than assuming:

* The Net/Gross price-mode selector (step 1.7) has no name of its own - it
  is the first ``ComboBox`` that appears after the ``Date`` field in widget
  order, so it is grounded structurally rather than by a name that doesn't
  exist.
* The running total's accessible name changes with that same selector: it
  reads "Total Gross" in Gross mode and "Total Net" in Net mode. A locator
  pinned to either literal name would work today and break the moment the
  flow does exactly what step 1.7 asks - switch the mode - so it is matched
  with a name pattern instead of a literal name.

The Items table (Pos./Qty./Item No./.../Price columns) renders its own
columns and rows with no corresponding UIA nodes at all - confirmed live,
not assumed: dumping its container returns nothing but a scrollbar. Reading
or writing individual cells therefore cannot use identity or structure
grounding (rungs 1-2) and needs the keyboard-traversal or OCR rungs; that
piece is tracked separately and is not yet implemented here.
"""

from __future__ import annotations

from datetime import date as date_type
from typing import Any

from ..errors import AutomationError, ControlNotFound
from ..uia.locator import Locator
from ..uia.waits import WaitTimeout, retry
from .base import Page, date_verifier

# -- header -------------------------------------------------------------

CUST_REF = Locator(control_type="Edit", name="Cust.Ref.").labelled("Cust.Ref. field")
CONSULTANT = Locator(control_type="Edit", name="Consultant").labelled("Consultant field")
VAT_MODE = Locator(control_type="ComboBox", name="VAT").labelled("order VAT mode dropdown")

# -- 'Create a follow-up document' group (enabled only after the Order is saved) --

FOLLOWUP_GROUP = Locator(
    control_type="Group", name="Create a follow-up document"
).labelled("follow-up document group")
FOLLOWUP_INVOICE = Locator(control_type="Button", name="Invoice").labelled(
    "follow-up Invoice button"
)

# -- Addresses: two unnamed icons beside the label. Order matters. --
#
# Confirmed live (2026-09-02, Fakturama 2, fresh workspace): the "Addresses"
# label is followed by exactly two Image children with no accessible name -
# the upper one opens the existing-contact selector, the lower one is the
# green "+" that starts a brand new Debtor. The brief is explicit that these
# must not be confused, so the index is the whole point, not an accident of
# structural grounding.
ADDRESSES_LABEL = "Addresses"
ADDRESS_SELECTOR_ICON_INDEX = 0  # upper: "Select the address" (existing)
NEW_DEBTOR_ICON_INDEX = 1  # lower: green + (do not click for step 2.1)

# -- Items: four unnamed icons beside the label. Only the first two are used. --
#
# Confirmed live: "Items" is followed by four unnamed Image children -
# product selector, add-row (green +), delete-row (red x), and a fourth
# (edit/duplicate) not used by this flow.
ITEMS_LABEL = "Items"
PRODUCT_SELECTOR_ICON_INDEX = 0  # upper: "Select a product" (existing)
NEW_ITEM_ROW_ICON_INDEX = 1  # green + (do not click for step 3.2)

# -- totals ---------------------------------------------------------------

#: "Total Gross" in Gross mode, "Total Net" in Net mode - see module docstring.
NET_OR_GROSS_TOTAL = Locator(
    control_type="Edit", name_re=r"(?i)^total (net|gross)$"
).labelled("running net/gross total field")
DISCOUNT = Locator(control_type="Edit", name="Discount").labelled("order Discount field")
SHIPPING_METHOD = Locator(control_type="ComboBox", name="Shipping").labelled(
    "Shipping method dropdown"
)
VAT_TOTAL = Locator(control_type="Edit", name="VAT").labelled("VAT total field")
GRAND_TOTAL = Locator(control_type="Edit", name="Total").labelled("grand Total field")

#: The toolbar button's visible caption is just "Order"; its accessible name
#: is the longer "Create: New Order" - confirmed live, not the caption text
#: a screen reader would announce. Same button as the ribbon-style icon
#: labelled "Order" in the top toolbar (step 1.3).
NEW_ORDER_BUTTON = Locator(control_type="Button", name="Create: New Order").labelled(
    "toolbar Order button (visible caption: 'Order')"
)


def open_new_order(session: Any) -> "OrderEditor":
    """Step 1.3: click Order in the top toolbar and wait for its editor tab.

    The editor area is one TabFolder shared by every open tab, and it turns
    out (confirmed live) that both the TabFolder itself and this specific
    tab's content composite are momentarily named "New Order" - the
    TabFolder because SWT mirrors the active CTabItem's title onto its
    accessible name. They differ in control type (``Tab`` vs. ``Pane``), so
    filtering on ``Pane`` is what keeps a second open order - or any other
    tab - from being mistaken for this one's content.

    A wiped workspace has no default Shipping method, and clicking Order
    without one shows a modal Error dialog (see docs/challenges.md) - but
    confirmed live, the click still genuinely opens the New Order tab; the
    dialog just sits on top of it. And, the same as every other modal in
    this app, the content behind a modal dialog is torn from the UIA tree
    while it's open - not merely hidden, the same "inactive tab" rule
    documented elsewhere in this project, just triggered by a dialog
    instead of a different tab taking focus. Dismissing it is enough: the
    Order's own Shipping field is simply left blank (nothing in this flow
    ever reads or sets it), and the tab is otherwise perfectly usable -
    there is no need to create a default Shipping method or click Order
    again. Clicking again after dismissing was tried and confirmed wrong:
    it opens a second, genuinely separate Order every time this path
    fires, and a later name-based tab lookup can then silently grab that
    one instead of the tab the rest of the flow has been filling in.
    """
    window = session.focus()

    def _click_order() -> None:
        control = NEW_ORDER_BUTTON.find(window, timeout=10.0)
        try:
            control.invoke()
        except Exception:  # noqa: BLE001 - not every SWT control exposes Invoke
            control.click_input()

    def _open() -> Any:
        _click_order()
        _dismiss_shipping_error(session)
        return Locator(control_type="Pane", name="New Order").labelled(
            "New Order editor content"
        ).find(window, timeout=15.0)

    # A light safety net for a genuine dropped click (the generic
    # cold-start quirk documented on click_and_await_pane), not for the
    # Shipping dialog - that case is already fully handled above without
    # ever needing a second click.
    content = retry(_open, attempts=2, delay=1.0, description="opening 'New Order'")
    return OrderEditor(session, content)


def _dismiss_shipping_error(session: Any) -> bool:
    """If the 'No default value found for Shippings' Error dialog is open,
    dismiss it and return True; otherwise return False without touching
    anything else."""
    try:
        dialog = session.dialog(r"^Error$", timeout=3.0)
    except AutomationError:
        return False
    if not Locator(control_type="Text", name_re=r".*Shippings.*").exists(dialog):
        return False
    Locator(control_type="Button", name="OK").find(dialog, timeout=5.0).click_input()
    return True


class OrderEditor(Page):
    """Wraps the tab opened by the toolbar's ``Order`` button."""

    # -- header fields -------------------------------------------------

    def order_number(self) -> str:
        """The automatically proposed ``No.`` - read, never written (step 1.4)."""
        field = self.field_after_label("No.", control_types=("Edit",))
        return self.read_text(field)

    def set_date(self, value: date_type) -> None:
        field = self.field_after_label("Date", control_types=("Edit",))
        self.set_text(field, value.isoformat(), verify=date_verifier(value))

    def set_cust_ref(self, value: str) -> None:
        self.set_text(CUST_REF, value)

    def price_mode_combo(self) -> Any:
        return self.field_after_label("Date", control_types=("ComboBox",))

    def set_price_mode(self, mode: str) -> None:
        """``mode`` is ``"Net"`` or ``"Gross"`` (step 1.7)."""
        self.select_combo(self.price_mode_combo(), mode)

    def set_vat_mode(self, mode: str) -> None:
        """``mode`` is ``"With VAT"`` or ``"Without VAT"`` (step 1.7)."""
        self.select_combo(VAT_MODE, mode)

    # -- Addresses icons -------------------------------------------------

    def _icon_after_label(self, label: str, index: int) -> Any:
        """The ``index``-th unnamed Image sibling that follows ``label`` in order.

        A generalisation of :meth:`Page.field_after_label` for icon-only
        toolbars beside a section label - the same structural relationship,
        just targeting ``Image`` instead of an editable control type.
        """
        return self.field_after_label(label, control_types=("Image",), offset=index + 1)

    def _open_dialog_via_icon(self, opener: Any, title_re: str, attempts: int = 3) -> Any:
        """Click an icon and wait for the dialog it opens, retrying the
        whole click-and-wait cycle - not just the click - if the dialog
        never appears.

        Confirmed live: a click on the address/product selector icon can be
        silently dropped the same way the cold-start button clicks
        documented on ``click_and_await_pane`` are - no exception, the
        dialog just never shows up, and waiting longer doesn't help. Since
        ``opener()`` re-finds and re-clicks the icon fresh each attempt
        (unlike retrying a bare ``click_input()`` on an already-resolved
        reference), a second attempt succeeds once whatever briefly made
        the first one land nowhere has passed.
        """

        def _open_and_wait() -> Any:
            opener()
            return self.session.dialog(title_re, timeout=10.0)

        return retry(_open_and_wait, attempts=attempts, delay=1.0, description=f"opening {title_re!r}")

    def open_address_selector(self) -> None:
        """Step 2.1: the upper icon. Opens the 'Select the address' dialog."""
        icon = self._icon_after_label(ADDRESSES_LABEL, ADDRESS_SELECTOR_ICON_INDEX)
        self.click(icon)

    def open_new_debtor_icon(self) -> None:
        """The lower green + icon - starts a new Debtor directly from the Order.

        The brief's primary path creates a new Debtor via the left
        Navigation panel's 'New Contact' instead (step 2.5); this exists for
        completeness and is not used by the default flow.
        """
        icon = self._icon_after_label(ADDRESSES_LABEL, NEW_DEBTOR_ICON_INDEX)
        self.click(icon)

    def select_address(self, search_text: str) -> bool:
        """Step 2.1-2.2: search the address selector and choose the match.

        Returns whether a match was found and selected - the lazy,
        order-first flow needs to know this to decide whether to branch off
        and create the Debtor, so this reports the outcome instead of
        assuming success or raising.

        Confirmed live: typing text that matches exactly one row sometimes
        auto-confirms and closes the dialog on its own (seen reliably for
        the product selector on an exact SKU), and sometimes just filters
        the grid without selecting anything (seen for a company-name
        substring here) - this app is not consistent about it. Rather than
        depend on which behaviour a given search text triggers, this waits
        briefly for an auto-close and, failing that, double-clicks the sole
        remaining row. The grid itself exposes no per-row identity to UIA
        (the same opaque-table pattern as the Items grid), so the row is
        reached by a coordinate offset from the dialog's own frame and the
        search box's rectangle, both of which *are* grounded - not a fixed
        screen coordinate. If neither the auto-close nor the double-click
        produces a match, there is no reliable "zero rows" signal from this
        opaque grid to check directly - a real risk if the double-click
        coordinate ever drifts on a genuine match, but this shape has held
        across every case tried this session - so the dialog is explicitly
        cancelled and treated as "not found" rather than left open.
        """
        dialog = self._open_dialog_via_icon(self.open_address_selector, r"^Select the address$")
        search = Locator(control_type="Edit").labelled("address search box").find(
            dialog, timeout=5.0
        )
        search.click_input()
        search.type_keys(search_text, with_spaces=True, set_foreground=False)

        try:
            self.session.dialog_closed(r"^Select the address$", timeout=2.0)
            return True
        except WaitTimeout:
            pass

        from pywinauto.mouse import double_click

        dialog_rect = dialog.rectangle()
        search_rect = search.rectangle()
        row_x = dialog_rect.left + 60
        row_y = search_rect.bottom + 45
        double_click(coords=(row_x, row_y))
        try:
            self.session.dialog_closed(r"^Select the address$", timeout=5.0)
            return True
        except WaitTimeout:
            cancel = Locator(control_type="Button", name="Cancel").find(dialog, timeout=5.0)
            cancel.click_input()
            return False

    def invoice_address_text(self) -> str:
        """The read-only, formatted address block shown once a Debtor is selected."""
        tab = Locator(control_type="Tab", name="Invoice address").labelled(
            "Invoice address tab"
        ).find(self.root)
        field = Locator(control_type="Edit").labelled("Invoice address text").find(tab)
        return self.read_text(field)

    def delivery_address_tab_exists(self) -> bool:
        """Whether a separate 'Delivery address' tab has appeared.

        Fakturama shows only 'Invoice address' until a Debtor with a
        distinct delivery address is selected; a matching address collapses
        to the single tab (step 2.8).
        """
        return Locator(control_type="TabItem", name="Delivery address").exists(self.root)

    # -- Items icons -------------------------------------------------------

    def open_product_selector(self) -> None:
        """Step 3.2: the upper icon beside Items. Opens 'Select a product'."""
        icon = self._icon_after_label(ITEMS_LABEL, PRODUCT_SELECTOR_ICON_INDEX)
        self.click(icon)

    def add_item(self, sku: str) -> bool:
        """Step 3.2-3.3: add a line by SKU via the product selector's search.

        Returns whether the product was found and added - the lazy,
        order-first flow needs this to decide whether to branch off and
        create the Product (and its VAT rate, if that's missing too).

        Confirmed live, and inconsistent in the same way already documented
        for the address selector (``select_address``): a SKU matching
        exactly one product sometimes auto-closes the dialog on its own,
        and sometimes just filters the grid to one visible, correct row
        without selecting it - seen for the identical search text on two
        different runs. So this follows the same two-rung approach: wait
        briefly for an auto-close, then double-click the sole remaining
        row (reached by a coordinate offset from the dialog's own frame and
        the search box, both grounded - the grid itself is UIA-opaque, the
        same pattern as the address selector and Items grid). Only cancels
        and reports not-found if neither produces a close.
        """
        dialog = self._open_dialog_via_icon(self.open_product_selector, r"^Select a product$")
        search = Locator(control_type="Edit").labelled("product search box").find(
            dialog, timeout=5.0
        )
        search.click_input()
        search.type_keys(sku, with_spaces=True, set_foreground=False)

        try:
            self.session.dialog_closed(r"^Select a product$", timeout=2.0)
            return True
        except WaitTimeout:
            pass

        from pywinauto.mouse import double_click

        dialog_rect = dialog.rectangle()
        search_rect = search.rectangle()
        row_x = dialog_rect.left + 60
        row_y = search_rect.bottom + 45
        double_click(coords=(row_x, row_y))
        try:
            self.session.dialog_closed(r"^Select a product$", timeout=5.0)
            return True
        except WaitTimeout:
            cancel = Locator(control_type="Button", name="Cancel").find(dialog, timeout=5.0)
            cancel.click_input()
            return False

    # -- Items grid cell editing --------------------------------------------
    #
    # The Items table renders its own rows with no corresponding UIA nodes -
    # confirmed live, dumping its container returns nothing but a
    # scrollbar (see the module docstring). Cells are therefore reached by a
    # coordinate offset from the one grounded anchor beside the grid (the
    # "Items" label's own icon column), not by identity - the closest this
    # specific, confirmed-opaque widget allows to the locator-based grounding
    # used everywhere else in this codebase. The offsets are fixed pixel
    # column widths on this widget (confirmed stable across window sizes,
    # as long as the window is wide enough not to scroll the column out of
    # view - callers should maximise the main window before editing Discount).

    _ITEMS_ROW_HEIGHT = 25
    _ITEMS_FIRST_ROW_OFFSET = 39
    _ITEMS_QTY_COLUMN_OFFSET = 88
    _ITEMS_DISCOUNT_COLUMN_OFFSET = 962

    def _items_icon_column_rect(self) -> Any:
        label = Locator(control_type="Text", name=ITEMS_LABEL).find(self.root, timeout=10.0)
        return label.parent().rectangle()

    def _edit_items_cell(self, row_index: int, column_offset: int, value: str) -> None:
        from pywinauto.keyboard import send_keys
        from pywinauto.mouse import double_click

        from .base import escape_special_keys

        rect = self._items_icon_column_rect()
        x = rect.right + column_offset
        y = rect.top + self._ITEMS_FIRST_ROW_OFFSET + self._ITEMS_ROW_HEIGHT * row_index
        double_click(coords=(x, y))
        send_keys("^a")
        send_keys(escape_special_keys(value))
        send_keys("{ENTER}")

    def set_item_quantity(self, row_index: int, quantity: str) -> None:
        """``row_index`` is 0-based (the first Items row is 0)."""
        self._edit_items_cell(row_index, self._ITEMS_QTY_COLUMN_OFFSET, quantity)

    def set_item_discount_percent(self, row_index: int, percent: str) -> None:
        """Requires the main window to be wide enough to show the Discount
        column without horizontal scrolling - see the class-level note above."""
        self._edit_items_cell(row_index, self._ITEMS_DISCOUNT_COLUMN_OFFSET, percent)

    # -- follow-up / save --------------------------------------------------

    def save(self) -> None:
        """Toolbar Save, falling back to Ctrl+S if the button can't be resolved.

        The button lives in the shared top ribbon, outside any specific
        editor's own content pane - confirmed live that ``self.root`` (this
        editor's content) never contains it. It is looked up against the
        session's main window instead; Ctrl+S is the fallback when that
        lookup itself fails (a different top-level state has the ribbon
        temporarily unavailable), not when the button is merely disabled -
        a disabled Save button means "nothing to save", which Ctrl+S would
        just no-op through anyway.
        """
        try:
            button = Locator(
                control_type="Button", name="Save the current contents"
            ).labelled("Save toolbar button").find(self.session.main_window, timeout=5.0)
        except ControlNotFound:
            button = None

        if button is not None and button.is_enabled():
            self.click(button)
        else:
            self.root.type_keys("^s", set_foreground=False)

    def create_followup_invoice(self) -> "InvoiceEditor":
        """Step 4.6: the Order's own follow-up action, not the toolbar Invoice button."""
        from .invoice_editor import InvoiceEditor

        group = FOLLOWUP_GROUP.find(self.root, timeout=10.0)
        button = FOLLOWUP_INVOICE.find(group, timeout=10.0)
        self.click(button)

        content = Locator(control_type="Pane", name="New Invoice").labelled(
            "New Invoice editor content"
        ).find(self.session.focus(), timeout=15.0)
        return InvoiceEditor(self.session, content)

    # -- shipping ------------------------------------------------------------

    def shipping_method_options(self) -> list[str]:
        return self.combo_options(SHIPPING_METHOD)

    def has_shipping_method(self, method: str) -> bool:
        from ..uia.locator import matches_text

        return any(matches_text(opt, method) for opt in self.shipping_method_options())

    def set_shipping_method(self, method: str) -> None:
        self.select_combo(SHIPPING_METHOD, method)

    # -- totals read-back (step 4.3) ---------------------------------------

    def read_totals(self) -> dict[str, str]:
        return {
            "net_or_gross": self.read_text(NET_OR_GROSS_TOTAL),
            "vat": self.read_text(VAT_TOTAL),
            "total": self.read_text(GRAND_TOTAL),
        }
