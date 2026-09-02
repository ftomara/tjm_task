"""The 'New product' editor.

Grounded live. Most fields are cleanly named; the price field is not - its
own label reads "Price (gross)" (a real Fakturama label, not a workaround
name chosen here) and the Edit beside it has no accessible name of its own,
so it needs :meth:`~..app.base.Page.field_after_label` like the Order and
Contact editors' unnamed pairs.
"""

from __future__ import annotations

from typing import Any

from ..errors import ControlNotFound
from ..uia.locator import Locator
from .base import Page, click_and_await_pane

NEW_PRODUCT_LINK = Locator(control_type="Text", name="New product").labelled(
    "left-panel New product link"
)

ITEM_NUMBER = Locator(control_type="Edit", name="Item Number").labelled("Item Number field")
NAME = Locator(control_type="Edit", name="Name").labelled("product Name field")
VAT = Locator(control_type="ComboBox", name="VAT").labelled("product VAT dropdown")


def open_new_product(session: Any) -> "ProductEditor":
    window = session.focus()
    content = click_and_await_pane(window, NEW_PRODUCT_LINK, "New product")
    return ProductEditor(session, content)


class ProductEditor(Page):
    """Wraps the tab opened by the left panel's 'New product' link."""

    def set_item_number(self, sku: str) -> None:
        self.set_text(ITEM_NUMBER, sku)

    def set_name(self, name: str) -> None:
        self.set_text(NAME, name)

    def set_gross_price(self, value: str) -> None:
        field = self.field_after_label("Price (gross)", control_types=("Edit",))
        self.set_text(field, value)

    def set_vat(self, vat_name: str) -> None:
        self.select_combo(VAT, vat_name)

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


def create_product(session: Any, *, sku: str, name: str, gross_price: str, vat_name: str) -> None:
    """Create and save a product master record."""
    editor = open_new_product(session)
    editor.set_item_number(sku)
    editor.set_name(name)
    editor.set_gross_price(gross_price)
    editor.set_vat(vat_name)
    editor.save()
