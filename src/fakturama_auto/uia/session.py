"""Owning the connection to a running Fakturama instance.

Attaching to an already-running instance is the default. Fakturama is an
Eclipse RCP application with a slow cold start and a workspace lock, so
launching a fresh copy per run makes the develop/test loop painful and can
leave orphaned processes holding the database.
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Any

from ..config import Settings
from ..errors import AutomationError, ControlNotFound
from .locator import describe_children
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

        Dialogs are looked up as top-level windows of the same process rather
        than as descendants of the shell, because SWT reparents them.
        """
        def _find() -> Any:
            window = self._app.window(title_re=title_re, top_level_only=True)
            return window if window.exists() else None

        try:
            return wait_until(_find, timeout=timeout, description=f"dialog matching {title_re!r}")
        except Exception as exc:
            raise ControlNotFound(
                f"dialog matching {title_re!r} did not appear within {timeout:.0f}s.\n"
                f"Main window held:\n{describe_children(self.main_window)}"
            ) from exc

    def dialog_closed(self, title_re: str, timeout: float = 20.0) -> None:
        """Wait for a dialog to disappear (i.e. the click actually took)."""
        wait_until(
            lambda: not self._app.window(title_re=title_re, top_level_only=True).exists(),
            timeout=timeout,
            description=f"dialog matching {title_re!r} to close",
        )

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
