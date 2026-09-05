"""Test suite bootstrap for quality-gates-toolkit.

Makes both import surfaces available:
- toolkit root (for the `scripts` package, e.g. `from scripts.redaction import ...`)
- scripts dir itself (for the flat module imports the runtime uses, e.g.
  `import review` / `from enrichment import ...` — review.py inserts the
  scripts dir into sys.path the same way)

In the origin repository the tests lived inside scripts/ and derived paths
from __file__; this toolkit keeps tests/ separate, so the path wiring lives
here instead.
"""

import sys
from pathlib import Path

TOOLKIT_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = TOOLKIT_ROOT / "scripts"

for path in (TOOLKIT_ROOT, SCRIPTS_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
