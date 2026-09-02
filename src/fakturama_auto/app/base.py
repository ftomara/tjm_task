"""Shared behaviour for Fakturama page objects.

Two things here earn their keep against an SWT application:

:meth:`Page.set_text` **verifies what it typed.** Eclipse text fields
reformat, auto-complete and occasionally swallow keystrokes, and this is an
accounting system - a silently truncated price is far worse than a crash. Every
write is read back and asserted.

:meth:`Page.field_after_label` grounds a field by the label *next to* it rather
than by position. SWT rarely gives text inputs a useful name or automation id,
but the label beside them is real, visible text. Walking the container's child
order from that label is layout-independent: it survives the window being
resized, themed, or re-laid-out, which a coordinate offset does not.
"""

from __future__ import annotations

from datetime import date as date_type
from datetime import datetime
from typing import Any, Callable

from ..errors import AutomationError, ControlNotFound
from ..uia.locator import Locator, describe_children, describe_element, matches_text
from ..uia.waits import retry, wait_until

# Every write/click/select method below accepts either a Locator (resolved
# against self.root) or an already-resolved element - the latter is what
# field_after_label() returns, and forcing a caller to wrap it back in a
# Locator just to act on it would be pure ceremony. See _resolve().

#: Control types SWT uses for editable single-line fields.
EDITABLE_TYPES = ("Edit", "ComboBox", "Document")

#: Control types SWT uses for static labels.
LABEL_TYPES = ("Text", "Static")


