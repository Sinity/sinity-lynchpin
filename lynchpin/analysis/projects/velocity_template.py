"""Loads the velocity dashboard HTML asset.

The HTML body used to live inline in this file as a 1489-line
``HTML_TEMPLATE`` string. It now lives in ``assets/velocity.html`` so
that editors lint it as HTML, diffs for template edits no longer bury
the rest of the analysis layer, and per-PR review surfaces only the
actual change. The Python here is a one-line loader; callers continue
to ``from .velocity_template import HTML_TEMPLATE``.
"""

from __future__ import annotations

from pathlib import Path

HTML_TEMPLATE = (Path(__file__).parent / "assets" / "velocity.html").read_text(encoding="utf-8")
