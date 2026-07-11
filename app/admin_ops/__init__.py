"""Demo-grade operator read APIs for metrics traces and gauntlet health.

This package is the architectural boundary for protected admin observability.
It reuses the deck-admin shared key, never mutates game rules, and redacts
prompt text before returning ``api_calls`` metadata to the browser.
"""
