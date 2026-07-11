"""Isolated venue load-testing toolkit for Wave 2 integration.

This package simulates authenticated polling clients, bounded join/pair/action
traffic, and optional fixture uploads against a non-production target. It
never imports FastAPI or worker code; HTTP and observability dependencies are
injectable. All mutating runs require an explicit isolated marker plus loopback
host, load-specific database metadata, and a DATA_DIR outside the repository
``data/`` tree.

Architectural boundary:
- Owned exclusively by the Wave 2 load-tester agent.
- Reads ``contracts/api_types.py`` shapes at the HTTP layer only.
- Reports shared observability and fake-worker seams; does not edit app/worker.
"""

from tools.load.config import LoadConfig, load_config

__all__ = ["LoadConfig", "load_config"]
