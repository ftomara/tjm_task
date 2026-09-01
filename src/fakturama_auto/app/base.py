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

from typing import Any

from ..errors import AutomationError, ControlNotFound
from ..uia.locator import Locator, describe_children, matches_text
from ..uia.waits import retry, wait_until

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

    def read_text(self, locator: Locator, timeout: float = 10.0) -> str:
        control = locator.find(self.root, timeout=timeout)
        return _value_of(control)

    # -- writing -----------------------------------------------------------

    def set_text(self, locator: Locator, value: str, timeout: float = 10.0) -> None:
        """Type ``value`` into a field and assert it landed.

        Tries the UIA ValuePattern first because it is atomic and immune to
        keyboard focus races. Falls back to select-all-and-type for widgets
        that expose no settable value - a real case in SWT.
        """
        control = locator.find(self.root, timeout=timeout)

        def _write() -> None:
            if not _try_value_pattern(control, value):
                _type_into(control, value)
            actual = _value_of(control)
            if not matches_text(actual, value):
                raise AutomationError(
                    f"{locator.label}: wrote {value!r} but the field reads {actual!r}"
                )

        retry(_write, attempts=3, description=f"setting {locator.label}")

    def click(self, locator: Locator, timeout: float = 10.0) -> Any:
        """Click a control, preferring the Invoke pattern over a synthetic mouse click."""
        control = locator.find(self.root, timeout=timeout)

        def _press() -> Any:
            try:
                control.invoke()
            except Exception:  # noqa: BLE001 - not every SWT control exposes Invoke
                control.click_input()
            return control

        return retry(_press, attempts=3, description=f"clicking {locator.label}")

    def set_checkbox(self, locator: Locator, checked: bool, timeout: float = 10.0) -> None:
        """Drive a checkbox to a desired state and verify it."""
        control = locator.find(self.root, timeout=timeout)

        def _apply() -> None:
            if _is_checked(control) != checked:
                try:
                    control.toggle()
                except Exception:  # noqa: BLE001
                    control.click_input()
            if _is_checked(control) != checked:
                raise AutomationError(
                    f"{locator.label}: could not set checked={checked}"
                )

        retry(_apply, attempts=3, description=f"toggling {locator.label}")

    def select_combo(self, locator: Locator, value: str, timeout: float = 10.0) -> None:
        """Choose an entry in a dropdown by its visible text, and verify it."""
        control = locator.find(self.root, timeout=timeout)

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
                    f"{locator.label}: selected {value!r} but it reads {actual!r}. "
                    f"Available: {self.combo_options(locator)}"
                )

        retry(_apply, attempts=3, description=f"selecting {value!r} in {locator.label}")

    def combo_options(self, locator: Locator, timeout: float = 10.0) -> list[str]:
        """Visible entries of a dropdown - used to explain a failed selection."""
        control = locator.find(self.root, timeout=timeout)
        try:
            return [t for t in control.texts() if t]
        except Exception:  # noqa: BLE001
            return []

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


# --------------------------------------------------------------------------
# Low-level helpers
# --------------------------------------------------------------------------


def _info(element: Any) -> Any | None:
    try:
        return element.element_info
    except Exception:  # noqa: BLE001
        return None


def _ordered_descendants(root: Any) -> list[Any]:
    """Descendants in UIA tree order, which mirrors SWT's widget creation order."""
    try:
        return list(root.descendants())
    except Exception:  # noqa: BLE001
        return []


def _value_of(control: Any) -> str:
    """Best-effort read of a control's current text."""
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


def _try_value_pattern(control: Any, value: str) -> bool:
    """Set a control's value atomically. Returns False if unsupported."""
    try:
        control.set_edit_text(value)
        return True
    except Exception:  # noqa: BLE001
        return False


def _type_into(control: Any, value: str) -> None:
    """Focus, clear, and type - the fallback when ValuePattern is unavailable."""
    control.click_input()
    control.type_keys("^a{DEL}", set_foreground=False)
    # with_spaces keeps multi-word values intact; braces in the text would
    # otherwise be read as pywinauto key codes.
    control.type_keys(value, with_spaces=True, with_newlines=False, set_foreground=False)


def _is_checked(control: Any) -> bool:
    try:
        return bool(control.get_toggle_state())
    except Exception:  # noqa: BLE001
        try:
            return bool(control.is_checked())
        except Exception:  # noqa: BLE001
            return False
