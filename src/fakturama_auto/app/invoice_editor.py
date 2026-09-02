"""The Invoice editor opened via an Order's 'Create a follow-up document' group.

Grounded live. Checking 'paid' reveals the payment-method combo, an 'at'
date field, and a 'Value' amount inline - all three come pre-filled from
the Order/Debtor (payment method, today's date, the full gross total), so
marking an invoice paid is really just: check the box, and correct the
date if the brief's extracted payment date differs from today.

The payment-method combo has no accessible name of its own ('paid' names
the checkbox, not the row), so it is found by scanning every ComboBox for
one that isn't the page's own Net/Gross or VAT-mode selector - the same
kind of unnamed-control problem the Order header's price-mode selector
already needed a workaround for, just solved by content instead of position
since there is no stable label to walk from here.
"""

from __future__ import annotations

from datetime import date as date_type
from typing import Any

from ..errors import ControlNotFound
from ..uia.locator import Locator
from .base import Page, date_verifier

PAID_CHECKBOX = Locator(control_type="CheckBox", name="paid").labelled("Invoice 'paid' checkbox")

#: Values these Order-header combos can show - excluded when hunting for the
#: payment-method combo among all of a page's ComboBoxes.
_NON_PAYMENT_COMBO_VALUES = {"Net", "Gross", "With VAT", "Without VAT"}


class InvoiceEditor(Page):
    """Wraps the tab opened by ``OrderEditor.create_followup_invoice()``."""

    def is_paid(self) -> bool:
        return bool(PAID_CHECKBOX.find(self.root, timeout=10.0).get_toggle_state())

    def payment_method(self) -> str:
        for combo in self.root.descendants(control_type="ComboBox"):
            value = self.read_text(combo)
            if value and value not in _NON_PAYMENT_COMBO_VALUES:
                return value
        return ""

    def value_paid(self) -> str:
        field = self.field_after_label("Value", control_types=("Edit",))
        return self.read_text(field)

    def mark_paid(self, payment_date: date_type) -> None:
        """Step 4.8-4.9: check 'paid' and set the actual payment date.

        The method combo and Value amount are left as Fakturama's own
        defaults (inherited from the Debtor and the Order total) - the
        brief only calls out the paid flag and its date as things this
        automation sets explicitly.
        """
        checkbox = PAID_CHECKBOX.find(self.root, timeout=10.0)
        if not bool(checkbox.get_toggle_state()):
            checkbox.toggle()

        date_field = self.field_after_label("at", control_types=("Edit",), offset=1)
        self.set_text(date_field, payment_date.isoformat(), verify=date_verifier(payment_date))

    def save(self) -> None:
        """See OrderEditor.save() - the button lives in the shared top ribbon."""
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
