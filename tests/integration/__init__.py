"""Wave 2 isolated end-to-end acceptance gate for Dialect Data Factory.

This package owns opt-in integration tests that exercise the real Postgres
schema, HTTP API contracts, fake-triage worker, and append-only corpus path
using an operator-provisioned isolated database and ``DATA_DIR``. Ordinary
pytest collection never invokes Docker or paid GenAI; the live gate runs only
when ``WAVE2_E2E_DATABASE_URL`` and ``WAVE2_E2E_DATA_DIR`` pass fail-closed
guards.
"""