class Page:
    """Base class for a screen, editor or dialog."""

    def __init__(self, session: Any, root: Any) -> None:
        self.session = session
        self.root = root

    # -- reading -----------------------------------------------------------

    def read_text(self, target: Any, timeout: float = 10.0) -> str:
        control, _ = self._resolve(target, timeout)
        return _value_of(control)

    # -- writing -----------------------------------------------------------

    def set_text(
        self,
        target: Any,
        value: str,
        timeout: float = 10.0,
        verify: Any = None,
    ) -> None:
        """Type ``value`` into a field and assert it landed.

        Always drives this through real keystrokes (``_type_into``), never
        through the UIA ValuePattern (``set_edit_text``). Confirmed live,
        the hard way: ValuePattern.SetValue updates this app's Edit widgets
        visually - the widget reads back correctly forever, including
        immediately after a save - while silently never reaching the
        underlying SWT/JFace model, because it bypasses the native
        keystroke pipeline those bindings listen on (Modify/FocusOut
        events). A field written this way looks completely correct in
        every read-back check and is dropped the moment the app restarts.
        Real typed keystrokes fire those events as they land, the same way
        a user's typing does, and that is what this app's data-binding
        actually observes.

        ``verify`` overrides the default exact-text check (``matches_text``)
        for fields that legitimately reformat what was typed - the date
        field takes ISO text and redisplays it as ``"Jul 14, 2026"``, which
        is the same date, not a wrong write. Pass a ``(actual, value) ->
        bool`` callable for those cases instead of loosening the default for
        everyone.
        """
        control, label = self._resolve(target, timeout)
        check = verify or matches_text

        def _write() -> None:
            _type_into(control, value)
            actual = _value_of(control)
            if not check(actual, value):
                raise AutomationError(f"{label}: wrote {value!r} but the field reads {actual!r}")

        retry(_write, attempts=3, description=f"setting {label}")

    def click(self, target: Any, timeout: float = 10.0) -> Any:
        """Click a control, preferring the Invoke pattern over a synthetic mouse click."""
        control, label = self._resolve(target, timeout)

        def _press() -> Any:
            try:
                control.invoke()
            except Exception:  # noqa: BLE001 - not every SWT control exposes Invoke
                control.click_input()
            return control

        return retry(_press, attempts=3, description=f"clicking {label}")

    def set_checkbox(self, target: Any, checked: bool, timeout: float = 10.0) -> None:
        """Drive a checkbox to a desired state and verify it."""
        control, label = self._resolve(target, timeout)

        def _apply() -> None:
            if _is_checked(control) != checked:
                try:
                    control.toggle()
                except Exception:  # noqa: BLE001
                    control.click_input()
            if _is_checked(control) != checked:
                raise AutomationError(f"{label}: could not set checked={checked}")

        retry(_apply, attempts=3, description=f"toggling {label}")

    def select_combo(self, target: Any, value: str, timeout: float = 10.0) -> None:
        """Choose an entry in a dropdown by its visible text, and verify it."""
        control, label = self._resolve(target, timeout)

        def _apply() -> None:
            try:
                control.select(value)
            except Exception:  # noqa: BLE001 - fall back to expanding and clicking
                control.expand()
                item = Locator(control_type="ListItem", name=value).find(control, timeout=5.0)
                item.click_input()
            actual = _value_of(control)
            if not matches_text(actual, value):
                raise AutomationError(
                    f"{label}: selected {value!r} but it reads {actual!r}. "
                    f"Available: {self.combo_options(control)}"
                )

        retry(_apply, attempts=3, description=f"selecting {value!r} in {label}")

    def combo_options(self, target: Any, timeout: float = 10.0) -> list[str]:
        """The real selectable entries of a dropdown - used to explain a failed selection.

        A collapsed combo's ``.texts()`` does not enumerate its options on
        this app (confirmed live: it returns the combo's own name twice plus
        the dropdown toggle button's name - never the actual list). The list
        only exists once the dropdown is open, as ``ListItem`` children, so
        this expands it, reads those, and collapses it again rather than
        leaving the UI in a state this call didn't ask for.
        """
        control, _ = self._resolve(target, timeout)
        try:
            control.expand()
            items = [item.window_text() for item in Locator(control_type="ListItem").find_all(control)]
            return [i for i in items if i]
        except Exception:  # noqa: BLE001
            return []
        finally:
            try:
                control.collapse()
            except Exception:  # noqa: BLE001
                pass

    def _resolve(self, target: Any, timeout: float) -> tuple[Any, str]:
        """Accept either a Locator (resolved against self.root) or an already-found element."""
        if isinstance(target, Locator):
            return target.find(self.root, timeout=timeout), target.label
        return target, describe_element(target)

    # -- structural grounding ---------------------------------------------

    def field_after_label(
        self,
        label: str,
        *,
        control_types: tuple[str, ...] = EDITABLE_TYPES,
        offset: int = 1,
        container: Any = None,
    ) -> Any:
        """Find the input that follows a visible label in the widget order.

        SWT lays a form out as alternating label/field siblings. Rather than
        guessing an automation id that usually is not there, this finds the
        label by its text and takes the ``offset``-th following control of an
        editable type.
        """
        root = container if container is not None else self.root
        elements = _ordered_descendants(root)

        for index, element in enumerate(elements):
            info = _info(element)
            if info is None or info.control_type not in LABEL_TYPES:
                continue
            if not matches_text(info.name, label) and not matches_text(
                (info.name or "").rstrip(":*").strip(), label
            ):
                continue

            seen = 0
            for candidate in elements[index + 1 :]:
                candidate_info = _info(candidate)
                if candidate_info is None:
                    continue
                if candidate_info.control_type in control_types:
                    seen += 1
                    if seen == offset:
                        return candidate

        raise ControlNotFound(
            f"no editable field found after a label reading {label!r}.\n"
            f"Container held:\n{describe_children(root)}"
        )

    # -- waiting -----------------------------------------------------------

    def wait_for(self, locator: Locator, timeout: float = 15.0) -> Any:
        return wait_until(
            lambda: locator.find_all(self.root)[locator.index : locator.index + 1] or None,
            timeout=timeout,
            description=locator.label,
        )[0]


def click_and_await_pane(
    window: Any,
    trigger: Locator,
    pane_name: str,
    *,
    trigger_timeout: float = 10.0,
    pane_timeout: float = 15.0,
    attempts: int = 3,
) -> Any:
    """Click ``trigger`` and wait for the editor Pane it opens.

    Every "New Order" / "New Debtor" / "Create a new X" action in this app
    follows this exact shape, and retries the *whole* click-and-wait cycle,
    not just the click. Confirmed live: the very first such action against a
    just-launched Fakturama can silently drop its click - no exception, no
    resulting tab, and no amount of waiting afterward recovers it - while
    the identical action against an already-warm instance works first try.
    Retrying only the click would keep pressing the same already-resolved
    button reference; re-finding ``trigger`` from scratch on each attempt is
    what actually recovers, since by the second attempt the app has finished
    whatever startup work made the first click land nowhere.
    """

    def _press_and_wait() -> Any:
        control = trigger.find(window, timeout=trigger_timeout)
        control.click_input()
        return Locator(control_type="Pane", name=pane_name).labelled(
            f"{pane_name!r} editor content"
        ).find(window, timeout=pane_timeout)

    return retry(_press_and_wait, attempts=attempts, delay=1.0, description=f"opening {pane_name!r}")


# --------------------------------------------------------------------------
# Low-level helpers
# --------------------------------------------------------------------------


