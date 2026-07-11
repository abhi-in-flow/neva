"""Phase 0 package marker for foundation smoke tests.

Tests in this package must remain non-destructive: use temporary directories
and mocked database pools so they never mutate the development Postgres
database or committed runtime assets.
"""
