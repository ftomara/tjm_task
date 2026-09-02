"""The 'New Debtor' / contact-editing tab.

Grounded live against Fakturama 2.x. One structural discrepancy from the
brief is worth flagging explicitly rather than silently working around:
the brief describes a separate **Payment** tab (step 2.10), but this
installed version has no such tab - `Payment` is a named combobox living
inside **Miscellaneous** instead, alongside Alias name, Discount and Net or
Gross. Confirmed live, not assumed; the page object follows the real
structure.

Most fields on the Main address sub-tab are cleanly named and need no
structural workaround at all - a pleasant contrast to the Order editor's
header row. Two are combined-label pairs (`First Name Last Name`,
`ZIP - City`) needing :meth:`~..app.base.Page.field_after_label`, same
pattern as the Order editor's price-mode selector.
"""

from __future__ import annotations

from typing import Any

from ..errors import ControlNotFound
from ..uia.locator import Locator
from .base import Page, click_and_await_pane

# -- header -----------------------------------------------------------

COMPANY = Locator(control_type="Edit", name="Company").labelled("Company field")
CUSTOMER_ID = Locator(control_type="Edit", name="Customer ID").labelled("Customer ID field")

# -- Main address sub-tab (all directly named) -------------------------

STREET = Locator(control_type="Edit", name="Street").labelled("Street field")
EMAIL = Locator(control_type="Edit", name="E-Mail").labelled("E-Mail field")
TELEPHONE = Locator(control_type="Edit", name="Telephone").labelled("Telephone field")
ADDITIONAL_NAME = Locator(control_type="Edit", name="additional name").labelled(
    "additional name field"
)
ADDRESS_SPECIFICATION = Locator(control_type="Edit", name="Address specification").labelled(
    "Address specification field"
)
COUNTRY = Locator(control_type="ComboBox", name="Country").labelled("Country dropdown")

# -- Miscellaneous sub-tab (all directly named) -------------------------

ALIAS_NAME = Locator(control_type="Edit", name="Alias name").labelled("Alias name field")
DISCOUNT = Locator(control_type="Edit", name="Discount").labelled("Debtor Discount field")
NET_OR_GROSS = Locator(control_type="ComboBox", name="Net or Gross").labelled(
    "Net or Gross dropdown"
)
PAYMENT_METHOD = Locator(control_type="ComboBox", name="Payment").labelled(
    "Payment method dropdown"
)

NEW_CONTACT_LINK = Locator(control_type="Text", name="New Contact").labelled(
    "left-panel New Contact link"
)


def open_new_debtor(session: Any) -> "ContactEditor":
    """Step 2.5: click New Contact in the left New panel and wait for the editor.

    Same disambiguation concern as ``open_new_order``: the editor-area
    TabFolder briefly mirrors its active tab's title, so this content is
    grounded by ``control_type="Pane"`` specifically, not just by name.
    """
    window = session.focus()
    content = click_and_await_pane(window, NEW_CONTACT_LINK, "New Debtor")
    return ContactEditor(session, content)


