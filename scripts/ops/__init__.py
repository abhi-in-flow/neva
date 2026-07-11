"""Wave 2 operational recovery helpers for backup, restore, and health checks.

Python modules in this package provide testable path validation, checksum
manifest generation, and command construction. Bash entrypoints under
``scripts/ops/neva-ops.sh`` orchestrate Compose, Postgres, and runtime-data
operations while delegating safety checks here.
"""
