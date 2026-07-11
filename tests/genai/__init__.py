"""Test package for the shared GenAI client.

All tests inject fakes; they must never call the live Gemini API or mutate
Postgres / runtime ``data/`` paths.
"""
