"""Owning the connection to a running Fakturama instance.

Attaching to an already-running instance is the default. Fakturama is an
Eclipse RCP application with a slow cold start and a workspace lock, so
launching a fresh copy per run makes the develop/test loop painful and can
leave orphaned processes holding the database.
"""

from __future__ import annotations

import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from ..config import Settings
from ..errors import AutomationError, ControlNotFound
from .locator import Locator, describe_children
from .waits import wait_until

#: The shell's window title varies by version and by which editor is focused,
#: so match loosely on the product name.
MAIN_WINDOW_RE = r".*[Ff]akturama.*"

#: Where Fakturama typically lands on Windows.
INSTALL_HINTS = (
    r"C:\Program Files\Fakturama2",
    r"C:\Program Files (x86)\Fakturama2",
    r"C:\Program Files\Fakturama",
    r"C:\Program Files (x86)\Fakturama",
    r"C:\Fakturama2",
    r"C:\Fakturama",
)


def find_fakturama_exe(explicit: Path | None = None) -> Path:
    """Locate ``Fakturama.exe``.

    Order: an explicit setting, then the usual install directories, then a
    shallow scan of the user's Programs folder.
    """
    if explicit:
        if explicit.exists():
            return explicit
        raise AutomationError(f"FAKTURAMA_EXE points at a missing file: {explicit}")

    candidates: list[Path] = []
    for hint in INSTALL_HINTS:
        candidates.extend(Path(hint).glob("Fakturama*.exe"))

    local_programs = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs"
    if local_programs.exists():
        candidates.extend(local_programs.glob("Fakturama*/Fakturama*.exe"))

    if not candidates:
        raise AutomationError(
            "Could not find Fakturama.exe. Install it from https://www.fakturama.info/download/ "
            "or set FAKTURAMA_EXE in .env to the full path."
        )
    return candidates[0]


