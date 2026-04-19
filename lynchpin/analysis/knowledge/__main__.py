"""Allow ``python -m lynchpin.analysis.knowledge`` to invoke the CLI."""

from .cli import main

raise SystemExit(main())
