"""Make the backend package importable when pytest runs from the repository
root (commands per EVA_WORK_PLAN_CORE.md: python -m pytest backend/tests/...).

Also installs a session-wide HERMETIC GUARD for inference configuration. The
self-hosted route is configured by a machine-local `.env` (see .env.example),
which pydantic-settings auto-loads from CWD (`.env`) and the repo root
(`../.env`). A real developer `.env` (a live endpoint + key) must NEVER leak
into the suite. An autouse fixture temporarily moves any such auto-loaded file
aside for the duration of the session and restores it afterwards.

This approach is deliberately chosen over blanking OS environment variables:
blanking env vars would also shadow tests that INTENTIONALLY load their own
`_env_file=<temp dotenv>` (env source outranks the dotenv source in
pydantic-settings), breaking them. Renaming only the ambient auto-load targets
leaves every test's explicit `_env_file=` and init kwargs fully intact while
guaranteeing no ambient repo `.env` configures a default `Settings()`.
"""

import os
import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

#: The exact auto-load targets from Settings.model_config.env_file.
_AUTOLOAD_DOTENV = (BACKEND_ROOT / ".env", REPO_ROOT / ".env")


@pytest.fixture(scope="session", autouse=True)
def _isolate_ambient_dotenv():
    """Move any real local `.env` aside so no default `Settings()` inherits it."""
    moved: list[tuple[Path, Path]] = []
    for dotenv in _AUTOLOAD_DOTENV:
        if dotenv.is_file():
            backup = dotenv.with_name(dotenv.name + ".eva-testbak")
            # Avoid clobbering an existing backup (should not happen).
            suffix = 0
            while backup.exists():
                suffix += 1
                backup = dotenv.with_name(f"{dotenv.name}.eva-testbak{suffix}")
            os.replace(dotenv, backup)
            moved.append((dotenv, backup))
    try:
        yield
    finally:
        for dotenv, backup in reversed(moved):
            if backup.exists():
                os.replace(backup, dotenv)

