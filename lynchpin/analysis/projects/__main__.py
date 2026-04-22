"""Allow ``python -m lynchpin.analysis.projects`` to invoke the CLI."""

from .cli import main

raise SystemExit(main())
