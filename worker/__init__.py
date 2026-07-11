"""Standalone cleaning gauntlet package.

This package owns asynchronous processing from durable ``jobs`` rows through
audio cleaning, model quality gates, golden-record packaging, and append-only
corpus shards. It deliberately contains no FastAPI imports or game rules.
"""
