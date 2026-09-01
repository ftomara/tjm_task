"""The grounding layer: how controls are found, waited for and inspected.

Nothing in here knows anything about Fakturama specifically - that lives in
``app``. Keeping the split means the locator ladder and the waiting primitives
can be reasoned about (and tested) without a running accounting application.
"""

from __future__ import annotations

from .dump import dump_tree, dump_window, list_top_level_windows, write_dump
from .locator import Locator, describe_children, describe_element, matches_text
from .session import FakturamaSession, find_fakturama_exe
from .waits import WaitTimeout, retry, wait_for_stable, wait_until

__all__ = [
    "FakturamaSession",
    "Locator",
    "WaitTimeout",
    "describe_children",
    "describe_element",
    "dump_tree",
    "dump_window",
    "find_fakturama_exe",
    "list_top_level_windows",
    "matches_text",
    "retry",
    "wait_for_stable",
    "wait_until",
    "write_dump",
]
