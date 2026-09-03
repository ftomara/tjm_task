"""Image -> OrderDoc extraction providers.

Adding a provider means adding one module and one line in :data:`PROVIDERS`.
Nothing downstream changes, because everything past this package consumes a
validated :class:`~fakturama_auto.models.OrderDoc` and does not care where it
came from.
"""

from __future__ import annotations

from pathlib import Path

from ..config import Settings
from .base import OrderExtractor
from .fixture import FixtureExtractor, save
from .validate import TOLERANCE, ValidationReport, validate

#: Selectable via ``--provider``. ``gemini`` is the default.
PROVIDERS = ("gemini", "fixture")


def build_extractor(name: str, settings: Settings, fixture_path: Path) -> OrderExtractor:
    """Resolve a provider name to a concrete extractor.

    Imported lazily so that an unused provider's SDK never has to be installed
    or configured just to run the others.
    """
    if name == "gemini":
        from .gemini_vision import GeminiVisionExtractor

        return GeminiVisionExtractor(settings)
    if name == "fixture":
        return FixtureExtractor(fixture_path)
    raise ValueError(f"unknown extraction provider {name!r}; expected one of {PROVIDERS}")


__all__ = [
    "PROVIDERS",
    "FixtureExtractor",
    "OrderExtractor",
    "TOLERANCE",
    "ValidationReport",
    "build_extractor",
    "save",
    "validate",
]
