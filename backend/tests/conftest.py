"""Make the backend package importable when pytest runs from the repository
root (commands per EVA_WORK_PLAN_CORE.md: python -m pytest backend/tests/...)."""

import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))
