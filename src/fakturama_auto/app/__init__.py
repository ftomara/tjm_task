"""Page objects for individual Fakturama screens and dialogs.

Everything Fakturama-specific lives here. The layer below (``uia``) knows
nothing about accounting; the layer above (``flow``) knows nothing about
widgets.
"""

from __future__ import annotations

from .base import Page

__all__ = ["Page"]
