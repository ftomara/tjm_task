"""The 'New Shipping' editor, and the list it's created from.

Grounded live. Discovered the hard way, not from the brief: opening a New
Order on a wiped workspace (no Shipping records at all) shows a modal
Error dialog ("No default value found for Shippings. Please set one from
list!") - but confirmed live, the Order editor still opens behind it; only
its own Shipping field is left blank (see OrderEditor.open_new_order()).
A normal Fakturama install ships a standard "Free of shipping costs" entry
out of the box (seen in every manual test this project ran against a
pre-existing workspace), which is why this recreates that exact name -
so this is created lazily, whenever the Order's own Shipping field
actually needs a value, not unconditionally up front.
"""

from __future__ import annotations

from typing import Any

from ..errors import ControlNotFound
from ..uia.locator import Locator
from .base import Page, click_and_await_pane

SHIPPINGS_LINK = Locator(control_type="Text", name="Shippings").labelled(
    "left-panel Shippings link"
)
CREATE_BUTTON = Locator(control_type="Button", name="Create a new shipping method").labelled(
    "Create a new shipping method button"
)

NAME = Locator(control_type="Edit", name="Name").labelled("shipping Name field")
GROSS_VALUE = Locator(control_type="Edit", name="Gross").labelled("shipping Gross value field")
SET_AS_STANDARD = Locator(control_type="Button", name="Set as standard").labelled(
    "Set as standard button"
)


def open_shippings_list(session: Any) -> Any:
    window = session.focus()
    return click_and_await_pane(window, SHIPPINGS_LINK, "Shippings")


def open_new_shipping(session: Any) -> "ShippingEditor":
    open_shippings_list(session)
    window = session.focus()
    content = click_and_await_pane(window, CREATE_BUTTON, "New Shipping")
    return ShippingEditor(session, content)


class ShippingEditor(Page):
    """Wraps the tab opened by the Shippings list's 'Create a new shipping method' button."""

    def set_name(self, name: str) -> None:
        self.set_text(NAME, name)

    def set_gross_value(self, value: str) -> None:
        self.set_text(GROSS_VALUE, value)

    def set_as_standard(self) -> None:
        button = SET_AS_STANDARD.find(self.root, timeout=10.0)
        self.click(button)

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


def create_default_shipping_method(session: Any, name: str = "Free of shipping costs") -> None:
    """Create a zero-cost shipping method and mark it standard.

    ``name`` matches the built-in method's own name so an Order filled in
    on this fresh workspace looks identical to one created against a
    normal, pre-seeded Fakturama install.
    """
    editor = open_new_shipping(session)
    editor.set_name(name)
    editor.set_gross_value("0")
    editor.set_as_standard()
    editor.save()
