"""Module entrypoint for ``python -m tools.load``."""

from __future__ import annotations

import logging

from tools.load.runner import main

LOGGER = logging.getLogger(__name__)

if __name__ == "__main__":
    LOGGER.info("__main__ invoked")
    raise SystemExit(main())
