"""Declarative control grounding.

The brief forbids hardcoded coordinates and fixed layouts, so every control is
described by *what it is* rather than *where it sits*: control type, name,
automation id, class name, and position within a named container.

Fakturama is an Eclipse RCP / SWT application, and SWT's UIA bridge is uneven -
dialogs and buttons expose cleanly, while custom grid widgets can collapse into
a single opaque node. A single strategy is therefore not enough, so a
:class:`Locator` can carry fallbacks:

    ok_button = (
        Locator(control_type="Button", name="OK")
        .or_else(Locator(control_type="Button", name_re=r"(?i)^(ok|okay)$"))
    )

Resolution tries each rung in order and only fails once every rung has. When
it does fail, the error lists what *was* present in the container, which is the
difference between a five-second fix and an afternoon of guessing.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field, replace
from typing import Any, Iterable, Literal

from ..errors import ControlNotFound

Scope = Literal["descendants", "children"]

#: How often to re-query the tree while waiting for a control to appear.
POLL_INTERVAL = 0.25
DEFAULT_TIMEOUT = 15.0


@dataclass(frozen=True)
class Locator:
    """A description of a control, resolved against a live UIA tree."""

    control_type: str | None = None
    name: str | None = None
    name_re: str | None = None
    automation_id: str | None = None
    class_name: str | None = None
    index: int = 0
    scope: Scope = "descendants"
    #: Human-readable label used in logs and error messages.
    describe: str = ""
    #: Alternative strategies, tried in order when this one finds nothing.
    fallbacks: tuple["Locator", ...] = field(default=())

    # -- composition -------------------------------------------------------

    def or_else(self, other: "Locator") -> "Locator":
        """Return a locator that falls back to ``other`` when this finds nothing."""
        return replace(self, fallbacks=self.fallbacks + (other,))

    def at(self, index: int) -> "Locator":
        """Select the nth match instead of the first."""
        return replace(self, index=index)

    def labelled(self, text: str) -> "Locator":
        """Attach a human-readable description for logs and errors."""
        return replace(self, describe=text)

    # -- resolution --------------------------------------------------------

    def find(self, parent: Any, timeout: float = DEFAULT_TIMEOUT) -> Any:
        """Resolve to a single control, polling until ``timeout``.

        Raises :class:`ControlNotFound` with a dump of what the container
        actually held.
        """
        deadline = time.monotonic() + timeout
        while True:
            for rung in self._ladder():
                matches = rung._match(parent)
                if len(matches) > rung.index:
                    return matches[rung.index]
            if time.monotonic() >= deadline:
                break
            time.sleep(POLL_INTERVAL)

        raise ControlNotFound(
            f"could not ground {self.label} within {timeout:.0f}s.\n"
            f"Container held:\n{describe_children(parent)}"
        )

    def find_all(self, parent: Any) -> list[Any]:
        """Every control matching this locator right now (no waiting)."""
        for rung in self._ladder():
            matches = rung._match(parent)
            if matches:
                return matches
        return []

    def exists(self, parent: Any, timeout: float = 0.0) -> bool:
        """Whether the control is present, without raising."""
        try:
            self.find(parent, timeout=timeout)
            return True
        except ControlNotFound:
            return False

    # -- internals ---------------------------------------------------------

    def _ladder(self) -> Iterable["Locator"]:
        yield replace(self, fallbacks=())
        for fallback in self.fallbacks:
            yield from fallback._ladder()

    def _criteria(self) -> dict[str, Any]:
        """Translate to pywinauto's search kwargs."""
        criteria: dict[str, Any] = {}
        if self.control_type:
            criteria["control_type"] = self.control_type
        if self.name is not None:
            criteria["title"] = self.name
        if self.name_re is not None:
            criteria["title_re"] = self.name_re
        if self.automation_id is not None:
            criteria["auto_id"] = self.automation_id
        if self.class_name is not None:
            criteria["class_name"] = self.class_name
        return criteria

    def _match(self, parent: Any) -> list[Any]:
        search = parent.children if self.scope == "children" else parent.descendants
        try:
            return list(search(**self._criteria()))
        except Exception:
            # A tree that mutates mid-walk (very common in SWT while a dialog
            # is opening) surfaces as COM errors. Treat it as "not yet".
            return []

    @property
    def label(self) -> str:
        if self.describe:
            return self.describe
        bits = [
            f"{key}={value!r}"
            for key, value in (
                ("control_type", self.control_type),
                ("name", self.name),
                ("name_re", self.name_re),
                ("automation_id", self.automation_id),
                ("class_name", self.class_name),
            )
            if value is not None
        ]
        rendered = " ".join(bits) or "<any control>"
        if self.index:
            rendered += f" [#{self.index}]"
        return rendered


# --------------------------------------------------------------------------
# Diagnostics
# --------------------------------------------------------------------------


def describe_children(parent: Any, limit: int = 40) -> str:
    """A readable inventory of a container's descendants.

    Appears in every :class:`ControlNotFound` message. On SWT this is usually
    what reveals that a control has an empty name, an unexpected control type,
    or no node at all.
    """
    try:
        found = list(parent.descendants())
    except Exception as exc:  # pragma: no cover - depends on live UI
        return f"  <could not enumerate: {exc}>"

    if not found:
        return "  <no descendants>"

    lines = []
    for element in found[:limit]:
        lines.append("  " + describe_element(element))
    if len(found) > limit:
        lines.append(f"  ... and {len(found) - limit} more")
    return "\n".join(lines)


def describe_element(element: Any) -> str:
    """One-line summary of a control, for logs and tree dumps."""
    try:
        info = element.element_info
        parts = [f"{info.control_type or '?'}"]
        if info.name:
            parts.append(f"name={info.name!r}")
        if info.automation_id:
            parts.append(f"auto_id={info.automation_id!r}")
        if info.class_name:
            parts.append(f"class={info.class_name!r}")
        return " ".join(parts)
    except Exception as exc:  # pragma: no cover - depends on live UI
        return f"<unreadable element: {exc}>"


def matches_text(actual: str | None, expected: str) -> bool:
    """Whitespace- and case-insensitive comparison used by verification steps."""
    return " ".join((actual or "").split()).casefold() == " ".join(expected.split()).casefold()


def compile_name_re(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern)