def _info(element: Any) -> Any | None:
    try:
        return element.element_info
    except Exception:  # noqa: BLE001
        return None


def _ordered_descendants(root: Any) -> list[Any]:
    """Descendants in depth-first document order.

    Deliberately NOT pywinauto's own ``.descendants()``: confirmed live on
    this SWT tree that it does not preserve document order the way an
    explicit ``.children()`` walk does - a search for "the first ComboBox
    after the Date label" returned an unrelated combo reading "Close",
    which is exactly what the window's own system menu (Restore / Move /
    Size / ... / Close) would produce if a raw-view walk surfaces it.
    ``uia.dump.dump_tree`` uses this same recursive ``.children()`` walk and
    its output has been checked by hand against the live app, so this
    mirrors the one traversal already known to match what is on screen.
    """
    ordered: list[Any] = []

    def _walk(element: Any) -> None:
        try:
            children = list(element.children())
        except Exception:  # noqa: BLE001
            return
        for child in children:
            ordered.append(child)
            _walk(child)

    try:
        _walk(root)
    except Exception:  # noqa: BLE001
        pass
    return ordered


def _value_of(control: Any) -> str:
    """Best-effort read of a control's current text.

    ComboBox needs its own first move. Checked live on this app: its
    ``get_value()`` doesn't exist (pywinauto's ``ComboBoxWrapper`` never
    defines it); ``window_text()`` returns the combo's own accessible NAME,
    not its selection ("With VAT" reads back as "VAT"); and ``.texts()``'s
    last entry is the dropdown toggle button's own accessible name ("Close")
    on every combo checked, not a real option. All three are individually
    plausible and all three are wrong. ``.selected_text()`` is the pywinauto
    API built for exactly this and reads correctly, so it goes first
    whenever the control exposes it.
    """
    selected_text = getattr(control, "selected_text", None)
    if callable(selected_text):
        try:
            value = selected_text()
            if value:
                return str(value)
        except Exception:  # noqa: BLE001
            pass

    for reader in (
        lambda: control.get_value(),
        lambda: control.window_text(),
        lambda: "\n".join(t for t in control.texts() if t),
    ):
        try:
            value = reader()
            if value:
                return str(value)
        except Exception:  # noqa: BLE001
            continue
    return ""


#: pywinauto's type_keys() reads these as modifier/grouping syntax
#: (``%`` = Alt, ``^`` = Ctrl, ``+`` = Shift, ``~`` = Enter, ``()``/``{}`` for
#: grouping) rather than literal characters. Confirmed live: typing the VAT
#: rate name "VAT 19%" landed as "VAT 19" - the trailing '%' was consumed as
#: a dangling Alt-modifier prefix and never appeared. Every one of these
#: needs the same brace-escape pywinauto documents for sending them as text.
_SPECIAL_KEYS = "+^%~(){}"


def escape_special_keys(value: str) -> str:
    return "".join(f"{{{ch}}}" if ch in _SPECIAL_KEYS else ch for ch in value)


def _type_into(control: Any, value: str) -> None:
    """Focus, clear, and type - real keystrokes, not the UIA ValuePattern."""
    control.click_input()
    control.type_keys("^a{DEL}", set_foreground=False)
    # with_spaces keeps multi-word values intact; special pywinauto syntax
    # characters are escaped first so a literal '%', '+', etc. in the value
    # types as itself instead of being read as a modifier/grouping key.
    control.type_keys(
        escape_special_keys(value), with_spaces=True, with_newlines=False, set_foreground=False
    )


def _is_checked(control: Any) -> bool:
    try:
        return bool(control.get_toggle_state())
    except Exception:  # noqa: BLE001
        try:
            return bool(control.is_checked())
        except Exception:  # noqa: BLE001
            return False


def date_verifier(target: date_type) -> Callable[[str, str], bool]:
    """A ``set_text`` ``verify`` callable for date fields.

    Fakturama accepts ISO text but redisplays it in a locale format
    ("2026-07-14" -> "Jul 14, 2026") once real keystrokes trigger the
    field's own reformat-on-input behaviour - the same date, not a wrong
    write - so this parses the read-back instead of comparing it as a
    literal string. Shared by the Order and Invoice date fields, which hit
    the exact same widget behaviour.
    """

    def _same_date(actual: str, _expected: str) -> bool:
        for fmt in ("%b %d, %Y", "%Y-%m-%d", "%m/%d/%Y", "%d.%m.%Y"):
            try:
                return datetime.strptime(actual.strip(), fmt).date() == target
            except ValueError:
                continue
        return False

    return _same_date
