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
from datetime import datetime
from typing import Any

from ..errors import ControlNotFound
from ..uia.locator import Locator
from ..uia.waits import retry
from .base import Page

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
    """
    window = session.focus()
    control = NEW_ORDER_BUTTON.find(window, timeout=10.0)

    def _press() -> None:
        try:
            control.invoke()
        except Exception:  # noqa: BLE001 - not every SWT control exposes Invoke
            control.click_input()

    retry(_press, attempts=3, description=f"clicking {NEW_ORDER_BUTTON.label}")

    content = Locator(control_type="Pane", name="New Order").labelled(
        "New Order editor content"
    ).find(window, timeout=15.0)
    return OrderEditor(session, content)


class OrderEditor(Page):
    """Wraps the tab opened by the toolbar's ``Order`` button."""

    # -- header fields -------------------------------------------------

    def order_number(self) -> str:
        """The automatically proposed ``No.`` - read, never written (step 1.4)."""
        field = self.field_after_label("No.", control_types=("Edit",))
        return self.read_text(field)

    def set_date(self, value: date_type) -> None:
        field = self.field_after_label("Date", control_types=("Edit",))
        # Fakturama accepts ISO text but redisplays it in a locale format
        # ("2026-07-14" -> "Jul 14, 2026") once the real keystrokes that
        # write it also trigger the field's own reformat-on-input behaviour
        # - the same date, not a wrong write, so verification parses the
        # read-back instead of comparing it as a literal string.
        def _same_date(actual: str, _expected: str) -> bool:
            for fmt in ("%b %d, %Y", "%Y-%m-%d", "%m/%d/%Y", "%d.%m.%Y"):
                try:
                    return datetime.strptime(actual.strip(), fmt).date() == value
                except ValueError:
                    continue
            return False

        self.set_text(field, value.isoformat(), verify=_same_date)

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

    def create_followup_invoice(self) -> None:
        """Step 4.6: the Order's own follow-up action, not the toolbar Invoice button."""
        group = FOLLOWUP_GROUP.find(self.root, timeout=10.0)
        button = FOLLOWUP_INVOICE.find(group, timeout=10.0)
        self.click(button)

    # -- totals read-back (step 4.3) ---------------------------------------

    def read_totals(self) -> dict[str, str]:
        return {
            "net_or_gross": self.read_text(NET_OR_GROSS_TOTAL),
            "vat": self.read_text(VAT_TOTAL),
            "total": self.read_text(GRAND_TOTAL),
        }
