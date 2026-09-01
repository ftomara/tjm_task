"""Replay a previously extracted order from disk.

The UI automation needs dozens of iterations to get right, and each one would
otherwise re-read the same unchanging image through a paid API call. Once the
vision pass has produced an extraction you trust, save it and point the runner
at the fixture instead: same :class:`OrderDoc`, no network, deterministic.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..errors import ExtractionError
from ..models import OrderDoc


class FixtureExtractor:
    """Load an :class:`OrderDoc` that was serialised by an earlier run."""

    name = "fixture"

    def __init__(self, fixture_path: Path) -> None:
        self._fixture_path = fixture_path

    def extract(self, image_path: Path) -> OrderDoc:  # noqa: ARG002 - signature is the seam
        if not self._fixture_path.exists():
            raise ExtractionError(
                f"no extraction fixture at {self._fixture_path}. "
                "Run `extract` with the llm_vision provider first."
            )
        try:
            payload = self._fixture_path.read_text(encoding="utf-8")
            return OrderDoc.model_validate_json(payload)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            raise ExtractionError(f"could not load fixture {self._fixture_path}: {exc}") from exc


def save(doc: OrderDoc, path: Path) -> Path:
    """Serialise an :class:`OrderDoc` so a later run can replay it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(doc.model_dump_json(indent=2), encoding="utf-8")
    return path
