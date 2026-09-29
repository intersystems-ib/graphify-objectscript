"""Make the package importable from a source checkout during local
iteration, without needing an editable install first. Purely a dev
convenience: the verification steps in README.md install the built wheel
into a fresh venv, and importing graphify_objectscript that way behaves
identically to importing it via this fallback path."""
from __future__ import annotations

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