class FakturamaSession:
    """A live connection to the Fakturama shell window."""

    def __init__(self, app: Any, settings: Settings) -> None:
        self._app = app
        self._settings = settings
        self._shot_index = 0

    # -- construction ------------------------------------------------------

    @classmethod
    def attach(cls, settings: Settings, timeout: float = 20.0) -> "FakturamaSession":
        """Connect to an already-running Fakturama."""
        from pywinauto.application import Application

        app = Application(backend="uia")
        try:
            app.connect(title_re=MAIN_WINDOW_RE, timeout=timeout)
        except Exception as exc:
            raise AutomationError(
                "No running Fakturama window found. Start Fakturama first, or use "
                "`--launch` to have the runner start it."
            ) from exc
        return cls(app, settings)

    @classmethod
    def launch(cls, settings: Settings, timeout: float = 180.0) -> "FakturamaSession":
        """Start Fakturama and wait for its shell window.

        The timeout is generous on purpose: an Eclipse RCP cold start with a
        database migration can take well over a minute.
        """
        from pywinauto.application import Application

        exe = find_fakturama_exe(settings.fakturama_exe)
        app = Application(backend="uia")
        app.start(str(exe), timeout=timeout)
        session = cls(app, settings)
        session.wait_ready(timeout=timeout)
        return session

    @classmethod
    def attach_or_launch(cls, settings: Settings) -> "FakturamaSession":
        try:
            return cls.attach(settings, timeout=5.0)
        except AutomationError:
            return cls.launch(settings)

    # -- windows -----------------------------------------------------------

    @property
    def main_window(self) -> Any:
        """The Fakturama shell window."""
        return self._app.window(title_re=MAIN_WINDOW_RE, top_level_only=True)

    def wait_ready(self, timeout: float = 60.0) -> Any:
        window = wait_until(
            lambda: self.main_window if self.main_window.exists() else None,
            timeout=timeout,
            description="the Fakturama main window",
        )
        window.wait("ready", timeout=timeout)
        return window

    def focus(self) -> Any:
        """Bring Fakturama to the foreground and return the shell window."""
        window = self.main_window
        try:
            window.set_focus()
        except Exception:  # noqa: BLE001 - focus races with dialogs; not fatal
            pass
        return window

    def dialog(self, title_re: str, timeout: float = 20.0) -> Any:
        """Wait for a modal dialog and return it.

        Two genuinely different mechanisms have each been confirmed live for
        different dialogs on this app, and neither covers both cases:

        * The main shell window itself is only reliably found via
          ``self._app.window(...)`` (an ``Application`` scoped at
          ``attach()`` time) - a plain ``Desktop`` lookup for it also works.
        * The 'Select the address' dialog, despite rendering as a fully
          independent floating window with its own titlebar, does **not**
          show up in ``Desktop(backend="uia").windows()`` at all - it only
          appears as a ``Window``-typed descendant of the main shell. SWT
          apparently reparents at least some of its dialogs into the shell's
          own accessibility subtree rather than registering them as
          Desktop-level top-level windows.

        Rather than guess which any given dialog will be, this tries both
        rungs on every poll and returns whichever answers first.
        """
        from pywinauto import Desktop

        pattern = re.compile(title_re, re.IGNORECASE)

        def _find() -> Any:
            for window in Desktop(backend="uia").windows():
                if window.is_visible() and pattern.match(window.element_info.name or ""):
                    return window
            for window in self.main_window.descendants(control_type="Window"):
                if pattern.match(window.element_info.name or ""):
                    return window
            return None

        try:
            return wait_until(_find, timeout=timeout, description=f"dialog matching {title_re!r}")
        except Exception as exc:
            raise ControlNotFound(
                f"dialog matching {title_re!r} did not appear within {timeout:.0f}s.\n"
                f"Main window held:\n{describe_children(self.main_window)}"
            ) from exc

    def dialog_closed(self, title_re: str, timeout: float = 20.0) -> None:
        """Wait for a dialog to disappear (i.e. the click actually took).

        Checks both rungs :meth:`dialog` does, for the same reason.
        """
        from pywinauto import Desktop

        pattern = re.compile(title_re, re.IGNORECASE)

        def _still_open() -> bool:
            for window in Desktop(backend="uia").windows():
                if window.is_visible() and pattern.match(window.element_info.name or ""):
                    return True
            for window in self.main_window.descendants(control_type="Window"):
                if pattern.match(window.element_info.name or ""):
                    return True
            return False

        wait_until(
            lambda: not _still_open(),
            timeout=timeout,
            description=f"dialog matching {title_re!r} to close",
        )

    def close_all_tabs(self) -> None:
        """Close every open editor tab via Ctrl+Shift+W (File > Close All's own shortcut).

        Confirmed live: plain Ctrl+W does nothing on this app - no exception,
        no tab closes, even sent several times in a row - this Eclipse build
        evidently doesn't route it anywhere. The menu itself confirms the
        real binding: its "Close All" item's own accessible name is literally
        "Close All\tCtrl+Shift+W". Sending that combo directly closes every
        editor tab (Order, Debtor, Product, VAT, Shipping, Invoice, and their
        own sub-tabs) in one shot with no "Save changes?" prompt, since
        everything on this path has already been saved by the time this is
        called - clicking through the File menu to reach the same command
        was tried first and is worse: it is one more place a stray click can
        land on the wrong item while the menu is transiently open. The
        dashboard/list tabs (Documents, Debtors, Products, VATs, Shippings,
        terms of payment) are left untouched either way - confirmed live.
        """
        window = self.focus()
        window.type_keys("^+w")

    def activate_tab(self, content_pane_name: str, timeout: float = 15.0) -> Any:
        """Bring a previously-opened editor tab to the front and return its content.

        Confirmed live, and expensive to learn the hard way: an editor
        tab's own content is torn down from the UIA tree **entirely**
        while a different tab is focused - not merely hidden. A script
        that opens 'New Order', then does other work in a second tab, then
        tries to re-resolve the Order's content pane by name alone will get
        ``ControlNotFound`` even though the tab is still open and its data
        is intact; the content simply does not exist in the tree until its
        tab is the active one again. Every page object that outlives a
        single focused moment needs this, not just a bare name lookup.

        The tab's own title may carry Fakturama's unsaved-changes ``*``
        prefix (``*New Debtor`` vs. ``New Debtor``) depending on whether
        anything has been typed into it yet, so the TabItem is matched
        loosely on that while the returned content pane is still looked up
        by the exact, unprefixed name - confirmed live that the content
        pane itself is never prefixed, only the tab header is.
        """
        window = self.focus()
        tab_item = Locator(
            control_type="TabItem", name_re=rf"^\*?{re.escape(content_pane_name)}$"
        ).labelled(f"{content_pane_name!r} tab header").find(window, timeout=timeout)
        tab_item.click_input()
        return Locator(control_type="Pane", name=content_pane_name).labelled(
            f"{content_pane_name!r} editor content"
        ).find(window, timeout=timeout)

    # -- evidence ----------------------------------------------------------

    def screenshot(self, label: str) -> Path:
        """Capture the shell window into the run's artifact directory.

        Every meaningful step calls this, so the "annotated screenshots"
        deliverable is a by-product of a normal run rather than a manual
        chore afterwards.
        """
        self._shot_index += 1
        safe = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in label).strip("-")
        target = self._settings.run_dir / "screenshots" / f"{self._shot_index:02d}-{safe}.png"
        target.parent.mkdir(parents=True, exist_ok=True)

        try:
            image = self.main_window.capture_as_image()
        except Exception:  # noqa: BLE001 - fall back to the whole desktop
            from PIL import ImageGrab

            image = ImageGrab.grab()

        image.save(target)
        return target

    @property
    def settings(self) -> Settings:
        return self._settings

    @property
    def app(self) -> Any:
        return self._app

    def __repr__(self) -> str:
        stamp = datetime.now().strftime("%H:%M:%S")
        return f"<FakturamaSession run={self._settings.run_id} at {stamp}>"
