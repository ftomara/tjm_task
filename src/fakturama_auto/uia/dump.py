"""UIA tree inspection.

This is the single most useful tool when writing page objects against an SWT
application. Before writing a locator you dump the live tree, see exactly what
the accessibility bridge exposes - names, automation ids, control types, and
crucially what it *fails* to expose - and write the locator against reality
instead of against a guess.

    fakturama-auto dump-tree --list
    fakturama-auto dump-tree --window ".*Select the address.*" --depth 6
    fakturama-auto dump-tree --out artifacts/tree.txt

Where a container turns out to be an opaque leaf (Eclipse's NatTable grids are
the usual offender), that is the signal to drop down a rung on the grounding
ladder: keyboard traversal from a named anchor, or runtime OCR over the
control's own bounding box.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

MAX_CHILDREN_PER_NODE = 200


def dump_tree(root: Any, max_depth: int = 12) -> str:
    """Render a UIA subtree as indented text."""
    lines: list[str] = []
    _walk(root, depth=0, max_depth=max_depth, lines=lines)
    return "\n".join(lines)


def _walk(element: Any, *, depth: int, max_depth: int, lines: list[str]) -> None:
    lines.append(("  " * depth) + _render(element))
    if depth >= max_depth:
        return

    try:
        children = list(element.children())
    except Exception as exc:  # noqa: BLE001 - opaque or mutating node
        lines.append(("  " * (depth + 1)) + f"<children unavailable: {exc}>")
        return

    if len(children) > MAX_CHILDREN_PER_NODE:
        lines.append(
            ("  " * (depth + 1)) + f"<{len(children)} children, showing first {MAX_CHILDREN_PER_NODE}>"
        )
        children = children[:MAX_CHILDREN_PER_NODE]

    for child in children:
        _walk(child, depth=depth + 1, max_depth=max_depth, lines=lines)


def _render(element: Any) -> str:
    try:
        info = element.element_info
    except Exception as exc:  # noqa: BLE001
        return f"<unreadable: {exc}>"

    parts = [str(info.control_type or "?")]
    if info.name:
        parts.append(f"name={_trim(info.name)!r}")
    if info.automation_id:
        parts.append(f"auto_id={info.automation_id!r}")
    if info.class_name:
        parts.append(f"class={info.class_name!r}")

    try:
        rect = info.rectangle
        parts.append(f"rect=({rect.left},{rect.top},{rect.right},{rect.bottom})")
    except Exception:  # noqa: BLE001
        pass

    for label, attribute in (("enabled", "is_enabled"), ("visible", "is_visible")):
        try:
            value = getattr(element, attribute)()
            if value is False:
                parts.append(f"{label}=False")
        except Exception:  # noqa: BLE001
            pass

    return " ".join(parts)


def _trim(text: str, limit: int = 80) -> str:
    collapsed = " ".join(text.split())
    return collapsed if len(collapsed) <= limit else collapsed[: limit - 1] + "…"


# --------------------------------------------------------------------------
# Entry points used by the CLI
# --------------------------------------------------------------------------


def list_top_level_windows() -> str:
    """Every visible top-level window, for finding the right --window regex."""
    from pywinauto import Desktop

    lines = []
    for window in Desktop(backend="uia").windows():
        try:
            info = window.element_info
            title = info.name or "<untitled>"
            lines.append(f"{title!r}  class={info.class_name!r}  pid={info.process_id}")
        except Exception as exc:  # noqa: BLE001
            lines.append(f"<unreadable window: {exc}>")
    return "\n".join(sorted(lines)) or "<no top-level windows>"


def dump_window(title_re: str, max_depth: int = 12) -> str:
    """Dump the tree of the first top-level window whose title matches."""
    from pywinauto import Desktop

    window = Desktop(backend="uia").window(title_re=title_re, top_level_only=True)
    if not window.exists():
        raise LookupError(f"no top-level window matching {title_re!r}")
    return dump_tree(window, max_depth=max_depth)


def write_dump(text: str, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path
