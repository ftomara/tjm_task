"""Regression test for a real bug found against the live app.

``field_after_label`` finds a field by walking the widget tree from a text
label in document order. It used to lean on pywinauto's own
``.descendants()`` for that walk - which, on the live Fakturama window,
returned an unrelated control (a ComboBox belonging to the window's own
system menu, reading "Close") when asked for "the first ComboBox after the
Date label". ``uia.dump.dump_tree`` uses an explicit ``.children()``
recursion instead, and its output had already been checked by hand against
the running app, so ``_ordered_descendants`` was rewritten to match that
same traversal.

These fakes assert two things: the walk is genuinely depth-first over
``.children()`` (not just "happens to work"), and it never calls
``.descendants()`` at all - if either regresses, this fails without needing
a running instance of Fakturama.
"""

from __future__ import annotations

import re

from fakturama_auto.app.base import Page, escape_special_keys, _ordered_descendants, _value_of


class FakeInfo:
    def __init__(self, control_type: str, name: str | None = None) -> None:
        self.control_type = control_type
        self.name = name
        self.automation_id = None
        self.class_name = None


class FakeElement:
    """A minimal stand-in for a pywinauto element wrapper."""

    def __init__(self, control_type: str, name: str | None = None, children=None) -> None:
        self.element_info = FakeInfo(control_type, name)
        self._children = children or []

    def children(self):
        return self._children

    def descendants(self):
        # field_after_label must never reach this - it is what returned the
        # wrong control on the live app.
        raise AssertionError(
            "field_after_label must not call .descendants(); it must walk .children()"
        )


def build_order_header() -> tuple[FakeElement, FakeElement, FakeElement]:
    """A miniature version of the real 'No. / Date / price-mode' row.

    Structurally identical to what dump-tree showed live: a Date label,
    a nested Pane holding the Date value Edit, and a sibling ComboBox (the
    Net/Gross selector) with no label of its own, all children of one row.
    """
    date_value = FakeElement("Edit")
    date_pane = FakeElement("Pane", children=[date_value])
    date_label = FakeElement("Text", name="Date")
    price_mode_combo = FakeElement("ComboBox")
    row = FakeElement("Pane", children=[date_label, date_pane, price_mode_combo])
    root = FakeElement("Pane", children=[row])
    return root, date_value, price_mode_combo


def test_ordered_descendants_is_depth_first_over_children():
    root, date_value, price_mode_combo = build_order_header()

    ordered = _ordered_descendants(root)

    assert date_value in ordered
    assert price_mode_combo in ordered
    # Document order: the Date label, then its value (nested one level
    # deeper), then the sibling combo that follows it.
    labels = [e.element_info.name for e in ordered]
    assert labels.index("Date") < ordered.index(date_value) < ordered.index(price_mode_combo)


def test_ordered_descendants_never_calls_descendants():
    root, _, _ = build_order_header()
    # Would raise AssertionError if the walk fell back to .descendants().
    _ordered_descendants(root)


def test_field_after_label_finds_the_combo_not_the_edit():
    root, date_value, price_mode_combo = build_order_header()
    page = Page(session=object(), root=root)

    found = page.field_after_label("Date", control_types=("ComboBox",))

    assert found is price_mode_combo
    assert found is not date_value


def test_field_after_label_finds_the_edit_when_asked_for_edit():
    root, date_value, _ = build_order_header()
    page = Page(session=object(), root=root)

    found = page.field_after_label("Date", control_types=("Edit",))

    assert found is date_value


def test_field_after_label_offset_skips_to_the_second_match():
    """Exercises the same offset the Addresses/Items icon lookups depend on."""
    icon_1 = FakeElement("Image")
    icon_2 = FakeElement("Image")
    label = FakeElement("Text", name="Addresses")
    row = FakeElement("Pane", children=[label, icon_1, icon_2])
    page = Page(session=object(), root=row)

    upper = page.field_after_label("Addresses", control_types=("Image",), offset=1)
    lower = page.field_after_label("Addresses", control_types=("Image",), offset=2)

    assert upper is icon_1
    assert lower is icon_2


