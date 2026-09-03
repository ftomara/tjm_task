"""Runtime configuration, resolved once and passed around explicitly."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

#: Repo root: .../src/fakturama_auto/config.py -> up three levels.
PROJECT_ROOT = Path(__file__).resolve().parents[2]

DEFAULT_ORDER_IMAGE = PROJECT_ROOT / "assets" / "order_input.png"

#: A trusted extraction, committed so the UI flow can be replayed offline.
DEFAULT_FIXTURE = PROJECT_ROOT / "assets" / "extraction.fixture.json"

MAX_IMAGE_LONG_EDGE = 1568


@dataclass(frozen=True)
class Settings:
    gemini_api_key: str | None
    gemini_model: str
    model: str
    fakturama_exe: Path | None
    artifacts_dir: Path
    run_id: str

    @property
    def run_dir(self) -> Path:
        """Per-run artifact directory (screenshots, logs, extraction JSON)."""
        return self.artifacts_dir / self.run_id


def load_settings(run_id: str | None = None) -> Settings:
    """Read configuration from ``.env`` and the environment.

    ``.env`` never overrides a variable that is already exported, so a shell
    export wins over the file - the usual precedence people expect.
    """
    load_dotenv(PROJECT_ROOT / ".env", override=False)

    exe = os.environ.get("FAKTURAMA_EXE")
    return Settings(
        gemini_api_key=os.environ.get("GEMINI_API_KEY"),
        gemini_model=os.environ.get("GEMINI_MODEL", "gemini-3.7-flash"),
        model=os.environ.get("FAKTURAMA_AUTO_MODEL", "claude-opus-5"),
        fakturama_exe=Path(exe) if exe else None,
        artifacts_dir=PROJECT_ROOT / "artifacts",
        run_id=run_id or datetime.now().strftime("%Y%m%d-%H%M%S"),
    )
