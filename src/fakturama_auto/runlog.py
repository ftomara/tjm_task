"""Structured run logging and step-by-step evidence capture.

Every step of the flow is wrapped in :meth:`RunLog.step`, which

* prints a live progress line to the terminal,
* appends a machine-readable record to ``run.jsonl``, and
* captures a screenshot on entry and on failure.

That last point is deliberate: the brief asks for annotated screenshots as a
deliverable, and screenshots produced by hand after the fact drift from what
the code actually did. Making them a by-product of the run means the evidence
and the behaviour cannot disagree.

``run.jsonl`` also gives a failed run a precise post-mortem - which step, with
what inputs, and the screenshot immediately before it went wrong.
"""

from __future__ import annotations

import json
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from rich.console import Console

from .config import Settings
from .errors import ManualReviewRequired


class RunLog:
    """Console + JSONL + screenshot recorder for a single run."""

    def __init__(
        self,
        settings: Settings,
        console: Console | None = None,
        session: Any | None = None,
    ) -> None:
        self.settings = settings
        self.console = console or Console()
        self.session = session
        self._depth = 0

        self._path = settings.run_dir / "run.jsonl"
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self.event("run_started", run_id=settings.run_id, model=settings.model)

    # -- attachment --------------------------------------------------------

    def bind(self, session: Any) -> None:
        """Attach a live session so later steps can capture screenshots."""
        self.session = session

    # -- recording ---------------------------------------------------------

    def event(self, kind: str, message: str = "", **fields: Any) -> None:
        record = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "kind": kind,
            "message": message,
            **fields,
        }
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, default=str) + "\n")

    def screenshot(self, label: str) -> Path | None:
        """Capture the app window, if a session is attached."""
        if self.session is None:
            return None
        try:
            path = self.session.screenshot(label)
        except Exception as exc:  # noqa: BLE001 - evidence must never break the run
            self.event("screenshot_failed", str(exc), label=label)
            return None
        self.event("screenshot", label=label, path=str(path))
        return path

    @contextmanager
    def step(self, title: str, **context: Any) -> Iterator["Step"]:
        """Wrap one meaningful step of the flow."""
        indent = "  " * self._depth
        self.console.print(f"{indent}[cyan]>[/] {title}")
        self.event("step_started", title, depth=self._depth, **context)
        self.screenshot(f"before-{title}")

        step = Step(self, title)
        started = time.monotonic()
        self._depth += 1
        try:
            yield step
        except ManualReviewRequired as exc:
            self._depth -= 1
            self.event(
                "manual_review_required",
                str(exc),
                title=title,
                reason=exc.reason,
                context=exc.context,
                seconds=round(time.monotonic() - started, 2),
            )
            self.screenshot(f"review-{title}")
            self.console.print(f"{indent}[bold yellow]![/] needs review: {exc}")
            raise
        except Exception as exc:
            self._depth -= 1
            self.event(
                "step_failed",
                str(exc),
                title=title,
                error_type=type(exc).__name__,
                seconds=round(time.monotonic() - started, 2),
            )
            self.screenshot(f"failed-{title}")
            self.console.print(f"{indent}[bold red]x[/] {title}: {exc}")
            raise
        else:
            self._depth -= 1
            elapsed = round(time.monotonic() - started, 2)
            self.event("step_finished", title, seconds=elapsed, notes=step.notes)
            detail = f" - {'; '.join(step.notes)}" if step.notes else ""
            self.console.print(f"{indent}[green]v[/] {title} ({elapsed}s){detail}")

    # -- summary -----------------------------------------------------------

    def finish(self, outcome: str, **fields: Any) -> None:
        self.event("run_finished", outcome=outcome, **fields)
        self.console.print(f"\nRun [bold]{self.settings.run_id}[/] finished: [bold]{outcome}[/]")
        self.console.print(f"Evidence: [green]{self.settings.run_dir}[/]")

    @property
    def path(self) -> Path:
        return self._path


class Step:
    """Handle passed into a ``with runlog.step(...)`` block."""

    def __init__(self, log: RunLog, title: str) -> None:
        self._log = log
        self._title = title
        self.notes: list[str] = []

    def note(self, message: str, **fields: Any) -> None:
        """Record a decision made inside this step."""
        self.notes.append(message)
        self._log.event("note", message, step=self._title, **fields)

    def capture(self, label: str) -> Path | None:
        return self._log.screenshot(f"{self._title}-{label}")
