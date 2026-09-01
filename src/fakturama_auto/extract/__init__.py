"""Image -> OrderDoc extraction providers."""

from __future__ import annotations

from pathlib import Path

from ..config import Settings
from .base import OrderExtractor
from .fixture import FixtureExtractor, save
from .llm_vision import LlmVisionExtractor
from .validate import TOLERANCE, ValidationReport, validate

PROVIDERS = ("llm_vision", "fixture")


def build_extractor(name: str, settings: Settings, fixture_path: Path) -> OrderExtractor:
    """Resolve a provider name to a concrete extractor."""
    if name == "llm_vision":
        return LlmVisionExtractor(settings)
    if name == "fixture":
        return FixtureExtractor(fixture_path)
    raise ValueError(f"unknown extraction provider {name!r}; expected one of {PROVIDERS}")


__all__ = [
    "PROVIDERS",
    "FixtureExtractor",
    "LlmVisionExtractor",
    "OrderExtractor",
    "TOLERANCE",
    "ValidationReport",
    "build_extractor",
    "save",
    "validate",
]
