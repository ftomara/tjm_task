"""The 'Documents' list (Data > Documents) - every saved Order and Invoice.

Grounded live against a real run's output. The grid itself renders no rows
in the UIA tree at all (confirmed live, even with real data loaded) - the
same opaque-table pattern already documented for the Items grid in
``order_editor.py`` and the address/product selector dialogs. Rows are
therefore reached the same way: a coordinate offset from a grounded,
*named* anchor (the "Documents" content Pane itself), not from anything
tied to this run's own automation ids - those are reassigned by Fakturama
on every launch and confirmed live to differ between runs, so hardcoding
one would break the very next session.

The grid sorts newest-first by each document's own date (an Invoice's
date defaults to today, an Order's to whatever the source document says),
so the two rows most likely to be "the one just created" are the topmost
ones - which is what :meth:`DocumentsView.highlight_last` selects. This is
a demo/verification convenience, not a search-driven exact match: a
workspace with unrelated, more-recently-dated documents already in it
could push the just-created rows out of the top two.
"""

from __future__ import annotations

from typing import Any

from ..uia.locator import Locator
from .base import Page, click_and_await_pane

DOCUMENTS_LINK = Locator(control_type="Text", name="Documents").labelled(
    "left-panel Documents link"
)


def open_documents(session: Any) -> "DocumentsView":
    window = session.focus()
    content = click_and_await_pane(window, DOCUMENTS_LINK, "Documents")
    return DocumentsView(session, content)


class DocumentsView(Page):
    """Wraps the Pane opened by the left panel's 'Documents' link."""

    #: Confirmed live: identical row height to the Items grid (order_editor.py) -
    #: apparently a shared Fakturama table style, not a coincidence.
    _ROW_HEIGHT = 25
    _FIRST_ROW_X = 285
    _FIRST_ROW_Y = 83

    def highlight_last(self, count: int = 2) -> None:
        """Select the topmost ``count`` rows (a contiguous Shift-click range).

        Confirmed live: this grid's rows support Shift-click range selection
        but *not* Ctrl-click toggle selection (a Ctrl-click here replaces the
        selection instead of adding to it) - so a range from row 0 is the
        only way to highlight more than one row at once.
        """
        if count < 1:
            return
        self.root.click_input(coords=(self._FIRST_ROW_X, self._FIRST_ROW_Y))
        if count > 1:
            last_y = self._FIRST_ROW_Y + self._ROW_HEIGHT * (count - 1)
            self.root.click_input(coords=(self._FIRST_ROW_X, last_y), pressed="shift")
