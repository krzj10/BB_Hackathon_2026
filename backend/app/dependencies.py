"""Composition dependencies (Stream A owns this file; see plan section 3).

A00 exposes settings only. As tasks land, registration points are:
- A01: get_session_factory()/repositories
- A02: Google auth/Calendar/Gmail services
- A03/A04: policy engine, ToolRegistry, ToolExecutor
- A05: STT providers
- B01: LLM provider router (imports this module's get_settings)
- B03/B04: agent/briefing/attention/focus/decisions services and routers
  (B supplies explicit registration instructions; A applies them here and in
  main.py - B never edits these files).
"""

from __future__ import annotations

from functools import lru_cache

from .config import Settings


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
