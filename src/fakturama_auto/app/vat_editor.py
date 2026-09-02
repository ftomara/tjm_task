"""The 'New TAX Rate' editor, and the list it's created from.

Grounded live against a fresh workspace with only the built-in 'Tax-free'
0% rate present. Every field here is cleanly named - no structural
workarounds needed, unlike the Order/Contact editors.
"""

from __future__ import annotations

from typing import Any

from ..uia.locator import Locator
from .base import Page, click_and_await_pane

VATS_LINK = Locator(control_type="Text", name="VATs").labelled("left-panel VATs link")

CREATE_BUTTON = Locator(control_type="Button", name="Create a new tax rate").labelled(
    "Create a new tax rate button"
)

NAME = Locator(control_type="Edit", name="Name").labelled("VAT rate Name field")
VALUE = Locator(control_type="Edit", name="Value").labelled("VAT rate Value field")


def open_vats_list(session: Any) -> Any:
    window = session.focus()
    return click_and_await_pane(window, VATS_LINK, "VATs")


def open_new_vat_rate(session: Any) -> "VatEditor":
    """The list's own toolbar button lives beside it, not inside its content pane."""
    open_vats_list(session)
    window = session.focus()
    content = click_and_await_pane(window, CREATE_BUTTON, "New TAX Rate")
    return VatEditor(session, content)


class VatEditor(Page):
    """Wraps the tab opened by the VATs list's 'Create a new tax rate' button."""

    def set_name(self, name: str) -> None:
        self.set_text(NAME, name)

    def set_value(self, percent: str) -> None:
        """``percent`` as printed, e.g. ``"19%"`` - a bare '%' round-trips
        correctly now that ``set_text`` escapes pywinauto's special keys."""
        self.set_text(VALUE, percent)

    def save(self) -> None:
        """See OrderEditor.save() - the button lives in the shared top ribbon."""
        from ..errors import ControlNotFound

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


def create_vat_rate(session: Any, name: str, percent: str) -> None:
    """Create and save a VAT rate, e.g. ``create_vat_rate(session, "VAT 19%", "19%")``."""
    editor = open_new_vat_rate(session)
    editor.set_name(name)
    editor.set_value(percent)
    editor.save()
