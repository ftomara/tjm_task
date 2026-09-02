"""Orchestration: sequences the ``app`` page objects into the brief's flow.

This layer knows nothing about UIA - every interaction goes through a page
object in ``app``. Its only job is sequencing and the handful of decisions
the brief calls out explicitly (skip a payment method that already exists,
skip a delivery address that matches billing, and so on).
"""

from __future__ import annotations

from .order_flow import FlowResult, run_order_flow

__all__ = ["FlowResult", "run_order_flow"]
