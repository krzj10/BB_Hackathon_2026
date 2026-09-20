"""Deterministic, hackathon-safe demo data provider (synthetic only).

Everything downstream of ingestion stays the REAL pipeline: this package only
replaces the external Gmail boundary. Demo items therefore travel through the
real AttentionEngine rules, the real Decision projection, Focus delivery and
the guarded approval lifecycle.

Enabled by configuration (``EVA_DATA_PROVIDER=demo``); the default provider is
``google`` and nothing here runs in that mode.
"""
