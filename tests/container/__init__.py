"""Static contract tests for the shared production container artifact.

These tests parse ``Dockerfile`` and ``.dockerignore`` without requiring a
Docker daemon. An optional integration test attempts ``docker build`` when the
CLI is available on the host.
"""
