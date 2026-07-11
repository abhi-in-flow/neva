"""Filesystem-backed administration boundary for isolated Gemma tuning.

The package exposes safe overview, queue, status, and held-out audio services
to FastAPI while deliberately containing no imports from the isolated
``tune`` harness and no subprocess or GPU execution.
"""
