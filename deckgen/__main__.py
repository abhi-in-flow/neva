"""Module entrypoint for ``python -m deckgen``.

Delegates to ``deckgen.cli.main`` so the package can be invoked as a module
without installing a console script. See ``deckgen.cli`` for flags.
"""

from __future__ import annotations

from deckgen.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