class FakeComboBox:
    """Reproduces the exact misleading trio confirmed live on this app.

    A combobox named 'VAT' with 'With VAT' selected reads back as:
    window_text() -> 'VAT' (the combo's own name, not its selection);
    .texts() -> ['VAT', 'VAT', 'Close'] ('Close' is the dropdown toggle
    button's own accessible name, not a real option); get_value() doesn't
    exist at all. Only .selected_text() is correct.
    """

    def window_text(self) -> str:
        return "VAT"

    def texts(self) -> list[str]:
        return ["VAT", "VAT", "Close"]

    def selected_text(self) -> str:
        return "With VAT"


class FakeComboBoxWithoutSelectedText:
    """A control with none of the reliable accessors - should still degrade,
    not crash, and should not silently return the misleading value either
    if a later, more specific fix is ever layered on top of this one."""

    def window_text(self) -> str:
        return ""

    def texts(self) -> list[str]:
        return []


def test_value_of_prefers_selected_text_over_the_misleading_fallbacks():
    assert _value_of(FakeComboBox()) == "With VAT"


def test_value_of_still_degrades_gracefully_without_selected_text():
    assert _value_of(FakeComboBoxWithoutSelectedText()) == ""


class FakeEditControl:
    """Reproduces the live app's most dangerous trait: ``set_edit_text()``
    (UIA ValuePattern.SetValue) succeeds - the widget's own text updates and
    every read-back agrees with it - while never reaching Fakturama's
    underlying data-bound model, which only observes real typed keystrokes.
    A field written this way looks completely correct forever and is
    silently dropped the moment the app restarts. ``set_edit_text`` raising
    here stands in for that trap: any code path that calls it has
    regressed, whether or not the read-back check would have caught it.
    """

    def __init__(self) -> None:
        self._text = ""
        self.clicked = False

    def set_edit_text(self, value: str) -> None:
        raise AssertionError(
            "set_text() must never use set_edit_text/ValuePattern - confirmed "
            "live it silently never reaches the underlying model"
        )

    def click_input(self) -> None:
        self.clicked = True

    def type_keys(self, value: str, **kwargs) -> None:
        if value == "^a{DEL}":
            self._text = ""
        else:
            # mirrors real pywinauto: a single char wrapped in braces types
            # as that literal character instead of being read as modifier
            # syntax - the same unescaping escape_special_keys relies on.
            self._text += re.sub(r"\{(.)\}", r"\1", value)

    def window_text(self) -> str:
        return self._text

    def texts(self) -> list[str]:
        return [self._text]


def test_set_text_never_uses_value_pattern_and_writes_via_real_keystrokes():
    control = FakeEditControl()
    page = Page(session=object(), root=control)

    page.set_text(control, "Northstar Office GmbH")

    assert control.clicked, "set_text() must click the control before typing"
    assert control.window_text() == "Northstar Office GmbH"


def test_escape_special_keys_protects_pywinauto_modifier_syntax():
    """Confirmed live: typing 'VAT 19%' landed as 'VAT 19' - pywinauto's
    type_keys() reads a bare '%' as a dangling Alt-modifier prefix, not a
    literal character. Same risk for +^~(){}."""
    assert escape_special_keys("VAT 19%") == "VAT 19{%}"
    assert escape_special_keys("A+B^C~D(E){F}") == "A{+}B{^}C{~}D{(}E{)}{{}F{}}"
    assert escape_special_keys("plain text") == "plain text"


def test_set_text_escapes_percent_so_it_is_not_swallowed():
    control = FakeEditControl()
    page = Page(session=object(), root=control)

    page.set_text(control, "VAT 19%")

    assert control.window_text() == "VAT 19%"
