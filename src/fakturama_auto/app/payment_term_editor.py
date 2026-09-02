"""The 'New Term of Payment' editor, and the list it's created from.

Grounded live. One field's accessible name is worth flagging so a future
reader doesn't assume it's a typo in this file: the payment-code dropdown
(brief step 2.10.4, "Bank Transfer = Credit transfer") is literally named
``!editorPaymentPaymentcode!`` - an untranslated i18n resource key leaking
through as the control's name, not a placeholder written by this codebase.
It is still the real, stable accessible name, so it is used as-is.
"""

from __future__ import annotations

from typing import Any

from ..errors import ControlNotFound, ManualReviewRequired
from ..uia.locator import Locator
from .base import Page, click_and_await_pane

TERMS_OF_PAYMENT_LINK = Locator(control_type="Text", name="terms of payment").labelled(
    "left-panel terms of payment link"
)
CREATE_BUTTON = Locator(
    control_type="Button", name="Create a new term of payment"
).labelled("Create a new term of payment button")

NAME = Locator(control_type="Edit", name="Name").labelled("payment term Name field")
DESCRIPTION = Locator(control_type="Edit", name="Description").labelled(
    "payment term Description field"
)
# See module docstring re: this name.
PAYMENT_CODE = Locator(control_type="ComboBox", name="!editorPaymentPaymentcode!").labelled(
    "payment code dropdown"
)
CASH_DISCOUNT = Locator(control_type="Edit", name="Cash discount").labelled("Cash discount field")
DISCOUNT_DAYS = Locator(control_type="Edit", name="Discount Days").labelled("Discount Days field")
NET_DAYS = Locator(control_type="Edit", name="Net Days").labelled("Net Days field")

#: Fakturama's full payment-code vocabulary (confirmed live by dumping the
#: dropdown's real options - each has a trailing space in the app itself,
#: harmless since ``matches_text``/``select_combo`` compare whitespace-
#: insensitively): 'In cash', 'Credit transfer', 'Debit transfer',
#: 'Bank card', 'Direct debit', 'Credit card', 'Debit card',
#: 'Standing agreement', 'SEPA credit transfer', 'SEPA direct debit',
#: 'Online payment service', 'Mutually defined'.
#:
#: Step 2.10.4 gives one exact example ("Bank Transfer = Credit transfer");
#: everything else here is this codebase's own best-effort mapping from a
#: printed payment method to the closest of the twelve codes above, not a
#: value confirmed against the brief. A method with no confident mapping
#: raises :class:`~..errors.ManualReviewRequired` rather than guessing.
PAYMENT_CODE_BY_METHOD = {
    "Bank Transfer": "Credit transfer",
    "Wire Transfer": "Credit transfer",
    "SEPA Credit Transfer": "SEPA credit transfer",
    "Credit Card": "Credit card",
    "Corporate Card": "Credit card",
    "Debit Card": "Debit card",
    "Bank Card": "Bank card",
    "Direct Debit": "Direct debit",
    "SEPA Direct Debit": "SEPA direct debit",
    "Cash": "In cash",
    "Standing Order": "Standing agreement",
    "PayPal": "Online payment service",
    "Online Payment": "Online payment service",
}


def open_terms_of_payment_list(session: Any) -> Any:
    """Step 2.10.1: opens the terms-of-payment list as a tab, alongside whatever else is open."""
    window = session.focus()
    return click_and_await_pane(window, TERMS_OF_PAYMENT_LINK, "terms of payment")


def open_new_payment_term(session: Any) -> "PaymentTermEditor":
    """Step 2.10.2: the green + control in the terms-of-payment list."""
    window = session.focus()
    content = click_and_await_pane(window, CREATE_BUTTON, "New Term of Payment")
    return PaymentTermEditor(session, content)


class PaymentTermEditor(Page):
    """Wraps the tab opened by 'Create a new term of payment'."""

    def set_name_and_description(self, method: str) -> None:
        """Step 2.10.3: Name and Description both get the exact Payment Method text."""
        self.set_text(NAME, method)
        self.set_text(DESCRIPTION, method)

    def set_payment_code(self, method: str) -> None:
        """Step 2.10.4: map the extracted method to Fakturama's payment-code list."""
        code = PAYMENT_CODE_BY_METHOD.get(method)
        if code is None:
            raise ManualReviewRequired(
                "no known payment-code mapping for this method",
                method=method,
                known_methods=sorted(PAYMENT_CODE_BY_METHOD),
            )
        self.select_combo(PAYMENT_CODE, code)

    def zero_out_terms(self) -> None:
        """Step 2.10.5: Cash discount, Discount Days and Net Days all to 0.

        Cash discount is a percentage field that always displays with a
        trailing '%' - typing the literal '0%' (not a bare '0') is what
        actually round-trips, the same reformat-on-input behaviour the
        Order date field has. Discount/Net Days are plain day counts, no
        such suffix.
        """
        self.set_text(CASH_DISCOUNT, "0%")
        self.set_text(DISCOUNT_DAYS, "0")
        self.set_text(NET_DAYS, "0")

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


def create_payment_method(session: Any, method: str) -> None:
    """The full step 2.10.1-2.10.6 branch: create ``method`` if it doesn't already exist.

    Leaves the terms-of-payment list and its editor tabs open - closing them
    is not part of the brief's flow, and the caller returns to its own
    still-open Debtor editor afterward regardless.
    """
    open_terms_of_payment_list(session)
    editor = open_new_payment_term(session)
    editor.set_name_and_description(method)
    editor.set_payment_code(method)
    editor.zero_out_terms()
    editor.save()
