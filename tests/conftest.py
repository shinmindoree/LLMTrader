"""conftest — make src/ importable for the unit test suite.

The project layout is::

    src/
      live/
      control/
      ...

There's no ``pyproject.toml`` pytest config nor an installed package,
so we extend ``sys.path`` here. Keeps the unit tests self-contained.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC_DIR = _REPO_ROOT / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))
