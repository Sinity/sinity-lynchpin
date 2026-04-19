"""Allow ``python -m lynchpin.analysis`` to invoke the CLI."""

from .cli import main

raise SystemExit(main())
