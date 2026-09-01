"""The extraction seam.

Everything downstream of this module consumes an :class:`OrderDoc` and does
not care whether it came from a vision model, an OCR pass, or a checked-in
fixture. Keeping that seam narrow is what lets the UI automation be developed
and replayed without burning an API call on every iteration.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from ..models import OrderDoc


class OrderExtractor(Protocol):
    """Turns an image of an order into a normalised :class:`OrderDoc`."""

    name: str

    def extract(self, image_path: Path) -> OrderDoc:  # pragma: no cover - protocol
        ...