class ContactEditor(Page):
    """Wraps the tab opened by the left panel's 'New Contact' link."""

    # -- header ----------------------------------------------------------

    def set_company(self, value: str) -> None:
        self.set_text(COMPANY, value)

    def set_name(self, first_name: str | None, last_name: str | None) -> None:
        """The combined 'First Name Last Name' label sits over two fields."""
        if first_name:
            field = self.field_after_label(
                "First Name Last Name", control_types=("Edit",), offset=1
            )
            self.set_text(field, first_name)
        if last_name:
            field = self.field_after_label(
                "First Name Last Name", control_types=("Edit",), offset=2
            )
            self.set_text(field, last_name)

    # -- Main address (step 2.7) ------------------------------------------

    def open_main_address_tab(self) -> None:
        Locator(control_type="TabItem", name="Addresses").find(self.root, timeout=10.0).click_input()

    def open_address_subtab(self, name: str) -> None:
        """Activate one of the inner address tabs, e.g. ``"Main address"`` or
        ``"additional address #1"`` (the latter appears only after
        :meth:`add_address_tab`). Same tab-teardown rule as everywhere else
        in this app: the other sub-tab's fields are not in the tree until
        this is called."""
        Locator(control_type="TabItem", name=name).find(self.root, timeout=10.0).click_input()

    def set_main_address(
        self,
        *,
        street: str | None,
        zip_code: str | None,
        city: str | None,
        country: str | None,
        email: str | None,
        phone: str | None,
    ) -> None:
        """Fills the billing fields the brief calls out (step 2.7).

        Additional-name / address-specification / district are deliberately
        left untouched here - the brief says to fill those only when the
        source document actually supplies them, and this method's caller is
        the one that knows whether it does.
        """
        if street:
            self.set_text(STREET, street)
        if zip_code:
            zip_field = self.field_after_label("ZIP - City", control_types=("Edit",), offset=1)
            self.set_text(zip_field, zip_code)
        if city:
            city_field = self.field_after_label("ZIP - City", control_types=("Edit",), offset=2)
            self.set_text(city_field, city)
        if country:
            self.select_combo(COUNTRY, country)
        if email:
            self.set_text(EMAIL, email)
        if phone:
            self.set_text(TELEPHONE, phone)

    def set_address_role(self, *, invoice: bool, delivery: bool) -> None:
        """Step 2.8: assign this address's role via the inline role flyout.

        'address type' is not a combobox or a pair of visible checkboxes -
        it is an unnamed Edit with a button beside it that opens a small
        transient popup containing two named CheckBox controls
        ('Invoice address', 'Delivery address'). The popup is not a modal
        Window - it does not show up in any Window-descendant search - and
        it auto-dismisses on any focus change, so the button click and the
        checkbox toggles must happen back to back, in the same call, never
        split across a wait long enough for focus to move elsewhere.

        Confirmed live: once the popup closes, the 'address type' Edit
        itself shows the resulting role(s) as plain text, so the outcome is
        independently verifiable via a normal read.
        """
        button = self.field_after_label(
            "address type", control_types=("Button",), offset=1
        )
        self.click(button)

        invoice_cb = Locator(control_type="CheckBox", name="Invoice address").find(
            self.session.main_window, timeout=5.0
        )
        delivery_cb = Locator(control_type="CheckBox", name="Delivery address").find(
            self.session.main_window, timeout=5.0
        )

        def _apply(checkbox: Any, desired: bool) -> None:
            if bool(checkbox.get_toggle_state()) != desired:
                checkbox.toggle()

        _apply(invoice_cb, invoice)
        _apply(delivery_cb, delivery)

        # Dismiss the flyout - Escape, not a click elsewhere, since a stray
        # click risks landing on an unrelated control in this dense form.
        self.session.main_window.type_keys("{ESC}", set_foreground=False)

    def address_role_text(self) -> str:
        """Read back what set_address_role() actually persisted."""
        field = self.field_after_label("address type", control_types=("Edit",), offset=1)
        return self.read_text(field)

    def add_address_tab(self) -> None:
        """The '+' button beside the Main address tabs - adds another address slot."""
        plus = Locator(control_type="Button", name="+").find(self.root, timeout=10.0)
        self.click(plus)

    # -- Miscellaneous (step 2.9) -----------------------------------------

    def open_miscellaneous_tab(self) -> None:
        Locator(control_type="TabItem", name="Miscellaneous").find(
            self.root, timeout=10.0
        ).click_input()

    def set_alias(self, alias: str) -> None:
        self.set_text(ALIAS_NAME, alias)

    def set_discount_zero(self) -> None:
        """Discount is a percentage field that always displays with a
        trailing '%' - typing the literal '0%' is what round-trips, same as
        PaymentTermEditor's Cash discount field."""
        self.set_text(DISCOUNT, "0%")

    def set_net_or_gross(self, mode: str) -> None:
        self.select_combo(NET_OR_GROSS, mode)

    # -- Payment (step 2.10; lives inside Miscellaneous - see module docstring) --

    def payment_method_options(self) -> list[str]:
        return self.combo_options(PAYMENT_METHOD)

    def set_payment_method(self, method: str) -> None:
        self.select_combo(PAYMENT_METHOD, method)

    def has_payment_method(self, method: str) -> bool:
        from ..uia.locator import matches_text

        return any(matches_text(opt, method) for opt in self.payment_method_options())

    # -- save --------------------------------------------------------------

    def save(self) -> None:
        """See OrderEditor.save() - same fix, same reason: the button lives
        in the shared top ribbon, not inside this editor's own content."""
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
